"""Isolated timeline projects and transactional image/video exports."""
from datetime import datetime, timezone
import hashlib
import inspect
import json
import math
from pathlib import Path
import re
import struct

from . import __version__, timeline, native_render
from .artifacts import checked_path, sha256, write_json
from .bundles import Bundle, CancelledError
from .snapshot import scene_fingerprint

_PROJECT = ('source.pse','timeline.json','replay.pml','report.json','manifest.json')
_VIDEO = ('animation.mp4','tools.json','logs/ffmpeg.stdout.log','logs/ffmpeg.stderr.log',
          'logs/ffprobe.stdout.log','logs/ffprobe.stderr.log')


def _cancel(cancelled):
    if cancelled is not None and cancelled(): raise CancelledError('Operation cancelled')


def _render_spec(render):
    if not isinstance(render,dict) or set(render)-{'width','height','dpi','ray'}:
        raise ValueError('Invalid render specification')
    result=dict(width=800,height=600,dpi=150,ray=True); result.update(render)
    for name,minimum,maximum in [('width',16,4096),('height',16,4096),('dpi',1,1200)]:
        value=result[name]
        if type(value) is not int or not minimum<=value<=maximum: raise ValueError('Invalid render '+name)
    if result['width']*result['height']>16777216: raise ValueError('Render exceeds pixel limit')
    if type(result['ray']) is not bool: raise ValueError('ray must be boolean')
    return result


def _replay(path):
    # Embed the pure implementation verbatim to keep standalone interpolation,
    # validation and seek semantics identical without a MolScene dependency.
    source=inspect.getsource(timeline)
    header='# Standalone timeline replay. Load with @replay.pml from its directory.\npython\n'
    trailer='''
from pathlib import Path
import time
_molscene_root = Path(__script__).resolve().parent
_timeline_path = _molscene_root / 'timeline.json'
if _timeline_path.stat().st_size > 8 * 1024 * 1024:
    raise ValueError('Timeline exceeds 8 MiB')
with _timeline_path.open('rb') as _timeline_reader:
    _timeline_bytes = _timeline_reader.read(8 * 1024 * 1024 + 1)
if len(_timeline_bytes) > 8 * 1024 * 1024:
    raise ValueError('Timeline exceeds 8 MiB')
_molscene_document = validate_timeline(json.loads(_timeline_bytes.decode('utf-8')))
cmd.load(str(_molscene_root / 'source.pse'))
validate_timeline(_molscene_document, cmd)
def molscene_seek(frame):
    return seek_timeline(cmd, _molscene_document, frame)
def molscene_play():
    start = time.monotonic()
    for frame in range(1, _molscene_document['frame_count'] + 1):
        delay = (frame - 1) / _molscene_document['fps'] - (time.monotonic() - start)
        if delay > 0:
            time.sleep(delay)
        molscene_seek(frame)
        cmd.refresh()
molscene_seek(1)
python end
'''
    path.write_text(header+source+trailer,encoding='utf-8')


def _source(path):
    path=checked_path(path)
    if path.suffix.lower()!='.pse' or not path.is_file() or not path.stat().st_size:
        raise ValueError('Source must be an existing nonempty PSE session')
    return path


def _png(path,width,height):
    with path.open('rb') as handle: header=handle.read(24)
    if len(header)!=24 or header[:8]!=b'\x89PNG\r\n\x1a\n' or header[12:16]!=b'IHDR':
        raise ValueError('Renderer did not produce a PNG')
    if struct.unpack('>II',header[16:24])!=(width,height) or path.stat().st_size<=24:
        raise ValueError('Renderer produced incorrect PNG dimensions or empty output')


def _report(cmd,doc,inputs,artifacts,render,stage,operation):
    digest=hashlib.sha256(json.dumps(doc,sort_keys=True,separators=(',',':'),ensure_ascii=False,
                                    allow_nan=False).encode('utf-8')).hexdigest()
    snapshot=inputs['session']['sha256']
    states=range(1,int(cmd.count_states('all'))+1)
    return dict(schema_version=2,tool='molscene',tool_version=__version__,pymol_version=cmd.get_version()[0],
        created_at=datetime.now(timezone.utc).isoformat(),operation=operation,inputs=inputs,
        parameters=dict(timeline=doc,render=render),structure=dict(atom_count=cmd.count_atoms('all')),
        analysis=dict(frame_count=doc['frame_count'],fps=doc['fps'],duration_seconds=doc['frame_count']/doc['fps']),
        artifacts=artifacts,passed=True,replay_contract='bundled_timeline',snapshot_id=snapshot,
        source_fingerprints={str(s):scene_fingerprint(cmd,s) for s in states},
        advanced_fingerprints=dict(files={'session':snapshot},maps={},timeline=digest,display_snapshot=snapshot,
                                   selection=None,session_epoch=None),
        limitations=['Molecular states and scene displays change only at keyframes; camera interpolation is not molecular dynamics.',
                     'Live scene equivalence requires a fresh equivalent display snapshot.'],warnings=[],
        artifact_hashes={p.relative_to(stage).as_posix():sha256(p) for p in sorted(stage.rglob('*')) if p.is_file()})


def _tool_logs(bundle):
    (bundle.stage/'logs').mkdir()
    paths={}
    for name in ('ffmpeg','ffprobe'):
        for stream in ('stdout','stderr'):
            paths[f'{name}.{stream}']=bundle.stage/f'logs/{name}.{stream}.log'
            paths[f'{name}-preflight.{stream}']=bundle.scratch/f'{name}-preflight.{stream}.log'
    bundle.retain_failure_logs(paths)


def _preflight(bundle,tools,cancelled,progress):
    from .external_tools import run_tool
    encoder=run_tool('ffmpeg',tools,['-hide_banner','-encoders'],cwd=bundle.scratch,
        log_prefix=bundle.scratch/'ffmpeg-preflight',cancelled=cancelled,progress=progress)
    text='\n'.join(Path(encoder[k]).read_text('utf-8',errors='replace') for k in ('stdout_path','stderr_path'))
    if not re.search(r'^\s*V\S*\s+libx264\s',text,re.MULTILINE):
        raise ValueError('Configured FFmpeg does not provide libx264')
    run_tool('ffprobe',tools,['-version'],cwd=bundle.scratch,log_prefix=bundle.scratch/'ffprobe-preflight',
             cancelled=cancelled,progress=progress)


def _encode(bundle,tools,doc,spec,cancelled,progress):
    from .external_tools import run_tool
    args=['-nostdin','-hide_banner','-loglevel','error','-n','-framerate',str(doc['fps']),
          '-start_number','1','-i','frames/frame_%06d.png','-frames:v',str(doc['frame_count']),
          '-c:v','libx264','-pix_fmt','yuv420p','animation.mp4']
    encoder=run_tool('ffmpeg',tools,args,cwd=bundle.stage,log_prefix=bundle.stage/'logs/ffmpeg',
                     cancelled=cancelled,progress=progress)
    output=bundle.stage/'animation.mp4'
    if not output.is_file() or not output.stat().st_size: raise RuntimeError('FFmpeg produced no nonempty MP4')
    _cancel(cancelled)
    args=['-v','error','-count_frames','-select_streams','v:0','-show_entries',
          'stream=codec_name,pix_fmt,width,height,nb_read_frames,duration:format=duration','-of','json','animation.mp4']
    probe=run_tool('ffprobe',tools,args,cwd=bundle.stage,log_prefix=bundle.stage/'logs/ffprobe',
                   cancelled=cancelled,progress=progress)
    try:
        data=json.loads(Path(probe['stdout_path']).read_text('utf-8'))
        streams=data['streams']
        if len(streams)!=1: raise ValueError('Expected exactly one video stream')
        stream=streams[0]
        duration=float(stream.get('duration',data.get('format',{}).get('duration')))
        decoded=int(stream['nb_read_frames'])
        if (stream['codec_name']!='h264' or stream['pix_fmt']!='yuv420p'
                or stream['width']!=spec['width'] or stream['height']!=spec['height']
                or decoded!=doc['frame_count'] or not math.isfinite(duration)
                or abs(duration-doc['frame_count']/doc['fps'])>max(1e-5,1/doc['fps'])):
            raise ValueError('H.264 format, dimensions, decoded frames or duration mismatch')
    except (ValueError,KeyError,TypeError,IndexError) as exc:
        raise RuntimeError('MP4 validation failed: '+str(exc)) from exc
    provenance={}
    for name,record in [('ffmpeg',encoder),('ffprobe',probe)]:
        record=dict(record)
        record['stdout_path']=f'logs/{name}.stdout.log'; record['stderr_path']=f'logs/{name}.stderr.log'
        provenance[name]=record
    write_json(bundle.stage/'tools.json',dict(schema_version=1,tools=provenance,encoder='libx264',pixel_format='yuv420p'))
    return dict(codec_name='h264',pix_fmt='yuv420p',width=stream['width'],height=stream['height'],
                decoded_frames=decoded,duration_seconds=duration)


def _export(session_path,output_dir,document,*,render=None,mp4=False,tools=None,cancelled=None,progress=None,protected=()):
    doc=timeline.validate_timeline(document); source=_source(session_path)
    if type(mp4) is not bool: raise ValueError('mp4 must be boolean')
    spec=None if render is None else _render_spec(render)
    if spec and spec['width']*spec['height']*4*doc['frame_count']>8*1024**3:
        raise ValueError('Estimated raw image sequence exceeds 8 GiB')
    if mp4:
        if not spec or spec['width']%2 or spec['height']%2: raise ValueError('MP4 requires even image dimensions')
        if not isinstance(tools,dict) or not isinstance(tools.get('tools'),dict) or not {'ffmpeg','ffprobe'}.issubset(tools['tools']):
            raise ValueError('MP4 requires configured FFmpeg and ffprobe')
    frames=[] if spec is None else [f'frames/frame_{frame:06d}.png' for frame in range(1,doc['frame_count']+1)]
    inventory=list(_PROJECT)+frames+(list(_VIDEO) if mp4 else [])
    total=len(frames)+2+int(mp4)
    def emit(stage,completed,message):
        _cancel(cancelled)
        if progress is not None: progress(dict(stage=stage,completed=completed,total=total,message=message))
        _cancel(cancelled)
    emit('prepare',0,'Preparing timeline snapshot')
    with Bundle(Path(output_dir),protected=[source,*protected],inventory=inventory,cancelled=cancelled) as bundle:
        completed=0
        def tool_progress(event):
            _cancel(cancelled)
            if progress is not None: progress(dict(event,completed=completed,total=total))
            _cancel(cancelled)
        if mp4:
            _tool_logs(bundle)
            _preflight(bundle,tools,cancelled,tool_progress)
        inputs={'session':bundle.copy_input(source,'source.pse')}
        write_json(bundle.stage/'timeline.json',doc); _replay(bundle.stage/'replay.pml')
        import pymol2
        with pymol2.PyMOL() as pm:
            cmd=pm.cmd; cmd.load(str(bundle.stage/'source.pse'))
            timeline.validate_timeline(doc,cmd)
            artifacts=dict(session='source.pse',timeline='timeline.json',replay='replay.pml',report='report.json',manifest='manifest.json')
            rendering=None; render_warnings=[]
            if frames:
                (bundle.stage/'frames').mkdir(); artifacts['frames']=frames
                if native_render.requires_native(cmd,spec['ray'],timeline=doc):
                    native=native_render.render_native(bundle.stage/'source.pse',[bundle.stage/name for name in frames],spec,
                        work_dir=bundle.scratch,timeline=doc,cancelled=cancelled,bundle=bundle,
                        progress=lambda event:emit('render',event['completed'],event['message']))
                    rendering=native_render.report_metadata(native,bundle.stage); render_warnings=native['warnings']
                else:
                    rendering=dict(backend='cpu_ray',requested_ray=spec['ray'],effective_ray=True,width=spec['width'],height=spec['height'])
                    for frame,name in enumerate(frames,1):
                        _cancel(cancelled); timeline.seek_timeline(cmd,doc,frame)
                        cmd.png(str(bundle.stage/name),width=spec['width'],height=spec['height'],dpi=spec['dpi'],ray=1,quiet=1)
                        _png(bundle.stage/name,spec['width'],spec['height'])
                        emit('render',frame,f'Rendered frame {frame} of {len(frames)}')
            video=None
            if mp4:
                completed=len(frames)
                emit('encode',completed,'Encoding H.264 video')
                video=_encode(bundle,tools,doc,spec,cancelled,tool_progress)
                artifacts.update(video='animation.mp4',tools='tools.json',logs=list(_VIDEO[2:]))
            report=_report(cmd,doc,inputs,artifacts,spec,bundle.stage,'animation' if spec else 'timeline_project')
            if rendering is not None: report['analysis']['rendering']=rendering
            report['warnings'].extend(render_warnings)
            if video is not None: report['analysis']['video']=video
            write_json(bundle.stage/'report.json',report)
        emit('publish',total-1,'Publishing verified timeline bundle')
        bundle.publish()
    if progress is not None: progress(dict(stage='complete',completed=total,total=total,message='Timeline bundle complete'))
    return report


def save_timeline_project(session_path,document,output_dir,*,cancelled=None,protected=()):
    """Bundle the exact source PSE, timeline JSON and portable standalone replay."""
    return _export(session_path,output_dir,document,cancelled=cancelled,protected=protected)


def export_animation(session_path,output_dir,document,*,render,mp4=False,tools=None,cancelled=None,progress=None,protected=()):
    """Render all frames in isolation, optionally validate H.264, then publish."""
    render=_render_spec(render)
    return _export(session_path,output_dir,document,render=render,mp4=mp4,tools=tools,
                   cancelled=cancelled,progress=progress,protected=protected)
