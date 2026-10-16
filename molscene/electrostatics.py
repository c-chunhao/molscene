"""Real APBS/PDB2PQR execution and transactional potential export.

No approximate or fallback potential solver is provided. Imports remain usable
without PyMOL or external scientific executables installed.
"""
from array import array
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct

from . import __version__
from .artifacts import checked_path, sha256, write_json
from .bundles import Bundle, CancelledError
from .external_tools import load_tools, run_tool, tool_manifest, ExternalToolError
from .grids import Grid, read_dx, write_dx, grid_metadata
from .pqr import read_pqr, validate_preparation, PROTEIN_RESIDUES, WATER_RESIDUES, atom_identity
from .potential_display import load_potential_copy, selected_atoms, selection_digest, validate_coverage
from .snapshot import scene_fingerprint
from .native_maps import replay_dx_loader_source

UNITS = ('kBT/e', 'V', 'mV', 'unknown')
SOLVER_DEFAULTS = dict(grid=[65,65,65], solute_dielectric=2., solvent_dielectric=78.5, salt_molar=.15,
                       ion_radius=2., probe_radius=1.4, coarse_padding=40., fine_padding=20.)
PREPARATION_DEFAULTS = dict(force_field='PARSE', ph=7., protonation='propka', drop_water=False)
RENDER_DEFAULTS = dict(width=800, height=600, dpi=150, ray=True)


def _object(value, allowed, label):
    if not isinstance(value, dict) or set(value)-set(allowed):
        raise ValueError(label+' must be an object with supported keys')
    return copy.deepcopy(value)


def _number(value, label, low=None, high=None, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value):
        raise ValueError(label+' must be finite')
    if (positive and value <= 0) or (low is not None and value < low) or (high is not None and value > high):
        raise ValueError(label+' is outside the supported range')
    return float(value)


def _json_document(value):
    try:
        encoded = json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8')
    except (TypeError,ValueError,OverflowError) as error:
        raise ValueError('Expected finite JSON-compatible data') from error
    if len(encoded) > 8*1024*1024:
        raise ValueError('JSON document exceeds 8 MiB')
    return encoded


def _tools_request(value):
    if not isinstance(value,dict):
        raise ValueError('Tools must be a configuration object')
    if not value:
        return {}
    if set(value) != {'schema_version','tools'} or type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise ValueError('Tool configuration requires schema_version 1 and tools')
    entries = value['tools']
    if not isinstance(entries,dict) or set(entries)-{'apbs','pdb2pqr','ffmpeg','ffprobe'}:
        raise ValueError('Unsupported tool name')
    keys = {'argv','architecture','path_dirs','runtime_files','timeout_seconds','memory_limit_mib'}
    for entry in entries.values():
        if not isinstance(entry,dict) or set(entry) != keys or entry['architecture'] not in ('x86','x64'):
            raise ValueError('Invalid tool configuration entry')
        for key,maximum in (('timeout_seconds',3600),('memory_limit_mib',1536 if entry['architecture']=='x86' else 4096)):
            if type(entry[key]) is not int or not 1 <= entry[key] <= maximum:
                raise ValueError('Invalid tool '+key)
        for key,maximum in (('argv',128),('path_dirs',64)):
            if not isinstance(entry[key],list) or len(entry[key]) > maximum or any(not isinstance(s,str) or '\0' in s for s in entry[key]):
                raise ValueError('Invalid tool '+key)
        if not entry['argv'] or not entry['argv'][0]:
            raise ValueError('Tool argv needs an executable')
        if not isinstance(entry['runtime_files'],list) or len(entry['runtime_files'])>128:
            raise ValueError('Invalid tool runtime_files')
        for runtime in entry['runtime_files']:
            if not isinstance(runtime,dict) or set(runtime) != {'path','role'} or runtime['role'] not in ('dll','interpreter','package_metadata') or not isinstance(runtime['path'],str):
                raise ValueError('Invalid typed runtime file')
    return copy.deepcopy(value)


def _solver(value):
    spec = {**copy.deepcopy(SOLVER_DEFAULTS), **_object(value, SOLVER_DEFAULTS, 'Solver')}
    if not isinstance(spec['grid'], list) or len(spec['grid']) != 3 or any(type(n) is not int or n not in (33,65,97,129,161,193) for n in spec['grid']):
        raise ValueError('APBS grid counts must be three supported multigrid dimensions')
    for key in set(spec)-{'grid'}:
        spec[key] = _number(spec[key], key, low=0 if key == 'salt_molar' else None,
                            high=2 if key == 'salt_molar' else None, positive=key != 'salt_molar')
    if spec['coarse_padding'] <= spec['fine_padding']:
        raise ValueError('Coarse padding must exceed fine padding')
    return spec


def potential_factor(source_unit, target_unit, temperature):
    if source_unit not in UNITS or target_unit not in UNITS:
        raise ValueError('Unsupported potential units')
    temperature = _number(temperature, 'Temperature', 250, 400)
    if source_unit == target_unit:
        return 1.
    if 'unknown' in (source_unit, target_unit):
        raise ValueError('Unknown potential units cannot be converted')
    volts = {'V':1., 'mV':.001, 'kBT/e':1.380649e-23*temperature/1.602176634e-19}
    return volts[source_unit]/volts[target_unit]


def validate_potential_request(request):
    allowed = ('mode','pqr_path','dx_path','selection','state','units','display_units','temperature',
               'preparation','solver','ramp_limit','render','tools')
    request = _object(request, allowed, 'Potential request')
    mode = request.get('mode')
    if mode not in ('prepared','prepare','import'):
        raise ValueError('Potential mode must be prepared, prepare or import')
    defaults = dict(pqr_path=None, dx_path=None, selection='all', state=1, units='kBT/e' if mode != 'import' else 'unknown',
                    temperature=298.15, preparation={}, solver={}, ramp_limit=5., render={}, tools={})
    spec = {**defaults, **request}
    spec['display_units'] = request.get('display_units', spec['units'])
    for key in ('pqr_path','dx_path'):
        if spec[key] is not None and (not isinstance(spec[key], str) or not spec[key] or '\0' in spec[key]):
            raise ValueError(key+' must be a nonempty path string or null')
    if mode == 'prepared' and (not spec['pqr_path'] or spec['dx_path'] is not None):
        raise ValueError('Prepared mode requires only a PQR input')
    if mode == 'prepare' and (spec['pqr_path'] is not None or spec['dx_path'] is not None or 'selection' not in request):
        raise ValueError('Prepare mode requires an explicit protein selection and no PQR/DX input')
    if mode == 'import' and not spec['dx_path']:
        raise ValueError('Import requires a DX input')
    if not isinstance(spec['selection'], str) or not spec['selection'].strip() or any(c in spec['selection'] for c in '\r\n\0'):
        raise ValueError('Selection must be a nonempty single-line string')
    if type(spec['state']) is not int or not 1 <= spec['state'] <= 100000:
        raise ValueError('State must be an integer in 1..100000')
    if mode != 'import' and spec['units'] != 'kBT/e':
        raise ValueError('Computed potential raw units are kBT/e; choose display_units for conversion')
    if mode == 'import' and spec['units'] != spec['display_units'] and 'kBT/e' in (spec['units'],spec['display_units']) and 'temperature' not in request:
        raise ValueError('Conversion involving kBT/e requires explicit temperature for an imported potential')
    spec['temperature'] = _number(spec['temperature'], 'Temperature', 250, 400)
    potential_factor(spec['units'], spec['display_units'], spec['temperature'])
    spec['ramp_limit'] = _number(spec['ramp_limit'], 'Ramp limit', positive=True)
    prep = {**PREPARATION_DEFAULTS, **_object(spec['preparation'], PREPARATION_DEFAULTS, 'Preparation')}
    if prep['force_field'] not in ('PARSE','AMBER','CHARMM') or prep['protonation'] not in ('propka','preserve') or type(prep['drop_water']) is not bool:
        raise ValueError('Unsupported preparation options')
    prep['ph'] = _number(prep['ph'], 'pH', 0, 14)
    spec['preparation'] = prep
    spec['solver'] = _solver(spec['solver'])
    render = {**RENDER_DEFAULTS, **_object(spec['render'], RENDER_DEFAULTS, 'Render')}
    for key, low, high in (('width',16,4096),('height',16,4096),('dpi',1,1200)):
        if type(render[key]) is not int or not low <= render[key] <= high:
            raise ValueError('Invalid render '+key)
    if type(render['ray']) is not bool or render['width']*render['height'] > 16777216:
        raise ValueError('Invalid render size or ray flag')
    spec['render'] = render
    spec['tools'] = _tools_request(spec['tools'])
    # Tool paths/architecture are validated by the execution layer, not this pure normalizer.
    _json_document(spec)
    return copy.deepcopy(spec)


def _geometry(pqr, solver):
    atoms = pqr['atoms']
    if not atoms:
        raise ValueError('Cannot solve an empty molecule')
    lower = [min(a[c] for a in atoms) for c in ('x','y','z')]
    upper = [max(a[c] for a in atoms) for c in ('x','y','z')]
    center = [(a+b)/2 for a,b in zip(lower,upper)]
    fine = [b-a+solver['fine_padding'] for a,b in zip(lower,upper)]
    coarse = [b-a+solver['coarse_padding'] for a,b in zip(lower,upper)]
    for atom in atoms:
        for axis,c in enumerate(('x','y','z')):
            if abs(atom[c]-center[axis])+atom['radius']+solver['probe_radius'] > fine[axis]/2:
                raise ValueError('Fine grid padding is insufficient for displayed radii plus probe')
    return center, fine, coarse


def build_apbs_input(pqr, solver, temperature):
    solver = _solver(solver)
    temperature = _number(temperature, 'Temperature', 250,400)
    center, fine, coarse = _geometry(pqr, solver)
    vector = lambda values: ' '.join(format(v,'.12g') for v in values)
    lines = ['read', ' mol pqr prepared.pqr', 'end', 'elec name potential', ' mg-auto',
             ' dime '+vector(solver['grid']), ' cglen '+vector(coarse), ' fglen '+vector(fine),
             ' cgcent '+vector(center), ' fgcent '+vector(center), ' mol 1', ' lpbe', ' bcfl mdh',
             ' pdie '+format(solver['solute_dielectric'],'.12g'), ' sdie '+format(solver['solvent_dielectric'],'.12g'),
             ' chgm spl2', ' srfm smol', ' srad '+format(solver['probe_radius'],'.12g'), ' swin 0.3', ' sdens 10',
             ' temp '+format(temperature,'.12g'), ' calcenergy no', ' calcforce no']
    if solver['salt_molar']:
        for charge in (1,-1):
            lines.append(f" ion charge {charge} conc {solver['salt_molar']:.12g} radius {solver['ion_radius']:.12g}")
    return '\n'.join(lines+[' write pot dx potential', 'end', 'quit', ''])


def _memory_preflight(solver, tools):
    if 'apbs' not in tools.get('tools', {}):
        raise ValueError('APBS must be configured')
    entry = tools['tools']['apbs']
    arch = entry['architecture']
    allowed = (33,65,97,129) if arch == 'x86' else (33,65,97,129,161,193)
    if any(n not in allowed for n in solver['grid']):
        raise ValueError('Requested APBS grid is unsupported by configured architecture')
    estimated = 128*1024**2+512*math.prod(solver['grid'])
    maximum = min(entry['memory_limit_mib'],1536 if arch == 'x86' else 4096)*1024**2
    if estimated > maximum:
        raise ValueError('Requested APBS grid exceeds configured memory budget; choose a smaller grid explicitly')
    return dict(architecture=arch, estimated_bytes=estimated, memory_limit_bytes=maximum,
                requested_counts=list(solver['grid']))


def _check(cancelled, progress, stage, completed=0, total=1, message=''):
    if cancelled and cancelled():
        raise CancelledError('Potential export cancelled')
    if progress:
        progress(dict(stage=stage,completed=completed,total=total,message=message or stage))


def _nonempty(path):
    if not path.is_file() or not path.stat().st_size:
        raise ValueError('Required scientific artifact is absent or empty: '+path.name)


def _diagnostics(stdout, stderr):
    text = stdout+'\n'+stderr
    if re.search(r'(?i)(failed to converge|not converg(?:ed|ing)|convergence fail|error while|fatal error|VASSERT|\bNaN\b)', text):
        raise ValueError('APBS reported failed convergence or invalid scientific output')
    return [line for line in text.splitlines() if re.search(r'(?i)(warn|caution)', line)]


def _verify_geometry(grid, pqr, solver):
    center, fine, _ = _geometry(pqr,solver)
    if list(grid.shape) != solver['grid']:
        raise ValueError('APBS produced different grid counts than requested')
    expected_origin = [c-length/2 for c,length in zip(center,fine)]
    expected_spacing = [length/(n-1) for length,n in zip(fine,solver['grid'])]
    for axis in range(3):
        # APBS ASCII DX writes seven significant digits.
        tolerance = max(1e-5, abs(expected_origin[axis])*1e-6)
        if abs(grid.origin[axis]-expected_origin[axis]) > tolerance or abs(grid.basis[axis][axis]-expected_spacing[axis]) > max(1e-6,expected_spacing[axis]*1e-6):
            raise ValueError('APBS produced unexpected physical grid geometry')
    return validate_coverage(grid,pqr['atoms'],solver['probe_radius'])


def _write_replay(path, cmd, record, *, source_session, view):
    """Serialize ordinary data and public cmd calls; no MolScene import or solver."""
    model = cmd.get_model(record['molecule'],1)
    data = dict(record=record, pdb=cmd.get_pdbstr(record['molecule'],state=1),
                radii=[a.vdw for a in model.atom], elec_radii=[a.elec_radius for a in model.atom],
                charges=[a.partial_charge for a in model.atom], coordinates=[list(a.coord) for a in model.atom],
                view=list(view), source_session=source_session)
    body = '''from pathlib import Path
import json
base = Path(__script__).resolve().parent
data = json.loads(DATA_JSON)
r = data['record']
if data['source_session']:
    cmd.load(str(base / 'source.pse'))
    for name in cmd.get_names('objects'):
        cmd.disable(name)
cmd.read_pdbstr(data['pdb'], r['molecule'])
cmd.alter(r['molecule'], 'vdw=radii[index-1]; elec_radius=elec_radii[index-1]; partial_charge=charges[index-1]', space=data)
cmd.alter_state(1, r['molecule'], '(x,y,z)=coordinates[index-1]', space=data)
load_native_dx_file(cmd,base / r['dx_path'],r['grid'],r['dx_sha256'],name=r['map'],state=1)
cmd.hide('everything', r['molecule'])
cmd.set('surface_solvent', 0, r['molecule'])
cmd.set('solvent_radius', r['probe_radius'], r['molecule'])
cmd.set('surface_ramp_above_mode', 0, r['molecule'])
cmd.ramp_new(r['ramp'], r['map'], r['ramp_values'], r['ramp_colors'])
cmd.set('surface_color', r['ramp'], r['molecule'])
cmd.show('surface', r['molecule'])
cmd.pseudoatom(r['legend'], pos=LEGEND_POSITION, label=r['legend_text'], vdw=0)
cmd.hide('everything', r['legend'])
cmd.show('labels', r['legend'])
for key,value in r['legend_settings'].items():
    cmd.set(key,value,r['legend'])
cmd.set_title(r['molecule'], 1, r['owner'])
cmd.set_title(r['legend'], 1, r['owner'])
cmd.bg_color('white')
cmd.set_view(data['view'])
'''
    body = body.replace('DATA_JSON', repr(json.dumps(data,allow_nan=False))).replace('LEGEND_POSITION', repr(cmd.get_model(record['legend']).atom[0].coord))
    path.write_text('python\n'+replay_dx_loader_source()+'\n'+body+'python end\n',encoding='utf-8')


def export_potential(session_path, output_dir, request, *, cancelled=None, progress=None, protected=()):
    spec = validate_potential_request(request)
    mode = spec['mode']
    session_path = checked_path(session_path) if session_path is not None else None
    if mode == 'prepare' and session_path is None:
        raise ValueError('Preparation requires a frozen source session')
    if mode == 'prepared' and session_path is not None:
        raise ValueError('Prepared PQR mode does not take a source session')
    if mode == 'import' and ((session_path is None) == (spec['pqr_path'] is None)):
        raise ValueError('Import requires exactly one PQR or frozen-session radius source')
    source_paths = [checked_path(spec[key]) for key in ('pqr_path','dx_path') if spec[key] is not None]
    if session_path is not None:
        source_paths.append(session_path)
    inventory = ['scene.pse','scene.png','replay.pml','report.json','manifest.json']
    if session_path is not None:
        inventory.append('source.pse')
    if spec['pqr_path'] is not None:
        inventory.append('inputs/source.pqr')
    if mode == 'import':
        inventory.append('inputs/potential.dx')
    else:
        inventory += ['prepared.pqr','potential.dx','apbs.in','tools.json','logs/apbs.stdout.log','logs/apbs.stderr.log']
    if mode == 'prepare':
        inventory += ['inputs/selection.pdb','preparation.json','logs/pdb2pqr.stdout.log','logs/pdb2pqr.stderr.log']
    conversion = spec['units'] != spec['display_units']
    if conversion:
        inventory.append('display.dx')
    tools = spec['tools']
    if mode != 'import' and not tools:
        config = os.environ.get('MOLSCENE_TOOLS_CONFIG')
        if not config:
            raise ValueError('Provide tools configuration or MOLSCENE_TOOLS_CONFIG')
        tools = load_tools(Path(config))
    memory = _memory_preflight(spec['solver'],tools) if mode != 'import' else None
    bundle = Bundle(Path(output_dir), protected=(*protected,*source_paths), inventory=inventory, cancelled=cancelled)
    current_tool = None
    try:
        with bundle:
            stage, scratch = bundle.stage,bundle.scratch
            inputs, runs, warnings = {},{},[]
            _check(cancelled,progress,'inputs')
            if session_path is not None:
                inputs['session'] = bundle.copy_input(session_path,'source.pse')
            if spec['pqr_path'] is not None:
                inputs['pqr'] = bundle.copy_input(Path(spec['pqr_path']),'inputs/source.pqr')
            if spec['dx_path'] is not None:
                inputs['potential'] = bundle.copy_input(Path(spec['dx_path']),'inputs/potential.dx')
            import pymol2
            with pymol2.PyMOL() as pm:
                cmd = pm.cmd
                source_fingerprints = {}
                original_atoms = None
                if session_path is not None:
                    cmd.load(str(stage/'source.pse'))
                    source_fingerprints[str(spec['state'])] = scene_fingerprint(cmd,spec['state'])
                    original_atoms = selected_atoms(cmd,spec['selection'],spec['state'])
                prep_report = dict(provenance='user-supplied PQR; preparation bypassed') if spec['pqr_path'] else None
                if mode == 'prepare':
                    _check(cancelled,progress,'preparation')
                    identities = set()
                    for atom in original_atoms:
                        identity = atom_identity(atom)
                        if atom['resn'] not in PROTEIN_RESIDUES | WATER_RESIDUES or atom['element'] not in ('H','C','N','O','S') or atom['alt'] or identity in identities:
                            raise ValueError('Unsupported selected ligand, metal, residue or ambiguous atom identity')
                        identities.add(identity)
                    if not any(a['resn'] in PROTEIN_RESIDUES for a in original_atoms):
                        raise ValueError('Preparation needs selected protein atoms')
                    (stage/'inputs').mkdir(exist_ok=True)
                    cmd.save(str(stage/'inputs/selection.pdb'), spec['selection'], state=spec['state'], format='pdb')
                    _nonempty(stage/'inputs/selection.pdb')
                    shutil.copyfile(stage/'inputs/selection.pdb', scratch/'selection.pdb')
                    prep = spec['preparation']
                    args = ['--ff='+prep['force_field'],'--keep-chain','--whitespace']
                    if prep['protonation'] == 'preserve':
                        args += ['--assign-only']
                    else:
                        args += ['--titration-state-method=propka','--with-ph='+str(prep['ph'])]
                    if prep['drop_water']:
                        args += ['--drop-water']
                    current_tool = 'pdb2pqr'
                    prefix = scratch/current_tool
                    bundle.retain_failure_logs({current_tool+'.stdout':Path(str(prefix)+'.stdout.log'),current_tool+'.stderr':Path(str(prefix)+'.stderr.log')})
                    runs[current_tool] = run_tool(current_tool,tools,args+['selection.pdb','prepared.pqr'],cwd=scratch,log_prefix=prefix,cancelled=cancelled,progress=progress)
                    _nonempty(scratch/'prepared.pqr')
                    parsed = read_pqr(scratch/'prepared.pqr')
                    diagnostics = '\n'.join(Path(runs[current_tool][key]).read_text(encoding='utf-8',errors='replace') for key in ('stdout_path','stderr_path'))
                    prep_report = validate_preparation(original_atoms,parsed,diagnostics,prep)
                    _json_document(prep_report)
                    write_json(stage/'preparation.json',prep_report)
                    shutil.copyfile(scratch/'prepared.pqr',stage/'prepared.pqr')
                elif mode == 'prepared':
                    parsed = read_pqr(stage/'inputs/source.pqr')
                    shutil.copyfile(stage/'inputs/source.pqr',stage/'prepared.pqr')
                    shutil.copyfile(stage/'prepared.pqr',scratch/'prepared.pqr')
                elif spec['pqr_path']:
                    parsed = read_pqr(stage/'inputs/source.pqr')
                if mode != 'import':
                    _check(cancelled,progress,'solver')
                    apbs_input = build_apbs_input(parsed,spec['solver'],spec['temperature'])
                    (stage/'apbs.in').write_text(apbs_input,encoding='ascii')
                    (scratch/'apbs.in').write_text(apbs_input,encoding='ascii')
                    current_tool = 'apbs'
                    prefix = scratch/current_tool
                    bundle.retain_failure_logs({current_tool+'.stdout':Path(str(prefix)+'.stdout.log'),current_tool+'.stderr':Path(str(prefix)+'.stderr.log')})
                    runs[current_tool] = run_tool(current_tool,tools,['apbs.in'],cwd=scratch,log_prefix=prefix,cancelled=cancelled,progress=progress)
                    _nonempty(scratch/'potential.dx')
                    grid = read_dx(scratch/'potential.dx',value_unit='kBT/e')
                    _verify_geometry(grid,parsed,spec['solver'])
                    stdout,stderr = (Path(runs['apbs'][key]).read_text(encoding='utf-8',errors='replace') for key in ('stdout_path','stderr_path'))
                    warnings += _diagnostics(stdout,stderr)
                    peak = re.findall(r'(?i)([\d.]+)\s*(?:MB|MiB)\s+high water',stdout)
                    memory.update(actual_counts=list(grid.shape),solver_reported_high_water_mb=float(peak[-1]) if peak else None)
                    shutil.copyfile(scratch/'potential.dx',stage/'potential.dx')
                    raw_relative = 'potential.dx'
                    (stage/'logs').mkdir(exist_ok=True)
                    for name,run in runs.items():
                        for stream in ('stdout','stderr'):
                            relative = 'logs/'+name+'.'+stream+'.log'
                            shutil.copyfile(Path(run[stream+'_path']),stage/relative)
                            run[stream+'_path'] = relative
                    write_json(stage/'tools.json',tool_manifest(tools,runs))
                else:
                    raw_relative = 'inputs/potential.dx'
                    grid = read_dx(stage/raw_relative,value_unit=spec['units'])
                _check(cancelled,progress,'display')
                display_relative = raw_relative
                factor = potential_factor(spec['units'],spec['display_units'],spec['temperature'])
                if conversion:
                    display_relative = 'display.dx'
                    converted = Grid(grid.shape,grid.origin,grid.basis,array('d',(v*factor for v in grid.values)),spec['display_units'],copy.deepcopy(grid.metadata))
                    write_dx(stage/display_relative,converted)
                if session_path is not None:
                    for name in cmd.get_names('objects'):
                        cmd.disable(name)
                display = load_potential_copy(cmd,pqr_path=stage/('prepared.pqr' if mode != 'import' else 'inputs/source.pqr') if mode != 'import' or spec['pqr_path'] else None,
                                              selection=spec['selection'] if mode == 'import' and not spec['pqr_path'] else None,
                                              state=spec['state'],dx_path=stage/display_relative,units=spec['display_units'],temperature=spec['temperature'],
                                              ramp_limit=spec['ramp_limit'],probe_radius=spec['solver']['probe_radius'])
                display['dx_path'] = display_relative
                cmd.bg_color('white')
                cmd.orient(display['molecule'])
                cmd.zoom(display['molecule']+' or '+display['legend'], buffer=4)
                _check(cancelled,progress,'render')
                render = spec['render']
                cmd.save(str(stage/'scene.pse'))
                from . import native_render
                if native_render.requires_native(cmd,render['ray']):
                    native=native_render.render_native(stage/'scene.pse',[stage/'scene.png'],render,
                        work_dir=scratch,cancelled=cancelled,bundle=bundle)
                    rendering=native_render.report_metadata(native,stage); warnings.extend(native['warnings'])
                else:
                    cmd.png(str(stage/'scene.png'),width=render['width'],height=render['height'],dpi=render['dpi'],ray=1,quiet=1)
                    rendering=dict(backend='cpu_ray',requested_ray=render['ray'],effective_ray=True,width=render['width'],height=render['height'])
                _write_replay(stage/'replay.pml',cmd,display,source_session=session_path is not None,view=cmd.get_view())
                for filename in ('scene.png','scene.pse','replay.pml'):
                    _nonempty(stage/filename)
                with (stage/'scene.png').open('rb') as handle:
                    header = handle.read(24)
                if len(header) != 24 or header[:8] != b'\x89PNG\r\n\x1a\n' or header[12:16] != b'IHDR' or struct.unpack('>II',header[16:24]) != (render['width'],render['height']):
                    raise ValueError('Rendered image header/dimensions do not match request')
                snapshot_id = inputs.get('session',{}).get('sha256')
                parameters = copy.deepcopy(spec)
                parameters['tools'] = {'artifact':'tools.json'} if runs else {}
                parameters['pqr_path'] = 'inputs/source.pqr' if spec['pqr_path'] else None
                parameters['dx_path'] = 'inputs/potential.dx' if spec['dx_path'] else None
                hashes = {name:sha256(stage/name) for name in inventory if name not in ('report.json','manifest.json')}
                raw_metadata = grid_metadata(grid)
                map_digest = hashlib.sha256(json.dumps(dict(grid=display['grid'],native_value_sha256=display['native_value_sha256'],native_extent=display['native_extent'],
                    source_sha256=hashes[display_relative],effective_matrix=[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]),sort_keys=True).encode()).hexdigest()
                report = dict(schema_version=2,tool='molscene',tool_version=__version__,pymol_version=cmd.get_version()[0],
                              created_at=datetime.now(timezone.utc).isoformat(),operation='potential',passed=True,inputs=inputs,parameters=parameters,
                              structure=dict(atom_count=display['coverage']['atom_count'],preparation=prep_report,
                                             pqr={key:value for key,value in parsed.items() if key != 'atoms'} if mode != 'import' or spec['pqr_path'] else None),
                              analysis=dict(raw_grid=raw_metadata,display=display,rendering=rendering,conversion=dict(source_unit=spec['units'],target_unit=spec['display_units'],factor=factor,temperature=spec['temperature']),
                                            solver=memory,model='linearized Poisson-Boltzmann; mg-auto; symmetric monovalent ions; smol molecular dielectric surface' if mode != 'import' else 'imported potential; solver/model unverified'),
                              artifacts={name:name for name in inventory},artifact_hashes=hashes,snapshot_id=snapshot_id,source_fingerprints=source_fingerprints,
                              advanced_fingerprints=dict(files={role:row['sha256'] for role,row in inputs.items()},maps={display['map']+'/1':map_digest},timeline=None,display_snapshot=snapshot_id,
                                                         selection=selection_digest(original_atoms) if original_atoms else display['selection_sha256'],session_epoch=None),
                              replay_contract='standalone public PyMOL cmd and stdlib; stored map; no solver required',warnings=warnings,
                              limitations=['Clipping reports grid voxels, not surface samples.', 'LPBE is a fixed linearized model; no NPBE or force-field coverage guarantee beyond validated selected protein/water.'])
                _json_document(report)
                write_json(stage/'report.json',report)
            _check(cancelled,progress,'publish')
            bundle.publish()
            return report
    except BaseException as error:
        if bundle.failure_path.is_dir() and (bundle.failure_path/'failure.json').is_file():
            error.failure_path = str(bundle.failure_path)
            if isinstance(error,ExternalToolError) and current_tool:
                for stream in ('stdout','stderr'):
                    retained = bundle.failure_path/'logs'/(current_tool+'.'+stream+'.log')
                    if retained.is_file():
                        error.result[stream+'_path'] = str(retained)
        raise
