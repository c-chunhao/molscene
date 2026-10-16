"""Validated, deterministic camera timelines independent of PyMOL's movie."""
import bisect
import copy
import json
import math
import re
import uuid


def _integer(value, name, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f'{name} must be an integer in 1..{maximum}')
    return value


def _name(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 256 or '\0' in value:
        raise ValueError('Scene names must contain 1..256 characters without NUL')
    return value


def _view(value):
    if not isinstance(value, list) or len(value) != 18:
        raise ValueError('Camera view requires 18 finite numbers')
    try:
        finite=all(type(x) in (int,float) and math.isfinite(x) for x in value)
    except OverflowError:
        finite=False
    if not finite:
        raise ValueError('Camera view requires finite numbers')
    r = [value[i:i+3] for i in (0,3,6)]
    for i in range(3):
        for j in range(3):
            if abs(sum(a*b for a,b in zip(r[i],r[j])) - int(i==j)) > 1e-5:
                raise ValueError('Camera rotation must be orthonormal')
    determinant = (r[0][0]*(r[1][1]*r[2][2]-r[1][2]*r[2][1])
                   - r[0][1]*(r[1][0]*r[2][2]-r[1][2]*r[2][0])
                   + r[0][2]*(r[1][0]*r[2][1]-r[1][1]*r[2][0]))
    if abs(determinant-1) > 1e-5:
        raise ValueError('Camera rotation must have determinant +1')
    if value[15] >= value[16]:
        raise ValueError('Camera front clipping plane must precede rear plane')
    return list(value)


def _keyframe(row):
    if not isinstance(row, dict) or set(row) != {'id','label','frame','scene','state','view','interpolation'}:
        raise ValueError('Invalid keyframe fields')
    if not isinstance(row['id'],str) or re.fullmatch(r'[A-Za-z0-9_-]{1,64}',row['id']) is None:
        raise ValueError('Invalid keyframe ID')
    if not isinstance(row['label'],str) or len(row['label']) > 256:
        raise ValueError('Keyframe label must contain at most 256 characters')
    _integer(row['frame'],'frame',10000); _integer(row['state'],'state',100000)
    if row['scene'] is not None: _name(row['scene'])
    _view(row['view'])
    if row['interpolation'] not in ('hold','smooth'):
        raise ValueError('Interpolation must be hold or smooth')


def validate_timeline(document, cmd=None):
    """Return an independent document; optionally verify live scenes/states."""
    if not isinstance(document,dict) or set(document) != {'schema_version','baseline_scene','fps','frame_count','keyframes'}:
        raise ValueError('Invalid timeline fields')
    if type(document['schema_version']) is not int or document['schema_version'] != 1:
        raise ValueError('Unsupported timeline schema')
    _name(document['baseline_scene']); _integer(document['fps'],'fps',120)
    _integer(document['frame_count'],'frame_count',10000)
    rows=document['keyframes']
    if not isinstance(rows,list) or not 1 <= len(rows) <= 10000:
        raise ValueError('Timeline requires 1..10000 keyframes')
    for row in rows: _keyframe(row)
    if len({row['id'] for row in rows}) != len(rows):
        raise ValueError('Keyframe IDs must be unique')
    if rows[0]['frame'] != 1 or rows[-1]['frame'] != document['frame_count']:
        raise ValueError('First and last keyframes must define the timeline duration')
    for left,right in zip(rows,rows[1:]):
        if left['frame'] >= right['frame']:
            raise ValueError('Keyframe frames must be strictly increasing')
        if left['interpolation']=='smooth' and left['view'][17] != right['view'][17]:
            raise ValueError('Smooth intervals require equal projection flags')
    try:
        encoded=json.dumps(document,ensure_ascii=False,allow_nan=False).encode('utf-8')
    except (ValueError,UnicodeError,TypeError) as exc:
        raise ValueError('Timeline must be finite UTF-8 JSON') from exc
    if len(encoded)>8*1024*1024: raise ValueError('Timeline exceeds 8 MiB')
    if cmd is not None:
        scenes=set(cmd.get_scene_list())
        references={document['baseline_scene']} | {r['scene'] for r in rows if r['scene'] is not None}
        if not references.issubset(scenes): raise ValueError('Referenced timeline scene is missing')
        states=int(cmd.count_states('all'))
        if any(r['state']>states for r in rows): raise ValueError('Referenced molecular state is missing')
    return copy.deepcopy(document)


def capture_keyframe(cmd, *, frame, state, scene=None, label='', interpolation='smooth'):
    """Capture the live camera with explicit molecular state and display reference."""
    result=dict(id=uuid.uuid4().hex,label=label,frame=frame,scene=scene,state=state,
                view=list(cmd.get_view()),interpolation=interpolation)
    _keyframe(result)
    if state>cmd.count_states('all'): raise ValueError('Referenced molecular state is missing')
    if scene is not None and scene not in cmd.get_scene_list(): raise ValueError('Referenced scene is missing')
    return result


def new_timeline(cmd, *, fps=24):
    """Store a unique baseline display and a frame-one camera at current state."""
    _integer(fps,'fps',120)
    first=capture_keyframe(cmd,frame=1,state=int(cmd.get_state()))
    name='_molscene_baseline_'+uuid.uuid4().hex
    while name in cmd.get_scene_list(): name='_molscene_baseline_'+uuid.uuid4().hex
    cmd.scene(name,'store')
    return dict(schema_version=1,baseline_scene=name,fps=fps,frame_count=1,keyframes=[first])


def _quaternion(v):
    m=[v[0:3],v[3:6],v[6:9]]
    trace=m[0][0]+m[1][1]+m[2][2]
    if trace>0:
        s=2*math.sqrt(trace+1)
        q=[s/4,(m[2][1]-m[1][2])/s,(m[0][2]-m[2][0])/s,(m[1][0]-m[0][1])/s]
    else:
        i=max(range(3),key=lambda n:m[n][n]); j=(i+1)%3; k=(i+2)%3
        s=2*math.sqrt(max(0,1+m[i][i]-m[j][j]-m[k][k]))
        q=[0.,0.,0.,0.]; q[0]=(m[k][j]-m[j][k])/s
        q[i+1]=s/4; q[j+1]=(m[j][i]+m[i][j])/s; q[k+1]=(m[k][i]+m[i][k])/s
    norm=math.sqrt(sum(x*x for x in q))
    return [x/norm for x in q]


def _interpolate(a,b,t):
    q,r=_quaternion(a),_quaternion(b)
    dot=sum(x*y for x,y in zip(q,r))
    if dot<0: r=[-x for x in r]; dot=-dot
    dot=min(1.,max(-1.,dot))
    if dot>0.9995: q=[x+t*(y-x) for x,y in zip(q,r)]
    else:
        angle=math.acos(dot); denominator=math.sin(angle)
        q=[(math.sin((1-t)*angle)*x+math.sin(t*angle)*y)/denominator for x,y in zip(q,r)]
    norm=math.sqrt(sum(x*x for x in q)); w,x,y,z=[v/norm for v in q]
    rotation=[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w),
              2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w),
              2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]
    return rotation+[(1-t)*a[i]+t*b[i] for i in range(9,17)]+[a[17]]


def _sample(document,frame):
    _integer(frame,'frame',document['frame_count'])
    rows=document['keyframes']; index=bisect.bisect_right([r['frame'] for r in rows],frame)-1
    left=rows[index]; scene=document['baseline_scene']
    for row in rows[:index+1]:
        if row['scene'] is not None: scene=row['scene']
    value=list(left['view'])
    if frame!=left['frame'] and index+1<len(rows) and left['interpolation']=='smooth':
        right=rows[index+1]; t=(frame-left['frame'])/(right['frame']-left['frame'])
        value=_interpolate(left['view'],right['view'],t)
    return dict(frame=frame,time_seconds=(frame-1)/document['fps'],view=value,state=left['state'],scene=scene)


def sample_timeline(document,frame):
    """Resolve one frame without backend access; state and display changes are cuts."""
    return _sample(validate_timeline(document),frame)


def seek_timeline(cmd,document,frame):
    """Validate before mutation, stop native playback, and apply an absolute sample."""
    result=_sample(validate_timeline(document,cmd),frame)
    cmd.mstop()
    cmd.scene(result['scene'],'recall',view=0,frame=0,animate=0)
    cmd.set('state',result['state'])
    cmd.set_view(result['view'],animate=0)
    return result


def edit_keyframe(document,action,keyframe_id=None,values=None):
    """Return a validated edit; the final row explicitly determines duration."""
    result=validate_timeline(document); rows=result['keyframes']
    if action=='set_fps':
        if keyframe_id is not None or not isinstance(values,dict) or set(values)!={'fps'}:
            raise ValueError('set_fps requires only fps')
        result['fps']=values['fps']
    elif action=='add':
        if keyframe_id is not None: raise ValueError('add does not take keyframe_id')
        _keyframe(values); rows.append(copy.deepcopy(values))
    elif action in ('update','remove','move_up','move_down'):
        indexes=[i for i,r in enumerate(rows) if r['id']==keyframe_id]
        if not indexes: raise ValueError('Unknown keyframe ID')
        index=indexes[0]
        if action=='update':
            if not isinstance(values,dict) or set(values)-{'label','frame','scene','state','view','interpolation'}:
                raise ValueError('Invalid update fields')
            rows[index].update(copy.deepcopy(values)); _keyframe(rows[index])
        else:
            if values is not None: raise ValueError('This action does not take values')
            if action=='remove':
                if len(rows)==1: raise ValueError('Cannot remove the last keyframe')
                rows.pop(index)
                if index==0: rows[0]['frame']=1
            else:
                other=index+(-1 if action=='move_up' else 1)
                if 0<=other<len(rows): rows[index]['frame'],rows[other]['frame']=rows[other]['frame'],rows[index]['frame']
    else: raise ValueError('Unknown keyframe action')
    rows.sort(key=lambda r:r['frame']); result['frame_count']=rows[-1]['frame']
    return validate_timeline(result)


def rename_scene_reference(document,old,new):
    """Update display references after the caller successfully renames the scene."""
    _name(old); _name(new); result=validate_timeline(document)
    if result['baseline_scene']==old: result['baseline_scene']=new
    for row in result['keyframes']:
        if row['scene']==old: row['scene']=new
    return validate_timeline(result)


def scene_is_referenced(document,name):
    """Allow scene lifecycle callers to refuse deletion of an in-use display."""
    document=validate_timeline(document)
    return name==document['baseline_scene'] or any(row['scene']==name for row in document['keyframes'])
