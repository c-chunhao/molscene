from contextlib import redirect_stdout, redirect_stderr
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

try:
    from molscene import cli
except ImportError:
    cli = None

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(cli, 'CLI module must be implemented')

    def invoke(self, args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(args)
        return code, out.getvalue(), err.getvalue()

    def test_help_and_version_without_pymol(self):
        for args in (['--help'], ['render', '--help'], ['--version']):
            with self.subTest(args=args):
                run = subprocess.run([sys.executable, '-S', '-m', 'molscene', *args],
                                     cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertNotIn('Traceback', run.stderr)
        self.assertIn('0.1.0', run.stdout)

    def test_required_inputs_and_selections_return_usage_error(self):
        for args in ([], ['render', '--output', 'out'], ['measure', 'a.pdb', '--output', 'out'],
                     ['measure', 'a.pdb', '--selection-a', 'index 1', '--output', 'out'],
                     ['align', 'a.pdb', '--output', 'out'], ['selftest'],
                     ['render', 'a.pdb', '--output', 'out', '--style', 'invalid']):
            with self.subTest(args=args):
                code, out, err = self.invoke(args)
                self.assertEqual(code, 2)
                self.assertIn('usage:', err)
                self.assertNotIn('saved', out.lower())

    def test_real_validation_failure_is_actionable_without_success(self):
        code, out, err = self.invoke(['render', 'missing.pdb', '--output', 'out'])
        self.assertEqual(code, 1)
        self.assertIn('Input does not exist', err)
        self.assertEqual(out, '')

    def test_runtime_failure_has_exit_one(self):
        with patch('molscene.engine.doctor', side_effect=RuntimeError('Use PyMOL Python')):
            code, out, err = self.invoke(['doctor'])
        self.assertEqual(code, 1)
        self.assertIn('Use PyMOL Python', err)
        self.assertEqual(out, '')

    def test_render_dispatch_preserves_options(self):
        # Ray tracing is slow/external, so replace only the engine boundary.
        def render(source, output, **options):
            self.assertEqual((source, output), ('a.pdb', 'out'))
            self.assertEqual(options, dict(style='pocket', ligand='resn LIG', radius=7.5,
                                          width=400, height=300, dpi=120, overwrite=True))
            return {'structure': {'molecule': {'atoms': 42}},
                    'analysis': {'ligand_atoms': 3, 'pocket_residues': 2}}
        with patch('molscene.engine.render', side_effect=render):
            code, out, err = self.invoke(['render', 'a.pdb', '--output', 'out', '--style', 'pocket',
                '--ligand', 'resn LIG', '--radius', '7.5', '--width', '400', '--height', '300',
                '--dpi', '120', '--overwrite'])
        self.assertEqual(code, 0, err)
        self.assertIn('42', out)
        self.assertIn('report.json', out)
        self.assertIn('geometric', out.lower())

    def test_measure_and_align_print_scientific_values(self):
        def measure(source, output, **options):
            self.assertEqual((source, output), ('a.pdb', 'out'))
            self.assertEqual(options['selection_a'], 'index 1')
            self.assertEqual(options['selection_b'], 'index 2')
            return {'analysis': {'distance_angstrom': 5.0}}
        with patch('molscene.engine.measure', side_effect=measure):
            code, out, err = self.invoke(['measure', 'a.pdb', '--selection-a', 'index 1',
                '--selection-b', 'index 2', '--output', 'out'])
        self.assertEqual(code, 0, err)
        self.assertIn('5', out)
        self.assertIn('angstrom', out.lower())
        def align(mobile, target, output, **options):
            self.assertEqual((mobile, target, output), ('a.pdb', 'b.cif', 'out'))
            self.assertEqual(options['selection'], 'name CA')
            return {'analysis': {'rmsd_angstrom': 0.002, 'aligned_atoms': 6}}
        with patch('molscene.engine.align', side_effect=align):
            code, out, err = self.invoke(['align', 'a.pdb', 'b.cif', '--selection', 'name CA', '--output', 'out'])
        self.assertEqual(code, 0, err)
        self.assertIn('RMSD', out)
        self.assertIn('6', out)

    def test_failed_selftest_returns_one_and_names_failure(self):
        with patch('molscene.selftest.run_selftest', return_value={'passed': False, 'tests': [
                {'name': 'ray_image', 'status': 'failed', 'error': 'all pixels white'}]}):
            code, out, err = self.invoke(['selftest', '--output', 'out'])
        self.assertEqual(code, 1)
        self.assertIn('ray_image', err)
        self.assertNotIn('passed', out.lower())
        self.assertIn('selftest.json', out + err)

    def test_successful_selftest_and_doctor(self):
        with patch('molscene.selftest.run_selftest', return_value={'passed': True, 'tests': [
                {'name': 'geometry', 'status': 'passed', 'evidence': {}}]}):
            code, out, err = self.invoke(['selftest', '--output', 'out', '--overwrite'])
        self.assertEqual(code, 0, err)
        self.assertIn('1', out)
        with patch('molscene.engine.doctor', return_value={'ok': True, 'pymol_version': '3.2',
                   'python': '3.10', 'executable': 'python.exe'}):
            code, out, err = self.invoke(['doctor'])
        self.assertEqual(code, 0, err)
        self.assertIn('3.2', out)
