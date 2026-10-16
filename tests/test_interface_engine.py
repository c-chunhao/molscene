"""Interface artifacts, exact retained display, snapshot provenance and CLI."""
from contextlib import redirect_stderr, redirect_stdout
import csv
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from molscene import artifacts, cli, engine

ROOT = Path(__file__).resolve().parents[1]
HAS_PYMOL = importlib.util.find_spec('pymol2') is not None


class InterfaceCliTests(unittest.TestCase):
    def test_help_and_imports_work_without_backend_or_qt(self):
        result = subprocess.run([sys.executable, '-B', '-S', '-m', 'molscene', 'interface', '--help'],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--selection-a', result.stdout)
        self.assertIn('--altloc', result.stdout)
        code = "import sys; import molscene.cli, molscene.worker; assert not any(k.startswith(('pymol', 'PyQt', 'PySide')) for k in sys.modules)"
        result = subprocess.run([sys.executable, '-B', '-S', '-c', code], cwd=ROOT,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dispatch_preserves_state_altloc_and_presents_area_definition(self):
        args = ['interface', 'a.pdb', '--output', 'out', '--selection-a', 'chain A',
                '--selection-b', 'chain B', '--state', '2', '--altloc', 'all',
                '--width', '160', '--height', '120', '--dpi', '100', '--overwrite']
        def run(source, output, **options):
            self.assertEqual((source, output), ('a.pdb', 'out'))
            self.assertEqual(options, dict(selection_a='chain A', selection_b='chain B', state=2,
                altloc='all', width=160, height=120, dpi=100, overwrite=True))
            return {'analysis': {'interface_area_angstrom2': 12.5},
                    'artifacts': {'interface_csv': 'interface.csv'}}
        out, err = io.StringIO(), io.StringIO()
        with patch.object(engine, 'interface', side_effect=run, create=True), redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(cli.main(args), 0, err.getvalue())
        self.assertIn('12.5', out.getvalue())
        self.assertIn('half', out.getvalue().lower())
        self.assertIn('interface.csv', out.getvalue())


@unittest.skipUnless(HAS_PYMOL, 'requires PyMOL')
class InterfaceEngineTests(unittest.TestCase):
    def setUp(self):
        import pymol2
        self.tmp = tempfile.TemporaryDirectory(prefix='interface-engine-v03-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / "tiny 中文';$().pdb"
        self.out = self.base / 'out'
        # Two retained atoms on A (one zero-loss residue), one on B. All other
        # atoms must be invisible under primary policy, including lower-case a.
        with pymol2.PyMOL() as pm:
            for name, chain, resi, pos, elem, resn, alt in (
                    ('C1', 'A', '1', (0, 0, 0), 'C', 'LIG', ''),
                    ('C2', 'A', '2', (0, 20, 0), 'C', 'LIG', 'A'),
                    ('C3', 'B', '1', (3, 0, 0), 'C', 'LIG', ''),
                    ('C4', 'A', '3', (40, 0, 0), 'C', 'LIG', 'a'),
                    ('C5', 'B', '2', (50, 0, 0), 'C', 'LIG', 'B'),
                    ('H1', 'A', '4', (60, 0, 0), 'H', 'LIG', ''),
                    ('O1', 'B', '3', (70, 0, 0), 'O', 'HOH', '')):
                pm.cmd.pseudoatom('tiny', name=name, chain=chain, resi=resi, pos=pos,
                                  elem=elem, resn=resn, hetatm=1)
                pm.cmd.alter('tiny and name ' + name, 'alt=' + repr(alt))
            pm.cmd.create('tiny', 'tiny', 1, 2)
            pm.cmd.translate([30, 0, 0], 'chain B', state=2)
            pm.cmd.save(str(self.source), 'tiny', state=0)

    def run_engine(self, **options):
        return engine.interface(self.source, self.out, selection_a='chain A', selection_b='chain B',
                                width=160, height=120, **options)

    def assert_visible_png(self, path):
        from PIL import Image
        with Image.open(path) as im:
            self.assertEqual(im.size, (160, 120))
            colors = im.convert('RGB').getcolors(160 * 120)
            self.assertGreater(len(colors or []), 8)
            self.assertGreater(sum(n for n, color in colors if min(color) < 220), 30)

    def restore(self, directory, replay):
        import pymol2
        pm = pymol2.PyMOL()
        pm.start()
        self.addCleanup(pm.stop)
        if replay:
            old = Path.cwd()
            output = io.StringIO()
            try:
                os.chdir(directory)
                with redirect_stdout(output), redirect_stderr(output):
                    pm.cmd.do('@scene.pml', echo=0)
            finally:
                os.chdir(old)
            self.assertNotIn('Error', output.getvalue())
        else:
            pm.cmd.load(str(directory / 'scene.pse'))
        return pm.cmd

    def test_complete_artifacts_csv_raw_areas_and_exact_nonprotein_replay(self):
        source_bytes = self.source.read_bytes()
        report = self.run_engine()
        self.assertEqual(report['schema_version'], 2)
        self.assertEqual(report['operation'], 'interface')
        self.assertEqual(report['inputs'][0]['sha256'], artifacts.sha256(self.source))
        for name in ('scene.png', 'scene.pse', 'scene.pml', 'report.json', 'interface.csv', 'inputs/input.pdb'):
            self.assertGreater((self.out / name).stat().st_size, 0)
        self.assert_visible_png(self.out / 'scene.png')
        result = report['analysis']
        self.assertEqual((result['partner_a']['selected_atom_count'], result['partner_b']['selected_atom_count']), (2, 1))
        self.assertAlmostEqual(result['interface_area_angstrom2'], 2 * math.pi * 3.1 * (3.1 - 1.5), delta=2)
        self.assertEqual(len(result['residues']), 3)
        self.assertTrue(any(abs(row['buried_area_angstrom2']) < 0.001 for row in result['residues']))
        with (self.out / 'interface.csv').open(encoding='utf-8', newline='') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), len(result['residues']))
        for row, scientific in zip(rows, result['residues']):
            self.assertEqual(set(row), set(scientific))
            for key, value in scientific.items():
                self.assertEqual(float(row[key]) if isinstance(value, (float, int)) else row[key], value)
        moved = self.base / "moved 中文';$()"
        shutil.move(str(self.out), moved)
        self.assertNotIn('import molscene', (moved / 'scene.pml').read_text(encoding='utf-8'))
        views = []
        images = []
        from PIL import Image, ImageChops
        for replay in (False, True):
            cmd = self.restore(moved, replay)
            self.assertEqual({a.name for a in cmd.get_model('interface_a').atom}, {'C1', 'C2'})
            self.assertEqual({a.name for a in cmd.get_model('interface_b').atom}, {'C3'})
            self.assertEqual(cmd.count_atoms('(rep spheres or rep sticks or rep cartoon) and not (interface_a or interface_b)'), 0)
            self.assertEqual(cmd.count_atoms('interface_a and rep spheres'), 2)
            self.assertEqual(cmd.count_atoms('interface_b and rep spheres'), 1)
            views.append(list(cmd.get_view()))
            image = self.base / ('replay-' + str(replay) + '.png')
            cmd.png(str(image), width=160, height=120, ray=1, quiet=1)
            with Image.open(image) as im:
                images.append(im.convert('RGB').copy())
        for a, b in zip(*views):
            self.assertAlmostEqual(a, b, places=5)
        self.assertIsNone(ImageChops.difference(*images).getbbox())
        standalone = '''import importlib.abc, os, sys
class NoMolScene(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname.startswith(('molscene', 'PyQt', 'PySide')):
   raise ImportError('MolScene and Qt forbidden in standalone replay')
sys.meta_path.insert(0, NoMolScene())
import pymol2
with pymol2.PyMOL() as pm:
 os.chdir(sys.argv[1])
 pm.cmd.do('@scene.pml', echo=0)
 assert pm.cmd.count_atoms('interface_a') == 2
 assert pm.cmd.count_atoms('interface_b') == 1
 assert pm.cmd.count_atoms('rep spheres') == 3
 pm.cmd.png(sys.argv[2], width=160, height=120, ray=1, quiet=1)
'''
        standalone_png = self.base / 'standalone.png'
        replayed = subprocess.run([sys.executable, '-B', '-c', standalone, str(moved), str(standalone_png)],
                                  cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(replayed.returncode, 0, replayed.stderr + replayed.stdout)
        self.assert_visible_png(standalone_png)
        self.assertEqual(self.source.read_bytes(), source_bytes)

    def test_all_alternates_requested_state_and_figures_agree(self):
        report = self.run_engine(altloc='all', state=2)
        self.assertEqual(report['analysis']['parameters']['context_atom_count'], 5)
        for replay in (False, True):
            cmd = self.restore(self.out, replay)
            self.assertEqual(cmd.get_state(), 2)
            self.assertEqual({a.name for a in cmd.get_model('interface_a', state=2).atom}, {'C1', 'C2', 'C4'})
            self.assertEqual({a.name for a in cmd.get_model('interface_b', state=2).atom}, {'C3', 'C5'})
            self.assertEqual(cmd.count_atoms('rep spheres'), 5)

    def test_requested_state_spatial_membership_and_no_singleton_fallback(self):
        report = engine.interface(self.source, self.out, selection_a='chain A and x < 10',
            selection_b='chain B and x > 30', state=2, width=160, height=120)
        self.assertEqual(report['analysis']['partner_b']['selected_atom_count'], 1)
        cmd = self.restore(self.out, True)
        self.assertEqual({a.name for a in cmd.get_model('interface_b', state=2).atom}, {'C3'})
        with self.assertRaises(ValueError):
            self.run_engine(state=3, overwrite=True)

    def test_validation_collisions_and_failed_overwrite_leave_sources_intact(self):
        before = self.source.read_bytes()
        for options in ({'altloc': 'bad'}, {'state': 0}, {'width': 0}, {'selection_a': ''}):
            args = dict(selection_a='chain A', selection_b='chain B', width=160, height=120)
            args.update(options)
            with self.subTest(options=options), self.assertRaises(ValueError):
                engine.interface(self.source, self.out, **args)
            self.assertFalse(self.out.exists())
        with self.assertRaisesRegex(ValueError, 'contain an input'):
            engine.interface(self.source, self.base, selection_a='chain A', selection_b='chain B', overwrite=True)
        self.run_engine()
        original = {p.relative_to(self.out): p.read_bytes() for p in self.out.rglob('*') if p.is_file()}
        with self.assertRaises(ValueError):
            self.run_engine()
        with self.assertRaises(ValueError):
            engine.interface(self.source, self.out, selection_a='all', selection_b='all', overwrite=True)
        self.assertEqual(original, {p.relative_to(self.out): p.read_bytes() for p in self.out.rglob('*') if p.is_file()})
        self.assertEqual(before, self.source.read_bytes())

    def test_owned_interface_table_replacement_and_unrelated_file_retained(self):
        self.out.mkdir()
        outside = self.base / 'outside.csv'
        outside.write_text('outside', encoding='utf-8')
        (self.out / 'keep.txt').write_text('keep', encoding='utf-8')
        try:
            os.link(outside, self.out / 'interface.csv')
        except OSError:
            # exFAT has no hardlinks. Still exercise owned replacement/type safety.
            (self.out / 'interface.csv').write_text('old', encoding='utf-8')
        self.run_engine(overwrite=True)
        self.assertEqual(outside.read_text(encoding='utf-8'), 'outside')
        self.assertEqual((self.out / 'keep.txt').read_text(encoding='utf-8'), 'keep')
        self.assertIn('partner', (self.out / 'interface.csv').read_text(encoding='utf-8'))
        (self.out / 'interface.csv').unlink()
        (self.out / 'interface.csv').mkdir()
        with self.assertRaisesRegex(ValueError, 'wrong file type'):
            self.run_engine(overwrite=True)

    def test_staged_commit_rechecks_interface_table_destination_before_replacement(self):
        original = artifacts.write_json
        def obstruct(path, value):
            original(path, value)
            self.out.mkdir()
            (self.out / 'interface.csv').mkdir()
            (self.out / 'keep.txt').write_text('keep', encoding='utf-8')
        with patch.object(artifacts, 'write_json', side_effect=obstruct):
            with self.assertRaisesRegex(ValueError, 'wrong file type'):
                self.run_engine(overwrite=True)
        self.assertEqual({p.name for p in self.out.iterdir()}, {'interface.csv', 'keep.txt'})
        self.assertEqual(list(self.base.glob('.molscene-*')), [])

    def test_signed_analysis_fields_are_not_clamped_in_report_or_csv(self):
        real = engine.science.interface
        def signed(*args, **kwargs):
            result = real(*args, **kwargs)
            result['residues'][0]['buried_area_angstrom2'] = -0.125
            result['partner_a']['buried_area_angstrom2'] = -0.125
            result['total_buried_area_angstrom2'] = -0.25
            result['interface_area_angstrom2'] = -0.125
            return result
        # Inject only the numeric backend boundary; exporter must preserve raw data.
        with patch.object(engine.science, 'interface', side_effect=signed):
            report = self.run_engine()
        self.assertEqual(report['analysis']['interface_area_angstrom2'], -0.125)
        with (self.out / 'interface.csv').open(encoding='utf-8', newline='') as handle:
            self.assertEqual(float(next(csv.DictReader(handle))['buried_area_angstrom2']), -0.125)

    def test_real_1hsg_dimer_cli_complete_table_and_colored_protein_replay(self):
        result = subprocess.run([sys.executable, '-B', '-m', 'molscene', 'interface',
            str(ROOT / 'examples/data/1hsg.pdb'), '--selection-a', 'chain A and polymer',
            '--selection-b', 'chain B and polymer', '--output', str(self.out),
            '--width', '160', '--height', '120'], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((self.out / 'report.json').read_text(encoding='utf-8'))
        self.assertGreater(report['analysis']['interface_area_angstrom2'], 100)
        self.assertEqual(len(report['analysis']['residues']), 198)
        self.assert_visible_png(self.out / 'scene.png')
        for replay in (False, True):
            cmd = self.restore(self.out, replay)
            for partner in ('a', 'b'):
                count = report['analysis']['partner_' + partner]['selected_atom_count']
                self.assertEqual(cmd.count_atoms('interface_' + partner), count)
                self.assertEqual(cmd.count_atoms('interface_' + partner + ' and rep cartoon'), count)
                colors = []
                cmd.iterate('interface_' + partner, 'colors.append(color)', space={'colors': colors})
                palette = ('marine', 'cyan') if partner == 'a' else ('orange', 'yelloworange')
                self.assertTrue(set(colors) <= {cmd.get_color_index(color) for color in palette})
            self.assertGreater(cmd.count_atoms('interface_buried and rep sticks'), 0)
            self.assertEqual(cmd.count_atoms('interface_buried and not (interface_a or interface_b)'), 0)

    def test_isolated_and_disconnected_cartoon_guides_remain_visible(self):
        from PIL import Image, ImageChops
        cases = (('single', '1', 1), ('disconnected', '1+50+99', 3),
                 ('short_fragments', '1+2+50+51+98+99', 6))
        source = ROOT / 'examples/data/1hsg.pdb'
        original = source.read_bytes()
        for label, residues, count in cases:
            with self.subTest(case=label):
                output = self.base / label
                report = engine.interface(source, output,
                    selection_a='chain A and resi ' + residues + ' and name CA',
                    selection_b='chain B and resi ' + residues + ' and name CA', width=160, height=120)
                self.assertEqual(report['analysis']['partner_a']['selected_atom_count'], count)
                self.assertEqual(report['analysis']['partner_b']['selected_atom_count'], count)
                self.assert_visible_png(output / 'scene.png')
                images = []
                for replay in (False, True):
                    cmd = self.restore(output, replay)
                    for partner in ('a', 'b'):
                        self.assertEqual(cmd.count_atoms('interface_' + partner + ' and rep spheres'), count)
                    self.assertEqual(cmd.count_atoms('(rep spheres or rep sticks or rep cartoon) and not (interface_a or interface_b)'), 0)
                    png = self.base / (label + '-' + str(replay) + '.png')
                    cmd.png(str(png), width=160, height=120, ray=1, quiet=1)
                    self.assert_visible_png(png)
                    with Image.open(png) as image:
                        images.append(image.convert('RGB').copy())
                self.assertIsNone(ImageChops.difference(*images).getbbox())
        self.assertEqual(source.read_bytes(), original)

    def test_protein_partners_without_cartoon_guides_are_visible(self):
        engine.interface(ROOT / 'examples/data/1hsg.pdb', self.out,
            selection_a='chain A and resi 1 and name CB',
            selection_b='chain B and resi 1 and name CB', width=160, height=120)
        self.assert_visible_png(self.out / 'scene.png')
        for replay in (False, True):
            cmd = self.restore(self.out, replay)
            self.assertEqual(cmd.count_atoms('(interface_a or interface_b) and rep spheres'), 2)

    def test_snapshot_worker_provenance_staleness_and_preflight(self):
        import pymol2
        from molscene.desktop.controller import WorkbenchController
        from molscene.snapshot import scene_fingerprint, record_snapshot_status
        with pymol2.PyMOL() as pm:
            pm.cmd.load(str(self.source), 'live')
            controller = WorkbenchController(pm.cmd, self.base)
            parameters = dict(selection_a='chain A', selection_b='chain B', state=2, altloc='primary')
            fingerprint = scene_fingerprint(pm.cmd, 2)
            for bad in (dict(parameters, selection_b='none'), dict(parameters, state=1)):
                with self.assertRaises(ValueError):
                    controller.prepare_job('interface', bad, state=2)
            self.assertFalse((self.base / '.molscene-jobs').exists())
            path = controller.prepare_job('interface', parameters, state=2)
            job = json.loads(path.read_text(encoding='utf-8'))
            snapshot_hash = artifacts.sha256(job['input_session'])
            result = subprocess.run([sys.executable, '-B', '-m', 'molscene.worker', str(path)],
                                    cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            stored = json.loads(Path(job['result_path']).read_text(encoding='utf-8'))['result']
            self.assertEqual(stored['kind'], 'interface')
            self.assertEqual(stored['source_fingerprint'], fingerprint)
            self.assertEqual(stored['snapshot_id'], snapshot_hash)
            self.assertEqual(stored['state'], 2)
            self.assertEqual(len(stored['residues']), 3)
            self.assertEqual(record_snapshot_status(pm.cmd, stored, state=2), 'current')
            pm.cmd.translate([1, 0, 0], 'live', state=2)
            self.assertEqual(record_snapshot_status(pm.cmd, stored, state=2), 'stale')
            self.assertEqual(artifacts.sha256(job['input_session']), snapshot_hash)

    def test_controller_preflight_rejects_boolean_request_state_before_snapshot(self):
        import pymol2
        from molscene.desktop.controller import WorkbenchController
        with pymol2.PyMOL() as pm:
            pm.cmd.load(str(self.source), 'live')
            controller = WorkbenchController(pm.cmd, self.base)
            with self.assertRaises(ValueError):
                controller.prepare_job('interface', dict(selection_a='chain A', selection_b='chain B', state=True), state=1)
            self.assertFalse((self.base / '.molscene-jobs').exists())

    def test_worker_rejects_bad_parameters_and_result_source_collisions(self):
        import pymol2
        session = self.base / 'snapshot.pse'
        with pymol2.PyMOL() as pm:
            pm.cmd.load(str(self.source), 'live')
            pm.cmd.save(str(session))
        original = session.read_bytes()
        jobpath = self.base / 'job.json'
        resultpath = self.base / 'result.json'
        for parameters, output in ((dict(selection_a='chain A', selection_b='chain B', bogus=True), resultpath),
                (dict(selection_a='chain A', selection_b='chain B', state=3), resultpath),
                (dict(selection_a='chain A', selection_b='chain B'), session)):
            jobpath.write_text(json.dumps(dict(schema_version=1, operation='interface',
                input_session=str(session), output=str(output), parameters=parameters)), encoding='utf-8')
            result = subprocess.run([sys.executable, '-B', '-m', 'molscene.worker', str(jobpath)],
                                    cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            if output == resultpath:
                envelope = json.loads(resultpath.read_text(encoding='utf-8'))
                self.assertFalse(envelope['passed'])
                self.assertIn('parameters' if 'bogus' in parameters else 'state', envelope['error'])
                resultpath.unlink()
            self.assertEqual(session.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
