"""Native image protocol, decoded output and owned renderer acceptance."""
import copy
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zlib

try:
    from molscene import native_render as native
except ImportError:
    native = None


def png_bytes(width=16, height=16):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB',width,height,8,6,0,0,0))
            + chunk(b'IDAT',zlib.compress((b'\0'+bytes([20,40,60,255])*width)*height)) + chunk(b'IEND',b''))


class NativeRenderProtocolTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(native, 'supervised native renderer implementation required')
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve(); self.source=self.root/'source.pse'; self.source.write_bytes(b'input')
        self.spec=dict(width=16,height=16,dpi=72,ray=True)

    def request(self):
        return native.build_request(self.source,[self.root/'frame.png'],self.spec,work_dir=self.root)

    def test_request_bounds_paths_and_independent_copy(self):
        request=self.request(); self.spec['width']=99
        self.assertEqual(request['render']['width'],16)
        self.assertEqual(native.validate_request(request),request)
        for key,value in [('width',True),('height',4097),('dpi',0),('ray',1)]:
            bad=copy.deepcopy(request); bad['render'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError): native.validate_request(bad)
        for mutate in (lambda r:r.update(extra=1),lambda r:r['outputs'].append(r['outputs'][0]),
                       lambda r:r.update(source_sha256='x'),lambda r:r.update(request_id='bad'),
                       lambda r:r['outputs'].__setitem__(0,str(self.source))):
            bad=copy.deepcopy(request); mutate(bad)
            with self.assertRaises(ValueError): native.validate_request(bad)

    def test_backend_considers_disabled_volume_when_timeline_can_enable_it(self):
        class Scene:
            def get_names(self,kind,enabled_only=0): return ['mol'] if enabled_only else ['mol','vol']
            def get_type(self,name): return 'object:volume' if name=='vol' else 'object:molecule'
        self.assertFalse(native.requires_native(Scene(),True))
        self.assertTrue(native.requires_native(Scene(),False))
        self.assertTrue(native.requires_native(Scene(),True,timeline={'scenes':'may enable volume'}))

    def test_png_rejects_partial_crc_and_trailing_data(self):
        target=self.root/'frame.png'; good=png_bytes()
        target.write_bytes(good); self.assertEqual(native.validate_png(target,16,16)['width'],16)
        for value in (good[:24],good[:-5],good+b'extra',good[:-20]+b'bad'+good[-17:]):
            target.write_bytes(value)
            with self.assertRaises(ValueError): native.validate_png(target,16,16)
        target.write_bytes(good)
        with self.assertRaises(ValueError): native.validate_png(target,17,16)

    def test_json_rejects_duplicates_nonfinite_and_oversized_data(self):
        path=self.root/'data.json'
        for value in ('{"a":1,"a":2}','{"a":NaN}','{"a":Infinity}','{"a":1e400}', ' '*1025):
            path.write_text(value)
            with self.assertRaises(ValueError): native.read_json(path,1024)

    @unittest.skipUnless(os.name=='nt','Windows readers can temporarily deny atomic replacement')
    def test_progress_update_survives_a_concurrent_windows_reader(self):
        path=self.root/'progress.json'; native.write_json(path,{'completed':1})
        reader=path.open('rb'); errors=[]
        def write():
            try: native.write_json(path,{'completed':2})
            except Exception as error: errors.append(error)
        thread=threading.Thread(target=write); thread.start()
        time.sleep(.05); reader.close(); thread.join(2)
        self.assertFalse(thread.is_alive()); self.assertEqual(errors,[])
        self.assertEqual(native.read_json(path),{'completed':2})

    def test_parent_rejects_missing_partial_or_mismatched_result(self):
        request=self.request(); path=self.root/'result.json'
        with self.assertRaises((ValueError,OSError)): native.validate_result(request,path)
        for value in ({},dict(request_id=request['request_id'],passed=True),{'passed':False}):
            path.write_text(json.dumps(value))
            with self.assertRaises(ValueError): native.validate_result(request,path)

    def result(self,request):
        image=Path(request['outputs'][0]); image.write_bytes(png_bytes())
        view=[1,0,0,0,1,0,0,0,1,0,0,-20,0,0,0,10,30,0]
        row=dict(frame=1,path=str(image),width=16,height=16,sha256=hashlib.sha256(image.read_bytes()).hexdigest(),
            bytes=image.stat().st_size,pixels=dict(count=256,rgba_min=[20,40,60,255],rgba_max=[20,40,60,255],
            nonblack_rgb_gt3=256,opaque_pixels=256),view_before=view,view_after=view)
        return dict(schema_version=1,request_id=request['request_id'],source_sha256=request['source_sha256'],passed=True,
            native_pid=123,parent_pid=124,renderer=['test vendor','test renderer','test version'],pymol_version='test',
            qt_binding='test',gl_valid=True,close_requested=True,close_event_seen=True,initial_view=view,frames=[row])

    def test_parent_accepts_flat_valid_image_and_rejects_corrupt_identity_or_pixels(self):
        request=self.request(); result=self.result(request); target=self.root/'result.json'
        native.write_json(target,result); self.assertEqual(native.validate_result(request,target),result)
        for mutate in (lambda r:r.update(request_id='a'*32),lambda r:r.update(close_event_seen=False),lambda r:r.update(schema_version=True),
                       lambda r:r['frames'][0].update(frame=2),lambda r:r['frames'][0].update(sha256='0'*64),
                       lambda r:r['frames'][0].update(path=str(self.root/'other.png')),
                       lambda r:r['frames'][0]['pixels'].update(count=255),lambda r:r.update(frames=[]),
                       lambda r:r['frames'][0].update(view_after=[1,0,0,0,1,0,0,0,1,0,0,-30,0,0,0,10,30,0]),
                       lambda r:r['frames'][0].update(view_before=[1,0,0,0,1,0,0,0,1,0,0,-30,0,0,0,10,30,0],
                                                        view_after=[1,0,0,0,1,0,0,0,1,0,0,-30,0,0,0,10,30,0])):
            bad=copy.deepcopy(result); mutate(bad); native.write_json(target,bad)
            with self.assertRaises(ValueError): native.validate_result(request,target)
        native.write_json(target,result); Path(request['outputs'][0]).write_bytes(png_bytes()[:-12])
        with self.assertRaises(ValueError): native.validate_result(request,target)

    def test_nonzero_managed_exit_rejects_even_valid_image_result(self):
        from molscene.external_tools import ExternalToolError
        def unsuccessful(*args,**kwargs):
            request=native.read_json(self.root/'request.json')
            native.write_json(self.root/'result.json',self.result(request))
            return dict(returncode=2)
        with patch('molscene.external_tools._managed',side_effect=unsuccessful),self.assertRaises(ExternalToolError):
            native.render_native(self.source,[self.root/'frame.png'],self.spec,work_dir=self.root)

    def test_process_metadata_distinguishes_renderer_parent_and_guardian(self):
        request=self.request(); result=self.result(request); target=self.root/'result.json'
        for parent,guardian in ((124,124),(124,125)):
            with self.subTest(parent=parent,guardian=guardian):
                result['parent_pid']=parent; native.write_json(target,result)
                native.validate_result(request,target)
                record=dict(result=result,process=dict(guardian_pid=guardian,returncode=0),backend='native_opengl',
                            requested_ray=True,width=16,height=16,renderer=result['renderer'])
                metadata=native.report_metadata(record,self.root)
                self.assertEqual(metadata['process'],dict(native_pid=123,parent_pid=parent,guardian_pid=guardian,
                                                         returncode=0,close_event_seen=True))
        legacy=copy.deepcopy(result); legacy['launcher_pid']=legacy.pop('parent_pid'); native.write_json(target,legacy)
        with self.assertRaises(ValueError): native.validate_result(request,target)

    def test_native_evidence_directory_prefers_override_and_creates_standard_fallback(self):
        self.assertTrue(callable(globals().get('_evidence_directory')),'native evidence directory helper required')
        explicit=self.root/'explicit'; standard=self.root/'standard'/'nested'
        with patch.dict(os.environ,{'MOLSCENE_NATIVE_EVIDENCE':'','MOLSCENE_TEST_ARTIFACTS':str(standard)}):
            self.assertEqual(_evidence_directory(),standard); self.assertTrue(standard.is_dir())
        with patch.dict(os.environ,{'MOLSCENE_NATIVE_EVIDENCE':str(explicit),'MOLSCENE_TEST_ARTIFACTS':str(standard)}):
            self.assertEqual(_evidence_directory(),explicit); self.assertTrue(explicit.is_dir())
        with patch.dict(os.environ,{'MOLSCENE_NATIVE_EVIDENCE':'','MOLSCENE_TEST_ARTIFACTS':''}):
            self.assertIsNone(_evidence_directory())


@unittest.skipUnless(importlib.util.find_spec('pymol2') or os.environ.get('MOLSCENE_REQUIRE_ADVANCED')=='1',
                     'requires headless PyMOL')
class FigureRenderRoutingTests(unittest.TestCase):
    def setUp(self):
        import pymol2
        from molscene.snapshot import scene_fingerprint
        self.tmp=tempfile.TemporaryDirectory(prefix='figure-route-'); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.source=self.root/'source.pse'
        with pymol2.PyMOL() as pm:
            c=pm.cmd; c.pseudoatom('anchor',pos=[0,0,0],state=1); c.pseudoatom('anchor',pos=[1,2,3],state=2)
            c.alter('anchor','vdw=2.7'); c.hide('everything')
            c.load_raw('object 1 class gridpositions counts 2 2 2\norigin -4 -4 -4\ndelta 8 0 0\ndelta 0 8 0\ndelta 0 0 8\n'
                       'object 2 class gridconnections counts 2 2 2\nobject 3 class array type double rank 0 items 8 data follows\n'
                       '0 1 0 1 0 1 0 1\n','dx','map')
            c.volume('vol','map',[0,0,0,1,0,1,1,0,0,.8]); c.zoom('map'); c.turn('y',37); c.frame(2)
            self.view=list(c.get_view()); self.ramp=c.volume_color('vol',state=1)
            self.radii=[a.vdw for a in c.get_model('anchor',state=2).atom]
            self.fingerprint=scene_fingerprint(c,2); c.save(str(self.source))
        self.original=self.source.read_bytes()

    def fake_native(self,session_path,outputs,render,**kwargs):
        import pymol2
        with pymol2.PyMOL() as pm:
            c=pm.cmd; c.load(str(session_path)); self.assertEqual(c.get_state(),2)
            self.assertEqual(list(c.get_view()),self.view)
            self.assertEqual(c.volume_color('vol',state=1),self.ramp)
            self.assertEqual([a.vdw for a in c.get_model('anchor',state=2).atom],self.radii)
            self.assertEqual(c.get_setting_int('ray_opaque_background'),0)
        path=Path(outputs[0]); path.write_bytes(png_bytes(render['width'],render['height']))
        count=render['width']*render['height']
        row=dict(frame=1,path=str(path),**native.validate_png(path,render['width'],render['height']),
                 pixels=dict(count=count,rgba_min=[20,40,60,255],rgba_max=[20,40,60,255],nonblack_rgb_gt3=count,opaque_pixels=count),
                 view_before=self.view,view_after=self.view)
        return dict(backend='native_opengl',requested_ray=render['ray'],effective_ray=False,width=render['width'],height=render['height'],
            renderer=['test vendor','test renderer','test version'],warnings=[native.WARNING],
            result=dict(schema_version=1,request_id='0'*32,source_sha256=hashlib.sha256(Path(session_path).read_bytes()).hexdigest(),
                        passed=True,frames=[row],native_pid=123,parent_pid=124,renderer=['test vendor','test renderer','test version'],
                        pymol_version='test',qt_binding='test',gl_valid=True,close_requested=True,close_event_seen=True,initial_view=self.view),
            process=dict(guardian_pid=124,returncode=0))

    def test_figure_routes_native_and_preserves_final_session_replay_and_analysis(self):
        from molscene.engine import export_session
        import pymol2
        for ray in (True,False):
            output=self.root/str(ray)
            with patch('molscene.native_render.render_native',side_effect=self.fake_native):
                report=export_session(self.source,output,width=16,height=16,dpi=72,ray=ray,background='transparent',
                    analysis_records=[dict(state=2,source_fingerprint=self.fingerprint)])
            rendering=report['analysis']['rendering']
            self.assertEqual(rendering['backend'],'native_opengl'); self.assertEqual(rendering['requested_ray'],ray)
            self.assertIs(rendering['effective_ray'],False); self.assertNotIn('passed',report)
            self.assertEqual(rendering['source_sha256'],hashlib.sha256((output/'scene.pse').read_bytes()).hexdigest())
            self.assertEqual(rendering['session'],'scene.pse'); self.assertEqual(report['analysis_records'][0]['snapshot_status'],'current')
            self.assertEqual(self.source.read_bytes(),self.original); self.assertEqual(report['view'],self.view)
            self.assertTrue(report['warnings'])
            self.assertEqual({p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file()},
                             {'scene.png','scene.pse','scene.pml','report.json','inputs/input.pse'})
            with pymol2.PyMOL() as replay:
                old=Path.cwd()
                try: os.chdir(output); replay.cmd.do('@scene.pml',echo=0)
                finally: os.chdir(old)
                self.assertEqual(replay.cmd.get_state(),2); self.assertEqual(list(replay.cmd.get_view()),self.view)
                self.assertEqual(replay.cmd.volume_color('vol',state=1),self.ramp)

    def test_figure_native_failure_and_cancellation_retain_only_reachable_bounded_diagnostics(self):
        from molscene.engine import export_session
        from molscene.external_tools import ExternalToolError
        from molscene.bundles import CancelledError
        for kind in ('failure','cancel','missing_result'):
            output=self.root/kind; output.mkdir(); (output/'report.json').write_text('existing')
            stages=[]
            def fail(name,entry,args,cwd,prefix,*rest,**kwargs):
                request=native.read_json(Path(cwd)/'request.json'); stages.append(Path(request['source']).parent)
                Path(request['outputs'][0]).write_bytes(b'partial PNG')
                stdout=Path(str(prefix)+'.stdout.log'); stderr=Path(str(prefix)+'.stderr.log')
                stdout.write_text('started'); stderr.write_text('bounded native failure')
                if kind=='cancel': raise CancelledError('cancelled at native boundary')
                if kind=='missing_result': return dict(returncode=0)
                raise ExternalToolError('failed at native boundary',dict(returncode=9,stdout_path=str(stdout),stderr_path=str(stderr)))
            expected={'failure':ExternalToolError,'cancel':CancelledError,'missing_result':FileNotFoundError}[kind]
            with patch('molscene.external_tools._managed',side_effect=fail),self.assertRaises(expected) as raised:
                export_session(self.source,output,width=16,height=16,dpi=72,overwrite=True)
            failure=Path(raised.exception.failure_path)
            self.assertTrue((failure/'failure.json').is_file()); self.assertIn(str(failure),str(raised.exception))
            self.assertEqual((output/'report.json').read_text(),'existing'); self.assertFalse((output/'scene.png').exists())
            self.assertEqual(self.source.read_bytes(),self.original); self.assertTrue(stages)
            self.assertTrue(all(not stage.exists() for stage in stages))
            self.assertEqual({p.relative_to(failure).as_posix() for p in failure.rglob('*') if p.is_file()},
                             {'failure.json','logs/native.stdout.log','logs/native.stderr.log'})
            self.assertEqual((failure/'logs/native.stderr.log').read_text(),'bounded native failure')
            if kind=='failure':
                for stream in ('stdout','stderr'):
                    self.assertEqual(Path(raised.exception.result[stream+'_path']),failure/'logs'/('native.'+stream+'.log'))

    def test_figure_native_bounds_are_actionable_and_cpu_dimensions_remain_legacy(self):
        from molscene.engine import export_session
        import pymol2
        for width,height,dpi in ((1,16,72),(4097,16,72),(16,16,1201)):
            with self.assertRaisesRegex(ValueError,r'Native OpenGL.*16.*4096.*1200'):
                export_session(self.source,self.root/'invalid',width=width,height=height,dpi=dpi)
        hidden=self.root/'hidden.pse'
        with pymol2.PyMOL() as pm:
            pm.cmd.load(str(self.source)); pm.cmd.disable('vol'); pm.cmd.save(str(hidden))
        with patch('molscene.native_render.render_native',side_effect=AssertionError('CPU scene must not launch native')):
            report=export_session(hidden,self.root/'cpu',width=1,height=16,dpi=2400,ray=True)
        self.assertEqual(report['parameters']['width'],1); self.assertEqual(report['parameters']['dpi'],2400)


def process_alive(pid):
    if os.name=='nt':
        import ctypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong]
        kernel.OpenProcess.restype=ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_ulong]
        kernel.CloseHandle.argtypes=[ctypes.c_void_p]
        handle=kernel.OpenProcess(0x100000,False,pid)
        if not handle: return False
        try: return kernel.WaitForSingleObject(handle,0)==258
        finally: kernel.CloseHandle(handle)
    try: os.kill(pid,0)
    except ProcessLookupError: return False
    return True


def _evidence_directory():
    value=os.environ.get('MOLSCENE_NATIVE_EVIDENCE') or os.environ.get('MOLSCENE_TEST_ARTIFACTS')
    if not value: return None
    directory=Path(value); directory.mkdir(parents=True,exist_ok=True)
    return directory


@unittest.skipUnless(os.environ.get('MOLSCENE_REQUIRE_ADVANCED')=='1' or os.environ.get('MOLSCENE_NATIVE_RENDER')=='1',
                     'requires allocated native desktop focus')
class NativeRenderTests(unittest.TestCase):
    def test_native_volume_frames_and_cancel_cleanup(self):
        self.assertIsNotNone(native,'native renderer implementation required')
        self.assertTrue(importlib.util.find_spec('pymol2'),'required gate needs actual native PyMOL')
        import math
        import pymol2
        from PIL import Image, ImageChops
        from molscene.timeline import new_timeline,capture_keyframe,edit_keyframe
        from molscene.bundles import Bundle,CancelledError
        evidence=_evidence_directory()
        if evidence:
            root=Path(tempfile.mkdtemp(prefix='run-',dir=evidence))
        else:
            temp=tempfile.TemporaryDirectory(prefix='native-render-'); self.addCleanup(temp.cleanup); root=Path(temp.name)
        source=root/'s.pse'; dx=root/'g.dx'; work=root/'w'; work.mkdir()
        values=[math.exp(-((x/2)**2+(y/2.5)**2+(z/3)**2))
                for x in [i*.5-6 for i in range(25)] for y in [i*.5-6 for i in range(25)] for z in [i*.5-6 for i in range(25)]]
        dx.write_text('object 1 class gridpositions counts 25 25 25\norigin -6 -6 -6\ndelta .5 0 0\ndelta 0 .5 0\ndelta 0 0 .5\n'
                      'object 2 class gridconnections counts 25 25 25\nobject 3 class array type double rank 0 items 15625 data follows\n'
                      +'\n'.join(' '.join(format(v,'.7g') for v in values[i:i+3]) for i in range(0,len(values),3))+'\n')
        with pymol2.PyMOL() as pm:
            c=pm.cmd; c.pseudoatom('anchor'); c.hide('everything'); c.load(str(dx),'map',format='dx')
            c.volume('vol','map',[0,0,0,1,0,.15,0,1,1,.15,.4,0,1,0,.5,.8,1,.5,0,.8,1,1,0,0,1])
            c.zoom('map',buffer=0); c.bg_color('black'); view=list(c.get_view())
            doc=new_timeline(c); c.disable('vol'); c.scene('hidden','store')
            doc=edit_keyframe(doc,'add',values=capture_keyframe(c,frame=2,state=1,scene='hidden',interpolation='hold'))
            c.set_view(view); c.save(str(source)); before=source.read_bytes()
            self.assertNotIn('vol',c.get_names('objects',enabled_only=1))
            result=native.render_native(source,[root/'a.png',root/'b.png'],dict(width=128,height=128,dpi=72,ray=True),
                                        work_dir=work,timeline=doc)
            self.assertEqual(list(c.get_view()),view)
            self.assertEqual(source.read_bytes(),before)
        self.assertEqual(result['backend'],'native_opengl'); self.assertFalse(result['effective_ray'])
        data=result['result']; self.assertTrue(data['close_event_seen']); self.assertEqual(len(data['frames']),2)
        self.assertEqual(data['source_sha256'],hashlib.sha256(before).hexdigest())
        self.assertEqual(data['initial_view'],view)
        self.assertGreater(data['frames'][0]['pixels']['nonblack_rgb_gt3'],100)
        self.assertEqual(data['frames'][1]['pixels']['nonblack_rgb_gt3'],0)
        with Image.open(root/'a.png') as a,Image.open(root/'b.png') as b:
            self.assertEqual(a.size,(128,128)); self.assertIsNotNone(ImageChops.difference(a.convert('RGB'),b.convert('RGB')).getbbox())
        for pid in (data['native_pid'],data['parent_pid'],result['process']['guardian_pid']):
            self.assertFalse(process_alive(pid),f'owned successful process {pid} survived')
        cancelled=[False]; events=[]
        def progress(event):
            events.append(event)
            if event['stage']=='render': cancelled[0]=True
        long=copy.deepcopy(doc); long['frame_count']=100; long['keyframes'][-1]['frame']=100
        destination=root/'cancelled'; names=[f'f{i}.png' for i in range(100)]
        with self.assertRaises(CancelledError):
            with Bundle(destination,protected=[source],inventory=[*names,'manifest.json'],cancelled=lambda:cancelled[0]) as bundle:
                native.render_native(source,[bundle.stage/name for name in names],dict(width=128,height=128,dpi=72,ray=False),
                    work_dir=bundle.scratch,timeline=long,cancelled=lambda:cancelled[0],progress=progress,bundle=bundle)
                bundle.publish()
        self.assertFalse(destination.exists()); self.assertTrue(events)
        failures=list(root.glob('.molscene-failure-*/logs/native.result.log')); self.assertEqual(len(failures),1)
        cancelled_result=native.read_json(failures[0]); self.assertFalse(cancelled_result['passed'])
        for pid in (cancelled_result['native_pid'],cancelled_result['parent_pid']):
            self.assertFalse(process_alive(pid),f'owned cancelled process {pid} survived')
        (root/'evidence.json').write_text(json.dumps(dict(success=result,cancelled=cancelled_result,events=events),indent=2))
        # Exercise public consumers, including a volume disabled at export time
        # but enabled by the timeline baseline, and ray=False potential import.
        from molscene.animation import export_animation
        from molscene.density_export import export_density
        from molscene.electrostatics import export_potential
        reports={}
        reports['animation']=export_animation(source,root/'anim',doc,render=dict(width=64,height=48,dpi=72,ray=True))
        self.assertEqual(reports['animation']['analysis']['rendering']['backend'],'native_opengl')
        ramp=[0,0,0,1,0,.15,0,1,1,.15,.4,0,1,0,.5,.8,1,.5,0,.8,1,1,0,0,1]
        reports['density']=export_density(None,root/'den',dict(map_path=str(dx),format='dx',
            display=dict(kind='volume',volume_ramp=ramp),render=dict(width=64,height=48,dpi=72,ray=True)))
        self.assertGreater(reports['density']['analysis']['rendering']['images'][0]['pixels']['nonblack_rgb_gt3'],50)
        pqr=root/'ion.pqr'; pqr.write_text('ATOM 1 C ALA 1 0 0 0 1 1.7\n')
        reports['potential']=export_potential(None,root/'pot',dict(mode='import',pqr_path=str(pqr),dx_path=str(dx),units='kBT/e',
            render=dict(width=64,height=48,dpi=72,ray=False)))
        for name,report in reports.items():
            rendering=report['analysis']['rendering']
            self.assertEqual(rendering['backend'],'native_opengl'); self.assertIs(rendering['effective_ray'],False)
            self.assertEqual(rendering['requested_ray'],name!='potential'); self.assertTrue(report['warnings'])
            self.assertEqual((rendering['width'],rendering['height']),(64,48))
            for row in rendering['images']:
                self.assertFalse(Path(row['path']).is_absolute()); self.assertEqual(row['pixels']['count'],3072)
        # Figure must export the exact current volume view through this same
        # required native gate, preserving its legacy final-PSE replay contract.
        from molscene.engine import export_session
        from molscene.snapshot import scene_fingerprint
        figure_source=root/'figure-source.pse'
        with pymol2.PyMOL() as pm:
            c=pm.cmd; c.load(str(source)); c.enable('vol'); c.hide('everything','anchor')
            c.alter('anchor','vdw=2.7'); c.save(str(figure_source))
            figure_bytes=figure_source.read_bytes(); figure_view=list(c.get_view()); figure_state=c.get_state()
            figure_ramp=c.volume_color('vol',state=1); figure_radius=c.get_model('anchor').atom[0].vdw
            fingerprint=scene_fingerprint(c,figure_state)
            reports['figure']=export_session(figure_source,root/'fig',width=128,height=128,dpi=72,ray=True,background='black',
                analysis_records=[dict(state=figure_state,source_fingerprint=fingerprint)])
            self.assertEqual(list(c.get_view()),figure_view); self.assertEqual(c.get_state(),figure_state)
        figure=reports['figure']; rendering=figure['analysis']['rendering']
        self.assertEqual(rendering['backend'],'native_opengl'); self.assertTrue(rendering['requested_ray'])
        self.assertFalse(rendering['effective_ray']); self.assertTrue(rendering['renderer'])
        self.assertNotIn('passed',figure); self.assertEqual(figure['analysis_records'][0]['snapshot_status'],'current')
        self.assertEqual(figure_source.read_bytes(),figure_bytes); self.assertEqual(figure['view'],figure_view)
        self.assertEqual(rendering['source_sha256'],hashlib.sha256((root/'fig/scene.pse').read_bytes()).hexdigest())
        self.assertEqual(rendering['session'],'scene.pse')
        self.assertGreater(rendering['images'][0]['pixels']['nonblack_rgb_gt3'],100)
        with Image.open(root/'fig/scene.png') as image,Image.open(root/'b.png') as hidden:
            self.assertEqual(image.size,(128,128))
            self.assertIsNotNone(ImageChops.difference(image.convert('RGB'),hidden.convert('RGB')).getbbox())
        with pymol2.PyMOL() as replay:
            old=Path.cwd()
            try: os.chdir(root/'fig'); replay.cmd.do('@scene.pml',echo=0)
            finally: os.chdir(old)
            self.assertEqual(list(replay.cmd.get_view()),figure_view); self.assertEqual(replay.cmd.get_state(),figure_state)
            self.assertEqual(replay.cmd.volume_color('vol',state=1),figure_ramp)
            self.assertEqual(replay.cmd.get_model('anchor').atom[0].vdw,figure_radius)
            self.assertIn('vol',replay.cmd.get_names('objects',enabled_only=1))
        self.assertTrue(rendering['process']['close_event_seen']); self.assertEqual(rendering['process']['returncode'],0)
        for key in ('native_pid','parent_pid','guardian_pid'):
            self.assertFalse(process_alive(rendering['process'][key]),f'Figure process {key} survived')
        self.assertEqual(source.read_bytes(),before)
        (root/'consumer-reports.json').write_text(json.dumps(reports,indent=2))


if __name__=='__main__': unittest.main()
