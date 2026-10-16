"""Deposited biological assemblies, exact native membership and provenance."""
import itertools
import hashlib
import math
from pathlib import Path
import re

from .artifacts import checked_path, sha256
from .bundles import CancelledError
from .cif_tables import MAX_BYTES, read_tables
from .grids import compose, validate_rigid

MAX_COPIES = 512
MAX_ATOMS = 200_000
IDENTITY = [1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.]
CORE_IDENTITY = ('segi','chain','resi','resn','name','element','alt')


def validate_assembly_request(request):
    if not isinstance(request, dict) or set(request)-{'source_path','assembly_id','state'} or 'source_path' not in request:
        raise ValueError('Assembly requires source_path and only assembly_id/state options')
    source = request['source_path']; aid = request.get('assembly_id','1'); state = request.get('state',1)
    if not isinstance(source,str) or not source or any(c in source for c in '\r\n\x00'):
        raise ValueError('source_path must be a nonempty single-line path')
    if not isinstance(aid,str) or not aid.strip() or len(aid)>128 or any(c in aid for c in '\r\n\x00'):
        raise ValueError('assembly_id must be a nonempty bounded identifier')
    if type(state) is not int or state < 1:
        raise ValueError('state must be a positive integer')
    return dict(source_path=str(checked_path(source)), assembly_id=aid, state=state)


def _source(path):
    path = checked_path(path)
    if path.suffix.lower() not in ('.pdb','.cif','.mmcif') or not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES:
        raise ValueError('Assembly source must be a nonempty local PDB/mmCIF up to 20 MiB')
    before=path.stat()
    try:
        with path.open('rb') as handle: data=handle.read(MAX_BYTES+1)
        if len(data)>MAX_BYTES: raise ValueError('Assembly source exceeds 20 MiB')
        text=data.decode('utf-8-sig')
    except UnicodeError as exc: raise ValueError('Assembly source must be UTF-8 text') from exc
    if '\x00' in text: raise ValueError('NUL in coordinate source')
    digest=hashlib.sha256(data).hexdigest(); after=path.stat()
    if ((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns) !=
            (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns) or sha256(path)!=digest):
        raise ValueError('Assembly source changed during metadata reading')
    return path, text, digest


def expand_operations(expression, operators):
    """Expand bounded lists/ranges/products, composing rightmost first."""
    if not isinstance(expression,str) or not expression or len(expression)>4096:
        raise ValueError('Missing or oversized operator expression')
    expr = re.sub(r'\s+', '', expression)
    if '(' in expr or ')' in expr:
        groups = re.findall(r'\(([^()]*)\)',expr)
        if ''.join('('+g+')' for g in groups) != expr:
            raise ValueError('Malformed Cartesian operator expression')
    else: groups = [expr]
    expanded, cardinality = [], 1
    for group in groups:
        ids = []
        for item in group.split(','):
            match = re.fullmatch(r'(\d+)-(\d+)',item)
            if match:
                low,high = map(int,match.groups())
                if high < low or high-low+1>MAX_COPIES: raise ValueError('Invalid or excessive operator range')
                ids.extend(str(n) for n in range(low,high+1))
            elif re.fullmatch(r'[A-Za-z0-9_]+',item): ids.append(item)
            else: raise ValueError('Unsupported operator identifier or expression')
            if len(ids)>MAX_COPIES: raise ValueError('Operator count exceeds 512')
        if any(i not in operators for i in ids): raise ValueError('Operator expression references an absent ID')
        cardinality *= len(ids)
        if cardinality > MAX_COPIES: raise ValueError('Expanded operator count exceeds 512')
        expanded.append(ids)
    result=[]
    for sequence in itertools.product(*expanded):
        matrix=IDENTITY
        for oid in sequence: matrix=compose(matrix,operators[oid])
        result.append(dict(operator_ids=list(sequence),matrix=list(validate_rigid(matrix))))
    return result


def _required(row,key):
    value=row.get(key)
    if not isinstance(value,str) or not value: raise ValueError('Missing assembly metadata: '+key)
    return value


def _chain_list(value):
    parts=[p.strip() for p in value.split(',') if p.strip()]
    if not parts or len(set(parts)) != len(parts): raise ValueError('Missing or duplicate assembly chain IDs')
    return parts


def _cif(text):
    tables=read_tables(text,categories={'_pdbx_struct_assembly','_pdbx_struct_assembly_gen',
        '_pdbx_struct_oper_list','_struct_asym','_atom_site'})
    operators={}
    for row in tables.get('_pdbx_struct_oper_list',[]):
        oid=_required(row,'id')
        if oid in operators: raise ValueError('Duplicate operator ID')
        try:
            matrix=[]
            for i in (1,2,3):
                matrix.extend(float(_required(row,f'matrix[{i}][{j}]')) for j in (1,2,3))
                matrix.append(float(_required(row,f'vector[{i}]')))
            matrix.extend((0.,0.,0.,1.))
            operators[oid]=list(validate_rigid(matrix))
        except (TypeError,OverflowError) as exc: raise ValueError('Invalid assembly matrix') from exc
    asym=[_required(r,'id') for r in tables.get('_struct_asym',[])]
    if len(set(asym)) != len(asym): raise ValueError('Duplicate struct_asym ID')
    atom_asym={_required(r,'label_asym_id') for r in tables.get('_atom_site',[])}
    known=set(asym) if asym else atom_asym
    assemblies={}
    for row in tables.get('_pdbx_struct_assembly',[]):
        aid=_required(row,'id')
        if aid in assemblies: raise ValueError('Duplicate assembly ID')
        assemblies[aid]=dict(assembly_id=aid,details=row.get('details'),method_details=row.get('method_details'),generations=[])
    for number,row in enumerate(tables.get('_pdbx_struct_assembly_gen',[]),1):
        aid=_required(row,'assembly_id')
        if aid not in assemblies: raise ValueError('Generation references missing assembly ID')
        ids=_chain_list(_required(row,'asym_id_list'))
        if set(ids)-known or set(ids)-atom_asym: raise ValueError('Assembly references absent label asym chain')
        expression=_required(row,'oper_expression')
        assemblies[aid]['generations'].append(dict(generation_row=number,asym_ids=ids,
            oper_expression=expression,copies=expand_operations(expression,operators)))
        if sum(len(g['copies']) for g in assemblies[aid]['generations'])>MAX_COPIES:
            raise ValueError('Assembly copy count exceeds 512')
    return list(assemblies.values()),len(operators)


def _pdb(text):
    assemblies={}; active=[]; group=None; rownum=0; all_operators=0
    chains={line[21:22].strip() for line in text.splitlines() if line.startswith(('ATOM  ','HETATM'))}
    def finish():
        nonlocal all_operators
        if group is None: return
        if not group['operators']: raise ValueError('Assembly application has no BIOMT operators')
        copies=[]
        for oid,rows in group['operators'].items():
            if set(rows)!={1,2,3}: raise ValueError('Incomplete BIOMT operator')
            matrix=[v for i in (1,2,3) for v in rows[i]]+[0.,0.,0.,1.]
            copies.append(dict(operator_ids=[oid],matrix=list(validate_rigid(matrix))))
        if set(group['asym_ids'])-chains: raise ValueError('Assembly references absent PDB chain')
        for aid in active:
            assemblies[aid]['generations'].append(dict(generation_row=group['generation_row'],
                asym_ids=list(group['asym_ids']),oper_expression=','.join(group['operators']),copies=copies))
            if sum(len(g['copies']) for g in assemblies[aid]['generations'])>MAX_COPIES:
                raise ValueError('Assembly copy count exceeds 512')
        all_operators += len(copies)
    for line in text.splitlines():
        if not line.startswith('REMARK 350'): continue
        body=line[10:].strip()
        if body.startswith('BIOMOLECULE:'):
            finish(); group=None
            active=_chain_list(body.split(':',1)[1])
            for aid in active:
                if aid in assemblies: raise ValueError('Duplicate PDB biomolecule ID')
                assemblies[aid]=dict(assembly_id=aid,details='Deposited PDB REMARK 350',method_details=None,generations=[])
        elif 'APPLY THE FOLLOWING TO CHAINS:' in body:
            finish()
            if not active: raise ValueError('Orphan PDB chain application')
            rownum+=1
            group=dict(generation_row=rownum,asym_ids=[],operators={})
            group['asym_ids']=['' if p=='NULL' else p for p in _chain_list(body.split(':',1)[1])]
        elif body.startswith('AND CHAINS:'):
            if group is None or group['operators']: raise ValueError('Orphan PDB chain continuation')
            group['asym_ids'].extend('' if p=='NULL' else p for p in _chain_list(body.split(':',1)[1]))
            if len(set(group['asym_ids']))!=len(group['asym_ids']): raise ValueError('Duplicate PDB chain')
        elif body.startswith('BIOMT'):
            match=re.fullmatch(r'BIOMT([123])\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)',body)
            if not match or group is None: raise ValueError('Malformed or orphan BIOMT row')
            n,oid,*values=match.groups(); rows=group['operators'].setdefault(oid,{})
            if int(n) in rows: raise ValueError('Duplicate BIOMT row')
            try: rows[int(n)]=[float(v) for v in values]
            except (ValueError,OverflowError) as exc: raise ValueError('Invalid BIOMT numbers') from exc
    finish()
    return list(assemblies.values()),all_operators


def assembly_catalog(path):
    path,text,digest=_source(path); fmt='pdb' if path.suffix.lower()=='.pdb' else 'cif'
    assemblies,operator_count=(_pdb(text) if fmt=='pdb' else _cif(text))
    if not assemblies: raise ValueError('Source has no deposited biological assembly definitions')
    for row in assemblies:
        row['copy_count']=sum(len(g['copies']) for g in row['generations'])
        row['generation_count']=len(row['generations'])
        row['chain_count']=len({c for g in row['generations'] for c in g['asym_ids']})
        if not 0 < row['copy_count'] <= MAX_COPIES: raise ValueError('Assembly must generate 1..512 copies')
    return dict(source_path=str(path),source_sha256=digest,format=fmt,
        chain_namespace='chain' if fmt=='pdb' else 'label_asym_id',operator_count=operator_count,assemblies=assemblies)


def _stage(cancelled,progress,label,completed,total,message):
    if cancelled is not None and cancelled(): raise CancelledError('Operation cancelled')
    if progress is not None: progress(dict(stage=label,completed=completed,total=total,message=message))


def _core(atom):
    return {k:atom[k] for k in CORE_IDENTITY}


def _verify_atoms(actual,expected,*,tolerance=.001):
    if len(actual)!=len(expected): raise ValueError('Copy atom count changed')
    for a,b in zip(sorted(actual,key=lambda a:a['index']),expected):
        if _core(a)!=_core(b) or math.dist(a['coord'],b['coord'])>tolerance:
            raise ValueError('Copy core identity or world geometry changed')


def build_assembly(cmd,source_path,*,assembly_id='1',state=1,cancelled=None,progress=None):
    """Create owned state-1 molecular copies, leaving live molecules untouched.

    Copy rows expose object_name, generation_row, asym_ids, author_chains,
    oper_expression, operator_ids, matrix, atom_count and source_atoms (original
    index, core identity, source coordinate and independently transformed target).
    """
    request=validate_assembly_request(dict(source_path=str(source_path),assembly_id=assembly_id,state=state))
    catalog=assembly_catalog(request['source_path'])
    chosen=next((a for a in catalog['assemblies'] if a['assembly_id']==assembly_id),None)
    if chosen is None: raise ValueError('Requested assembly ID is absent')
    _stage(cancelled,progress,'validate',0,chosen['copy_count'],'Validated deposited assembly')
    from .analysis import collect_atoms
    source=cmd.get_unused_name('_molscene_assembly_asu'); owned=[]
    selection=cmd.get_unused_name('_molscene_assembly_members')
    enabled=cmd.get_names('selections',enabled_only=1)
    try:
        # These reader settings are global-only. Isolate them from the passed
        # live cmd, including when its cif_use_auth/assembly policy differs.
        from .engine import _runtime
        with _runtime().PyMOL() as reader:
            reader.cmd.set('assembly','')
            reader.cmd.set('cif_use_auth',1)
            reader.cmd.load(request['source_path'],'assembly_source',format=catalog['format'],zoom=0,quiet=1,multiplex=0)
            if state>reader.cmd.count_states('assembly_source'):
                raise ValueError('Requested source coordinate state is absent')
            original=sorted(collect_atoms(reader.cmd,'assembly_source',state),key=lambda a:a['index'])
            cmd.load_model(reader.cmd.get_model('assembly_source',state),source,state=1,zoom=0,quiet=1)
        atoms=sorted(collect_atoms(cmd,'%'+source,1),key=lambda a:a['index'])
        _verify_atoms(atoms,original)
        # load_model may renumber object-local indices (e.g. deleted/filtered
        # coordinate rows). The verified ordinal/core-identity transfer binds
        # original source indices separately from exact destination indices.
        for atom,source_atom in zip(atoms,original):
            atom['source_index']=source_atom['index']
        namespace='chain' if catalog['format']=='pdb' else 'segi'
        plans=[]; total=0
        for gen in chosen['generations']:
            members=[a for a in atoms if a[namespace] in gen['asym_ids']]
            if set(gen['asym_ids'])-{a[namespace] for a in members}:
                raise ValueError('Native source membership does not match deposited chain namespace')
            for operation in gen['copies']:
                total+=len(members)
                if total>MAX_ATOMS: raise ValueError('Generated atom count exceeds 200000')
                plans.append((gen,operation,members))
        if not total: raise ValueError('Assembly has no atoms')
        copies=[]
        for number,(gen,operation,members) in enumerate(plans,1):
            _stage(cancelled,progress,'copy',number-1,len(plans),'Creating deposited assembly copy')
            name=cmd.get_unused_name('molscene_assembly_copy'); owned.append(name)
            cmd.select_list(selection,source,[a['index'] for a in members],state=1,mode='index',quiet=1)
            cmd.disable(selection)
            cmd.create(name,'%'+selection,source_state=1,target_state=1,zoom=0,quiet=1)
            matrix=operation['matrix']
            cmd.transform_object(name,matrix,state=1,homogenous=1,transpose=0)
            expected=[]; mapping=[]
            for atom in members:
                target=[sum(matrix[i*4+j]*atom['coord'][j] for j in range(3))+matrix[i*4+3] for i in range(3)]
                expected.append(dict(atom,coord=target))
                mapping.append(dict(_core(atom),source_index=atom['source_index'],source_coord=atom['coord'],coord=target))
            _verify_atoms(collect_atoms(cmd,'%'+name,1),expected)
            copies.append(dict(object_name=name,generation_row=gen['generation_row'],asym_ids=list(gen['asym_ids']),
                author_chains=sorted({a['chain'] for a in members}),oper_expression=gen['oper_expression'],
                operator_ids=list(operation['operator_ids']),matrix=list(matrix),atom_count=len(members),source_atoms=mapping))
        if sha256(request['source_path'])!=catalog['source_sha256']: raise ValueError('Source changed during assembly build')
        _stage(cancelled,progress,'complete',len(plans),len(plans),'Verified assembly copies')
        return dict(operation='assembly',source_path=request['source_path'],source_sha256=catalog['source_sha256'],
            format=catalog['format'],assembly_id=assembly_id,state=state,chain_namespace=catalog['chain_namespace'],
            details=chosen['details'],copy_count=len(copies),atom_count=total,source_atom_count=len(atoms),copies=copies,
            limitations=['Only deposited assembly instructions are materialized; no contact or lattice inference.',
                'Each declared generation/operator copy is a separate state-1 object; identifiers are preserved.'])
    except BaseException:
        for name in owned: cmd.delete(name)
        raise
    finally:
        cmd.delete(selection); cmd.delete(source)
        existing=set(cmd.get_names('selections'))
        for name in enabled:
            if name in existing: cmd.enable(name)
