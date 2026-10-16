"""Daily examples: immutable history, closed inventories and real saved data."""
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'examples/reproduce_v05.py'
HAS_PYMOL = importlib.util.find_spec('pymol2') is not None


def example_module():
    assert SCRIPT.is_file(), 'The standalone v0.5 example is missing'
    spec = importlib.util.spec_from_file_location('reproduce_v05', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReproduceV05Tests(unittest.TestCase):
    def test_help_is_backend_lazy_and_version_is_consistent(self):
        self.assertTrue(SCRIPT.is_file(), 'The standalone v0.5 example is missing')
        result = subprocess.run([sys.executable, '-B', '-S', str(SCRIPT), '--help'],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--output', result.stdout)
        from molscene import __version__
        self.assertEqual(__version__, '0.5.0')
        self.assertIn('version = "0.5.0"', (ROOT/'pyproject.toml').read_text())

    def test_existing_output_and_historic_output_tree_are_protected(self):
        example = example_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            occupied = root/'occupied'
            occupied.mkdir()
            sentinel = occupied/'scene.png'
            sentinel.write_bytes(b'historical bytes\r\n')
            with self.assertRaisesRegex(ValueError, 'new example output directory'):
                example.generate(occupied)
            self.assertEqual(sentinel.read_bytes(), b'historical bytes\r\n')
            with self.assertRaisesRegex(ValueError, 'historical'):
                example.generate(ROOT/'examples/outputs/unwritten-v05')
            self.assertFalse((ROOT/'examples/outputs/unwritten-v05').exists())

    def test_closed_audit_rejects_extra_files_empty_directories_and_tampering(self):
        example = example_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'report.json').write_text('{"passed": true}')
            example.seal_bundle(root)
            self.assertEqual(example.audit_bundle(root), {'report.json','manifest.json'})
            (root/'private').mkdir()
            with self.assertRaises(ValueError): example.audit_bundle(root)
            (root/'private').rmdir()
            (root/'unexpected').write_bytes(b'private')
            with self.assertRaises(ValueError): example.audit_bundle(root)
            (root/'unexpected').unlink()
            (root/'report.json').write_text('{"passed": false}')
            with self.assertRaises(ValueError): example.audit_bundle(root)

    def test_projection_keeps_binary_science_numbers_and_original_hashes(self):
        example = example_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root/'original'
            original.mkdir()
            payloads = {'scene.pse': b'opaque native session', 'scene.png': b'opaque original PNG',
                'input.pdb': b'REMARK public coordinates\n'}
            for name,data in payloads.items(): (original/name).write_bytes(data)
            report = {'passed': True, 'output_path': str(root), 'analysis': {'distance': 3.0},
                'object_files': [{'source_object': 'don', 'source_indices': [1,2]}]}
            (original/'report.json').write_text(json.dumps(report))
            example.seal_bundle(original)
            before = {p.name:p.read_bytes() for p in original.iterdir()}
            public = root/'public'
            example.project_public_bundle(original, public, private_paths=[str(root)])
            self.assertEqual(before, {p.name:p.read_bytes() for p in original.iterdir()})
            for name,data in payloads.items(): self.assertEqual((public/name).read_bytes(), data)
            projected = json.loads((public/'report.json').read_text())
            self.assertEqual(projected['analysis'], report['analysis'])
            self.assertEqual(projected['object_files'], report['object_files'])
            self.assertEqual(projected['public_projection']['original_artifact_sha256'],
                {name:hashlib.sha256(data).hexdigest() for name,data in before.items()})
            self.assertNotIn(str(root), (public/'report.json').read_text())
            example.audit_bundle(public)

    def test_packaged_scripts_and_fixture_are_declared_without_private_imports(self):
        self.assertTrue(SCRIPT.is_file(), 'The standalone v0.5 example is missing')
        text = SCRIPT.read_text(encoding='utf-8')
        self.assertNotIn('from tests', text)
        self.assertNotIn('.runtime', text)
        for name in ('examples/reproduce_v05.py', 'examples/data/polar-fragments.json'):
            self.assertIn(name, (ROOT/'MANIFEST.in').read_text())
            self.assertIn(name, (ROOT/'pyproject.toml').read_text())
        fixture = json.loads((ROOT/'examples/data/polar-fragments.json').read_text())
        fixture_bytes = (ROOT/'examples/data/polar-fragments.json').read_bytes()
        self.assertNotIn(b'\r', fixture_bytes, 'Pinned JSON must retain canonical LF bytes in Git archives')
        self.assertEqual(hashlib.sha256(fixture_bytes).hexdigest(), example_module().POLAR_FIXTURE_SHA256)
        self.assertEqual(len(fixture['objects']), 2)
        self.assertEqual(sum(len(row['bonds']) for row in fixture['objects']), 6)


@unittest.skipUnless(HAS_PYMOL, 'requires PyMOL for actual CPU-ray daily gallery and replay')
class ReproduceV05BackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='v05-gallery-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.example = example_module()
        cls.root = Path(cls.temporary.name)
        historical = [ROOT/'examples/reproduce_v02.py', ROOT/'examples/reproduce_v03.py',
                      ROOT/'examples/reproduce_v04.py', *sorted((ROOT/'examples/data').glob('*')),
                      *sorted((ROOT/'examples/outputs').rglob('*'))]
        cls.history = {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in historical if p.is_file()}
        cls.output = cls.example.generate(cls.root/'public')

    def test_closed_inventories_real_polar_and_literal_labels(self):
        e, output = self.example, self.output
        self.assertEqual(self.history, {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.history})
        e.audit_bundle(output)
        validation = json.loads((output/'validation.json').read_text())
        self.assertEqual(set(validation['bundles']), set(e.EXPECTED_FILES))
        originals = self.root/validation['private_original_directory']
        for name, expected in e.EXPECTED_FILES.items():
            self.assertEqual(e.audit_bundle(output/name), expected)
            self.assertEqual(e.audit_bundle(originals/name), expected)
            report = json.loads((output/name/'report.json').read_text())
            if name.startswith('polar-'):
                self.assertEqual(report['operation'], 'polar')
                self.assertGreater(report['analysis']['pair_count'], 0)
            else:
                self.assertTrue(report['passed'])
            for relative,digest in report['public_projection']['original_artifact_sha256'].items():
                self.assertEqual(hashlib.sha256((originals/name/relative).read_bytes()).hexdigest(), digest)
            for relative in expected:
                if Path(relative).suffix in {'.pdb','.cif','.pse','.png'}:
                    self.assertEqual((output/name/relative).read_bytes(), (originals/name/relative).read_bytes())
        report = json.loads((output/'polar-synthetic/report.json').read_text())
        self.assertEqual(report['analysis']['pair_count'], 1)
        pair = report['analysis']['pairs'][0]
        self.assertEqual((pair['donor']['name'],pair['acceptor']['name']), ('N','O'))
        self.assertAlmostEqual(pair['distance_angstrom'], 3.0)
        with (output/'polar-synthetic/polar.csv').open(newline='') as handle:
            self.assertEqual(len(list(csv.DictReader(handle))), 1)
        labels = json.loads((output/'labels/labels.json').read_text())
        self.assertEqual([row['text'] for row in labels['targets']],
            ['A:MET1','A:GLN2','A:ILE3','A:PHE4','A:VAL5'])
        self.assertEqual(labels['size_scope'], 'object_state')
        with (output/'labels/labels.csv').open(newline='') as handle:
            self.assertEqual([r['text'] for r in csv.DictReader(handle)], [r['text'] for r in labels['targets']])

    def test_saved_coordinate_source_mapping_and_session_identity(self):
        output = self.output
        report = json.loads((output/'coordinates/report.json').read_text())
        self.assertEqual(report['result_kind'], 'saved_snapshot')
        self.assertEqual(report['object_files'][0]['path'], 'objects/0001.cif')
        self.assertEqual(report['object_files'][0]['source_object'], 'ubiquitin')
        self.assertEqual(report['reload_verification']['atom_count'], 5)
        self.assertLessEqual(report['reload_verification']['max_euclidean_error_angstrom'], .001)
        with (output/'coordinates/membership.csv').open(newline='') as handle:
            members = list(csv.DictReader(handle))
        self.assertEqual([r['resi'] for r in members], ['1','2','3','4','5'])
        self.assertTrue(all(r['source_object']=='ubiquitin' and r['source_state']=='1' for r in members))
        session = json.loads((output/'session-save/report.json').read_text())
        self.assertEqual(hashlib.sha256((output/'session-save/session.pse').read_bytes()).hexdigest(), session['snapshot_id'])

    def test_relocated_standalone_replay_without_molscene(self):
        relocated = self.root/'relocated'
        shutil.copytree(self.output, relocated)
        code = '''
import importlib.abc, json, sys
from pathlib import Path
import pymol2
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'molscene' or fullname.startswith('molscene.'):
            raise ImportError('MolScene deliberately unavailable')
sys.meta_path.insert(0, Block())
root = Path(sys.argv[1])
for name in ('polar-public','polar-synthetic','labels','coordinates','session-save'):
    with pymol2.PyMOL() as pm:
        replay = root/name/('scene.pml' if name.startswith('polar-') else 'replay.pml')
        pm.cmd.do('@'+str(replay))
        assert pm.cmd.count_atoms('all') > 0, name
        if name == 'labels':
            actual=[]
            pm.cmd.iterate('all', 'actual.append(label)', space={'actual':actual})
            assert [s for s in actual if s] == ['A:MET1','A:GLN2','A:ILE3','A:PHE4','A:VAL5'], actual
        if name == 'coordinates': assert pm.cmd.count_atoms('all') == 5
        if name == 'polar-synthetic':
            assert pm.cmd.get_type('polar') == 'object:measurement'
            assert abs(pm.cmd.get_distance('molecule and name N','molecule and name O')-3) < .001
            for element,color in [('N','blue'),('O','red'),('C','gray70'),('H','gray85')]:
                colors=[]
                pm.cmd.iterate('molecule and elem '+element,'colors.append(color)',space={'colors':colors})
                assert colors and all(c == pm.cmd.get_color_index(color) for c in colors), (element,color,colors)
assert 'molscene' not in sys.modules
'''
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(relocated)],
            cwd=self.root, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)


if __name__ == '__main__':
    unittest.main()
