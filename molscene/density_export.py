"""Transactional density bundles and standalone public-PyMOL replay."""
from copy import deepcopy
from datetime import datetime, timezone
import struct

from . import __version__
from .artifacts import checked_path, sha256, write_json
from .bundles import Bundle, CancelledError
from . import density, native_render
from .grids import compose, inverse_affine
from .native_maps import replay_dx_loader_source
from .snapshot import scene_fingerprint


def validate_density_request(request):
    """Pure deep-copy normalization shared by CLI, worker and native GUI."""
    value=density._keys(request,('map_path','format','interpretation','source_state','map_name','display','registration','render'),('map_path','format'))
    result=dict(interpretation='auto',source_state=1,map_name=None,display={},registration=None,render={})
    result.update(value)
    if not isinstance(result['map_path'],str) or not result['map_path'] or '\x00' in result['map_path']:
        raise ValueError('map_path must be a nonempty path string')
    if result['format'] not in ('dx','mrc','ccp4') or result['interpretation'] not in ('auto','origin','starts'):
        raise ValueError('Unsupported map format or interpretation')
    if result['format']=='dx' and result['interpretation']!='auto': raise ValueError('DX has no MRC interpretation')
    if result['interpretation']=='origin' and result['format']!='mrc': raise ValueError('Origin interpretation requires MRC')
    density._state(result['source_state'])
    if result['map_name'] is None:
        if result['source_state']!=1: raise ValueError('A fresh file load requires source_state=1')
    else: density._name(result['map_name'])
    result['display']=density.validate_display(result['display'])
    if result['registration'] is not None: result['registration']=density.validate_registration(result['registration'])
    render=density._keys(result['render'],('width','height','dpi','ray'))
    result['render']=dict(width=800,height=600,dpi=150,ray=True)
    result['render'].update(render)
    for key,low,high in (('width',16,4096),('height',16,4096),('dpi',1,1200)):
        number=result['render'][key]
        if type(number) is not int or not low<=number<=high: raise ValueError('Invalid render '+key)
    if type(result['render']['ray']) is not bool: raise ValueError('ray must be boolean')
    if result['render']['width']*result['render']['height']>16777216: raise ValueError('Image exceeds pixel limit')
    return result


# Backward-compatible short spelling for direct domain callers.
validate_request=validate_density_request


def _associate(cmd,path,request):
    name=request['map_name']; state=request['source_state']
    if name is None:
        return density.load_density(cmd,path,format=request['format'],interpretation=request['interpretation'])
    density._controlled(cmd,name)
    current_field=density._field(cmd,name,state)
    current_geometry=density._native_geometry(cmd,name,state,current_field)
    temporary=density._unique(cmd,'density_validation')
    try:
        fresh=density.load_density(cmd,path,format=request['format'],interpretation=request['interpretation'],name=temporary)
        fresh_geometry=density._native_geometry(cmd,temporary,1)
        if current_geometry!=fresh_geometry or density._field_digest(current_field)!=fresh['value_sha256']:
            raise ValueError('Snapshot map geometry or raw values do not match supplied density source')
        fresh.update(name=name,state=state,world_matrix=density._matrix(cmd,name,state),derived=[])
        density._verify_record(cmd,fresh)
        return fresh
    finally:
        if temporary in cmd.get_names('objects'): cmd.delete(temporary)


def _relative_record(record,relative):
    record=deepcopy(record)
    record['source_path']=relative
    return record


def _write_replay(path,metadata,has_session):
    """Data is emitted as Python literals, never command-language interpolation."""
    lines=['# Standalone density replay. Run with @replay.pml in PyMOL.',
        'python','from pathlib import Path','_density_root = Path(__script__).resolve().parent',
        '_density_data = '+ascii(metadata)]
    if has_session: lines.append("cmd.load(str(_density_root / 'source.pse'), format='pse')")
    source=metadata['source']
    if source['format']=='dx': lines.append(replay_dx_loader_source())
    if not metadata['associated_snapshot_map']:
        lines.extend(_load_lines(source))
    registration=metadata['registration']
    if registration is not None:
        alignment=registration['alignment']
        if alignment is not None:
            args=alignment['parameters']; model=args['model_copy_name']
            lines.append(f"cmd.create({model!r}, {'%'+args['mobile']!r}, source_state={args['mobile_state']}, target_state=1, zoom=0)")
            lines.append(f"cmd.transform_object({model!r}, {ascii(alignment['replay_transform'])}, state=1, homogenous=1, transpose=0)")
        rec=registration['record']
        lines.extend(_load_lines(rec))
        transform=list(compose(inverse_affine(rec['load_matrix']),rec['world_matrix']))
        lines.append(f"cmd.transform_object({rec['name']!r}, {ascii(transform)}, state=1, homogenous=1, transpose=0)")
    disp=metadata['display']; spec=disp['display']; name=disp['name']; source_name=disp['source']
    if spec['kind']=='volume':
        ramp=[v*spec['opacity'] if i%5==4 else v for i,v in enumerate(spec['volume_ramp'])]
        lines.append(f"cmd.volume({name!r}, {source_name!r}, ramp={ascii(ramp)}, state=1, source_state={disp['source_state']})")
    else:
        method='isomesh' if spec['kind']=='mesh' else 'isosurface'
        lines.append(f"cmd.{method}({name!r}, {source_name!r}, level={spec['level']!r}, state=1, source_state={disp['source_state']})")
        lines.append(f"cmd.color({spec['color']!r}, {name!r})")
        lines.append(f"cmd.set('transparency', {1-spec['opacity']!r}, {name!r})")
    lines.append(f"cmd.{'enable' if spec['visible'] else 'disable'}({name!r})")
    lines.append('cmd.set_view('+ascii(metadata['view'])+')')
    lines.extend(['python end',''])
    path.write_text('\n'.join(lines),encoding='utf-8')


def _load_lines(record):
    name,relative,format,state=(record[k] for k in ('name','source_path','format','state'))
    if format=='dx':
        return [f'load_native_dx_file(cmd, _density_root / {relative!r}, {ascii(record["grid"])}, '
                f'{record["source_sha256"]!r}, name={name!r}, state={state})']
    return ["_density_normalizer = cmd.get_setting_int('normalize_ccp4_maps')",
            'try:',"    cmd.set('normalize_ccp4_maps', 0)",
            f'    cmd.load(str(_density_root / {relative!r}), {name!r}, format={format!r}, state={state}, zoom=0)',
            'finally:',"    cmd.set('normalize_ccp4_maps', _density_normalizer)"]


def export_density(session_path,output_dir,request,*,cancelled=None,progress=None,protected=()):
    request=validate_density_request(request)
    source=checked_path(request['map_path'])
    if source.suffix.lower() not in ('.dx','.mrc','.ccp4','.map'): raise ValueError('Density source suffix must be dx, mrc, ccp4 or map')
    # Reject unsupported science before starting the backend or staging output.
    density._grid(source,request['format'],request['interpretation'],'unknown')
    session=None if session_path is None else checked_path(session_path)
    if session is not None and (not session.is_file() or session.suffix.lower()!='.pse' or not session.stat().st_size):
        raise ValueError('Session must be a nonempty PSE file')
    if session is None and (request['map_name'] is not None or (request['registration'] or {}).get('kind')=='align'):
        raise ValueError('Snapshot map association and alignment require a source session')
    relative='inputs/density'+source.suffix.lower()
    inventory=[relative,'density.json','scene.pse','scene.png','replay.pml','report.json','manifest.json']
    if session is not None: inventory.append('source.pse')
    protections=tuple(protected)+(source,)+((session,) if session is not None else ())
    def stage(label,completed,message):
        if cancelled is not None and cancelled(): raise CancelledError('Operation cancelled')
        if progress is not None: progress(dict(stage=label,completed=completed,total=5,message=message))
    stage('validate',0,'Validated density inputs')
    with Bundle(output_dir,protected=protections,inventory=inventory,cancelled=cancelled) as bundle:
        inputs={'density':bundle.copy_input(source,relative)}
        if session is not None: inputs['session']=bundle.copy_input(session,'source.pse')
        from .engine import _runtime
        with _runtime().PyMOL() as pm:
            cmd=pm.cmd
            if session is not None: cmd.load(str(bundle.stage/'source.pse'),format='pse')
            states={request['source_state']}
            if (request['registration'] or {}).get('kind')=='align':
                states.update(request['registration'][k] for k in ('mobile_state','target_state'))
            fingerprints={str(s):scene_fingerprint(cmd,s) for s in states}
            stage('load',1,'Loading validated raw density')
            record=_associate(cmd,bundle.stage/relative,request)
            source_map_digest=density.map_fingerprint(cmd,record)
            stage('register',2,'Preparing owned density result')
            registration=None
            if request['registration'] is not None: registration=density.register_density(cmd,record,request['registration'])
            result_record=registration['record'] if registration is not None else record
            display_name=density._display_name(result_record,request['display']['kind'])
            display_warnings=[]
            if display_name in cmd.get_names('all'):
                retained=display_name
                display_name=density._unique(cmd,'density_display')
                display_warnings.append(f"Existing source object/display '{retained}' was retained; "
                    f"fresh result display '{display_name}' was created. Original and new layers may coexist, "
                    "including their volume opacity; this does not replace the current view.")
            display=density.display_density(cmd,result_record,request['display'],name=display_name)
            cmd.zoom(result_record['name'],buffer=3,state=result_record['state'])
            view=list(cmd.get_view())
            stage('render',3,'Rendering density scene')
            render=request['render']
            cmd.save(str(bundle.stage/'scene.pse'))
            render_warnings=[]
            if native_render.requires_native(cmd,render['ray']):
                native=native_render.render_native(bundle.stage/'scene.pse',[bundle.stage/'scene.png'],render,
                    work_dir=bundle.scratch,cancelled=cancelled,bundle=bundle)
                rendering=native_render.report_metadata(native,bundle.stage); render_warnings=native['warnings']
            else:
                cmd.png(str(bundle.stage/'scene.png'),width=render['width'],height=render['height'],dpi=render['dpi'],ray=1,quiet=1)
                rendering=dict(backend='cpu_ray',requested_ray=render['ray'],effective_ray=True,width=render['width'],height=render['height'])
            metadata=dict(schema_version=1,source=_relative_record(record,relative),
                          associated_snapshot_map=request['map_name'] is not None,registration=deepcopy(registration),display=display,view=view,
                          matrix_convention='Row-major column coordinates; source world to registered world; translation angstrom')
            if metadata['registration'] is not None:
                metadata['registration']['record']=_relative_record(result_record,relative)
            write_json(bundle.stage/'density.json',metadata)
            _write_replay(bundle.stage/'replay.pml',metadata,session is not None)
            version=cmd.get_version()[0]
            advanced_maps={record['name']+'/'+str(record['state']):source_map_digest,
                           result_record['name']+'/'+str(result_record['state']):density.map_fingerprint(cmd,result_record)}
        stage('validate_artifacts',4,'Checking density bundle artifacts')
        for name in set(inventory)-{'report.json','manifest.json'}:
            if not (bundle.stage/name).is_file() or not (bundle.stage/name).stat().st_size:
                raise RuntimeError('Missing or empty density artifact: '+name)
        with (bundle.stage/'scene.png').open('rb') as handle: header=handle.read(24)
        if len(header)!=24 or header[:8]!=b'\x89PNG\r\n\x1a\n' or struct.unpack('>2I',header[16:24])!=(render['width'],render['height']):
            raise RuntimeError('Density image has invalid PNG dimensions')
        parameters=deepcopy(request); parameters['map_path']=relative
        snapshot=inputs.get('session',{}).get('sha256')
        artifacts=dict(density='density.json',session='scene.pse',image='scene.png',replay='replay.pml',report='report.json',manifest='manifest.json')
        if session is not None: artifacts['source_session']='source.pse'
        report=dict(schema_version=2,tool='molscene',tool_version=__version__,pymol_version=version,
            created_at=datetime.now(timezone.utc).isoformat(),operation='density',inputs=inputs,parameters=parameters,
            structure={},analysis=dict(density=metadata,rendering=rendering),artifacts=artifacts,passed=True,
            replay_contract='Relative source inputs and public PyMOL calls; no MolScene dependency',snapshot_id=snapshot,
            source_fingerprints=fingerprints,advanced_fingerprints=dict(files={k:v['sha256'] for k,v in inputs.items()},
                maps=advanced_maps,timeline=None,display_snapshot=snapshot,selection=None,session_epoch=None),
            limitations=['Rigid registration maps coordinate frames; it does not optimize density correlation, refine atoms, or assess experimental quality.',
                          'Raw map values have unknown units unless separately established. Bounds are geometric diagnostics only.',
                          'Aligned-copy registration requires global, mobile and target matrix_mode -1, 0 or 1; mode2 is unsupported.'],warnings=display_warnings+render_warnings,
            artifact_hashes={name:sha256(bundle.stage/name) for name in inventory if name not in ('report.json','manifest.json')})
        write_json(bundle.stage/'report.json',report)
        stage('publish',5,'Publishing validated density bundle')
        bundle.publish()
        return report
