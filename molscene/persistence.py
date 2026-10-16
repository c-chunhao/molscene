"""Guarded coordinate and byte-preserved session snapshot bundles; no renderer."""
from collections import defaultdict
from copy import deepcopy
import csv
from datetime import datetime, timezone
import math
from pathlib import Path
import re

from . import __version__
from .artifacts import checked_path, sha256, write_json
from .bundles import Bundle, CancelledError

CORE_IDENTITY = ('element', 'name', 'resn', 'resi', 'chain', 'segi', 'alt')
MAX_ATOMS = 200_000
MAX_OBJECTS = 1_000


def _frozen_selection(members, *, object_key='object', index_key='index'):
    """Encode exact membership once per object, merging only adjacent indices."""
    groups = defaultdict(set)
    for row in members:
        groups[row[object_key]].add(row[index_key])
    clauses = []
    for name, indices in sorted(groups.items()):
        spans = []
        ordered = sorted(indices)
        start = end = ordered[0]
        for index in ordered[1:]:
            if index == end + 1:
                end = index
            else:
                spans.append(str(start) if start == end else f'{start}-{end}')
                start = end = index
        spans.append(str(start) if start == end else f'{start}-{end}')
        clauses.append('(%'+name+' and index '+'+'.join(spans)+')')
    return ' or '.join(clauses) if clauses else 'none'


def validate_persistence_request(operation, request):
    """Normalize only public, backend-free persistence parameters."""
    if operation not in ('coordinates', 'session_save') or not isinstance(request, dict):
        raise ValueError('Expected coordinates or session_save and a request dictionary')
    allowed = {'selection', 'state', 'format'} if operation == 'coordinates' else set()
    if set(request) - allowed:
        raise ValueError('Unexpected persistence request fields')
    if operation == 'session_save':
        return {}
    from .analysis import _selection
    result = dict(selection='all', state=1, format='cif')
    result.update(deepcopy(request))
    _selection(result['selection'])
    if type(result['state']) is not int or result['state'] < 1:
        raise ValueError('state must be an explicit positive integer')
    if result['format'] not in ('cif', 'pdb'):
        raise ValueError('Coordinate format must be cif or pdb')
    return result


def _cancel(cancelled):
    if cancelled is not None and cancelled():
        raise CancelledError('Operation cancelled')


def _members(cmd, selection, state):
    from .analysis import _resolved_selection
    # Bind spatial predicates at the explicit state; singleton fallback is
    # rejected by _resolved_selection's native coordinate-set check.
    with _resolved_selection(cmd, selection, state) as resolved:
        model = cmd.get_model(resolved, state=state)
    if len(model.atom) > MAX_ATOMS:
        raise ValueError('Coordinate export exceeds 200,000 atoms')
    if len({atom.model for atom in model.atom}) > MAX_OBJECTS:
        raise ValueError('Coordinate export exceeds 1,000 objects')
    rows = []
    for atom in model.atom:
        row = dict(source_object=atom.model, source_index=int(atom.index), source_id=int(atom.id),
                   source_state=state, element=atom.symbol, name=atom.name, resn=atom.resn,
                   resi=atom.resi, chain=atom.chain, segi=atom.segi, alt=atom.alt,
                   coord=[float(value) for value in atom.coord], b=float(atom.b),
                   occupancy=float(atom.q), vdw=float(atom.vdw),
                   formal_charge=int(atom.formal_charge), partial_charge=float(atom.partial_charge))
        if not all(math.isfinite(value) for value in (*row['coord'], row['b'], row['occupancy'], row['vdw'], row['partial_charge'])):
            raise ValueError('Coordinate export requires finite atom properties')
        rows.append(row)
    return sorted(rows, key=lambda row: (row['source_object'], row['source_index']))


def _pdb_preflight(rows):
    """Check portable PDB fixed-width fields before invoking its native writer."""
    counts = defaultdict(int)
    for row in rows:
        counts[row['source_object']] += 1
        for key, width in (('element', 2), ('name', 4), ('resn', 3), ('chain', 1), ('segi', 4), ('alt', 1)):
            value = row[key]
            if not value.isascii() or len(value) > width or any(ord(c) < 32 or ord(c) > 126 for c in value):
                raise ValueError('PDB '+key+' does not fit its ASCII field; use cif')
        match = re.fullmatch(r'(-?\d+)([A-Za-z]?)', row['resi'])
        if not match or not row['resi'].isascii() or len(match[1]) > 4:
            raise ValueError('PDB residue/insertion identifier does not fit; use cif')
        if any(len(f'{value:8.3f}') > 8 for value in row['coord']):
            raise ValueError('PDB coordinate does not fit its 8.3 field; use cif')
        # These noncore properties can lose precision, but must not shift columns.
        if any(len(f'{row[key]:6.2f}') > 6 for key in ('b', 'occupancy')):
            raise ValueError('PDB B-factor/occupancy field overflow; use cif')
    if any(count > 99_999 for count in counts.values()):
        raise ValueError('PDB atom serial field exceeds 99,999 atoms per object; use cif')


def _verify(cmd, path, expected):
    """Match core identity plus world geometry, never reloaded native indices."""
    cmd.reinitialize()
    cmd.load(str(path), 'exported', quiet=1)
    actual = cmd.get_model('all', state=1).atom
    if len(actual) != len(expected):
        raise ValueError('Coordinate reload atom count does not match source')
    # A small spatial cell index avoids quadratic duplicate-identity matching.
    cells = defaultdict(list)
    def cell(coord):
        return tuple(math.floor(value / 0.002) for value in coord)
    for index, row in enumerate(expected):
        cells[tuple(row[key] for key in CORE_IDENTITY), cell(row['coord'])].append(index)
    maximum = 0.0
    for atom in actual:
        identity = (atom.symbol, atom.name, atom.resn, atom.resi, atom.chain, atom.segi, atom.alt)
        center = cell(atom.coord)
        found = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    key = identity, (center[0]+dx, center[1]+dy, center[2]+dz)
                    candidates = cells.get(key, ())
                    for offset in range(len(candidates)-1, -1, -1):
                        distance = math.dist(atom.coord, expected[candidates[offset]]['coord'])
                        if distance <= 0.001:
                            found = key, offset, distance
                            break
                    if found:
                        break
                if found:
                    break
            if found:
                break
        if found is None:
            raise ValueError('Coordinate reload core identity or world geometry differs from source')
        key, offset, distance = found
        candidates = cells[key]
        candidates[offset] = candidates[-1]
        candidates.pop()
        maximum = max(maximum, distance)
    return dict(path=path.name, atom_count=len(actual), core_identity=list(CORE_IDENTITY),
                reload_state=1, max_euclidean_error_angstrom=maximum, passed=True)


def _write_replay(path, operation, object_files):
    lines = ['# Portable saved snapshot using public PyMOL calls only.', 'python',
             'from pathlib import Path',
             '_persistence_root = Path(__script__).resolve().parent']
    if operation == 'session_save':
        lines.append('cmd.load(str(_persistence_root / "session.pse"))')
    else:
        for row in object_files:
            # Load into safe generated names; source names remain explicit metadata.
            lines.append('cmd.load(str(_persistence_root / '+repr(row['path'])+'), '+repr('saved_'+f"{row['number']:04d}")+')')
    lines += ['python end', '']
    path.write_text('\n'.join(lines), encoding='utf-8')


def export_persistence(input_session, output_dir, operation, request, *, cancelled=None, progress=None, protected=(), source_input=None):
    """Export a protected frozen PSE, publish only a complete verified bundle."""
    request = validate_persistence_request(operation, request)
    _cancel(cancelled)
    from .engine import _runtime, _session
    session = _session(checked_path(input_session))
    output = checked_path(output_dir)
    # Protect/validate before any backend work. The dynamic inventory is known
    # only after bounded coordinate membership has been resolved.
    if source_input is not None and (not isinstance(source_input, dict) or set(source_input) != {'path','sha256'}
            or not isinstance(source_input['sha256'],str) or re.fullmatch('[0-9a-f]{64}',source_input['sha256']) is None):
        raise ValueError('Original input provenance requires path and captured sha256')
    source = checked_path(source_input['path']) if source_input is not None else None
    if source is not None and (operation != 'coordinates' or source.suffix.lower() not in ('.pdb','.cif','.mmcif','.pse')):
        raise ValueError('Original coordinate input must be a PDB/mmCIF/PSE file')
    protected = (session, *protected, *((source,) if source is not None else ()))
    Bundle(output, protected=protected, inventory=('report.json','manifest.json'),
           cancelled=cancelled, new_directory_only=True)
    snapshot_digest = sha256(session)
    if source is not None and sha256(source) != source_input['sha256']:
        raise ValueError('Original coordinate input changed after snapshot capture')
    rows, object_files, diagnostics = [], [], []
    runtime = _runtime()
    with runtime.PyMOL() as pm:
        cmd = pm.cmd
        cmd.load(str(session), quiet=1)
        if sha256(session) != snapshot_digest:
            raise ValueError('Source snapshot changed while loading')
        version = cmd.get_version()[0]
        if operation == 'coordinates':
            rows = _members(cmd, request['selection'], request['state'])
            request['selection'] = _frozen_selection(rows, object_key='source_object', index_key='source_index')
            if request['format'] == 'pdb':
                _pdb_preflight(rows)
            groups = defaultdict(list)
            for row in rows:
                groups[row['source_object']].append(row)
            for number, (name, members) in enumerate(sorted(groups.items()), 1):
                relative = f"objects/{number:04d}.{request['format']}"
                object_files.append(dict(number=number, path=relative, source_object=name,
                    source_state=request['state'], atom_count=len(members),
                    source_indices=[row['source_index'] for row in members],
                    source_object_matrix=list(cmd.get_object_matrix(name, state=request['state']))))
        inventory = ['report.json','manifest.json','replay.pml',
                     'source.pse' if operation == 'coordinates' else 'session.pse']
        if operation == 'coordinates':
            inventory += ['membership.csv', *(row['path'] for row in object_files)]
        if source is not None:
            inventory.append('inputs/source'+source.suffix.lower())
        with Bundle(output, protected=protected, inventory=inventory, cancelled=cancelled,
                    new_directory_only=True) as bundle:
            relative = 'source.pse' if operation == 'coordinates' else 'session.pse'
            inputs = dict(session=bundle.copy_input(session, relative))
            if inputs['session']['sha256'] != snapshot_digest:
                raise ValueError('Source snapshot changed after loading')
            if source is not None:
                inputs['source'] = bundle.copy_input(source,'inputs/source'+source.suffix.lower())
                if inputs['source']['sha256'] != source_input['sha256']:
                    raise ValueError('Original coordinate input changed after snapshot capture')
            if operation == 'coordinates':
                (bundle.stage/'objects').mkdir()
                with runtime.PyMOL() as reload:
                    for completed, record in enumerate(object_files, 1):
                        _cancel(cancelled)
                        members = groups[record['source_object']]
                        expression = _frozen_selection(members, object_key='source_object', index_key='source_index')
                        path = bundle.stage/record['path']
                        cmd.save(str(path), expression, state=request['state'], format=request['format'], quiet=1)
                        diagnostic = _verify(reload.cmd, path, members)
                        diagnostic['path'] = record['path']
                        diagnostics.append(diagnostic)
                        for row in members:
                            row['file'] = record['path']
                        if progress:
                            progress(dict(stage='coordinates', completed=completed, total=len(object_files),
                                          message='Verified coordinate file '+record['path']))
                        _cancel(cancelled)
                fields = ['file','source_object','source_index','source_id','source_state', *CORE_IDENTITY,
                          'world_x','world_y','world_z','b','occupancy','vdw','formal_charge','partial_charge']
                with (bundle.stage/'membership.csv').open('x', newline='', encoding='utf-8') as handle:
                    writer = csv.DictWriter(handle,fieldnames=fields)
                    writer.writeheader()
                    for row in rows:
                        values = {key:row[key] for key in fields if key in row}
                        values.update(zip(('world_x','world_y','world_z'),row['coord']))
                        writer.writerow(values)
            _write_replay(bundle.stage/'replay.pml',operation,object_files)
            limitations = (['Selected single-state world coordinates; styling, scenes, maps and other states are excluded.',
                'Native writer coordinates use 3 decimal places; reload Euclidean tolerance is 0.001 angstrom.',
                'B-factor and occupancy have native format precision; charges, radii and bonds are not a complete chemistry/topology round-trip promise.',
                'Source object/index/ID and state are membership metadata; reloaded indices and local state numbers can differ.',
                'Core identity (element/name/resn/full resi/chain/segi/alt) was independently reloaded without loss.']
                if operation == 'coordinates' else
                ['Byte-preserved complete captured native PSE; this save does not verify any later live scene.',
                 'Reopening a native PSE uses the compatible PyMOL session reader and remains an explicit action.'])
            report = dict(schema_version=2, tool='molscene', tool_version=__version__, pymol_version=version,
                created_at=datetime.now(timezone.utc).isoformat(), operation=operation, passed=True,
                result_kind='saved_snapshot', snapshot_status='saved', output_path=str(output),
                snapshot_id=inputs['session']['sha256'], inputs=inputs, parameters=request,
                object_files=object_files, reload_verification=dict(passed=True, files=diagnostics,
                    atom_count=len(rows), core_identity=list(CORE_IDENTITY),
                    max_euclidean_error_angstrom=max((row['max_euclidean_error_angstrom'] for row in diagnostics),default=0.0),
                    method='independent headless backend' if operation=='coordinates' else 'captured PSE bytes preserved'),
                artifacts=dict(report='report.json',manifest='manifest.json',replay='replay.pml',session=relative),
                limitations=limitations, replay_contract='Relative files and public PyMOL calls; no MolScene dependency',
                artifact_hashes={name:sha256(bundle.stage/name) for name in inventory if name not in ('report.json','manifest.json')})
            if operation=='coordinates':
                report['artifacts']['membership']='membership.csv'
            write_json(bundle.stage/'report.json',report)
            if progress:
                progress(dict(stage='publish',completed=0,total=1,message='Publishing saved snapshot'))
            bundle.publish()
    return report
