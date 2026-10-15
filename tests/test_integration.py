from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

try:
    from molscene import selftest
except ImportError:
    selftest = None

HAS_PYMOL = importlib.util.find_spec('pymol2') is not None
ROOT = Path(__file__).resolve().parents[1]


class SelftestValidationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(selftest, 'Offline self-test module must be implemented')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.out = self.base / 'out'

    def test_nonempty_directory_requires_explicit_overwrite(self):
        self.out.mkdir()
        marker = self.out / 'unrelated.txt'
        marker.write_text('keep')
        with self.assertRaises(ValueError):
            selftest.run_selftest(self.out)
        self.assertEqual(marker.read_text(), 'keep')

    def test_missing_runtime_is_recorded_as_failed_json(self):
        with patch('molscene.engine._runtime', side_effect=RuntimeError('Install PyMOL')):
            result = selftest.run_selftest(self.out)
        self.assertFalse(result['passed'])
        stored = json.loads((self.out / 'selftest.json').read_text(encoding='utf-8'))
        self.assertEqual(stored, result)
        self.assertTrue(any('Install PyMOL' in item.get('error', '') for item in result['tests']))

    def test_explicit_checks_survive_optimized_python(self):
        run = subprocess.run([sys.executable, '-O', '-c',
            'from molscene.selftest import _check; _check(False, "failure stays active")'],
            cwd=ROOT, capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('failure stays active', run.stderr)


@unittest.skipUnless(HAS_PYMOL, 'requires the user-provided PyMOL interpreter')
class OfflineIntegrationTests(SelftestValidationTests):
    def test_offline_checks_record_geometry_scene_session_image_and_portable_replay(self):
        self.out.mkdir()
        marker = self.out / 'unrelated.txt'
        marker.write_text('keep')
        previous = Path.cwd()
        transcript = io.StringIO()
        with redirect_stdout(transcript), redirect_stderr(transcript):
            result = selftest.run_selftest(self.out, overwrite=True)
        self.assertEqual(Path.cwd(), previous)
        self.assertTrue(result['passed'], json.dumps(result, indent=2) + transcript.getvalue())
        checks = {item['name']: item for item in result['tests']}
        for name in ('pdb_mmcif_coordinates', 'ca_selection', 'representations', 'distance_5_angstrom',
                     'rigid_alignment', 'session_restore', 'ray_image', 'portable_replay'):
            self.assertEqual(checks[name]['status'], 'passed', name)
        self.assertEqual(checks['ca_selection']['evidence']['ca_atoms'], 6)
        self.assertAlmostEqual(checks['distance_5_angstrom']['evidence']['distance_angstrom'], 5.0, places=6)
        self.assertLess(checks['rigid_alignment']['evidence']['rmsd_angstrom'], 1e-4)
        self.assertEqual(checks['ray_image']['evidence']['size'], [240, 180])
        self.assertEqual(len(checks['portable_replay']['evidence']['directories']), 5)
        self.assertEqual(marker.read_text(), 'keep')
        self.assertEqual(json.loads((self.out / 'selftest.json').read_text(encoding='utf-8')), result)

    def test_replay_printed_errors_fail_even_when_scene_state_is_correct(self):
        replay = selftest._replay
        def printed_error(cmd, directory):
            replay(cmd, directory)
            print('Error: simulated parser failure without a Python exception')
        with patch('molscene.selftest._replay', side_effect=printed_error):
            result = selftest.run_selftest(self.out)
        checks = {item['name']: item for item in result['tests']}
        self.assertFalse(result['passed'])
        self.assertEqual(checks['portable_replay']['status'], 'failed')
        self.assertIn('simulated parser failure', checks['portable_replay']['error'])
        self.assertIn('Replay transcript:', checks['portable_replay']['error'])
