"""CLI boundary tests: the new engine implementation is developed separately."""
from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import patch

from molscene import cli

ROOT = Path(__file__).resolve().parents[1]


class V02CliTests(unittest.TestCase):
    def invoke(self, args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(args)
        return code, out.getvalue(), err.getvalue()

    def test_new_help_works_without_site_packages_and_gui_import(self):
        for command in ('angle', 'dihedral', 'contacts', 'sasa', 'inspect', 'batch', 'gui'):
            with self.subTest(command=command):
                result = subprocess.run([sys.executable, '-B', '-S', '-m', 'molscene', command, '--help'],
                                        cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('usage:', result.stdout)
        result = subprocess.run([sys.executable, '-B', '-S', '-c',
            "from molscene import cli; import sys; "
            "assert not any(n.startswith(('PySide6', 'pymol', 'molscene.desktop')) for n in sys.modules)"],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_geometry_dispatches_selections_dimensions_and_state(self):
        for command, selections, value, label in (
                ('angle', ('index 1', 'index 2', 'index 3'), {'kind': 'angle', 'value': 90.0,
                    'units': 'degrees', 'state': 2, 'atoms': []}, 'Angle: 90 degrees'),
                ('dihedral', ('index 1', 'index 2', 'index 3', 'index 4'), {'kind': 'dihedral',
                    'value': -90.0, 'units': 'degrees', 'state': 2, 'atoms': []}, 'Dihedral: -90 degrees')):
            expected = dict(zip(('selection_a', 'selection_b', 'selection_c', 'selection_d'), selections))
            expected.update(width=200, height=160, dpi=96, state=2, overwrite=True)
            def calculate(source, output, **options):
                self.assertEqual((source, output), ('a.pdb', 'out'))
                self.assertEqual(options, expected)
                return {'analysis': value, 'artifacts': {'csv': 'analysis.csv'}}
            args = [command, 'a.pdb', '--output', 'out', '--state', '2', '--width', '200',
                    '--height', '160', '--dpi', '96', '--overwrite']
            for letter, selection in zip('abcd', selections):
                args += ['--selection-' + letter, selection]
            with self.subTest(command=command), patch.object(cli.engine, command, calculate, create=True):
                code, out, err = self.invoke(args)
                self.assertEqual(code, 0, err)
                self.assertIn(label, out)
                self.assertIn('degrees', out)
                self.assertIn('analysis.csv', out)

    def test_contacts_sasa_inspect_typed_dispatch_and_summaries(self):
        cases = (
            ('contacts', ['--selection-a', 'chain A', '--selection-b', 'chain B', '--cutoff', '3.2'],
             dict(selection_a='chain A', selection_b='chain B', cutoff=3.2, state=3,
                  width=1600, height=1200, dpi=300, overwrite=False),
             {'kind': 'contacts', 'pair_count': 12, 'pairs': [], 'residue_pairs': []}, '12'),
            ('sasa', ['--selection', 'chain A', '--context', 'all', '--altloc', 'all'],
             dict(selection='chain A', context='all', altloc='all', state=3,
                  width=1600, height=1200, dpi=300, overwrite=False), {'kind': 'sasa',
                  'total_area_angstrom2': 25.5, 'context_area_angstrom2': 30.0, 'residues': []}, '25.5'),
            ('inspect', ['--selection', 'chain A'],
             dict(selection='chain A', state=3, overwrite=False), {'kind': 'inspect', 'atom_count': 42,
                  'residue_site_count': 10, 'elements': {}, 'objects': [], 'chains': [], 'sequences': []}, '42'),
        )
        for command, extra, expected, analysis, summary in cases:
            def calculate(source, output, **options):
                self.assertEqual((source, output), ('a.cif', 'out'))
                self.assertEqual(options, expected)
                return {'analysis': analysis, 'artifacts': {'csv': 'analysis.csv'}}
            with self.subTest(command=command), patch.object(cli.engine, command, calculate, create=True):
                code, out, err = self.invoke([command, 'a.cif', '--output', 'out', '--state', '3', *extra])
                self.assertEqual(code, 0, err)
                self.assertIn(summary, out)
                self.assertIn('analysis.csv', out)

    def test_render_and_alignment_new_options_preserve_types(self):
        def render(source, output, **options):
            self.assertEqual(options, dict(style='cartoon', ligand=None, radius=5.0, width=1600,
                height=1200, dpi=300, overwrite=False, representation='sticks', selection='chain A',
                coloring='element', color='red', state=2, background='black',
                surface_transparency=0.4, stick_radius=0.25))
            return {'structure': {'molecule': {'atoms': 3}}, 'analysis': {}}
        with patch.object(cli.engine, 'render', render):
            code, _, err = self.invoke(['render', 'a.pdb', '--output', 'out', '--representation', 'sticks',
                '--selection', 'chain A', '--coloring', 'element', '--color', 'red', '--state', '2',
                '--background', 'black', '--surface-transparency', '0.4', '--stick-radius', '0.25'])
        self.assertEqual(code, 0, err)
        def align(mobile, target, output, **options):
            self.assertEqual(options, dict(selection='polymer.protein and name CA', width=1600,
                height=1200, dpi=300, overwrite=False, method='super', mobile_state=2,
                target_state=3, cutoff=1.5, cycles=2))
            return {'analysis': {'rmsd_angstrom': 0.1, 'aligned_atoms': 3}}
        with patch.object(cli.engine, 'align', align):
            code, _, err = self.invoke(['align', 'a.pdb', 'b.pdb', '--output', 'out', '--method',
                'super', '--mobile-state', '2', '--target-state', '3', '--cutoff', '1.5', '--cycles', '2'])
        self.assertEqual(code, 0, err)

    def test_new_usage_errors_and_dependency_errors_keep_exit_contract(self):
        for args in (['angle', 'a.pdb', '--output', 'out'], ['contacts', 'a.pdb', '--output', 'out'],
                     ['sasa', 'a.pdb', '--output', 'out', '--altloc', 'invalid'],
                     ['inspect', 'a.pdb', '--output', 'out', '--width', '200'],
                     ['batch', 'inputs'], ['gui', '--unknown']):
            code, _, err = self.invoke(args)
            self.assertEqual(code, 2, args)
            self.assertIn('usage:', err)
        with patch.object(cli.engine, 'angle', side_effect=RuntimeError('PyMOL unavailable'), create=True):
            code, out, err = self.invoke(['angle', 'a.pdb', '--selection-a', 'index 1',
                '--selection-b', 'index 2', '--selection-c', 'index 3', '--output', 'out'])
        self.assertEqual(code, 1)
        self.assertEqual(out, '')
        self.assertIn('PyMOL unavailable', err)

    def test_unsupported_cealign_is_a_usage_error_and_absent_from_help(self):
        code, out, err = self.invoke(['align', 'a.pdb', 'b.pdb', '--output', 'out', '--method', 'cealign'])
        self.assertEqual(code, 2)
        self.assertIn('invalid choice', err)
        code, out, err = self.invoke(['align', '--help'])
        self.assertEqual(code, 0, err)
        self.assertIn('--method {align,super}', out)
        self.assertNotIn('cealign', out)

    def test_unsupported_representation_and_coloring_are_usage_errors(self):
        # New engine code is not yet integrated: failure must occur in parsing.
        with patch.object(cli.engine, 'render', side_effect=ValueError('unsupported styling')):
            for option in ('--representation', '--coloring'):
                with self.subTest(option=option):
                    code, out, err = self.invoke(['render', 'a.pdb', '--output', 'out', option, 'invalid'])
                    self.assertEqual(code, 2)
                    self.assertEqual(out, '')
                    self.assertIn('usage:', err)

    def test_gui_lazy_launch_and_missing_dependency_error(self):
        module = types.ModuleType('molscene.desktop')
        def launch(paths=None, workspace=None):
            self.assertEqual(paths, ['a.pdb', 'b.cif'])
            self.assertEqual(workspace, 'work')
            return 0
        module.launch = launch
        with patch.dict(sys.modules, {'molscene.desktop': module}):
            code, _, err = self.invoke(['gui', 'a.pdb', 'b.cif', '--workspace', 'work'])
        self.assertEqual(code, 0, err)
        with patch.dict(sys.modules, {'molscene.desktop': None}):
            code, out, err = self.invoke(['gui'])
        self.assertEqual(code, 1)
        self.assertEqual(out, '')
        self.assertIn('GUI', err)

    def test_batch_dispatch_and_unsuccessful_result_exit_one(self):
        from molscene import batch
        def run(input_dir, output_dir, **options):
            self.assertEqual((input_dir, output_dir), ('inputs', 'out'))
            self.assertEqual(options, dict(render_options={'style': 'surface', 'ligand': None,
                'radius': 5.0, 'width': 200, 'height': 1200, 'dpi': 300, 'state': 2}, overwrite=True))
            return {'passed': False, 'items': [], 'completed': 1, 'failed': 1, 'cancelled': False}
        with patch.object(batch, 'run_batch', run):
            code, out, err = self.invoke(['batch', 'inputs', '--output', 'out', '--style', 'surface',
                                        '--width', '200', '--state', '2', '--overwrite'])
        self.assertEqual(code, 1, err)
        self.assertIn('1 completed', out)
        self.assertIn('1 failed', out)
        self.assertIn('batch.csv', out)


if __name__ == '__main__':
    unittest.main()
