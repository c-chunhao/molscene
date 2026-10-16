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
        self.root=Path(self.tmp.name); self.source=self.root/'source.pse'; self.source.write_bytes(b'input')
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
            native_pid=123,launcher_pid=124,renderer=['test vendor','test renderer','test version'],pymol_version='test',
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
        evidence=os.environ.get('MOLSCENE_NATIVE_EVIDENCE')
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
        for pid in (data['native_pid'],data['launcher_pid'],result['process']['guardian_pid']):
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
        for pid in (cancelled_result['native_pid'],cancelled_result['launcher_pid']):
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
        self.assertEqual(source.read_bytes(),before)
        (root/'consumer-reports.json').write_text(json.dumps(reports,indent=2))


if __name__=='__main__': unittest.main()
