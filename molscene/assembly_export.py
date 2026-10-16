"""Transactional closed bundles for deposited biological assemblies."""
import csv
from datetime import datetime, timezone
import json
import math

from . import __version__
from .artifacts import checked_path, sha256, write_json
from .bundles import Bundle
from .assembly import (assembly_catalog, validate_assembly_request, build_assembly,
                       _stage, _verify_atoms, MAX_ATOMS, MAX_COPIES)


def _replay(path,copy_files):
    # Literals carry all user data; no interpolated PyMOL command syntax.
    lines=['# Standalone deposited assembly replay. Run this script in PyMOL.',
           'python','from pathlib import Path',
           '_assembly_root = Path(__script__).resolve().parent',
           '_assembly_files = '+ascii(copy_files),
           "_assembly_reader = (cmd.get_setting_text('assembly'), cmd.get_setting_int('cif_use_auth'))",
           'try:',
           "    cmd.set('assembly', '')",
           "    cmd.set('cif_use_auth', 1)",
           '    for _assembly_copy in _assembly_files:',
           "        _assembly_name = _assembly_copy['object_name']",
           "        if _assembly_name in cmd.get_names('all'):",
           "            _assembly_name = cmd.get_unused_name(_assembly_name)",
           "        cmd.load(str(_assembly_root / _assembly_copy['path']), _assembly_name, format='cif', zoom=0)",
           'finally:',
           "    cmd.set('assembly', _assembly_reader[0])",
           "    cmd.set('cif_use_auth', _assembly_reader[1])",
           'python end','']
    path.write_text('\n'.join(lines),encoding='utf-8')


def export_assembly(input_session,output_dir,request,*,cancelled=None,progress=None,protected=()):
    """Build from a deposited file only and publish a fully checked new bundle.

    Report copies are build rows with relative file paths. copy_files is a
    list of {object_name,path,atom_count,sha256,bytes}; paths are bundle-relative.
    parameters is the canonical request with source_path made bundle-relative.
    """
    request=validate_assembly_request(request)
    if input_session is not None: raise ValueError('Deposited assembly builds require input_session=None')
    source=checked_path(request['source_path']); catalog=assembly_catalog(source)
    chosen=next((a for a in catalog['assemblies'] if a['assembly_id']==request['assembly_id']),None)
    if chosen is None: raise ValueError('Requested assembly ID is absent')
    relative='inputs/source'+source.suffix.lower()
    files=[f'copies/copy_{i:03d}.cif' for i in range(1,chosen['copy_count']+1)]
    inventory=[relative,*files,'assembly.pse','copies.csv','replay.pml','report.json','manifest.json']
    _stage(cancelled,progress,'validate',0,4,'Validated assembly export inputs')
    with Bundle(output_dir,protected=tuple(protected)+(source,),inventory=inventory,
                cancelled=cancelled,new_directory_only=True) as bundle:
        inputs={'source':bundle.copy_input(source,relative)}
        if inputs['source']['sha256']!=catalog['source_sha256']:
            raise ValueError('Assembly source changed between catalog and input capture')
        from .engine import _runtime
        from .analysis import collect_atoms
        with _runtime().PyMOL() as pm:
            result=build_assembly(pm.cmd,bundle.stage/relative,assembly_id=request['assembly_id'],
                state=request['state'],cancelled=cancelled,progress=progress)
            _stage(cancelled,progress,'export_copies',1,4,'Saving independently reloadable assembly copies')
            copy_files=[]
            for row,path in zip(result['copies'],files):
                _stage(cancelled,None,'export_copy',1,4,'Saving assembly copy')
                target=bundle.stage/path; target.parent.mkdir(exist_ok=True)
                pm.cmd.save(str(target),'%'+row['object_name'],state=1,format='cif',quiet=1)
                row['path']=path
                copy_files.append(dict(object_name=row['object_name'],path=path,atom_count=row['atom_count'],
                    sha256=sha256(target),bytes=target.stat().st_size))
            pm.cmd.save(str(bundle.stage/'assembly.pse'),quiet=1)
            version=pm.cmd.get_version()[0]
        _stage(cancelled,progress,'reload',2,4,'Independently validating core identities and world geometry')
        max_error=0.; verified=0
        with _runtime().PyMOL() as reload:
            # Per-copy CIFs have no assembly metadata. Explicit reader identity
            # policy is scoped to this owned validation runtime.
            reload.cmd.set('assembly',''); reload.cmd.set('cif_use_auth',1)
            for row in result['copies']:
                _stage(cancelled,None,'reload_copy',2,4,'Validating assembly copy')
                reload.cmd.load(str(bundle.stage/row['path']),'verify',format='cif',zoom=0)
                actual=sorted(collect_atoms(reload.cmd,'verify'),key=lambda a:a['index'])
                expected=[dict(a,index=i) for i,a in enumerate(row['source_atoms'],1)]
                _verify_atoms(actual,expected)
                max_error=max(max_error,max(math.dist(a['coord'],b['coord']) for a,b in zip(actual,expected)))
                verified+=len(actual); reload.cmd.delete('verify')
        with (bundle.stage/'copies.csv').open('w',encoding='utf-8',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=['object_name','path','generation_row','asym_ids','author_chains',
                'oper_expression','operator_ids','matrix','atom_count'])
            writer.writeheader()
            for row in result['copies']:
                writer.writerow({k:json.dumps(row[k],ensure_ascii=False) if isinstance(row[k],list) else row[k] for k in writer.fieldnames})
        _replay(bundle.stage/'replay.pml',copy_files)
        parameters=dict(request,source_path=relative)
        # Never publish an ephemeral private-stage absolute path in provenance.
        result.pop('source_path',None)
        report=dict(result,schema_version=2,tool='molscene',tool_version=__version__,pymol_version=version,
            created_at=datetime.now(timezone.utc).isoformat(),inputs=inputs,parameters=parameters,
            copy_files=copy_files,passed=True,warnings=[],
            reload_validation=dict(tolerance_angstrom=.001,max_coordinate_error_angstrom=max_error,
                error_metric='Euclidean distance per atom',verified_atom_count=verified,
                core_identity_fields=['segi','chain','resi','resn','name','element','alt']),
            bounds=dict(max_source_bytes=20*1024*1024,max_copies=MAX_COPIES,max_generated_atoms=MAX_ATOMS),
            artifacts=dict(source=relative,session='assembly.pse',copies='copies.csv',replay='replay.pml',
                report='report.json',manifest='manifest.json'),
            replay_contract='Relative deposited input and per-copy CIFs; public PyMOL only, no MolScene dependency',
            artifact_hashes={name:sha256(bundle.stage/name) for name in inventory if name not in ('report.json','manifest.json')})
        write_json(bundle.stage/'report.json',report)
        _stage(cancelled,progress,'publish',4,4,'Publishing fully verified assembly bundle')
        bundle.publish()
        return report
