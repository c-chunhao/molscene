"""Real isolated worker processes and collision/error guarantees."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HAS_PYMOL = importlib.util.find_spec('pymol2') is not None


@unittest.skipUnless(HAS_PYMOL, 'requires PyMOL')
class WorkerTests(unittest.TestCase):
    def setUp(self):
        import pymol2
        self.tmp = tempfile.TemporaryDirectory(prefix='worker-v02-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.session = self.base / "现场 ';$().pse"
        self.job = self.base / 'job.json'
        self.result = self.base / 'result.json'
        with pymol2.PyMOL() as pm:
            pm.cmd.pseudoatom('object_a', pos=(0, 0, 0), elem='C')
            pm.cmd.pseudoatom('object_b', pos=(3, 4, 0), elem='C')
            pm.cmd.show('spheres')
            pm.cmd.turn('y', 37)
            self.view = list(pm.cmd.get_view())
            pm.cmd.save(str(self.session))

    def invoke(self, operation='geometry', parameters=None, output=None, result_path=None):
        job = dict(schema_version=1, operation=operation, input_session=str(self.session),
                   output=str(output or self.result), parameters=parameters or
                   {'kind': 'distance', 'selections': ['object_a', 'object_b'], 'state': 1})
        if result_path is not None:
            job['result_path'] = str(result_path)
        self.job.write_text(json.dumps(job), encoding='utf-8')
        return subprocess.run([sys.executable, '-B', '-m', 'molscene.worker', str(self.job)],
                              cwd=ROOT, capture_output=True, text=True)

    def test_geometry_live_names_success_without_importing_gui(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        stored = json.loads(self.result.read_text(encoding='utf-8'))
        self.assertTrue(stored['passed'])
        self.assertEqual(stored['result']['value'], 5)
        self.assertEqual(stored['result']['atoms'][0]['object'], 'object_a')

    def test_error_written_atomically_and_nonzero(self):
        result = self.invoke(parameters={'kind': 'distance', 'selections': ['none', 'object_b'], 'state': 1})
        self.assertNotEqual(result.returncode, 0)
        stored = json.loads(self.result.read_text(encoding='utf-8'))
        self.assertFalse(stored['passed'])
        self.assertIn('error', stored)
        self.assertEqual(list(self.base.glob('.molscene-result-*')), [])

    def test_existing_result_job_and_snapshot_collisions_never_clobber_sources(self):
        for destination in (self.session, self.job, self.result):
            if destination == self.result:
                self.result.write_text('previous')
            original = self.session.read_bytes()
            result = self.invoke(output=destination)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.session.read_bytes(), original)
            if destination == self.result:
                self.assertEqual(self.result.read_text(encoding='utf-8'), 'previous')
            self.assertIn('molscene worker', result.stderr.lower())

    def test_export_uses_separate_envelope_and_preserves_snapshot(self):
        output = self.base / 'export'
        result = self.invoke('export', {'width': 200, 'height': 160, 'ray': True},
                             output=output, result_path=self.result)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(self.result.read_text(encoding='utf-8'))['passed'])
        import pymol2
        with pymol2.PyMOL() as pm:
            pm.cmd.load(str(output / 'scene.pse'))
            self.assertEqual(pm.cmd.get_names('objects'), ['object_a', 'object_b'])
            for actual, expected in zip(pm.cmd.get_view(), self.view):
                self.assertAlmostEqual(actual, expected, places=5)

    def test_nonray_export_produces_visible_structure_with_requested_dimensions(self):
        output = self.base / 'nonray'
        result = self.invoke('export', {'width': 200, 'height': 160, 'ray': False},
                             output=output, result_path=self.result)
        self.assertEqual(result.returncode, 0, result.stderr)
        from PIL import Image
        with Image.open(output / 'scene.png') as image:
            self.assertEqual(image.size, (200, 160))
            self.assertGreater(len(image.convert('RGB').getcolors(image.width * image.height) or []), 4)

    def test_unsafe_error_parent_and_bad_schema_return_stderr(self):
        result = self.invoke(output=self.session / 'result.json')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('molscene worker', result.stderr.lower())
        self.job.write_text('{"schema_version": 2}')
        result = subprocess.run([sys.executable, '-B', '-m', 'molscene.worker', str(self.job)],
                                 cwd=ROOT, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('schema_version', result.stderr)

    def test_distinct_science_output_and_envelope_and_snapshot_identity(self):
        raw = self.base / 'raw.json'
        result = self.invoke(output=raw, result_path=self.result)
        self.assertEqual(result.returncode, 0, result.stderr)
        scientific = json.loads(raw.read_text(encoding='utf-8'))
        envelope = json.loads(self.result.read_text(encoding='utf-8'))
        self.assertEqual(scientific, envelope['result'])
        self.assertEqual(scientific['state'], 1)
        self.assertEqual(len(scientific['source_fingerprint']), 64)
        self.assertEqual(len(scientific['snapshot_id']), 64)

    def test_contacts_sasa_inspect_worker_dispatch_and_default_export_envelope(self):
        for operation, parameters, key in (
                ('contacts', {'selection_a': 'object_a', 'selection_b': 'object_b', 'cutoff': 6}, 'pair_count'),
                ('sasa', {'selection': 'all'}, 'total_area_angstrom2'),
                ('inspect', {'selection': 'all'}, 'atom_count')):
            result = self.invoke(operation, parameters)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(self.result.read_text(encoding='utf-8'))['result']
            self.assertGreater(report[key], 0)
            self.assertEqual(report['state'], 1)
            self.result.unlink()
        result = self.invoke('export', {'width': 120, 'height': 100}, output=self.base / 'export')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads((self.base / 'job.result.json').read_text(encoding='utf-8'))['passed'])

    def test_worker_import_and_science_does_not_require_gui(self):
        self.job.write_text(json.dumps(dict(schema_version=1, operation='inspect',
            input_session=str(self.session), output=str(self.result), parameters={})), encoding='utf-8')
        code = '''import importlib.abc, runpy, sys
class NoGui(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname.startswith(('molscene.desktop', 'PySide6', 'PyQt5', 'PyQt6')):
   raise ImportError('GUI forbidden in worker')
sys.meta_path.insert(0, NoGui())
runpy.run_module('molscene.worker', run_name='__main__')
'''
        result = subprocess.run([sys.executable, '-B', '-c', code, str(self.job)],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_parameters_write_actionable_error_envelope(self):
        result = self.invoke(parameters={'kind': 'distance', 'selections': ['object_a', 'object_b'], 'bogus': 1})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('parameters', json.loads(self.result.read_text(encoding='utf-8'))['error'])

    def test_batch_cancel_file_preserves_pending_and_atomic_progress(self):
        inputs = self.base / 'inputs'
        inputs.mkdir()
        (inputs / 'tiny.pdb').write_text('ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\nEND\n')
        cancel = self.base / 'cancel'
        cancel.touch()
        output = self.base / 'batch'
        result = self.invoke('batch', dict(input_dir=str(inputs), cancel_file=str(cancel),
            render_options={'width': 120, 'height': 100}), output, self.result)
        self.assertNotEqual(result.returncode, 0)
        stored = json.loads(self.result.read_text(encoding='utf-8'))
        self.assertFalse(stored['passed'])
        self.assertTrue(stored['result']['cancelled'])
        self.assertEqual(stored['result']['items'][0]['status'], 'pending')
        self.assertEqual(json.loads((output / 'batch.json').read_text(encoding='utf-8')), stored['result'])

    def test_batch_cancel_marker_cannot_alias_job_session_result_or_output(self):
        inputs = self.base / 'inputs'
        inputs.mkdir()
        (inputs / 'tiny.pdb').write_text('ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\nEND\n')
        original = self.session.read_bytes()
        output = self.base / 'batch'
        for marker in (self.job, self.session, self.result, output / 'cancel'):
            result = self.invoke('batch', dict(input_dir=str(inputs), cancel_file=str(marker)), output, self.result)
            self.assertNotEqual(result.returncode, 0)
            envelope = json.loads(self.result.read_text(encoding='utf-8'))
            self.assertIn('cancel_file', envelope['error'])
            self.assertEqual(self.session.read_bytes(), original)
            self.result.unlink()

    def test_batch_rejects_direct_and_nested_source_markers_before_output_mutation(self):
        inputs = self.base / 'inputs'
        inputs.mkdir()
        (inputs / 'tiny.pdb').write_text('ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\nEND\n')
        (inputs / 'nested').mkdir()
        markers = (inputs / 'cancel', inputs / 'nested' / 'cancel')
        for marker in markers:
            marker.write_bytes(b'preserve marker')
        source_before = {p.relative_to(inputs): p.read_bytes() for p in inputs.rglob('*') if p.is_file()}
        session_before = self.session.read_bytes()
        for index, marker in enumerate(markers):
            for existing in (False, True):
                with self.subTest(marker=marker.relative_to(inputs), existing_output=existing):
                    self.result.unlink(missing_ok=True)
                    output = self.base / ('batch-' + str(index) + '-' + str(existing))
                    if existing:
                        output.mkdir()
                        (output / 'batch.json').write_bytes(b'preserve previous summary')
                    result = self.invoke('batch', dict(input_dir=str(inputs), cancel_file=str(marker),
                        overwrite=True, render_options={'width': 120, 'height': 100}), output, self.result)
                    self.assertNotEqual(result.returncode, 0)
                    envelope = json.loads(self.result.read_text(encoding='utf-8'))
                    self.assertIn('error', envelope, 'Source-contained marker must fail validation before batch dispatch')
                    self.assertIn('cancel_file', envelope['error'])
                    if existing:
                        self.assertEqual({p.name: p.read_bytes() for p in output.iterdir()},
                                         {'batch.json': b'preserve previous summary'})
                    else:
                        self.assertFalse(output.exists(), 'Rejected job must not create any batch output')
                    self.assertEqual({p.relative_to(inputs): p.read_bytes() for p in inputs.rglob('*') if p.is_file()},
                                     source_before)
                    self.assertEqual(self.session.read_bytes(), session_before)
                    self.result.unlink()
