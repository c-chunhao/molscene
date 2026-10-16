"""Actual external solver acceptance gates; mocks cannot satisfy these tests."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from molscene.artifacts import sha256
from molscene.electrostatics import export_potential
from molscene.external_tools import load_tools, run_tool
from molscene.grids import read_dx
from molscene.pqr import read_pqr

ROOT = Path(__file__).resolve().parents[1]


def colored_pixels(image):
    pixels=image.convert('RGB').tobytes()
    return sum(max(pixel)-min(pixel)>30 for pixel in zip(pixels[0::3],pixels[1::3],pixels[2::3]))


class RealElectrostaticsTests(unittest.TestCase):
    def setUp(self):
        config = os.environ.get('MOLSCENE_TOOLS_CONFIG')
        if not config:
            if os.environ.get('MOLSCENE_REQUIRE_ADVANCED') == '1':
                self.fail('MOLSCENE_REQUIRE_ADVANCED requires actual MOLSCENE_TOOLS_CONFIG')
            self.skipTest('Actual APBS/PDB2PQR tools not configured')
        self.tools = load_tools(Path(config))
        evidence = os.environ.get('MOLSCENE_TEST_EVIDENCE') or os.environ.get('MOLSCENE_TEST_ARTIFACTS')
        if evidence:
            Path(evidence).mkdir(parents=True, exist_ok=True)
            # Keep nested managed guardian/probe paths below Windows MAX_PATH.
            label = 'born' if 'born' in self._testMethodName else 'uniform' if 'uniform' in self._testMethodName else 'ubiquitin'
            self.work = Path(tempfile.mkdtemp(prefix=label+'-', dir=evidence))
        else:
            temp = tempfile.TemporaryDirectory()
            self.addCleanup(temp.cleanup)
            self.work = Path(temp.name)

    def uniform(self, name, *, count=129, charge=1., dielectric=78.54, shift=(0.,0.,0.)):
        pqr = self.work/(name+'.pqr')
        pqr.write_text(f'ATOM 1 I ION 1 {shift[0]} {shift[1]} {shift[2]} {charge} 3.0\n')
        destination = self.work/name
        report = export_potential(None,destination,dict(mode='prepared',pqr_path=str(pqr),tools=self.tools,
                  temperature=298.15,solver=dict(grid=[count]*3,solute_dielectric=dielectric,solvent_dielectric=dielectric,
                  salt_molar=0,coarse_padding=80,fine_padding=32),render=dict(width=160,height=120)))
        grid = read_dx(destination/'potential.dx')
        self.assertEqual(grid.shape,(count,)*3)
        self.assertEqual(report['analysis']['solver']['actual_counts'],[count]*3)
        samples=[]
        for distance in (4.,6.,8.,10.):
            xyz=(shift[0]+distance,shift[1],shift[2])
            i,j,k=[round((p-o)/grid.basis[a][a]) for a,(p,o) in enumerate(zip(xyz,grid.origin))]
            actual=grid.values[(i*count+j)*count+k]
            expected=charge*(1.602176634e-19)**2/(4*math.pi*8.8541878128e-12*dielectric*distance*1e-10*1.380649e-23*298.15)
            samples.append(dict(distance=distance,actual=actual,expected=expected,relative_error=abs(actual-expected)/abs(expected)))
        return samples

    def test_real_apbs_uniform_sign_dielectric_translation_convergence(self):
        fine=self.uniform('fine')
        coarse=self.uniform('coarse',count=65)
        negative=self.uniform('negative',charge=-1.)
        dielectric=self.uniform('dielectric',dielectric=157.08)
        translated=self.uniform('translated',shift=(2.,-1.,3.))
        metrics=dict(fine=fine,coarse=coarse,negative=negative,dielectric=dielectric,translated=translated)
        (self.work/'physics.json').write_text(json.dumps(metrics,indent=2))
        for samples in metrics.values():
            self.assertLess(max(s['relative_error'] for s in samples),.01)
        self.assertLess(max(s['relative_error'] for s in fine), max(s['relative_error'] for s in coarse))
        for a,b,c,d in zip(fine,negative,dielectric,translated):
            self.assertLess(abs(a['actual']+b['actual'])/a['actual'],.01)
            self.assertLess(abs(a['actual']-2*c['actual'])/a['actual'],.01)
            self.assertLess(abs(a['actual']-d['actual'])/a['actual'],.01)

    def test_real_apbs_born_dielectric_boundary(self):
        fixture=ROOT/'tests/fixtures/advanced/born'
        for name in ('ion.pqr','apbs-mol-auto.in'):
            shutil.copyfile(fixture/name,self.work/name)
        result=run_tool('apbs',self.tools,['apbs-mol-auto.in'],cwd=self.work,log_prefix=self.work/'apbs')
        self.assertEqual(result['returncode'],0)
        stdout=Path(result['stdout_path']).read_text(errors='replace')
        energies=re.findall(r'Global net ELEC energy\s*=\s*([-+\deE.]+)',stdout)
        self.assertTrue(energies)
        actual=float(energies[-1])
        independent=(1.602176634e-19)**2/(8*math.pi*8.8541878128e-12*3e-10)*(1/78.54-1)*6.02214076e23/1000
        (self.work/'physics.json').write_text(json.dumps(dict(actual_kj_mol=actual,independent_kj_mol=independent,
            relative_error=abs(actual-independent)/abs(independent),source_hashes={name:sha256(fixture/name) for name in ('ion.pqr','apbs-mol-auto.in')}),indent=2))
        self.assertAlmostEqual(independent,-228.6108009877,places=8)
        self.assertLess(abs(actual-(-229.7740)),.1)
        self.assertLess(abs(actual-independent)/abs(independent),.01)

    def test_real_pdb2pqr_ubiquitin_surface_and_replay(self):
        import pymol2
        from PIL import Image
        source=ROOT/'examples/data/1ubq.pdb'
        before=sha256(source)
        snapshot=self.work/'snapshot.pse'
        with pymol2.PyMOL() as pm:
            pm.cmd.load(str(source),'ubiquitin')
            pm.cmd.save(str(snapshot))
        snapshot_before=sha256(snapshot)
        destination=self.work/'ubiquitin'
        report=export_potential(snapshot,destination,dict(mode='prepare',selection='ubiquitin',tools=self.tools,
               preparation=dict(drop_water=True),solver=dict(grid=[129]*3),display_units='mV',ramp_limit=128.5,render=dict(width=480,height=360)))
        self.assertEqual(sha256(source),before)
        self.assertEqual(sha256(snapshot),snapshot_before)
        preparation=report['structure']['preparation']
        self.assertEqual(preparation['retained_heavy_atoms'],602)
        self.assertEqual(len(preparation['excluded_atoms']),58)
        prepared=read_pqr(destination/'prepared.pqr')
        self.assertGreater(prepared['zero_radius_sites'],0)
        self.assertTrue(any(a['radius']==0 and a['charge']!=0 and a['element']=='H' for a in prepared['atoms']))
        self.assertTrue(all(a['radius']>0 for a in prepared['atoms'] if a['element']!='H'))
        with pymol2.PyMOL() as surface:
            surface.cmd.load(str(destination/'scene.pse'))
            radii={(a.chain,a.resi,a.resn,a.name):(a.vdw,a.elec_radius) for a in surface.cmd.get_model(report['analysis']['display']['molecule']).atom}
            for atom in prepared['atoms']:
                pair=radii[(atom['chain'],atom['resi'],atom['resn'],atom['name'])]
                self.assertAlmostEqual(pair[0],atom['radius'],places=6)
                self.assertAlmostEqual(pair[1],atom['radius'],places=6)
        grid=read_dx(destination/'potential.dx')
        self.assertTrue(all(math.isfinite(v) for v in grid.values))
        self.assertTrue((destination/'display.dx').is_file())
        self.assertNotEqual(sha256(destination/'display.dx'),sha256(destination/'potential.dx'))
        self.assertEqual(json.loads((destination/'report.json').read_text()),report)
        with Image.open(destination/'scene.png') as image:
            self.assertEqual(image.size,(480,360))
            self.assertGreater(colored_pixels(image),100)
        script=self.work/'verify_replay.py'
        script.write_text('''import importlib.abc
import json
import os
from pathlib import Path
import sys
import pymol2
class NoMolScene(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname == 'molscene' or fullname.startswith('molscene.'):
            raise ImportError('MolScene deliberately unavailable for standalone replay')
sys.meta_path.insert(0,NoMolScene())
root=Path(sys.argv[1]).resolve()
report=json.loads((root/'report.json').read_text())
r=report['analysis']['display']
with pymol2.PyMOL() as saved:
    saved.cmd.load(str(root/'scene.pse'))
    original=[(a.vdw,a.elec_radius,list(a.coord)) for a in saved.cmd.get_model(r['molecule']).atom]
    field=saved.cmd.get_volume_field(r['map'],copy=1).copy()
    ramp=saved.cmd.get_session(r['ramp'])['names'][0]
with pymol2.PyMOL() as replay:
    os.chdir(root)
    replay.cmd.do('@replay.pml',echo=0)
    actual=[(a.vdw,a.elec_radius,list(a.coord)) for a in replay.cmd.get_model(r['molecule']).atom]
    assert actual == original, 'replay atom coordinates/radii differ'
    assert (replay.cmd.get_volume_field(r['map'],copy=1)==field).all(), 'replay field differs'
    assert replay.cmd.get_setting_int('surface_solvent',r['molecule']) == 0
    assert replay.cmd.get_setting_int('surface_ramp_above_mode',r['molecule']) == 0
    assert abs(replay.cmd.get_setting_float('solvent_radius',r['molecule'])-1.4)<1e-6
    actual_ramp=replay.cmd.get_session(r['ramp'])['names'][0]
    assert actual_ramp[5][2] == ramp[5][2], 'replay ramp differs'
    replay.cmd.png(str(root.parent/'replayed.png'),width=480,height=360,ray=1)
(root.parent/'replay-verification.json').write_text(json.dumps(dict(passed=True,atom_count=len(actual),molscene_available=False)))
''',encoding='utf-8')
        environment=os.environ.copy()
        environment.pop('PYTHONPATH',None)
        result=subprocess.run([sys.executable,'-B',str(script),str(destination)],cwd=self.work,env=environment,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        (self.work/'replay.stdout.log').write_bytes(result.stdout)
        (self.work/'replay.stderr.log').write_bytes(result.stderr)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace')[-3000:])
        # PML echoes its Python source, including "except ... as error:".
        # Inspect diagnostics, not echoed command/source lines.
        diagnostic_lines=[line for line in (result.stdout+result.stderr).decode(errors='replace').splitlines()
                          if not line.startswith('PyMOL>') and not re.match(r'^\s*\d+:',line)]
        self.assertNotRegex('\n'.join(diagnostic_lines),r'(?im)(?:\bTraceback\b|\bException\b|\bError\s*:)')
        with Image.open(self.work/'replayed.png') as image:
            self.assertGreater(colored_pixels(image),100)


if __name__ == '__main__':
    unittest.main()
