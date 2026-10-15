from contextlib import redirect_stdout, redirect_stderr
import io
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

try:
    from molscene import engine
except ImportError:
    engine = None

HAS_PYMOL = importlib.util.find_spec('pymol2') is not None
ROOT = Path(__file__).resolve().parents[1]
PDB = '''ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C
ATOM      2  CA  GLY A   2       3.000   4.000   0.000  1.00 20.00           C
ATOM      3  CA  ALA A   3       3.000   4.000   5.000  1.00 20.00           C
END
'''

class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(engine, 'scene engine must be implemented')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / 'tiny.pdb'
        self.source.write_text(PDB)
        self.out = self.base / 'out'

    def test_missing_and_unsupported_inputs(self):
        for source in (self.base / 'missing.pdb', self.base / 'bad.txt'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                engine.render(source, self.out)

    def test_nonfinite_radius_and_dimensions_are_rejected_before_runtime(self):
        for options in ({'radius': float('nan')}, {'radius': float('inf')}, {'radius': 0},
                        {'width': 0}, {'height': -1}, {'dpi': 0}, {'width': 2.5},
                        {'width': True}, {'style': 'bad'}, {'style': 'pocket'}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                engine.render(self.source, self.out, **options)
        self.assertFalse(self.out.exists())

    def test_occupied_output_and_input_collision_are_rejected(self):
        self.out.mkdir()
        marker = self.out / 'keep.txt'
        marker.write_text('keep')
        with self.assertRaises(ValueError):
            engine.render(self.source, self.out)
        with self.assertRaises(ValueError):
            engine.render(self.source, self.base, overwrite=True)
        self.assertEqual(marker.read_text(), 'keep')

    def test_output_parent_file_is_a_clear_validation_error(self):
        blocker = self.base / 'not-a-directory'
        blocker.write_text('keep')
        with self.assertRaises((ValueError, RuntimeError)):
            engine.render(self.source, blocker / 'out')
        self.assertEqual(blocker.read_text(), 'keep')

    def test_hardlinked_owned_target_preserves_other_inode_name(self):
        self.out.mkdir()
        outside = self.base / 'outside.txt'
        outside.write_text('safe')
        try:
            os.link(outside, self.out / 'scene.png')
        except OSError as exc:
            self.skipTest(str(exc))
        # Replacement, rather than opening the existing inode, must preserve outside.
        if HAS_PYMOL:
            engine.render(self.source, self.out, overwrite=True, width=200, height=160)
            self.assertEqual(outside.read_text(), 'safe')


@unittest.skipUnless(HAS_PYMOL, 'requires the user-provided PyMOL interpreter')
class SceneTests(ValidationTests):
    def check_artifacts(self, report):
        for key in ('tool', 'tool_version', 'pymol_version', 'created_at', 'operation',
                    'inputs', 'parameters', 'structure', 'analysis', 'artifacts'):
            self.assertIn(key, report)
        for name in ('scene.png', 'scene.pse', 'scene.pml', 'report.json'):
            self.assertGreater((self.out / name).stat().st_size, 0)
        from PIL import Image
        with Image.open(self.out / 'scene.png') as im:
            self.assertEqual(im.size, (240, 180))
            self.assertIsNotNone(im.convert('RGB').getbbox())
            self.assertGreater(len(im.getcolors(im.width * im.height) or []), 4)

    def test_render_and_relocated_literal_replay(self):
        import pymol2
        tricky = self.base / "空 格';&$()"
        tricky.mkdir()
        self.source = tricky / "源';&$().pdb"
        self.source.write_text(PDB)
        report = engine.render(self.source, self.out, width=240, height=180)
        self.check_artifacts(report)
        moved = self.base / "搬 家';&$()"
        shutil.move(str(self.out), moved)
        def replay(directory):
            old_cwd = Path.cwd()
            transcript = io.StringIO()
            try:
                with pymol2.PyMOL() as pm:
                    os.chdir(directory)
                    with redirect_stdout(transcript), redirect_stderr(transcript):
                        pm.cmd.do('@scene.pml', echo=0)
                    self.assertEqual(pm.cmd.count_atoms('molecule'), 3)
                    self.assertEqual(pm.cmd.count_atoms('molecule and rep cartoon'), 3)
                    self.assertEqual(pm.cmd.get_setting_int('cartoon_trace_atoms'), 0)
                    self.assertEqual(pm.cmd.get_setting_int('cartoon_trace_atoms', 'molecule'), 1)
            finally:
                os.chdir(old_cwd)
            self.assertNotIn('Error', transcript.getvalue())
        replay(moved)
        for name in ('bare;semicolon', 'space dir', '中文', 'dollar$()', "apostrophe'"):
            next_path = self.base / name
            shutil.move(str(moved), next_path)
            moved = next_path
            replay(moved)

    def test_nonprotein_render_is_rejected_without_committing_artifacts(self):
        fixtures = {
            'water': 'HETATM    1  O   HOH A   1       0.000   0.000   0.000  1.00 20.00           O\nEND\n',
            'nucleic': 'ATOM      1  P    DA A   1       0.000   0.000   0.000  1.00 20.00           P\nEND\n',
        }
        for name, content in fixtures.items():
            self.source.write_text(content)
            for style in ('cartoon', 'surface'):
                with self.subTest(fixture=name, style=style), self.assertRaisesRegex(ValueError, 'protein'):
                    engine.render(self.source, self.out, style=style, width=240, height=180)
                self.assertFalse(self.out.exists())

    def test_cartoon_trace_is_scoped_to_ca_only_protein(self):
        import pymol2
        engine.render(self.source, self.out, width=240, height=180)
        with pymol2.PyMOL() as pm:
            pm.cmd.load(str(self.out / 'scene.pse'))
            self.assertEqual(pm.cmd.get_setting_int('cartoon_trace_atoms'), 0)
            self.assertEqual(pm.cmd.get_setting_int('cartoon_trace_atoms', 'molecule'), 1)
        self.source.write_text(PDB.replace('  CA  ALA A   1', '  N   ALA A   1'))
        engine.render(self.source, self.out, width=240, height=180, overwrite=True)
        with pymol2.PyMOL() as pm:
            pm.cmd.load(str(self.out / 'scene.pse'))
            self.assertEqual(pm.cmd.get_setting_int('cartoon_trace_atoms'), 0)
            self.assertEqual(pm.cmd.get_setting_int('cartoon_trace_atoms', 'molecule'), 0)

    def test_measure_and_failed_overwrite_preserves_previous_outputs(self):
        report = engine.measure(self.source, self.out, selection_a='index 1',
                                selection_b='index 2', width=240, height=180)
        self.check_artifacts(report)
        self.assertAlmostEqual(report['analysis']['distance_angstrom'], 5.0, places=6)
        before = {p.name: p.read_bytes() for p in self.out.iterdir() if p.is_file()}
        with self.assertRaises(ValueError):
            engine.measure(self.source, self.out, selection_a='all', selection_b='index 2',
                           overwrite=True, width=240, height=180)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.out.iterdir() if p.is_file()})

    def test_align_same_named_inputs_and_compound_selection(self):
        target_dir = self.base / 'target'
        target_dir.mkdir()
        target = target_dir / self.source.name
        target.write_text(PDB.replace('0.000   0.000   0.000', '1.000   0.000   0.000')
                          .replace('3.000   4.000', '4.000   4.000'))
        report = engine.align(self.source, target, self.out,
                              selection='name CA or name N', width=240, height=180)
        self.check_artifacts(report)
        self.assertEqual(len(list((self.out / 'inputs').iterdir())), 2)
        self.assertEqual(report['analysis']['mobile_atoms'], 3)
        self.assertEqual(report['analysis']['target_atoms'], 3)
        self.assertEqual(len(report['analysis']['align_result']), 7)
        self.assertLess(report['analysis']['rmsd_angstrom'], 1e-5)
        import pymol2
        for replay in (False, True):
            with self.subTest(replay=replay), pymol2.PyMOL() as pm:
                if replay:
                    old_cwd = Path.cwd()
                    try:
                        os.chdir(self.out)
                        with redirect_stdout(io.StringIO()):
                            pm.cmd.do('@scene.pml', echo=0)
                    finally:
                        os.chdir(old_cwd)
                else:
                    pm.cmd.load(str(self.out / 'scene.pse'))
                for mobile_atom, target_atom in zip(pm.cmd.get_model('mobile').atom,
                                                    pm.cmd.get_model('target').atom):
                    for mobile_coord, target_coord in zip(mobile_atom.coord, target_atom.coord):
                        self.assertAlmostEqual(mobile_coord, target_coord, places=5)
                self.assertEqual(pm.cmd.count_atoms('mobile and rep cartoon'), 3)
                self.assertEqual(pm.cmd.get_setting_int('cartoon_trace_atoms'), 0)
                self.assertEqual(pm.cmd.get_setting_int('cartoon_trace_atoms', 'mobile'), 1)


    def test_real_pdb_and_cif_counts(self):
        for ext in ('pdb', 'cif'):
            with self.subTest(ext=ext):
                report = engine.render(ROOT / ('examples/data/1ubq.' + ext), self.out,
                                       overwrite=True, style='surface', width=240, height=180)
                self.assertEqual(report['structure']['molecule']['atoms'], 660)
                self.assertEqual(report['structure']['molecule']['protein_residues'], 76)
                self.assertEqual(report['structure']['molecule']['residues'], 134)
                self.check_artifacts(report)

    def test_full_atom_ubiquitin_cartoon_does_not_trace_sidechains(self):
        import pymol2
        engine.render(ROOT / 'examples/data/1ubq.pdb', self.out, width=240, height=180)
        with pymol2.PyMOL() as pm:
            pm.cmd.load(str(self.out / 'scene.pse'))
            self.assertEqual(pm.cmd.get_setting_int('cartoon_trace_atoms', 'molecule'), 0)
            self.assertEqual(pm.cmd.count_atoms('molecule and polymer.protein and rep cartoon'), 602)

    def test_real_pocket_neighborhood_and_representations(self):
        import pymol2
        report = engine.render(ROOT / 'examples/data/1hsg.pdb', self.out, style='pocket',
                               ligand='resn MK1', width=240, height=180)
        self.assertEqual(report['analysis']['ligand_atoms'], 45)
        self.assertEqual(report['analysis']['pocket_neighbor_atoms'], 115)
        self.assertEqual(report['analysis']['pocket_atoms'], 236)
        self.assertEqual(report['analysis']['pocket_residues'], 33)
        for replay in (False, True):
            with self.subTest(replay=replay), pymol2.PyMOL() as pm:
                if replay:
                    old_cwd = Path.cwd()
                    try:
                        os.chdir(self.out)
                        with redirect_stdout(io.StringIO()):
                            pm.cmd.do('@scene.pml', echo=0)
                    finally:
                        os.chdir(old_cwd)
                else:
                    pm.cmd.load(str(self.out / 'scene.pse'))
                self.assertEqual(pm.cmd.count_atoms('pocket'), 236)
                self.assertEqual(pm.cmd.count_atoms('ligand and rep sticks'), 45)
                self.assertEqual(pm.cmd.count_atoms('pocket and rep sticks'), 236)
                self.assertEqual(pm.cmd.get_setting_int('cartoon_trace_atoms', 'molecule'), 0)
                self.assertGreater(pm.cmd.get_setting_float('cartoon_transparency', 'molecule'), 0.5)
                carbon_colors = []
                pm.cmd.iterate('ligand and elem C', 'values.append(color)', space={'values': carbon_colors})
                self.assertTrue(carbon_colors)
                self.assertTrue(all(color == pm.cmd.get_color_index('orange') for color in carbon_colors))
                # stick_radius is stored on bonds; atom-level s returns the default.
                ligand_radius = [bond[2] for _, bonds in pm.cmd.get_bond('stick_radius', 'ligand', 'ligand')
                                 for bond in bonds]
                pocket_radius = [bond[2] for _, bonds in pm.cmd.get_bond('stick_radius', 'pocket', 'pocket')
                                 for bond in bonds]
                self.assertTrue(ligand_radius)
                self.assertTrue(pocket_radius)
                self.assertTrue(all(abs(value - 0.23) < 1e-5 for value in ligand_radius))
                self.assertTrue(all(abs(value - 0.10) < 1e-5 for value in pocket_radius))

if __name__ == '__main__':
    unittest.main()
