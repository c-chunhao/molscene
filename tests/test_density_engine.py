"""Real headless map geometry, immutable registration and portable replay."""
import hashlib
from array import array
import importlib.util
import itertools
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_grids import dx_text, mrc_bytes
try:
    from molscene import density, density_export
except ImportError:
    density = density_export = None

HAS_PYMOL = importlib.util.find_spec('pymol2') is not None
D = [0,-1,0,11,1,0,0,-7,0,0,1,3,0,0,0,1]
IDENTITY = [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]


@unittest.skipUnless(HAS_PYMOL or os.environ.get('MOLSCENE_REQUIRE_ADVANCED')=='1', 'requires PyMOL')
class DensityEngineTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(density, 'density core is required')
        self.assertIsNotNone(density_export, 'density export is required')
        self.assertTrue(HAS_PYMOL, 'required advanced gate needs an actual PyMOL backend')
        import pymol2
        self.tmp = tempfile.TemporaryDirectory(prefix='density-v04-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pm = pymol2.PyMOL(); self.pm.start()
        self.addCleanup(self.pm.stop)
        self.c = self.pm.cmd
        self.path = self.root/'source.mrc'

    def load_origin(self, state=1):
        self.path.write_bytes(mrc_bytes(origin=(20,30,40)))
        return density.load_density(self.c, self.path, format='mrc', interpretation='origin', name='source', state=state)

    def points(self, name, state=1):
        path = self.root/(name+'.txt')
        self.c.dump(str(path), name, state=state)
        return [[float(v) for v in line.split()] for line in path.read_text().splitlines() if line.strip()]

    def assertVector(self, actual, expected, delta=1e-4):
        self.assertEqual(len(actual), len(expected))
        for a,b in zip(actual, expected): self.assertAlmostEqual(a,b,delta=delta)

    def test_all_six_axes_raw_values_and_oblique_physical_points(self):
        for index, axes in enumerate(itertools.permutations((1,2,3))):
            self.path.write_bytes(mrc_bytes(axes))
            record = density.load_density(self.c,self.path,format='ccp4',name='map'+str(index))
            self.assertEqual(self.c.get_setting_int('normalize_ccp4_maps'),1)
            field = self.c.get_volume_field(record['name'],state=1,copy=1)
            observed = self.points(record['name'])
            self.assertEqual(len(observed),60)
            for row,(i,j,k) in zip(observed,itertools.product(range(4),range(3),range(5))):
                self.assertEqual(float(field[i,j,k]),100*i+10*j+k)
                self.assertVector(row,[2*(i+1)+3*math.cos(math.radians(70))*(j+2),
                    3*math.sin(math.radians(70))*(j+2),4*(k+3),100*i+10*j+k])

    def test_load_failure_restores_normalizer_and_only_owned_names(self):
        self.path.write_bytes(mrc_bytes())
        self.c.pseudoatom('sentinel')
        original = self.c.load
        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('injected after native load')
        self.c.set('normalize_ccp4_maps', 1)
        with patch.object(self.c, 'load', side_effect=fail), self.assertRaises(RuntimeError):
            density.load_density(self.c,self.path,format='ccp4',name='failed')
        self.assertEqual(self.c.get_names('objects'), ['sentinel'])
        self.assertEqual(self.c.get_setting_int('normalize_ccp4_maps'),1)

    def wide_dx(self):
        from molscene.grids import Grid, write_dx
        values=[4.9326540340094691e-5,2.3141592653589793e-16,1.,2.,3.,4.,5.,6.]
        path=self.root/'full precision.dx'
        write_dx(path,Grid((2,2,2),(11.,-7.,3.),((2.,0.,0.),(0.,3.,0.),(0.,0.,4.)),
                          array('d',values),'unknown',{}))
        expected=[struct.unpack('<f',struct.pack('<f',v))[0] for v in values]
        return path,expected

    def test_wide_dx_writer_values_load_exactly_without_changing_source(self):
        path,expected=self.wide_dx(); original=path.read_bytes()
        record=density.load_density(self.c,path,format='dx',name='wide',state=2)
        field=self.c.get_volume_field('wide',state=2,copy=1).ravel().tolist()
        self.assertEqual(field,expected)
        self.assertGreater(field[0],0); self.assertGreater(field[1],0)
        self.assertEqual(record['value_sha256'],hashlib.sha256(struct.pack('<8d',*expected)).hexdigest())
        self.assertEqual(record['source_sha256'],hashlib.sha256(original).hexdigest())
        self.assertEqual(path.read_bytes(),original)
        self.assertEqual(self.c.get_setting_int('normalize_ccp4_maps'),1)
        for row,(i,j,k) in zip(self.points('wide',2),itertools.product(range(2),repeat=3)):
            self.assertVector(row[:3],[11+2*i,-7+3*j,3+4*k])

    def test_wide_dx_export_relocated_standalone_replay_preserves_values(self):
        path,expected=self.wide_dx(); original=path.read_bytes()
        out=self.root/'bundle'
        report=density_export.export_density(None,out,dict(map_path=str(path),format='dx',
            registration=dict(kind='matrix',matrix=D,copy_name='registered'),
            display=dict(kind='surface',level=2.5),render=dict(width=32,height=32)))
        relocated=self.root/"relocated 中文';$()"; shutil.move(str(out),str(relocated))
        code='''import builtins,json,os,pathlib,pymol2,sys
original=builtins.__import__
def isolated(name,*args,**kwargs):
 if name=='molscene' or name.startswith('molscene.'): raise ImportError('MolScene deliberately unavailable')
 return original(name,*args,**kwargs)
builtins.__import__=isolated
root=pathlib.Path(sys.argv[1]); os.chdir(root)
with pymol2.PyMOL() as pm:
 c=pm.cmd; c.do('@replay.pml'); c.ray(32,32)
 c.dump('contour.txt','registered_surface',state=1)
 observed={'raw':c.get_volume_field('registered',state=1,copy=1).ravel().tolist(),
           'matrix':list(c.get_object_matrix('registered',state=1)),
           'normalizer':c.get_setting_int('normalize_ccp4_maps')}
 (root/'observed.json').write_text(json.dumps(observed))
'''
        result=subprocess.run([sys.executable,'-B','-c',code,str(relocated)],cwd=self.root,
                              capture_output=True,text=True,timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        observed=json.loads((relocated/'observed.json').read_text())
        self.assertEqual(observed['raw'],expected)
        self.assertGreater(observed['raw'][1],0)
        self.assertVector(observed['matrix'],D)
        self.assertEqual(observed['normalizer'],1)
        self.assertTrue((relocated/'contour.txt').stat().st_size)
        self.assertEqual(path.read_bytes(),original)
        self.assertEqual((relocated/'inputs/density.dx').read_bytes(),original)
        self.assertEqual(report['analysis']['density']['source']['value_sha256'],
                         hashlib.sha256(struct.pack('<8d',*expected)).hexdigest())
        manifest=json.loads((relocated/'manifest.json').read_text())
        for name,meta in manifest['files'].items():
            self.assertEqual(hashlib.sha256((relocated/name).read_bytes()).hexdigest(),meta['sha256'])

    def test_registered_origin_map_world_composition(self):
        record = self.load_origin()
        self.c.pseudoatom('molecule',pos=[4,5,6])
        original_field = self.c.get_volume_field('source',copy=1).copy()
        before = list(self.c.get_object_matrix('source',state=1))
        self.c.show('cell','source')
        representations = self.c.get_session('source')['names'][0][5][0][3]
        result = density.register_density(self.c,record,dict(kind='matrix',matrix=D,copy_name='registered'))
        want = [0,-1,0,-19,1,0,0,13,0,0,1,43,0,0,0,1]
        self.assertVector(result['after_matrix'],want)
        self.assertVector(result['record']['load_matrix'],before)
        self.assertEqual(self.c.get_object_matrix('source'),tuple(before))
        self.assertTrue((self.c.get_volume_field('registered',copy=1)==original_field).all())
        self.assertEqual(self.c.get_session('source')['names'][0][5][0][3],representations)
        self.assertVector(self.c.get_model('molecule').atom[0].coord,[4,5,6])
        # Dump is intrinsic coordinates; apply the independently expected native matrix.
        for row,(i,j,k) in zip(self.points('registered'),itertools.product(range(4),range(3),range(5))):
            world = [-row[1]-19,row[0]+13,row[2]+43]
            expected = [-19-3*math.sin(math.radians(70))*j,13+2*i+3*math.cos(math.radians(70))*j,43+4*k]
            self.assertVector(world,expected)
        self.assertVector(result['bounds']['minimum'],[-19-6*math.sin(math.radians(70)),13,43])
        self.assertVector(result['bounds']['maximum'],[-19,19+6*math.cos(math.radians(70)),59])

    def test_explicit_state_owned_displays_and_unverified_inventory(self):
        record = self.load_origin(state=2)
        result = density.display_density(self.c,record,{'kind':'surface','level':100,'opacity':.4,'visible':False})
        name = result['name']
        self.assertEqual(self.c.count_states(name),1)
        self.assertNotIn(name,self.c.get_names('objects',enabled_only=1))
        density.display_density(self.c,record,{'kind':'surface','level':200,'visible':True})
        self.assertIn(name,self.c.get_names('objects',enabled_only=1))
        self.assertAlmostEqual(self.c.isolevel(name,state=1,query=1),200)
        self.c.copy('foreign','source')
        inventory = density.map_inventory(self.c,[record])
        foreign = next(v for v in inventory if v['name']=='foreign')
        self.assertFalse(foreign['verified'])
        with self.assertRaises(ValueError):
            density.register_density(self.c,foreign,dict(kind='matrix',matrix=D,copy_name='invalid'))
        density.display_density(self.c,foreign,{'kind':'mesh','level':50})
        for state in (True,0,-1,3):
            bad = dict(record,state=state)
            with self.assertRaises(ValueError): density.display_density(self.c,bad,{})

    def test_registration_preflight_rejects_stale_ttt_movie_collision(self):
        record = self.load_origin()
        action = dict(kind='matrix',matrix=D,copy_name='registered')
        initial = set(self.c.get_names('objects'))
        self.path.write_bytes(self.path.read_bytes()+b'changed')
        with self.assertRaises(ValueError): density.register_density(self.c,record,action)
        self.path.write_bytes(mrc_bytes(origin=(20,30,40)))
        self.c.set_object_ttt('source',[1,0,0,4,0,1,0,0,0,0,1,0,0,0,0,1])
        with self.assertRaises(ValueError): density.register_density(self.c,record,action)
        self.c.reset('source')
        self.c.mset('1 x3')
        with self.assertRaises(ValueError): density.register_density(self.c,record,action)
        self.c.mclear(); self.c.mset()
        with self.assertRaises(ValueError): density.register_density(self.c,record,dict(action,copy_name='source'))
        self.assertEqual(set(self.c.get_names('objects')),initial)

    def models(self):
        coords = [(0,0,0),(1,2,0),(3,1,2),(4,4,1),(6,3,4),(7,6,2)]
        for name, points in [('mobile',coords),('target',[(-y+11,x-7,z+3) for x,y,z in coords])]:
            pdb = '\n'.join('ATOM  %5d  CA  ALA A%4d    %8.3f%8.3f%8.3f  1.00 20.00           C' % (i+1,i+1,*p) for i,p in enumerate(points))+'\nEND\n'
            self.c.read_pdbstr(pdb,name,state=2)
        return coords

    def test_aligned_copy_verifies_every_atom_and_preserves_originals(self):
        record = self.load_origin(); coords = self.models()
        request = dict(kind='align',mobile='mobile',target='target',mobile_state=2,target_state=2,
            selection='all',algorithm='align',cutoff=2.,cycles=0,model_copy_name='aligned',copy_name='registered')
        result = density.register_density(self.c,record,request)
        self.assertVector(result['world_transform'],D)
        self.assertLessEqual(result['alignment']['max_atom_error_angstrom'],1e-4)
        for atom,(x,y,z) in zip(self.c.get_model('aligned',state=1).atom,coords):
            self.assertVector(atom.coord,[-y+11,x-7,z+3])
        for atom,point in zip(self.c.get_model('mobile',state=2).atom,coords): self.assertVector(atom.coord,point)
        self.assertEqual(self.c.count_states('aligned'),1)

    def test_failed_registration_cleans_owned_copies_only(self):
        record = self.load_origin(); self.models()
        request = dict(kind='align',mobile='mobile',target='target',mobile_state=2,target_state=2,
            selection='all',algorithm='align',cutoff=2.,cycles=0,model_copy_name='aligned',copy_name='registered')
        initial = set(self.c.get_names('objects'))
        with patch.object(self.c,'transform_object',side_effect=RuntimeError('injected transform failure')), self.assertRaises(RuntimeError):
            density.register_density(self.c,record,request)
        self.assertEqual(set(self.c.get_names('objects')),initial)

    def test_gaussian_positive_negative_contours_converge(self):
        errors=[]
        for n in (13,25):
            spacing=6/(n-1)
            for sign in (1,-1):
                p=self.root/f'gaussian{n}_{sign}.dx'
                values=[sign*math.exp(-((i*spacing-3)**2/2+(j*spacing-3)**2/4+(k*spacing-3)**2/4.5)) for i,j,k in itertools.product(range(n),repeat=3)]
                p.write_text(f'object 1 class gridpositions counts {n} {n} {n}\norigin -3 -3 -3\n'
                    f'delta {spacing} 0 0\ndelta 0 {spacing} 0\ndelta 0 0 {spacing}\n'
                    f'object 2 class gridconnections counts {n} {n} {n}\nobject 3 class array type double rank 0 items {n**3} data follows\n'+' '.join(map(str,values))+'\n')
                rec=density.load_density(self.c,p,format='dx',name=f'gauss{n}_{abs(sign)}'+('neg' if sign<0 else 'pos'))
                display=density.display_density(self.c,rec,dict(kind='surface',level=sign*.5))
                self.c.ray(32,32)
                vertices=self.points(display['name']); self.assertTrue(vertices)
                error=max(abs(math.exp(-(v[0]**2/2+v[1]**2/4+v[2]**2/4.5))-.5) for v in vertices)
                self.assertLess(error,.04)
                errors.append(error)
        self.assertLess(max(errors[2:]),max(errors[:2])*.5)

    def test_registered_density_relocated_replay(self):
        self.path.write_bytes(mrc_bytes(origin=(20,30,40))); self.models()
        session=self.root/'input.pse'; self.c.set('normalize_ccp4_maps',1); self.c.save(str(session))
        out=self.root/'bundle'
        request=dict(map_path=str(self.path),format='mrc',interpretation='origin',
            display=dict(kind='surface',level=100,color='cyan',opacity=.7),
            registration=dict(kind='matrix',matrix=D,copy_name='registered'),render=dict(width=64,height=48,dpi=72,ray=True))
        report=density_export.export_density(session,out,request)
        relocated=self.root/"relocated 中文';$()"; shutil.move(str(out),str(relocated))
        code = '''import builtins,json,os,pathlib,pymol2,sys
original=builtins.__import__
def isolated(name,*args,**kwargs):
 if name=='molscene' or name.startswith('molscene.'): raise ImportError('MolScene deliberately unavailable')
 return original(name,*args,**kwargs)
builtins.__import__=isolated
root=pathlib.Path(sys.argv[1]); os.chdir(root)
with pymol2.PyMOL() as p:
 c=p.cmd; c.do('@replay.pml'); c.ray(32,32)
 c.dump('geometry.txt','registered_surface',state=1)
 result={'matrix':list(c.get_object_matrix('registered',state=1)), 'raw':c.get_volume_field('registered',state=1,copy=1).ravel().tolist(),'normalizer':c.get_setting_int('normalize_ccp4_maps'),'atoms':[a.coord for a in c.get_model('mobile',state=2).atom]}
 (root/'observed.json').write_text(json.dumps(result))
'''
        result=subprocess.run([sys.executable,'-B','-c',code,str(relocated)],cwd=self.root,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        observed=json.loads((relocated/'observed.json').read_text())
        self.assertVector(observed['matrix'],[0,-1,0,-19,1,0,0,13,0,0,1,43,0,0,0,1])
        self.assertEqual(observed['raw'],[100*i+10*j+k for i,j,k in itertools.product(range(4),range(3),range(5))])
        self.assertEqual(observed['normalizer'],1)
        self.assertVector(observed['atoms'][1],[1,2,0])
        self.assertTrue((relocated/'geometry.txt').is_file(),result.stdout+result.stderr)
        self.assertTrue((relocated/'geometry.txt').stat().st_size)
        manifest=json.loads((relocated/'manifest.json').read_text())
        self.assertEqual(set(manifest['files']),{'source.pse','inputs/density.mrc','density.json','scene.pse','scene.png','replay.pml','report.json'})
        self.assertEqual(set(report['artifact_hashes']),set(manifest['files'])-{'report.json'})
        for name,meta in manifest['files'].items(): self.assertEqual(hashlib.sha256((relocated/name).read_bytes()).hexdigest(),meta['sha256'])

    def test_export_invalid_or_cancelled_leaves_no_completed_bundle(self):
        self.path.write_bytes(mrc_bytes())
        out=self.root/'out'
        from molscene.bundles import CancelledError
        with self.assertRaises(CancelledError):
            density_export.export_density(None,out,dict(map_path=str(self.path),format='ccp4'),cancelled=lambda:True)
        self.assertFalse(out.exists())
        out.mkdir(); sentinel=out/'keep'; sentinel.write_bytes(b'preserve')
        with self.assertRaises(ValueError): density_export.export_density(None,out,dict(map_path=str(self.path),format='ccp4'))
        self.assertEqual(sentinel.read_bytes(),b'preserve')

    def test_same_values_changed_native_origin_invalidate_association_and_fingerprint(self):
        path=self.root/'grid.dx'; path.write_text(dx_text())
        record=density.load_density(self.c,path,format='dx',name='source')
        before=density.map_fingerprint(self.c,record)
        alternate=self.root/'changed.dx'; alternate.write_text(dx_text().replace('origin 11 -7 3','origin 21 -7 3'))
        self.c.delete('source'); self.c.load(str(alternate),'source',format='dx',zoom=0)
        self.assertNotEqual(density.map_fingerprint(self.c,record),before)
        with self.assertRaises(ValueError): density.register_density(self.c,record,dict(kind='matrix',matrix=D,copy_name='registered'))
        session=self.root/'changed.pse'; self.c.save(str(session))
        with self.assertRaises(ValueError):
            density_export.export_density(session,self.root/'bad',dict(map_path=str(path),format='dx',map_name='source',render=dict(width=32,height=32)))
        self.assertFalse((self.root/'bad').exists())

    def test_backend_precision_zero_maps_and_nonrepresentable_values(self):
        path=self.root/'rounded.dx'
        text=dx_text().replace('0 1 2 3 4','0.123456789 1 2 3 4')
        path.write_text(text)
        record=density.load_density(self.c,path,format='dx',name='rounded')
        self.assertNotEqual(record['grid']['value_sha256'],record['value_sha256'])
        self.assertGreater(record['native_precision']['max_abs_quantization_error'],0)
        self.assertLess(record['native_precision']['max_abs_quantization_error'],1e-7)
        original=set(self.c.get_names('objects'))
        path.write_text(text.replace('0.123456789','1e100'))
        with self.assertRaises(ValueError): density.load_density(self.c,path,format='dx',name='overflow')
        self.assertEqual(set(self.c.get_names('objects')),original)
        path.write_text(dx_text().split('data follows')[0]+'data follows\n'+'0 '*60)
        record=density.load_density(self.c,path,format='dx',name='zeros')
        self.assertTrue(record['verified'])
        self.assertEqual(record['grid']['statistics']['max'],0)

    def test_registered_state_two_becomes_single_state_one(self):
        record=self.load_origin(state=2)
        result=density.register_density(self.c,record,dict(kind='matrix',matrix=D,copy_name='registered'))
        self.assertEqual(result['source_state'],2)
        self.assertEqual(self.c.count_states('registered'),1)
        self.assertVector(self.c.get_object_matrix('source',state=2),[1,0,0,20,0,1,0,30,0,0,1,40,0,0,0,1])
        self.assertVector(result['after_matrix'],[0,-1,0,-19,1,0,0,13,0,0,1,43,0,0,0,1])

    def test_unknown_schema_is_display_only_and_volume_has_explicit_ramp(self):
        record=self.load_origin()
        with patch.object(self.c,'get_version',return_value=('9.0',)):
            self.assertFalse(density.map_inventory(self.c,[record])[0]['verified'])
            with self.assertRaises(ValueError): density.map_fingerprint(self.c,record)
        ramp=[0,0,0,1,0,324,1,0,0,.8]
        result=density.display_density(self.c,record,dict(kind='volume',volume_ramp=ramp,opacity=.5,visible=False))
        self.assertEqual(self.c.get_type(result['name']),'object:volume')
        self.assertNotIn(result['name'],self.c.get_names('objects',enabled_only=1))
        self.assertVector(self.c.volume_color(result['name'],state=1),[0,0,0,1,0,324,1,0,0,.4])
        density.display_density(self.c,record,dict(kind='volume',volume_ramp=[0,0,1,0,0,324,1,0,0,.4]))
        self.assertIn(result['name'],self.c.get_names('objects',enabled_only=1))

    def test_display_failure_and_invalid_input_leave_only_originals(self):
        record=self.load_origin()
        initial=set(self.c.get_names('objects'))
        with patch.object(self.c,'color',side_effect=RuntimeError('injected color failure')), self.assertRaises(RuntimeError):
            density.display_density(self.c,record,dict(kind='surface'))
        self.assertEqual(set(self.c.get_names('objects')),initial)
        self.assertEqual(record['derived'],[])
        self.path.write_bytes(b'invalid')
        with self.assertRaises(ValueError): density.load_density(self.c,self.path,format='mrc',name='bad')
        self.assertEqual(set(self.c.get_names('objects')),initial)

    def test_replay_matches_saved_contour_geometry_and_aligned_copy(self):
        self.path.write_bytes(mrc_bytes(origin=(20,30,40))); self.models()
        session=self.root/'input.pse'; self.c.save(str(session))
        out=self.root/'bundle'
        request=dict(map_path=str(self.path),format='mrc',interpretation='origin',
            registration=dict(kind='align',mobile='mobile',target='target',mobile_state=2,target_state=2,selection='all',
                algorithm='align',cycles=0,cutoff=2,model_copy_name='aligned',copy_name='registered'),
            display=dict(kind='mesh',level=100),render=dict(width=32,height=32))
        density_export.export_density(session,out,request)
        self.c.reinitialize(); self.c.load(str(out/'scene.pse')); self.c.ray(32,32)
        expected=self.points('registered_mesh')
        expected_atoms=[a.coord for a in self.c.get_model('aligned',state=1).atom]
        self.c.reinitialize()
        # Execute the generated Python block in a fresh context using only cmd
        # and __script__, independent of the export implementation namespace.
        script=(out/'replay.pml').read_text()
        body=script.split('python\n',1)[1].rsplit('python end',1)[0]
        exec(compile(body,'replay.pml','exec'),{'cmd':self.c,'__script__':str(out/'replay.pml')})
        self.c.ray(32,32)
        replay=self.points('registered_mesh')
        self.assertEqual(len(replay),len(expected)); self.assertTrue(replay)
        for got,want in zip(replay,expected): self.assertVector(got,want)
        for got,want in zip(self.c.get_model('aligned',state=1).atom,expected_atoms): self.assertVector(got.coord,want)

    def test_associated_state_export_preserves_current_transform_and_source(self):
        record=self.load_origin(state=2)
        self.c.transform_object('source',[1,0,0,5,0,1,0,6,0,0,1,7,0,0,0,1],state=2,homogenous=1,transpose=0)
        session=self.root/'source.pse'; self.c.save(str(session))
        source_hash=hashlib.sha256(session.read_bytes()).hexdigest()
        out=self.root/'associated'
        report=density_export.export_density(session,out,dict(map_path=str(self.path),format='mrc',interpretation='origin',
            map_name='source',source_state=2,registration=dict(kind='matrix',matrix=D,copy_name='registered'),
            display=dict(level=100),render=dict(width=32,height=32)))
        self.assertVector(report['analysis']['density']['registration']['after_matrix'],[0,-1,0,-25,1,0,0,18,0,0,1,50,0,0,0,1])
        self.assertEqual(hashlib.sha256(session.read_bytes()).hexdigest(),source_hash)
        self.assertEqual(report['analysis']['density']['source']['state'],2)

    def test_associated_existing_display_exports_new_owned_display_and_replays(self):
        for kind in ('mesh','surface'):
            with self.subTest(kind=kind):
                self.c.reinitialize()
                record=self.load_origin()
                old=density.display_density(self.c,record,dict(kind=kind,level=50,color='red',opacity=.4))['name']
                self.c.ray(32,32)
                original_geometry=self.points(old)
                self.assertTrue(original_geometry)
                self.c.disable(old)
                matrix=list(self.c.get_object_matrix('source',state=1))
                raw=self.c.get_volume_field('source',state=1,copy=1).ravel().tolist()
                session=self.root/(kind+'.pse'); self.c.save(str(session))
                session_bytes=session.read_bytes(); source_bytes=self.path.read_bytes()
                out=self.root/('bundle_'+kind)
                report=density_export.export_density(session,out,dict(map_path=str(self.path),format='mrc',
                    interpretation='origin',map_name='source',display=dict(kind=kind,level=100),
                    render=dict(width=32,height=32,ray=True)))
                metadata=report['analysis']['density']; new=metadata['display']['name']
                self.assertNotEqual(new,old)
                self.assertEqual(metadata['display']['source'],'source')
                self.assertTrue(any(old in warning and new in warning and 'coexist' in warning for warning in report['warnings']))
                self.c.reinitialize(); self.c.load(str(out/'scene.pse')); self.c.ray(32,32)
                self.assertEqual(self.c.get_volume_field('source',state=1,copy=1).ravel().tolist(),raw)
                self.assertVector(self.c.get_object_matrix('source',state=1),matrix)
                self.assertEqual(self.c.isolevel(old,state=1,query=1),50)
                self.assertEqual(self.c.isolevel(new,state=1,query=1),100)
                self.assertAlmostEqual(self.c.get_setting_float('transparency',old),.6,places=6)
                self.assertNotIn(old,self.c.get_names('objects',enabled_only=1))
                self.assertIn(new,self.c.get_names('objects',enabled_only=1))
                # Reloaded hidden contours have no realized vertices until shown.
                self.c.enable(old); self.c.ray(32,32)
                self.assertEqual(self.points(old),original_geometry)
                self.c.disable(old)
                exported_geometry=self.points(new); self.assertTrue(exported_geometry)
                moved=self.root/('moved_'+kind); shutil.move(str(out),str(moved))
                code='''import builtins,json,os,pathlib,pymol2,sys
original=builtins.__import__
def isolated(name,*args,**kwargs):
 if name=='molscene' or name.startswith('molscene.'): raise ImportError('MolScene deliberately unavailable')
 return original(name,*args,**kwargs)
builtins.__import__=isolated
root=pathlib.Path(sys.argv[1]); old,new=sys.argv[2:4]; os.chdir(root)
with pymol2.PyMOL() as pm:
 c=pm.cmd; c.do('@replay.pml'); c.ray(32,32)
 result={'matrix':list(c.get_object_matrix('source',state=1)),
         'raw':c.get_volume_field('source',state=1,copy=1).ravel().tolist(),
         'old_level':c.isolevel(old,state=1,query=1),'new_level':c.isolevel(new,state=1,query=1),
         'old_transparency':c.get_setting_float('transparency',old),
         'enabled':c.get_names('objects',enabled_only=1)}
 c.enable(old); c.ray(32,32)
 c.dump('old.txt',old,state=1); c.dump('new.txt',new,state=1); c.disable(old)
 (root/'observed.json').write_text(json.dumps(result))
'''
                result=subprocess.run([sys.executable,'-B','-c',code,str(moved),old,new],cwd=self.root,
                                      capture_output=True,text=True,timeout=90)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                observed=json.loads((moved/'observed.json').read_text())
                self.assertEqual(observed['raw'],raw); self.assertVector(observed['matrix'],matrix)
                self.assertEqual(observed['old_level'],50); self.assertEqual(observed['new_level'],100)
                self.assertAlmostEqual(observed['old_transparency'],.6,places=6)
                self.assertNotIn(old,observed['enabled']); self.assertIn(new,observed['enabled'])
                for filename,expected in (('old.txt',original_geometry),('new.txt',exported_geometry)):
                    actual=[[float(v) for v in line.split()] for line in (moved/filename).read_text().splitlines() if line.strip()]
                    self.assertEqual(len(actual),len(expected))
                    for got,want in zip(actual,expected): self.assertVector(got,want)
                self.assertEqual(session.read_bytes(),session_bytes)
                self.assertEqual((moved/'source.pse').read_bytes(),session_bytes)
                self.assertEqual(self.path.read_bytes(),source_bytes)

    def test_explicit_display_name_still_rejects_unowned_objects(self):
        record=self.load_origin()
        self.c.pseudoatom('unrelated',pos=[4,5,6])
        with self.assertRaises(ValueError):
            density.display_density(self.c,record,dict(kind='mesh',level=100),name='unrelated')
        self.assertVector(self.c.get_model('unrelated').atom[0].coord,[4,5,6])
        self.assertEqual(record['derived'],[])
        self.c.isomesh('unowned_mesh','source',level=50)
        with self.assertRaises(ValueError):
            density.display_density(self.c,record,dict(kind='mesh',level=100),name='unowned_mesh')
        self.assertEqual(self.c.isolevel('unowned_mesh',state=1,query=1),50)
        self.c.isomesh('source_mesh','source',level=25)
        with self.assertRaises(ValueError):
            density.display_density(self.c,record,dict(kind='mesh',level=100))
        self.assertEqual(self.c.isolevel('source_mesh',state=1,query=1),25)

    def test_associated_export_retains_unrelated_colliding_object(self):
        self.load_origin()
        self.c.pseudoatom('source_mesh',pos=[4,5,6])
        session=self.root/'unrelated.pse'; self.c.save(str(session)); original=session.read_bytes()
        report=density_export.export_density(session,self.root/'unrelated_bundle',dict(
            map_path=str(self.path),format='mrc',interpretation='origin',map_name='source',
            display=dict(kind='mesh',level=100),render=dict(width=32,height=32,ray=True)))
        self.c.reinitialize(); self.c.load(str(self.root/'unrelated_bundle/scene.pse'))
        self.assertEqual(self.c.get_type('source_mesh'),'object:molecule')
        self.assertVector(self.c.get_model('source_mesh').atom[0].coord,[4,5,6])
        new=report['analysis']['density']['display']['name']
        self.assertNotEqual(new,'source_mesh'); self.assertEqual(self.c.get_type(new),'object:mesh')
        self.assertEqual(session.read_bytes(),original)

    def test_cancellation_after_render_never_publishes(self):
        self.path.write_bytes(mrc_bytes())
        cancel=[False]
        def progress(value):
            if value['stage']=='validate_artifacts': cancel[0]=True
        from molscene.bundles import CancelledError
        with self.assertRaises(CancelledError):
            density_export.export_density(None,self.root/'cancelled',dict(map_path=str(self.path),format='ccp4',
                render=dict(width=32,height=32)),cancelled=lambda:cancel[0],progress=progress)
        self.assertFalse((self.root/'cancelled').exists())

    def test_displays_of_distinct_source_states_do_not_overwrite_each_other(self):
        record=self.load_origin(state=2)
        self.c.load(str(self.path),'source',state=1,format='mrc',zoom=0)
        first=next(r for r in density.map_inventory(self.c,[record]) if r['state']==1)
        one=density.display_density(self.c,first,dict(kind='mesh',level=10))
        two=density.display_density(self.c,record,dict(kind='mesh',level=100))
        self.assertNotEqual(one['name'],two['name'])
        self.assertEqual(self.c.isolevel(one['name'],state=1,query=1),10)
        self.assertEqual(self.c.isolevel(two['name'],state=1,query=1),100)

    def test_native_cell_metadata_change_is_stale_even_with_retained_corners(self):
        record=self.load_origin()
        saved=self.c.get_session('source',partial=1)
        saved['names'][0][5][2][0][10][0]+=1
        self.c.set_session(saved,partial=1)
        with self.assertRaises(ValueError): density.display_density(self.c,record,{'kind':'mesh'})
        with self.assertRaises(ValueError):
            density.register_density(self.c,record,dict(kind='matrix',matrix=D,copy_name='registered'))
        self.assertNotIn('registered',self.c.get_names('objects'))

    def test_matrix_mode_two_alignment_rejected_in_isolated_process(self):
        record=self.load_origin(); self.models()
        session=self.root/'source.pse'; self.c.save(str(session))
        record_path=self.root/'record.json'; record_path.write_text(json.dumps(record))
        code='''import hashlib,json,pathlib,sys,pymol2
from molscene import density,density_export
root=pathlib.Path(sys.argv[1]); record=json.loads((root/'record.json').read_text())
request=dict(kind='align',mobile='mobile',target='target',mobile_state=2,target_state=2,cycles=0,model_copy_name='aligned',copy_name='registered')
for scope in ('global','mobile','target'):
 with pymol2.PyMOL() as p:
  c=p.cmd; c.load(str(root/'source.pse'))
  c.set('matrix_mode',2)
  c.transform_object('mobile',[1,0,0,5,0,1,0,6,0,0,1,7,0,0,0,1],state=2,homogenous=1,transpose=0)
  if scope!='global':
   c.set('matrix_mode',1); c.set('matrix_mode',2,scope)
  def capture():
   return dict(names=c.get_names('all'),global_mode=c.get_setting_int('matrix_mode'),
    molecules={name:dict(matrix=list(c.get_object_matrix(name,state=2)),ttt=c.get_object_ttt(name),coords=[a.coord for a in c.get_model('%'+name,state=2).atom],mode=c.get_setting_int('matrix_mode',name,state=2)) for name in ('mobile','target')})
  before=capture()
  for algorithm in ('align','super'):
   try: density.register_density(c,record,dict(request,algorithm=algorithm))
   except ValueError as exc: assert 'matrix_mode' in str(exc),str(exc)
   else: raise AssertionError('unsupported matrix_mode reached native alignment')
   assert capture()==before
  # The independent numeric registration path remains usable in mode2.
  result=density.register_density(c,record,dict(kind='matrix',matrix=[0,-1,0,11,1,0,0,-7,0,0,1,3,0,0,0,1],copy_name='numeric'))
  assert result['copy']=='numeric'; c.delete('numeric'); assert capture()==before
  if scope=='global': c.save(str(root/'unsupported.pse'))
source=root/'unsupported.pse'; digest=hashlib.sha256(source.read_bytes()).hexdigest()
try:
 density_export.export_density(source,root/'bad_bundle',dict(map_path=record['source_path'],format='mrc',interpretation='origin',registration=request,render=dict(width=32,height=32)))
except ValueError as exc: assert 'matrix_mode' in str(exc),str(exc)
else: raise AssertionError('unsupported alignment export succeeded')
assert not (root/'bad_bundle').exists()
assert not list(root.glob('.molscene-stage-*')) and not list(root.glob('.molscene-scratch-*'))
assert hashlib.sha256(source.read_bytes()).hexdigest()==digest
print('mode2 controlled rejection; source matrices/coordinates/settings preserved; numeric registration passes')
'''
        result=subprocess.run([sys.executable,'-u','-B','-X','faulthandler','-c',code,str(self.root)],
            cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=45)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_transformed_model_mode_one_replay_in_isolated_process(self):
        self.load_origin(); self.models()
        code='''import builtins,hashlib,json,math,pathlib,sys,pymol2
from molscene import density_export
root=pathlib.Path(sys.argv[1]); case=sys.argv[2]
request=dict(map_path=str(root/'source.mrc'),format='mrc',interpretation='origin',
 registration=dict(kind='align',mobile='mobile',target='target',mobile_state=2,target_state=2,cycles=0,model_copy_name='aligned',copy_name='registered'),
 display=dict(kind='mesh',level=100),render=dict(width=32,height=32))
digest=hashlib.sha256((root/'transformed.pse').read_bytes()).hexdigest()
report=density_export.export_density(root/'transformed.pse',root/('bundle_'+case),request)
bundle=root/('bundle_'+case)
assert hashlib.sha256((root/'transformed.pse').read_bytes()).hexdigest()==digest
expected_world=[0,-1,0,17,1,0,0,-12,0,0,1,-4,0,0,0,1] if case=='translation' else [0,-1,0,17,0,0,-1,0,1,0,0,-2,0,0,0,1]
delta=report['analysis']['density']['registration']['world_transform']
assert max(abs(a-b) for a,b in zip(delta,expected_world))<=1e-4,(delta,expected_world)
base=[(0,0,0),(1,2,0),(3,1,2),(4,4,1),(6,3,4),(7,6,2)]
expected_atoms=[[-y+11,x-7,z+3] for x,y,z in base]
expected_source=[[x+5,y+6,z+7] if case=='translation' else [z+5,y+6,7-x] for x,y,z in base]
def capture(c):
 assert c.get_setting_int('matrix_mode')==1
 source=[a.coord for a in c.get_model('mobile',state=2).atom]
 assert max(math.dist(a,b) for a,b in zip(source,expected_source))<=1e-4
 atoms=[a.coord for a in c.get_model('aligned',state=1).atom]
 assert max(math.dist(a,b) for a,b in zip(atoms,expected_atoms))<=1e-4,atoms
 assert not any(n.startswith('density_replay_check') for n in c.get_names('all'))
 return dict(atoms=atoms,matrix=list(c.get_object_matrix('aligned',state=1)),source_matrix=list(c.get_object_matrix('mobile',state=2)),map_matrix=list(c.get_object_matrix('registered',state=1)),source=source)
with pymol2.PyMOL() as p:
 p.cmd.load(str(bundle/'scene.pse')); expected=capture(p.cmd)
original=builtins.__import__
def standalone(name,*args,**kwargs):
 if name=='molscene' or name.startswith('molscene.'): raise ImportError('MolScene unavailable during replay')
 return original(name,*args,**kwargs)
builtins.__import__=standalone
with pymol2.PyMOL() as p:
 body=(bundle/'replay.pml').read_text().split('python\\n',1)[1].rsplit('python end',1)[0]
 exec(compile(body,'replay.pml','exec'),{'cmd':p.cmd,'__script__':str(bundle/'replay.pml')})
 actual=capture(p.cmd)
 for key in ('matrix','map_matrix','source_matrix'):
  assert max(abs(a-b) for a,b in zip(actual[key],expected[key]))<=1e-4,(key,actual[key],expected[key])
print('supported '+case+' state-matrix alignment and standalone replay agree; sources/settings preserved')
'''
        for case,matrix in [('translation',[1,0,0,5,0,1,0,6,0,0,1,7,0,0,0,1]),
                            ('rotation',[0,0,1,5,0,1,0,6,-1,0,0,7,0,0,0,1])]:
            with self.subTest(case=case):
                self.c.set('matrix_mode',2)
                self.c.matrix_reset('mobile',state=2,mode=2)
                self.c.transform_object('mobile',matrix,state=2,homogenous=1,transpose=0)
                self.c.set('matrix_mode',1)
                self.c.save(str(self.root/'transformed.pse'))
                result=subprocess.run([sys.executable,'-u','-B','-X','faulthandler','-c',code,str(self.root),case],
                    cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=45)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
