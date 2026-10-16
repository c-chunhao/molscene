import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from molscene.worker import run_job


class AdvancedWorkerTests(unittest.TestCase):
    def test_invalid_advanced_parameters_report_error_envelope(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            job = root / 'job.json'
            result = root / 'result.json'
            job.write_text(json.dumps(dict(schema_version=1, operation='density', output=str(root / 'output'),
                result_path=str(result), parameters={'unknown': True})))
            self.assertEqual(run_job(job), 1)
            self.assertIn('parameters', json.loads(result.read_text())['error'])

    def test_tools_failure_retains_each_status_and_nonzero(self):
        import sys
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = dict(schema_version=1, tools={name: dict(argv=[sys.executable, '-B', '-c', 'print("bad version")'],
                architecture='x64', path_dirs=[], runtime_files=[], timeout_seconds=15, memory_limit_mib=512)
                for name in ('apbs', 'ffmpeg')})
            job = root / 'job.json'
            result = root / 'result.json'
            job.write_text(json.dumps(dict(schema_version=1, operation='tools', output=str(result),
                result_path=str(result), parameters={'tools': config})))
            self.assertEqual(run_job(job), 1)
            envelope = json.loads(result.read_text())
            self.assertFalse(envelope['passed'])
            self.assertEqual(set(envelope['result']['tools']), {'apbs', 'ffmpeg'})
            for row in envelope['result']['tools'].values():
                self.assertFalse(row['available'])
                self.assertIn('version', row['error'])
                self.assertIsInstance(row['manifest'], dict)
                self.assertIn('returncode', row['manifest'])

    def test_tools_without_session_has_compatible_envelope_and_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            job = root / 'job.json'
            result = root / 'result.json'
            job.write_text(json.dumps(dict(schema_version=1, operation='tools', output=str(result),
                result_path=str(result), parameters={'tools': {'schema_version': 1, 'tools': {}},
                'cancel_file': str(root / 'cancel.request')})))
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                self.assertEqual(run_job(job), 0)
            envelope = json.loads(result.read_text())
            self.assertEqual(envelope['result'], dict(operation='tools', passed=True, cancelled=False, tools={}))
            self.assertTrue(envelope['passed'])
            self.assertFalse(list(root.glob('*.pse')))
            self.assertIn('progress', json.loads(stream.getvalue().splitlines()[0]))

    def test_tools_cancel_marker_cannot_collide_with_envelope(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'job.json'
            result = root / 'result.json'
            path.write_text(json.dumps(dict(schema_version=1, operation='tools', output=str(result),
                result_path=str(result), parameters=dict(tools={'schema_version': 1, 'tools': {}}, cancel_file=str(result)))))
            self.assertEqual(run_job(path), 1)
            self.assertFalse(result.exists())

    def test_advanced_source_and_envelope_collisions_never_write_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'map.dx'
            source.write_bytes(b'original source bytes')
            path = root / 'job.json'
            path.write_text(json.dumps(dict(schema_version=1, operation='density', output=str(root/'output'),
                result_path=str(source), parameters=dict(request=dict(map_path=str(source),format='dx')))))
            self.assertEqual(run_job(path), 1)
            self.assertEqual(source.read_bytes(), b'original source bytes')
            self.assertFalse((root/'output').exists())

    def test_worker_refuses_changed_private_input_copy(self):
        from molscene.artifacts import sha256
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, copied = root / 'original.dx', root / 'copy.dx'
            source.write_bytes(b'original')
            copied.write_bytes(b'changed')
            result = root / 'result.json'
            path = root / 'job.json'
            path.write_text(json.dumps(dict(schema_version=1, operation='density', output=str(root/'output'),
                result_path=str(result), original_inputs=[dict(role='map_path',path=str(source),copy=str(copied),sha256=sha256(source))],
                parameters=dict(request=dict(map_path=str(copied),format='dx')))))
            self.assertEqual(run_job(path), 1)
            self.assertIn('copy', json.loads(result.read_text())['error'].lower())
            self.assertFalse((root/'output').exists())

    def test_failed_managed_solver_envelope_keeps_retained_evidence(self):
        import importlib.util
        import sys
        if not importlib.util.find_spec('pymol2'):
            self.skipTest('requires PyMOL')
        with tempfile.TemporaryDirectory(prefix='a6failure-') as folder:
            root = Path(folder)
            script = root/'solver.py'
            script.write_text('import sys\nif "--version" in sys.argv:\n print("APBS 3.4.1")\n sys.exit(13)\nprint("controlled solver failure",file=sys.stderr)\nsys.exit(5)\n')
            config = dict(schema_version=1,tools={'apbs':dict(argv=[sys.executable,'-B',str(script)],architecture='x64',
                path_dirs=[],runtime_files=[dict(path=str(script),role='package_metadata')],timeout_seconds=15,memory_limit_mib=512)})
            pqr = root/'ion.pqr'
            pqr.write_text('ATOM 1 I ION 1 0 0 0 1 3\n')
            path,result = root/'job.json',root/'result.json'
            path.write_text(json.dumps(dict(schema_version=1,operation='potential',output=str(root/'output'),result_path=str(result),
                parameters=dict(request=dict(mode='prepared',pqr_path=str(pqr),tools=config,solver=dict(grid=[33]*3))))))
            self.assertEqual(run_job(path),1)
            envelope=json.loads(result.read_text())
            self.assertFalse(envelope['passed'])
            failure=Path(envelope['failure_path'])
            self.assertTrue((failure/'failure.json').is_file())
            stderr=Path(envelope['result']['stderr_path'])
            self.assertEqual(stderr.parent,failure/'logs')
            self.assertIn('controlled solver failure',stderr.read_text())
            self.assertEqual(envelope['result']['returncode'],5)
            self.assertFalse((root/'output').exists())
