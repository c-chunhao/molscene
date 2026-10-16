"""Validated scalar maps, owned displays, and verified rigid map copies.

Matrices are row-major and act on column coordinates. Source grid geometry
already includes its physical origin; current points use B_current / B_load.
The optional native backend is supplied by the caller, never imported here.
"""
from array import array
from copy import deepcopy
import hashlib
import itertools
import json
import math
import re
import struct
import sys
import uuid

from .artifacts import checked_path, sha256
from .grids import (MAX_VOXELS, compose, grid_metadata, grid_point,
                    inverse_affine, read_dx, read_mrc, validate_rigid)
from .snapshot import scene_fingerprint

IDENTITY = (1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.)
_RESERVED = frozenset(('all none sele auto new same origin center enabled visible '
                       'present polymer protein nucleic solvent organic inorganic '
                       'hydrogens hetatm guide backbone sidechain byres byobject '
                       'and or not pk1 pk2 pk3 pk4 pkset pkresi pkchain pkobject').split())


def _keys(value, allowed, required=()):
    if type(value) is not dict or set(value)-set(allowed) or set(required)-set(value):
        raise ValueError('Invalid or unknown request fields')
    return deepcopy(value)


def _number(value, label, low=None, high=None):
    try:
        finite=type(value) in (int,float) and math.isfinite(value)
    except OverflowError:
        finite=False
    if not finite:
        raise ValueError(label+' must be finite')
    if (low is not None and value < low) or (high is not None and value > high):
        raise ValueError(label+' is out of range')
    return float(value)


def _state(state):
    if type(state) is not int or not 1 <= state <= 100000:
        raise ValueError('State must be an explicit integer in 1..100000')
    return state


def _name(name):
    if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,119}',name) or name.lower() in _RESERVED:
        raise ValueError('Use a nonreserved object name containing letters, digits and underscores')
    return name


def _new_name(cmd, name):
    _name(name)
    if name in cmd.get_names('all'):
        raise ValueError('Object or selection name already exists: '+name)
    return name


def _unique(cmd, prefix):
    names = set(cmd.get_names('all'))
    if prefix not in names: return prefix
    while True:
        candidate = prefix+'_'+uuid.uuid4().hex[:12]
        if candidate not in names: return candidate


def validate_display(display):
    value = _keys(display, ('kind','level','color','opacity','visible','volume_ramp'))
    result = dict(kind='mesh',level=1.,color='cyan',opacity=1.,visible=True,volume_ramp=[])
    result.update(value)
    if result['kind'] not in ('mesh','surface','volume'):
        raise ValueError('Display kind must be mesh, surface or volume')
    result['level'] = _number(result['level'],'level')
    result['opacity'] = _number(result['opacity'],'opacity',0,1)
    if type(result['visible']) is not bool:
        raise ValueError('visible must be boolean')
    if not isinstance(result['color'],str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}',result['color']):
        raise ValueError('Invalid color name')
    ramp=result['volume_ramp']
    if type(ramp) is not list:
        raise ValueError('Volume ramp must be a list of value/R/G/B/alpha stops')
    if result['kind']=='volume' or ramp:
        if len(ramp)%5 or not 10 <= len(ramp) <= 160:
            raise ValueError('Volume requires 2..32 transfer-ramp stops')
        result['volume_ramp']=[_number(v,'volume ramp',None if i%5==0 else 0,None if i%5==0 else 1) for i,v in enumerate(ramp)]
        if any(a>=b for a,b in zip(ramp[::5],ramp[5::5])):
            raise ValueError('Volume ramp levels must strictly increase')
    return result


def validate_registration(registration):
    if type(registration) is not dict:
        raise ValueError('Registration must be a dictionary')
    if registration.get('kind')=='matrix':
        value=_keys(registration,('kind','matrix','copy_name'),('kind','matrix','copy_name'))
        if type(value['matrix']) is not list: raise ValueError('Matrix must be a list of 16 finite numbers')
        value['matrix']=list(validate_rigid(value['matrix']))
    elif registration.get('kind')=='align':
        value=_keys(registration,('kind','mobile','target','mobile_state','target_state','selection','algorithm','cutoff','cycles','model_copy_name','copy_name'),('kind','mobile','target','model_copy_name','copy_name'))
        defaults=dict(mobile_state=1,target_state=1,selection='all',algorithm='align',cutoff=2.,cycles=5)
        defaults.update(value); value=defaults
        for key in ('mobile','target','model_copy_name'): _name(value[key])
        for key in ('mobile_state','target_state'): _state(value[key])
        if value['algorithm'] not in ('align','super'): raise ValueError('Unsupported alignment algorithm')
        if not isinstance(value['selection'],str) or not value['selection'].strip() or len(value['selection'])>4096 or any(c in value['selection'] for c in '\x00\r\n'):
            raise ValueError('Alignment requires a single-line atom selection')
        value['cutoff']=_number(value['cutoff'],'cutoff',0.000001,1000)
        if type(value['cycles']) is not int or not 0 <= value['cycles'] <= 100: raise ValueError('cycles must be an integer in 0..100')
        if value['model_copy_name']==value['copy_name']: raise ValueError('New copy names must differ')
    else:
        raise ValueError('Registration requires a new matrix or a new aligned model copy')
    _name(value['copy_name'])
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _matrix(cmd,name,state,*,effective=True):
    value=cmd.get_object_matrix(name,state=state,incl_ttt=int(effective))
    if value is None: raise ValueError('Map state has no matrix')
    # Inversion validates finite, affine, nonsingular native geometry.
    inverse_affine(list(value))
    return list(value)


def _point(matrix,point):
    return [sum(matrix[i*4+j]*point[j] for j in range(3))+matrix[i*4+3] for i in range(3)]


def _close(a,b,tolerance=1e-4):
    return len(a)==len(b) and all(abs(x-y)<=tolerance for x,y in zip(a,b))


def _field(cmd,name,state):
    _state(state); _name(name)
    if cmd.get_type(name)!='object:map' or state>cmd.count_states(name):
        raise ValueError('Map or explicit map state is unavailable')
    field=cmd.get_volume_field(name,state=state,copy=1)
    if not hasattr(field,'shape') or len(field.shape)!=3 or any(not 2<=int(n)<=4096 for n in field.shape) or field.size>MAX_VOXELS:
        raise ValueError('Map state is absent or exceeds supported grid bounds')
    return field


def _field_digest(field):
    digest=hashlib.sha256()
    # Canonical little-endian float64, independent of numpy/native endian.
    flat=field.reshape(-1)
    for start in range(0,len(flat),8192):
        chunk=array('d',(float(v) for v in flat[start:start+8192]))
        if any(not math.isfinite(v) for v in chunk): raise ValueError('Native map contains nonfinite values')
        if sys.byteorder!='little': chunk.byteswap()
        digest.update(chunk.tobytes())
    return digest.hexdigest()


def _native_geometry(cmd,name,state,field=None):
    """Read documented public-session data using guarded 3.1/3.2 schema.

    ObjectMapStateAsPyList has sixteen fields. Field[13][3] is a native
    unused dimension slot and is deliberately not an identity component.
    get_session serializes the map field too; bound its size before calling.
    """
    if field is None: field=_field(cmd,name,state)
    if not re.match(r'^3\.[12](?:\.|$)',str(cmd.get_version()[0])):
        raise ValueError('Verified map geometry requires supported PyMOL 3.1/3.2 session schema; map remains display-only')
    try:
        names=cmd.get_session(name,partial=1)['names']
        item=next(v for v in names if v is not None and v[0]==name)
        obj=item[5]
        if item[4]!=2 or len(obj)!=3: raise ValueError()
        data=obj[2][state-1]
        if len(data)!=16 or data[0]!=1 or len(data[6])!=24 or len(data[13])!=4:
            raise ValueError()
        if list(field.shape)!=data[13][:3]: raise ValueError()
        geometry=dict(symmetry=data[1],origin=data[2],range=data[3],dimensions=data[4],spacing=data[5],
                      corners=data[6],minimum=data[7],maximum=data[8],map_source=data[9],
                      divisions=data[10],starts=data[11],ends=data[12],shape=data[13][:3])
        for key in ('corners','minimum','maximum'):
            if any(type(v) not in (float,int) or not math.isfinite(v) for v in geometry[key]): raise ValueError()
        if len(geometry['minimum'])!=3 or len(geometry['maximum'])!=3: raise ValueError()
        # Validate nested numerical metadata by canonical finite JSON encoding.
        _digest(geometry)
        return geometry
    except (TypeError,IndexError,KeyError,StopIteration,ValueError) as exc:
        raise ValueError('Unsupported native map geometry schema; map remains display-only') from exc


def _grid(path,format,interpretation,units):
    if format not in ('dx','mrc','ccp4') or interpretation not in ('auto','origin','starts'):
        raise ValueError('Unsupported density format or interpretation')
    if not isinstance(units,str) or not units.strip() or len(units)>128 or any(c in units for c in '\x00\r\n'):
        raise ValueError('Invalid density units')
    if format=='dx':
        if interpretation!='auto': raise ValueError('DX has no MRC origin interpretation')
        return read_dx(path,value_unit=units)
    return read_mrc(path,format=format,interpretation=interpretation,value_unit=units)


def _verify_grid(cmd,name,state,grid,load_matrix):
    field=_field(cmd,name,state)
    if tuple(field.shape)!=grid.shape: raise ValueError('Native map shape disagrees with validated source')
    # PyMOL stores float32. Exact comparison to independently parsed values,
    # with only the mandatory float32 conversion, catches silent normalization.
    max_quantization=0.
    for actual,want in zip(field.reshape(-1),grid.values):
        try: rounded=struct.unpack('f',struct.pack('f',want))[0]
        except (OverflowError,struct.error) as exc: raise ValueError('Map values exceed native float32') from exc
        if float(actual)!=rounded: raise ValueError('Native raw map values disagree with source')
        max_quantization=max(max_quantization,abs(float(actual)-want))
    native=_native_geometry(cmd,name,state,field)
    corners=native['corners']
    # Native corner order varies X fastest, then Y, then Z.
    for index,(k,j,i) in enumerate(itertools.product((0,grid.shape[2]-1),(0,grid.shape[1]-1),(0,grid.shape[0]-1))):
        if not _close(_point(load_matrix,corners[index*3:index*3+3]),grid_point(grid,i,j,k)):
            raise ValueError('Native map physical geometry disagrees with validated source')
    return _field_digest(field),max_quantization


def load_density(cmd,path,*,format,interpretation='auto',name=None,state=1,units='unknown'):
    _state(state)
    path=checked_path(path)
    grid=_grid(path,format,interpretation,units)
    name=_new_name(cmd,name if name is not None else _unique(cmd,'density'))
    previous=cmd.get_setting_int('normalize_ccp4_maps')
    try:
        cmd.set('normalize_ccp4_maps',0)
        cmd.load(str(path),name,state=state,format=grid.metadata['source_format'],zoom=0)
        matrix=_matrix(cmd,name,state)
        digest,error=_verify_grid(cmd,name,state,grid,matrix)
        if sha256(path)!=grid.metadata['source_sha256']: raise ValueError('Density source changed during load')
        return dict(name=name,state=state,source_path=str(path),source_sha256=grid.metadata['source_sha256'],
                    format=grid.metadata['source_format'],interpretation=grid.metadata['interpretation'],
                    grid=grid_metadata(grid),load_matrix=matrix,world_matrix=matrix,value_sha256=digest,
                    verified=True,derived=[],native_geometry=_native_geometry(cmd,name,state),
                    native_precision=dict(storage='float32',max_abs_quantization_error=error))
    except BaseException:
        if name in cmd.get_names('objects'): cmd.delete(name)
        raise
    finally:
        cmd.set('normalize_ccp4_maps',previous)


def _controlled(cmd,name):
    if cmd.get_movie_length()>0: raise ValueError('Registration does not support an existing native movie')
    ttt=cmd.get_object_ttt(name)
    if ttt is not None and not _close(ttt,IDENTITY,1e-8):
        raise ValueError('Registration does not support existing object TTT transforms')


def _verify_record(cmd,record):
    if type(record) is not dict or record.get('verified') is not True or not record.get('source_path'):
        raise ValueError('Map has no verified source association; reload or reassociate its source')
    path=checked_path(record['source_path'])
    if sha256(path)!=record.get('source_sha256'): raise ValueError('Density source digest is stale')
    interpretation=record['interpretation'] if record['format']!='dx' else 'auto'
    grid=_grid(path,record['format'],interpretation,record['grid']['value_unit'])
    if grid_metadata(grid)!=record['grid']: raise ValueError('Map source geometry metadata is stale')
    if _native_geometry(cmd,record['name'],record['state'])!=record.get('native_geometry'):
        raise ValueError('Native map geometry changed since validated loading')
    digest,_=_verify_grid(cmd,record['name'],record['state'],grid,record['load_matrix'])
    if digest!=record['value_sha256']: raise ValueError('Map values are stale')
    return grid


def map_fingerprint(cmd,record):
    field=_field(cmd,record['name'],record['state'])
    return _digest(dict(state=record['state'],shape=list(field.shape),values=_field_digest(field),
        geometry=_native_geometry(cmd,record['name'],record['state'],field),
        matrix=_matrix(cmd,record['name'],record['state']),source=dict(
        sha256=record.get('source_sha256'),format=record.get('format'),interpretation=record.get('interpretation'),
        load_matrix=record.get('load_matrix'),value_unit=(record.get('grid') or {}).get('value_unit'))))


def map_inventory(cmd,records):
    associated={(r['name'],r['state']):r for r in records}
    result=[]
    for name in cmd.get_names('objects'):
        if cmd.get_type(name)!='object:map': continue
        for state in range(1,cmd.count_states(name)+1):
            try: field=_field(cmd,name,state)
            except ValueError: continue
            record=deepcopy(associated.get((name,state)))
            if record is None:
                record=dict(name=name,state=state,source_path=None,source_sha256=None,format=None,interpretation=None,
                            grid=None,load_matrix=None,world_matrix=_matrix(cmd,name,state),value_sha256=_field_digest(field),verified=False,derived=[])
            else:
                record['world_matrix']=_matrix(cmd,name,state)
                try: _verify_record(cmd,record)
                except (ValueError,OSError): record['verified']=False
            result.append(record)
    return result


def display_density(cmd,record,display):
    spec=validate_display(display)
    _field(cmd,record['name'],record['state'])
    if record.get('verified') is True:
        _verify_record(cmd,record)
    if cmd.get_color_index(spec['color'])<0: raise ValueError('Unknown PyMOL color')
    kind=spec['kind']
    state_suffix='' if record['state']==1 else '_s'+str(record['state'])
    name=record['name']+state_suffix+'_'+kind
    _name(name)
    existing=name in cmd.get_names('all')
    if existing and (name not in record['derived'] or cmd.get_type(name)!='object:'+('surface' if kind=='surface' else kind)):
        raise ValueError('Display name collides with an unowned object')
    try:
        if kind=='volume':
            ramp=[v*spec['opacity'] if i%5==4 else v for i,v in enumerate(spec['volume_ramp'])]
            if not existing: cmd.volume(name,record['name'],ramp=ramp,state=1,source_state=record['state'])
            else: cmd.volume_color(name,ramp,state=1)
        else:
            if not existing:
                method=cmd.isomesh if kind=='mesh' else cmd.isosurface
                method(name,record['name'],level=spec['level'],state=1,source_state=record['state'])
            else: cmd.isolevel(name,spec['level'],state=1)
            cmd.color(spec['color'],name)
            cmd.set('transparency',1-spec['opacity'],name)
        (cmd.enable if spec['visible'] else cmd.disable)(name)
        if name not in record['derived']: record['derived'].append(name)
        return dict(name=name,source=record['name'],source_state=record['state'],state=1,display=spec)
    except BaseException:
        if not existing and name in cmd.get_names('objects'): cmd.delete(name)
        raise


def _atoms(cmd,name,state,selection='all'):
    if cmd.get_type(name)!='object:molecule' or state>cmd.count_states(name): raise ValueError('Molecular object/state unavailable')
    atoms=cmd.get_model('(%'+name+') and ('+selection+')',state=state).atom
    if not atoms: raise ValueError('Alignment selection has no atoms in explicit state')
    points=[list(a.coord) for a in atoms]
    if any(len(p)!=3 or not all(math.isfinite(v) for v in p) for p in points): raise ValueError('Invalid molecular coordinates')
    return points


def _bounds(grid,current,loaded):
    delta=compose(current,inverse_affine(loaded))
    corners=[_point(delta,grid_point(grid,i,j,k)) for i,j,k in itertools.product((0,grid.shape[0]-1),(0,grid.shape[1]-1),(0,grid.shape[2]-1))]
    return dict(minimum=[min(p[i] for p in corners) for i in range(3)],maximum=[max(p[i] for p in corners) for i in range(3)],
                corners=corners,meaning='Geometric bounds only; not evidence of density fit')


def _alignment_modes(cmd, request):
    """Reject the native state-matrix alignment path before creating objects.

    PyMOL 3.2 can access-violate inside align/super with matrix_mode=2,
    including identity sources. Changing that setting also changes the source
    coordinate frame, so never silently override global or source settings.
    """
    modes = {'global': cmd.get_setting_int('matrix_mode')}
    for role in ('mobile', 'target'):
        modes[role] = cmd.get_setting_int('matrix_mode', request[role],
                                         state=request[role+'_state'])
    if any(mode not in (-1, 0, 1) for mode in modes.values()):
        raise ValueError('Density alignment supports matrix_mode -1, 0 or 1 only; '
                         'matrix_mode=2 is unsafe in the native alignment backend. '
                         'Use numeric rigid registration or a separately prepared supported snapshot.')
    return modes


def _verify_model_replay(cmd, request, model, before, after, delta, expected, owned):
    """Measure a reconstruction before publishing a native replay matrix.

    Molecules in mode1 require forward conjugation, unlike map transforms.
    This behavior is checked on every reconstruction against both coordinates
    and the effective matrix, rather than assumed from a backend version.
    """
    mode = cmd.get_setting_int('matrix_mode', model, state=1)
    if mode not in (-1, 0, 1):
        raise ValueError('Copied molecule has unsupported matrix_mode')
    replay = compose(compose(before, delta), inverse_affine(before)) if mode == 1 else delta
    name = _unique(cmd, 'density_replay_check')
    owned.append(name)
    try:
        cmd.create(name, '%'+request['mobile'], source_state=request['mobile_state'],
                   target_state=1, zoom=0)
        if not _close(_matrix(cmd, name, 1), before):
            raise ValueError('Molecular replay copy does not reproduce its source matrix')
        cmd.transform_object(name, list(replay), state=1, homogenous=1, transpose=0)
        actual = _atoms(cmd, name, 1)
        if len(actual) != len(expected):
            raise ValueError('Molecular replay changed atom membership')
        error = max(math.dist(a, b) for a, b in zip(actual, expected))
        if error > 1e-4 or not _close(_matrix(cmd, name, 1), after):
            raise ValueError('Native molecular replay does not reproduce the verified alignment')
        return mode, list(replay), error
    finally:
        if name in cmd.get_names('objects'):
            cmd.delete(name)


def register_density(cmd,record,registration):
    request=validate_registration(registration)
    grid=_verify_record(cmd,record)
    _controlled(cmd,record['name'])
    _new_name(cmd,request['copy_name'])
    source_identity=map_fingerprint(cmd,record)
    before=_matrix(cmd,record['name'],record['state'])
    alignment=None; owned=[]
    if request['kind']=='align':
        _alignment_modes(cmd, request)
        _new_name(cmd,request['model_copy_name'])
        for role in ('mobile','target'):
            _controlled(cmd,request[role])
            _atoms(cmd,request[role],request[role+'_state'],request['selection'])
        originals={role:_atoms(cmd,request[role],request[role+'_state']) for role in ('mobile','target')}
        fingerprints={str(s):scene_fingerprint(cmd,s) for s in (request['mobile_state'],request['target_state'])}
    try:
        if request['kind']=='matrix': delta=request['matrix']
        else:
            model=request['model_copy_name']; owned.append(model)
            cmd.create(model,'%'+request['mobile'],source_state=request['mobile_state'],target_state=1,zoom=0)
            atom_before=_atoms(cmd,model,1); matrix_before=_matrix(cmd,model,1)
            stats=getattr(cmd,request['algorithm'])('(%'+model+') and ('+request['selection']+')',
                '(%'+request['target']+') and ('+request['selection']+')',mobile_state=1,target_state=request['target_state'],
                cutoff=request['cutoff'],cycles=request['cycles'],transform=1,quiet=1)
            matrix_after=_matrix(cmd,model,1); atom_after=_atoms(cmd,model,1)
            delta=list(validate_rigid(compose(matrix_after,inverse_affine(matrix_before))))
            if len(atom_before)!=len(atom_after): raise ValueError('Alignment changed atom membership')
            error=max(math.dist(_point(delta,a),b) for a,b in zip(atom_before,atom_after))
            if error>1e-4: raise ValueError('Alignment matrix does not explain every copied atom')
            mode, replay, replay_error = _verify_model_replay(
                cmd, request, model, matrix_before, matrix_after, delta, atom_after, owned)
            alignment=dict(parameters=request,before_matrix=matrix_before,after_matrix=matrix_after,world_transform=delta,
                model_copy=model,atom_count=len(atom_before),rmsd=float(stats[0]),aligned_atoms=int(stats[1]),
                statistics=[float(v) for v in stats],max_atom_error_angstrom=error,source_fingerprints=fingerprints,
                matrix_mode=mode,replay_transform=replay,max_replay_atom_error_angstrom=replay_error)
        name=request['copy_name']; owned.append(name)
        copy=load_density(cmd,record['source_path'],format=record['format'],interpretation=record['interpretation'] if record['format']!='dx' else 'auto',name=name,state=1,units=grid.value_unit)
        # Reload selected source state as a single-state copy, then reproduce its
        # current matrix before applying the new world transform.
        native=copy['world_matrix']
        initial=compose(inverse_affine(native),before)
        cmd.transform_object(name,list(initial),state=1,homogenous=1,transpose=0)
        transform=compose(compose(inverse_affine(before),delta),before)
        cmd.transform_object(name,list(transform),state=1,homogenous=1,transpose=0)
        after=_matrix(cmd,name,1)
        if not _close(after,compose(delta,before)): raise ValueError('Backend map matrix composition verification failed')
        copy['load_matrix']=list(record['load_matrix']); copy['world_matrix']=after
        _verify_record(cmd,copy)
        if map_fingerprint(cmd,record)!=source_identity: raise ValueError('Registration changed the source map')
        if alignment:
            for role in ('mobile','target'):
                if _atoms(cmd,request[role],request[role+'_state'])!=originals[role]: raise ValueError('Alignment changed an original molecule')
        return dict(source=record['name'],copy=name,source_state=record['state'],copy_state=1,world_transform=list(delta),
                    before_matrix=before,after_matrix=after,alignment=alignment,bounds=_bounds(grid,after,copy['load_matrix']),record=copy)
    except BaseException:
        for name in reversed(owned):
            if name in cmd.get_names('objects'): cmd.delete(name)
        raise
