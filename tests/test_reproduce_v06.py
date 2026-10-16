"""Offline example contracts: actual native bundles, analytic values and relocation."""
import hashlib
import importlib.util
import json
import math
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT/'examples/reproduce_v06.py'


def example_module(path=SCRIPT):
    assert path.is_file(), 'The standalone v0.6 example is missing'
    spec = importlib.util.spec_from_file_location('reproduce_v06', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReproduceV06Tests(unittest.TestCase):
    def test_help_does_not_import_optional_backend(self):
        self.assertTrue(SCRIPT.is_file(), 'The standalone v0.6 example is missing')
        result = subprocess.run([sys.executable, '-B', '-S', str(SCRIPT), '--help'],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--output', result.stdout)

    def test_installed_layout_finds_data_beside_script(self):
        example = example_module()
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)/'share/molscene/examples'
            shutil.copytree(ROOT/'examples/data', folder/'data')
            shutil.copy2(SCRIPT, folder/SCRIPT.name)
            installed = example_module(folder/SCRIPT.name)
            self.assertEqual(installed.data_path('3c70.cif'), folder/'data/3c70.cif')
            with (folder/'data/3c70.cif').open('ab') as handle:
                handle.write(b'\n')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                installed.data_path('3c70.cif')
        self.assertTrue(example.data_path('3c70.cif').is_file())

    def test_existing_historical_and_source_destinations_are_protected(self):
        example = example_module()
        with tempfile.TemporaryDirectory() as directory:
            occupied = Path(directory)/'occupied'
            occupied.mkdir()
            sentinel = occupied/'keep'
            sentinel.write_bytes(b'original\r\n')
            with self.assertRaises(ValueError): example.generate(occupied)
            self.assertEqual(sentinel.read_bytes(), b'original\r\n')
        for destination in (ROOT/'examples/outputs/v06-forbidden', ROOT/'examples/data/new'):
            with self.assertRaises(ValueError): example.generate(destination)
            self.assertFalse(destination.exists())

    def test_v06_data_and_both_generators_are_packaged(self):
        # Missing package declarations cause a real installed example to lose inputs.
        metadata = (ROOT/'pyproject.toml').read_text(encoding='utf-8')
        data_section = metadata.split('[tool.setuptools.data-files]', 1)[1]
        declared = set(re.findall(r'"(examples/[^"]+)"', data_section))
        required = {'examples/reproduce_v05.py', 'examples/reproduce_v06.py'}
        required.update('examples/data/'+name for name in (
            '3c70.cif', 'assembly-original.pdb', 'assembly-original.cif', 'v06-sources.json',
            'trajectory-tiny-v06.pdb', 'trajectory-tiny-v06.dcd',
            'trajectory-tiny-v06.json', 'trajectory-tiny-v06.md'))
        self.assertTrue(required <= declared, required-declared)


@unittest.skipUnless(importlib.util.find_spec('pymol2'), 'requires native PyMOL and NumPy; no rendering')
class ReproduceV06BackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='v06-offline-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.example = example_module()
        historical = [*sorted((ROOT/'examples/data').glob('*')),
                      *sorted((ROOT/'examples/outputs').rglob('*')),
                      *[ROOT/f'examples/reproduce_v0{n}.py' for n in (2,3,4,5)]]
        cls.before = {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in historical if p.is_file()}
        cls.output = cls.example.generate(cls.root/'public')

    def test_actual_bundles_expected_science_and_immutable_inputs(self):
        self.assertEqual(self.before, {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.before})
        e, output = self.example, self.output
        e.audit_bundle(output)
        assembly = json.loads((output/'assembly-3c70/report.json').read_text())
        self.assertEqual((assembly['copy_count'], assembly['atom_count']), (2,10410))
        self.assertEqual((output/'assembly-3c70/inputs/source.cif').read_bytes(), (ROOT/'examples/data/3c70.cif').read_bytes())
        fitted = json.loads((output/'trajectory-fit/report.json').read_text())
        raw = json.loads((output/'trajectory-dcd/report.json').read_text())
        self.assertEqual((fitted['frame_count'], fitted['atom_count']), (3,4))
        self.assertEqual((raw['frame_count'], raw['atom_count']), (6,4))
        for row in fitted['frames']:
            self.assertAlmostEqual(row['rmsd_angstrom'], 0, places=6)
            self.assertAlmostEqual(row['rg_angstrom'], math.sqrt(87)/4, places=6)
        self.assertAlmostEqual(fitted['frames'][1]['raw_rmsd_angstrom'], math.sqrt(30), places=6)
        self.assertAlmostEqual(fitted['frames'][2]['raw_rmsd_angstrom'], math.sqrt(11), places=6)
        for row in fitted['atoms']: self.assertAlmostEqual(row['rmsf_angstrom'], 0, places=6)
        for ordinal,row in enumerate(raw['frames']):
            self.assertEqual(row['state'], ordinal+1)
            self.assertIsNone(row['fitted_rmsd_angstrom'])
            self.assertIsNone(row['time_ps'])
            self.assertAlmostEqual(row['rmsd_angstrom'], ordinal*math.sqrt(113), places=6)
            self.assertAlmostEqual(row['rg_angstrom'], math.sqrt(22.5), places=6)
        for row in raw['atoms']:
            self.assertAlmostEqual(row['rmsf_angstrom'], math.sqrt(113*35/12), places=6)
        for name in ('assembly-3c70','trajectory-fit','trajectory-dcd'):
            e.audit_bundle(output/name)
            text = (output/name/'report.json').read_text()
            self.assertNotIn(str(self.root), text)
            self.assertNotIn(str(ROOT), text)
        for name in ('trajectory-fit','trajectory-dcd'):
            for metric in ('rmsd','rmsf','rg'):
                svg = (output/name/(metric+'.svg')).read_text()
                self.assertIn('<svg', svg)
                self.assertNotIn('<script', svg)
                self.assertNotIn('href=', svg)
        self.assertFalse(list(output.rglob('*.png')))

    def test_relocated_public_replays_have_no_molscene_dependency(self):
        relocated = self.root/'relocated'
        shutil.copytree(self.output, relocated)
        code = '''
import importlib.abc, sys
from pathlib import Path
import pymol2
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'molscene' or fullname.startswith('molscene.'):
            raise ImportError('MolScene deliberately unavailable')
sys.meta_path.insert(0, Block())
root=Path(sys.argv[1])
for name,atoms,states in [('assembly-3c70',10410,1),('trajectory-fit',4,3),('trajectory-dcd',4,6)]:
    with pymol2.PyMOL() as pm:
        pm.cmd.do('@'+str(root/name/'replay.pml'), echo=0)
        assert pm.cmd.count_atoms('all') == atoms, name
        assert pm.cmd.count_states('all') == states, name
assert 'molscene' not in sys.modules
'''
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(relocated)],
            cwd=self.root, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)


if __name__ == '__main__': unittest.main()
