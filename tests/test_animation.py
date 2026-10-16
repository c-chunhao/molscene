"""Real isolated session bundles, standalone replay and video validation."""
import builtins
import copy
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import sys
import struct
import unittest
from unittest import mock

try:
    from molscene import animation
except ImportError:
    animation=None


@unittest.skipUnless(importlib.util.find_spec('pymol2'),'PyMOL unavailable')
class AnimationReplayTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(animation,'animation implementation is required')
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.source=self.root/'original.pse'
        import pymol2
        from molscene.timeline import new_timeline,capture_keyframe,edit_keyframe
        from molscene.snapshot import scene_fingerprint
        with pymol2.PyMOL() as pm:
            c=pm.cmd; c.pseudoatom('mol',pos=[0,0,0],state=1); c.pseudoatom('mol',pos=[4,0,0],state=2)
            c.pseudoatom('other',pos=[0,3,0]); c.show('spheres'); c.color('blue','mol'); c.zoom('all')
            self.doc=new_timeline(c)
            c.color('red','mol'); c.disable('other'); c.turn('y',90)
            scene="red '; __import__('evil') # 中文"; c.scene(scene,'store')
            self.doc=edit_keyframe(self.doc,'add',values=capture_keyframe(c,frame=3,state=2,scene=scene))
            self.doc=edit_keyframe(self.doc,'add',values=capture_keyframe(c,frame=4,state=1))
            c.save(str(self.source))
            self.fingerprints={str(s):scene_fingerprint(c,s) for s in (1,2)}
        self.original=self.source.read_bytes(); self.original_doc=copy.deepcopy(self.doc)

    def test_project_closed_inventory_hashes_and_immutable_sources(self):
        out=self.root/'project'
        report=animation.save_timeline_project(self.source,self.doc,out)
        self.assertEqual({p.name for p in out.iterdir()}, {'source.pse','timeline.json','replay.pml','report.json','manifest.json'})
        self.assertEqual(report['schema_version'],2)
        self.assertTrue(report['passed']); self.assertEqual(report['snapshot_id'],hashlib.sha256(self.original).hexdigest())
        manifest=json.loads((out/'manifest.json').read_text('utf-8'))
        self.assertEqual(set(manifest['files']),{'source.pse','timeline.json','replay.pml','report.json'})
        self.assertEqual(set(report['artifact_hashes']),{'source.pse','timeline.json','replay.pml'})
        for name,entry in manifest['files'].items():
            self.assertEqual(entry['sha256'],hashlib.sha256((out/name).read_bytes()).hexdigest())
        self.assertEqual(self.source.read_bytes(),self.original); self.assertEqual(self.doc,self.original_doc)
        self.assertEqual(json.loads((out/'timeline.json').read_text('utf-8')),self.doc)
        self.assertEqual(report['source_fingerprints'],self.fingerprints)

    def test_png_exact_inventory_dimensions_decoded_changes_progress(self):
        from PIL import Image,ImageChops
        events=[]; out=self.root/'sequence'
        report=animation.export_animation(self.source,out,self.doc,render={'width':64,'height':48,'dpi':72,'ray':True},progress=events.append)
        names=[f'frames/frame_{i:06d}.png' for i in range(1,5)]
        self.assertEqual(report['artifacts']['frames'],names)
        self.assertEqual(sorted(p.name for p in (out/'frames').iterdir()),[Path(n).name for n in names])
        with Image.open(out/names[0]) as a, Image.open(out/names[-1]) as b:
            self.assertEqual(a.size,(64,48)); self.assertEqual(b.size,(64,48))
            self.assertIsNotNone(ImageChops.difference(a.convert('RGB'),b.convert('RGB')).getbbox())
        self.assertEqual([e['completed'] for e in events],sorted(e['completed'] for e in events))
        self.assertEqual(events[-1]['completed'],events[-1]['total'])
        self.assertNotIn('tools.json',report['artifact_hashes'])
        self.assertEqual(report['source_fingerprints'],self.fingerprints)
        self.assertEqual(self.source.read_bytes(),self.original); self.assertEqual(self.doc,self.original_doc)

    def test_cancellation_between_frames_and_before_commit_publishes_nothing(self):
        from molscene.bundles import CancelledError
        for target in ('render','publish'):
            flag=[False]; out=self.root/target
            def progress(event):
                if event['stage']==target: flag[0]=True
            with self.assertRaises(CancelledError):
                animation.export_animation(self.source,out,self.doc,render={'width':32,'height':32,'ray':True},
                    progress=progress,cancelled=lambda:flag[0])
            self.assertFalse(out.exists())

    def test_mp4_preconditions_and_invalid_render_fail_without_output(self):
        for options in ({'mp4':True,'render':{'width':33,'height':32}},
                        {'mp4':True,'render':{'width':32,'height':32}},
                        {'render':{'width':True}}, {'render':{'width':4097}},
                        {'render':{'bogus':1}}, {'render':{'ray':1}}, {'render':None}):
            out=self.root/'invalid'
            with self.assertRaises(ValueError): animation.export_animation(self.source,out,self.doc,**options)
            self.assertFalse(out.exists())

    def test_relocated_timeline_replay_without_molscene(self):
        import pymol2
        from molscene.timeline import sample_timeline
        expected={frame:sample_timeline(self.doc,frame) for frame in range(1,5)}
        out=self.root/'project'; animation.save_timeline_project(self.source,self.doc,out)
        moved=self.root/"迁移 ; quotes ' [bundle]"; shutil.move(str(out),str(moved))
        original_import=builtins.__import__
        def guarded(name,*args,**kwargs):
            if name=='molscene' or name.startswith('molscene.'): raise ImportError('MolScene deliberately unavailable')
            return original_import(name,*args,**kwargs)
        with pymol2.PyMOL() as pm:
            c=pm.cmd; old_cwd=Path.cwd(); transcript=io.StringIO()
            try:
                os.chdir(moved)
                with mock.patch('builtins.__import__',side_effect=guarded), redirect_stdout(transcript), redirect_stderr(transcript):
                    c.do('@replay.pml',echo=0)
                    for frame,color,enabled,state in [(4,'red',False,1),(1,'blue',True,1),(3,'red',False,2),(2,'blue',True,1)]:
                        c.do('/molscene_seek('+str(frame)+')',echo=0)
                        colors=[]; c.iterate('mol','colors.append(color)',space={'colors':colors})
                        self.assertEqual(colors[0],c.get_color_index(color))
                        self.assertEqual('other' in c.get_names('objects',enabled_only=1),enabled)
                        self.assertEqual(c.get_state(),state)
                        for got,want in zip(c.get_view(),expected[frame]['view']): self.assertAlmostEqual(got,want,places=5)
                    c.do('/molscene_play()',echo=0)
                    self.assertEqual(c.get_state(),1)
            finally:
                os.chdir(old_cwd)
            # PyMOL echoes Python source, including "except OverflowError:".
            # Match emitted diagnostic lines, not legitimate source text.
            self.assertNotRegex(transcript.getvalue(),r'(?m)^\s*(?:Traceback \(most recent call last\):|\w*Error:)')


@unittest.skipUnless(importlib.util.find_spec('pymol2'),'PyMOL unavailable')
class AnimationEncoderTests(unittest.TestCase):
    setUp=AnimationReplayTests.setUp

    def fake_config(self,mode):
        script=self.root/'encoder boundary.py'
        script.write_text('''import sys
from pathlib import Path
mode, name = sys.argv[1:3]
if '-version' in sys.argv:
    print(name + ' version 1.0'); sys.exit(0)
if '-encoders' in sys.argv:
    print(' V..... ' + ('other' if mode == 'missing' else 'libx264'))
    sys.exit(0)
if mode == 'fail':
    print('deliberate encode failure', file=sys.stderr); sys.exit(9)
Path(sys.argv[-1]).write_bytes(b'')
''',encoding='utf-8')
        return dict(schema_version=1,tools={name:dict(argv=[str(Path(sys.executable).resolve()),'-B',str(script),mode,name],
            architecture='x64' if struct.calcsize('P')==8 else 'x86',path_dirs=[str(Path(sys.executable).parent)],
            runtime_files=[],timeout_seconds=10,memory_limit_mib=512) for name in ('ffmpeg','ffprobe')})

    def test_missing_libx264_fails_before_render(self):
        events=[]; out=self.root/'missing'
        with self.assertRaisesRegex(ValueError,'libx264'):
            animation.export_animation(self.source,out,self.doc,render={'width':32,'height':32},mp4=True,
                                       tools=self.fake_config('missing'),progress=events.append)
        self.assertFalse(out.exists()); self.assertFalse(any(e['stage']=='render' for e in events))

    def test_zero_byte_and_failing_video_never_publish_and_retain_logs(self):
        for mode in ('zero','fail'):
            out=self.root/mode
            with self.assertRaises(RuntimeError):
                animation.export_animation(self.source,out,self.doc,render={'width':32,'height':32},mp4=True,
                                           tools=self.fake_config(mode))
            self.assertFalse(out.exists())
        failures=list(self.root.glob('.molscene-failure-*/failure.json'))
        self.assertEqual(len(failures),2)
        self.assertTrue(all('ffmpeg.stderr' in json.loads(p.read_text())['logs'] for p in failures))

    def test_cancellation_at_encode_boundary_never_publishes(self):
        from molscene.bundles import CancelledError
        flag=[False]
        def progress(event):
            if event['stage']=='encode': flag[0]=True
        out=self.root/'cancel'
        with self.assertRaises(CancelledError):
            animation.export_animation(self.source,out,self.doc,render={'width':32,'height':32},mp4=True,
                tools=self.fake_config('zero'),progress=progress,cancelled=lambda:flag[0])
        self.assertFalse(out.exists())

    def test_real_h264_frame_count_and_duration(self):
        from molscene.external_tools import load_tools
        config_path=os.environ.get('MOLSCENE_TOOLS_CONFIG')
        if not config_path:
            if os.environ.get('MOLSCENE_REQUIRE_ADVANCED')=='1': self.fail('MOLSCENE_TOOLS_CONFIG required')
            self.skipTest('Configure actual FFmpeg and ffprobe for real codec gate')
        config=load_tools(Path(config_path)); config['tools']={k:v for k,v in config['tools'].items() if k in ('ffmpeg','ffprobe')}
        out=self.root/'real'; events=[]
        report=animation.export_animation(self.source,out,self.doc,render={'width':64,'height':48,'ray':True},
            mp4=True,tools=config,progress=events.append)
        video=report['analysis']['video']
        self.assertEqual(video['codec_name'],'h264'); self.assertEqual(video['pix_fmt'],'yuv420p')
        self.assertEqual((video['width'],video['height']),(64,48)); self.assertEqual(video['decoded_frames'],4)
        self.assertAlmostEqual(video['duration_seconds'],4/24,delta=max(1e-5,1/24))
        self.assertGreater((out/'animation.mp4').stat().st_size,0)
        provenance=json.loads((out/'tools.json').read_text('utf-8'))
        self.assertEqual(provenance['tools']['ffmpeg']['returncode'],0)
        self.assertEqual(provenance['tools']['ffprobe']['returncode'],0)
        self.assertEqual([e['completed'] for e in events],sorted(e['completed'] for e in events))
        expected={'source.pse','timeline.json','replay.pml','report.json','manifest.json','animation.mp4','tools.json',
                  'logs/ffmpeg.stdout.log','logs/ffmpeg.stderr.log','logs/ffprobe.stdout.log','logs/ffprobe.stderr.log',
                  *(f'frames/frame_{f:06d}.png' for f in range(1,5))}
        self.assertEqual({p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file()},expected)
        self.assertEqual(self.source.read_bytes(),self.original); self.assertEqual(self.doc,self.original_doc)


if __name__=='__main__': unittest.main()
