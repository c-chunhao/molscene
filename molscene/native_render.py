"""Bounded image protocol and supervision of an official native PyMOL child.

Qt and PyMOL are imported only in the child. Ownership, timeout and worker-death
cleanup belong to the existing external-tool guardian, including venv launchers.
"""
import copy
import json
import math
import os
from pathlib import Path
import re
import struct
import sys
import time
import uuid
import zlib

from .artifacts import checked_path, sha256
from .bundles import CancelledError
from .timeline import validate_timeline

JSON_LIMIT = 16 * 1024 * 1024
WARNING = ('Native OpenGL export uses GPU-dependent volume and opacity rendering; '
           'the image may be opaque and does not promise ray-traced alpha parity.')


def requires_native(cmd, ray, *, timeline=None):
    """Scenes can enable a currently hidden volume: inspect all for timelines."""
    return not ray or any(cmd.get_type(name)=='object:volume' for name in
                         cmd.get_names('objects',enabled_only=0 if timeline is not None else 1))


def read_json(path, limit=JSON_LIMIT):
    path=checked_path(path)
    if path.stat().st_size>limit: raise ValueError('Native JSON exceeds byte limit')
    with path.open('rb') as handle: data=handle.read(limit+1)
    if len(data)>limit: raise ValueError('Native JSON exceeds byte limit')
    def pairs(rows):
        result={}
        for key,value in rows:
            if key in result: raise ValueError('Duplicate native JSON key')
            result[key]=value
        return result
    def invalid(value): raise ValueError('Nonfinite native JSON number')
    result=json.loads(data.decode('utf-8'),object_pairs_hook=pairs,parse_constant=invalid)
    # JSON's ordinary numeric parser also accepts overflowing exponents.
    def finite(value):
        if isinstance(value,float) and not math.isfinite(value): invalid(value)
        if isinstance(value,dict):
            for item in value.values(): finite(item)
        elif isinstance(value,list):
            for item in value: finite(item)
    finite(result)
    return result


def write_json(path, data):
    """Atomic bounded state updates so polling never observes half a frame."""
    path=checked_path(path); encoded=json.dumps(data,ensure_ascii=True,allow_nan=False).encode('utf-8')
    if len(encoded)>JSON_LIMIT: raise ValueError('Native JSON exceeds byte limit')
    temporary=checked_path(str(path)+'.tmp')
    with temporary.open('xb') as handle: handle.write(encoded)
    # Windows Python readers do not share DELETE access. Their short polling
    # read may overlap this replace; retry only that transient, with a deadline.
    deadline=time.monotonic()+1
    while True:
        try:
            os.replace(temporary,path); break
        except PermissionError:
            if os.name!='nt' or time.monotonic()>=deadline: raise
            time.sleep(.01)


def _path(value):
    if not isinstance(value,str) or not Path(value).is_absolute() or '\0' in value:
        raise ValueError('Native paths must be absolute literal strings')
    return checked_path(value)


def validate_request(value):
    keys={'schema_version','request_id','source','source_sha256','outputs','render','timeline','work_dir'}
    if not isinstance(value,dict) or set(value)!=keys or type(value['schema_version']) is not int or value['schema_version']!=1:
        raise ValueError('Invalid native render request schema')
    if not isinstance(value['request_id'],str) or not re.fullmatch('[0-9a-f]{32}',value['request_id']):
        raise ValueError('Invalid render request identity')
    if not isinstance(value['source_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',value['source_sha256']):
        raise ValueError('Invalid source digest')
    source=_path(value['source']); work=_path(value['work_dir'])
    if source.suffix.lower()!='.pse' or not source.is_file() or not source.stat().st_size or not work.is_dir():
        raise ValueError('Native render requires a nonempty PSE and private working directory')
    spec=value['render']
    if not isinstance(spec,dict) or set(spec)!={'width','height','dpi','ray'}: raise ValueError('Invalid native render settings')
    for key,low,high in [('width',16,4096),('height',16,4096),('dpi',1,1200)]:
        if type(spec[key]) is not int or not low<=spec[key]<=high: raise ValueError('Invalid native '+key)
    if type(spec['ray']) is not bool: raise ValueError('Native ray request must be boolean')
    doc=None if value['timeline'] is None else validate_timeline(value['timeline'])
    count=1 if doc is None else doc['frame_count']; outputs=value['outputs']
    if not isinstance(outputs,list) or len(outputs)!=count: raise ValueError('Native frame count mismatch')
    if spec['width']*spec['height']*4*count>8*1024**3: raise ValueError('Native sequence exceeds pixel budget')
    paths=[_path(name) for name in outputs]
    reserved={source,work/'request.json',work/'result.json',work/'progress.json',work/'native.stdout.log',work/'native.stderr.log'}
    if len(set(paths))!=len(paths) or any(path in reserved or path.suffix.lower()!='.png' or not path.parent.is_dir() for path in paths):
        raise ValueError('Native outputs must be unique PNG paths distinct from inputs/protocol')
    # The caller supplies exact paths in its owned Bundle stage/scratch. Never
    # interpolate these paths, scenes, or labels into PyMOL command language.
    if len(json.dumps(value,ensure_ascii=True,allow_nan=False).encode('utf-8'))>JSON_LIMIT:
        raise ValueError('Native request exceeds byte limit')
    return copy.deepcopy(value)


def build_request(session_path, outputs, render, *, work_dir, timeline=None):
    source=checked_path(session_path)
    return validate_request(dict(schema_version=1,request_id=uuid.uuid4().hex,source=str(source),
        source_sha256=sha256(source),outputs=[str(checked_path(p)) for p in outputs],render=copy.deepcopy(render),
        timeline=copy.deepcopy(timeline),work_dir=str(checked_path(work_dir))))


def validate_png(path, width, height):
    """Validate the entire bounded PNG stream, CRCs and decompressed scanlines.

    The native writer emits noninterlaced 8-bit RGB/RGBA. The child additionally
    decodes through QImage and reports actual pixel diagnostics.
    """
    path=checked_path(path); maximum=width*height*8+1024*1024
    if not path.is_file() or path.stat().st_size>maximum: raise ValueError('Missing or oversized native PNG')
    raw=path.read_bytes()
    if raw[:8]!=b'\x89PNG\r\n\x1a\n': raise ValueError('Invalid PNG signature')
    offset=8; chunks=[]; compressed=[]; channels=None; ended=False
    while offset<len(raw):
        if offset+12>len(raw): raise ValueError('Truncated PNG chunk')
        length=struct.unpack_from('>I',raw,offset)[0]; kind=raw[offset+4:offset+8]; end=offset+12+length
        if end>len(raw): raise ValueError('Truncated PNG payload')
        data=raw[offset+8:end-4]
        if zlib.crc32(kind+data)!=struct.unpack_from('>I',raw,end-4)[0]: raise ValueError('PNG CRC mismatch')
        if not chunks and kind!=b'IHDR': raise ValueError('Missing PNG header')
        if kind==b'IHDR':
            if chunks or length!=13: raise ValueError('Invalid PNG header')
            w,h,depth,color,compression,filters,interlace=struct.unpack('>IIBBBBB',data)
            if (w,h)!=(width,height) or depth!=8 or color not in (2,6) or (compression,filters,interlace)!=(0,0,0):
                raise ValueError('PNG dimensions or encoding mismatch')
            channels=3 if color==2 else 4
        elif kind==b'IDAT':
            if b'IDAT' in chunks and chunks[-1]!=b'IDAT': raise ValueError('Nonconsecutive PNG image data')
            compressed.append(data)
        elif kind==b'IEND':
            if length or end!=len(raw) or not compressed: raise ValueError('Invalid PNG ending')
            ended=True
        elif kind[:1].isupper() and kind!=b'PLTE': raise ValueError('Unsupported critical PNG chunk')
        chunks.append(kind); offset=end
    if not ended or channels is None: raise ValueError('Incomplete PNG')
    expected=(width*channels+1)*height
    decoder=zlib.decompressobj()
    try: decoded=decoder.decompress(b''.join(compressed),expected+1)
    except zlib.error as error: raise ValueError('Invalid PNG compressed data') from error
    if len(decoded)!=expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError('Incomplete or excessive PNG pixels')
    if any(decoded[row*(width*channels+1)]>4 for row in range(height)):
        raise ValueError('Invalid PNG row filter')
    return dict(width=width,height=height,sha256=sha256(path),bytes=len(raw))


def validate_result(request, path):
    data=read_json(path)
    required={'schema_version','request_id','source_sha256','passed','native_pid','parent_pid','renderer',
              'pymol_version','qt_binding','gl_valid','close_requested','close_event_seen','initial_view','frames'}
    if not isinstance(data,dict) or set(data)!=required: raise ValueError('Invalid native result schema')
    if (type(data['schema_version']) is not int or data['schema_version']!=1 or data['request_id']!=request['request_id'] or data['source_sha256']!=request['source_sha256']
            or any(data[key] is not True for key in ('passed','gl_valid','close_requested','close_event_seen'))):
        raise ValueError('Native renderer did not complete successfully')
    if any(type(data[key]) is not int or data[key]<=0 for key in ('native_pid','parent_pid')):
        raise ValueError('Invalid native process identity')
    if not isinstance(data['renderer'],list) or len(data['renderer'])!=3 or any(not isinstance(s,str) or not s for s in data['renderer']):
        raise ValueError('Native graphics renderer is missing')
    for key in ('pymol_version','qt_binding'):
        if not isinstance(data[key],str) or not data[key]: raise ValueError('Native runtime identity is missing')
    from .timeline import _view, sample_timeline
    _view(data['initial_view'])
    rows=data['frames']
    if not isinstance(rows,list) or len(rows)!=len(request['outputs']): raise ValueError('Native result frame count mismatch')
    for index,(row,name) in enumerate(zip(rows,request['outputs']),1):
        keys={'frame','path','width','height','sha256','bytes','pixels','view_before','view_after'}
        if not isinstance(row,dict) or set(row)!=keys or type(row['frame']) is not int or row['frame']!=index or row['path']!=name:
            raise ValueError('Native result frame identity mismatch')
        actual=validate_png(name,request['render']['width'],request['render']['height'])
        if any(row[key]!=value for key,value in actual.items()): raise ValueError('Native image digest/dimensions mismatch')
        _view(row['view_before']); _view(row['view_after'])
        if row['view_before']!=row['view_after']: raise ValueError('Native drawing changed the camera')
        expected_view=(data['initial_view'] if request['timeline'] is None else sample_timeline(request['timeline'],index)['view'])
        if any(abs(a-b)>1e-5*max(1,abs(b)) for a,b in zip(row['view_before'],expected_view)):
            raise ValueError('Native frame does not match the requested camera')
        pixels=row['pixels']; total=actual['width']*actual['height']
        if not isinstance(pixels,dict) or set(pixels)!={'count','rgba_min','rgba_max','nonblack_rgb_gt3','opaque_pixels'} or pixels['count']!=total:
            raise ValueError('Missing decoded pixel diagnostics')
        for key in ('nonblack_rgb_gt3','opaque_pixels'):
            if type(pixels[key]) is not int or not 0<=pixels[key]<=total: raise ValueError('Invalid decoded pixel count')
        for key in ('rgba_min','rgba_max'):
            if not isinstance(pixels[key],list) or len(pixels[key])!=4 or any(type(v) is not int or not 0<=v<=255 for v in pixels[key]):
                raise ValueError('Invalid decoded channel diagnostics')
        if any(a>b for a,b in zip(pixels['rgba_min'],pixels['rgba_max'])): raise ValueError('Inverted channel diagnostics')
    if sha256(request['source'])!=request['source_sha256']: raise ValueError('Native input changed during rendering')
    return data


def render_native(session_path, outputs, render, *, work_dir, timeline=None, cancelled=None, progress=None, bundle=None):
    """Render a complete immutable session/sequence in one owned Qt process."""
    from .external_tools import _managed, ExternalToolError
    work=checked_path(work_dir)
    request=build_request(session_path,outputs,render,work_dir=work,timeline=timeline)
    protocol={name:work/(name+'.json') for name in ('request','result','progress')}
    if any(p.exists() for p in [*protocol.values(),*[Path(n) for n in request['outputs']]]):
        raise ValueError('Refusing to overwrite native protocol or image output')
    prefix=work/'native'
    if bundle is not None:
        bundle.retain_failure_logs({'native.stdout':Path(str(prefix)+'.stdout.log'),
            'native.stderr':Path(str(prefix)+'.stderr.log'),'native.result':protocol['result'],'native.progress':protocol['progress']})
    write_json(protocol['request'],request)
    entry=dict(argv=[sys.executable,'-B','-u'],architecture='x64' if struct.calcsize('P')==8 else 'x86',
               path_dirs=[str(Path(sys.executable).parent)],runtime_files=[],timeout_seconds=3600,memory_limit_mib=4096)
    last=[0]
    def poll():
        if cancelled and cancelled(): return True
        if protocol['progress'].exists():
            state=read_json(protocol['progress'],65536)
            if (not isinstance(state,dict) or set(state)!={'request_id','completed','total'} or state['request_id']!=request['request_id']
                    or type(state['completed']) is not int or type(state['total']) is not int
                    or state['total']!=len(outputs) or not last[0]<=state['completed']<=len(outputs)):
                raise ValueError('Invalid native progress identity')
            if state['completed']>last[0]:
                # Each rendered frame remains visible to callers even when two
                # progress updates arrive between consecutive guardian polls.
                for number in range(last[0]+1,state['completed']+1):
                    if progress: progress(dict(stage='render',completed=number,total=len(outputs),message=f'Rendered frame {number} of {len(outputs)}'))
                    last[0]=number
                    if cancelled and cancelled(): return True
        return False
    managed=_managed('native_opengl',entry,[str(Path(__file__).with_name('native_render_child.py')),str(protocol['request'])],
                     work,prefix,poll,None)
    if managed['returncode']!=0: raise ExternalToolError('Native renderer exited unsuccessfully',managed)
    if poll(): raise CancelledError('Native rendering cancelled')
    result=validate_result(request,protocol['result'])
    return dict(backend='native_opengl',requested_ray=render['ray'],effective_ray=False,width=render['width'],height=render['height'],
                renderer=result['renderer'],warnings=[WARNING],result=result,process=managed)


def report_metadata(record, stage):
    """Keep artifact paths portable; private control paths never become artifacts."""
    result=record['result']; process=record['process']
    images=copy.deepcopy(result['frames'])
    for image in images: image['path']=Path(image['path']).relative_to(stage).as_posix()
    return dict(backend=record['backend'],requested_ray=record['requested_ray'],effective_ray=False,
                width=record['width'],height=record['height'],renderer=record['renderer'],
                pymol_version=result['pymol_version'],qt_binding=result['qt_binding'],images=images,
                process=dict(native_pid=result['native_pid'],parent_pid=result['parent_pid'],guardian_pid=process['guardian_pid'],
                             returncode=process['returncode'],close_event_seen=result['close_event_seen']))
