"""State, portable artifacts and real CLI workflows for the v0.2 engine."""
import csv
from contextlib import redirect_stdout, redirect_stderr
import hashlib
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

from molscene import engine

ROOT = Path(__file__).resolve().parents[1]
HAS_PYMOL = importlib.util.find_spec('pymol2') is not None


@unittest.skipUnless(HAS_PYMOL, 'requires PyMOL')
class V02EngineTests(unittest.TestCase):
    def setUp(self):
        import pymol2
        self.tmp = tempfile.TemporaryDirectory(prefix='engine-v02-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / "原始 ';$().pdb"
        self.out = self.base / 'out'
        with pymol2.PyMOL() as pm:
            for i, pos in enumerate(((0, 0, 0), (3, 0, 0), (3, 4, 0), (3, 4, 2)), 1):
                pm.cmd.pseudoatom('points', pos=pos, elem='C', name='CA',
                                  resn='ALA', resi=str(i), b=10 * i)
            pm.cmd.load_coordset([[0, 0, 0], [8, 0, 0], [2, 0, 0], [2, 4, 2]], 'points', state=2)
            pm.cmd.save(str(self.source), 'points', state=0)

    def restore(self, replay=False, directory=None):
        import pymol2
        directory = directory or self.out
        pm = pymol2.PyMOL()
        pm.start()
        self.addCleanup(pm.stop)
        if replay:
            old = Path.cwd()
            log = io.StringIO()
            try:
                os.chdir(directory)
                with redirect_stdout(log), redirect_stderr(log):
                    pm.cmd.do('@scene.pml', echo=0)
            finally:
                os.chdir(old)
            self.assertNotIn('Error', log.getvalue())
        else:
            pm.cmd.load(str(directory / 'scene.pse'))
        return pm.cmd

    def test_geometry_report_annotation_session_and_replay_use_requested_state(self):
        report = engine.angle(self.source, self.out, selection_a='index 1',
            selection_b='index 2', selection_c='index 3', width=200, height=160, state=1)
        self.assertEqual(report['schema_version'], 2)
        self.assertAlmostEqual(report['analysis']['value'], 90, places=4)
        report = engine.dihedral(self.source, self.out, selection_a='index 1', selection_b='index 2',
            selection_c='index 3', selection_d='index 4', width=200, height=160, overwrite=True)
        self.assertAlmostEqual(report['analysis']['value'], 90, places=4)
        # Spatial predicate selects atom 3 only in state 2; state 1 selects atom 2.
        report = engine.measure(self.source, self.out, selection_a='index 1',
            selection_b='(all within 2.1 of index 1) and not index 1', state=2,
            width=200, height=160, overwrite=True)
        self.assertEqual(report['analysis']['distance_angstrom'], 2)
        self.assertEqual(report['analysis']['atoms'][1]['index'], 3)
        self.assertEqual(report['analysis']['state'], 2)
        for replay in (False, True):
            cmd = self.restore(replay)
            self.assertEqual(cmd.get_state(), 2)
            # Measurement object raw coordinates must be those of frame 2.
            measured = cmd.get_session('measurement')['names'][0][5][2][1][1]
            self.assertAlmostEqual(math.dist(measured[:3], measured[3:6]), 2)
            self.assertAlmostEqual(cmd.get_distance('selection_a', 'selection_b', state=2), 2)
        before = (self.out / 'report.json').read_bytes()
        with self.assertRaises(ValueError):
            engine.measure(self.source, self.out, selection_a='index 1', selection_b='index 2',
                           state=3, overwrite=True, width=200, height=160)
        self.assertEqual((self.out / 'report.json').read_bytes(), before)

    def test_generic_render_styling_portable_replay_and_transparent_alpha(self):
        report = engine.render(self.source, self.out, representation='spheres', selection='index 1 or index 3',
            coloring='bfactor', background='transparent', state=2, width=200, height=160)
        self.assertEqual(report['analysis']['coloring']['range'], [10.0, 30.0])
        self.assertEqual(report['inputs'][0]['sha256'], hashlib.sha256(self.source.read_bytes()).hexdigest())
        from PIL import Image
        with Image.open(self.out / 'scene.png') as image:
            self.assertEqual(image.mode, 'RGBA')
            self.assertEqual(image.getchannel('A').getextrema(), (0, 255))
        moved = self.base / "搬迁 ';$()"
        shutil.move(self.out, moved)
        self.source.unlink()
        for replay in (False, True):
            cmd = self.restore(replay, moved)
            self.assertEqual(cmd.get_state(), 2)
            self.assertEqual(cmd.count_atoms('rep spheres'), 2)
            self.assertEqual(cmd.count_atoms('rep cartoon'), 0)

    def test_explicit_render_nonprotein_and_unrenderable_selection(self):
        self.source.write_text('HETATM    1  C   LIG A   1       0.000   0.000   0.000  1.00 20.00           C\nEND\n')
        report = engine.render(self.source, self.out, representation='sticks', width=200, height=160)
        self.assertEqual(report['structure']['molecule']['atoms'], 1)
        for options in ({'representation': 'cartoon'}, {'representation': 'ribbon'},
                        {'representation': 'spheres', 'selection': 'none'},
                        {'representation': 'spheres', 'style': 'pocket', 'ligand': 'all'}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                engine.render(self.source, self.out, overwrite=True, width=200, height=160, **options)

    def test_contacts_sasa_inspect_complete_portable_tables(self):
        report = engine.contacts(self.source, self.out, selection_a='all', selection_b='all',
                                 cutoff=5, width=200, height=160)
        with (self.out / 'contacts.csv').open(encoding='utf-8', newline='') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), report['analysis']['pair_count'])
        self.assertEqual(len(rows), 5)
        self.assertIn('atom_a_object', rows[0])
        self.assertEqual(report['analysis']['displayed_pair_count'], 5)
        report = engine.sasa(self.source, self.out, overwrite=True, width=200, height=160)
        with (self.out / 'sasa.csv').open(encoding='utf-8', newline='') as handle:
            rows = list(csv.DictReader(handle))
        self.assertAlmostEqual(sum(float(row['area_angstrom2']) for row in rows),
                               report['analysis']['total_area_angstrom2'])
        report = engine.inspect(self.source, self.base / 'inspect', state=2)
        self.assertEqual(report['analysis']['atom_count'], 4)
        self.assertEqual(report['analysis']['kind'], 'inspect')
        self.assertFalse((self.base / 'inspect' / 'scene.png').exists())
        self.assertIn('AAAA', (self.base / 'inspect' / 'sequence.fasta').read_text(encoding='utf-8'))
        with (self.base / 'inspect' / 'chains.csv').open(encoding='utf-8', newline='') as handle:
            self.assertEqual(len(list(csv.DictReader(handle))), 1)
        json.dumps(report, allow_nan=False)

    def test_nonprotein_analysis_figures_show_molecules_in_png_session_and_replay(self):
        from PIL import Image
        # A partial nucleotide has no continuous cartoon trace: sticks must remain visible.
        self.source.write_text(
            "ATOM      1    P  DA A   1       0.000   0.000   0.000  1.00 20.00           P\n"
            "ATOM      2  O5'  DA A   1       1.500   0.000   0.000  1.00 20.00           O\n"
            "ATOM      3  C5'  DA A   1       2.500   1.000   0.000  1.00 20.00           C\n"
            "ATOM      4  C4'  DA A   1       3.500   0.000   0.000  1.00 20.00           C\n"
            "ATOM      5  C3'  DA A   1       4.500   1.000   0.000  1.00 20.00           C\n"
            "ATOM      6  O3'  DA A   1       5.500   0.000   0.000  1.00 20.00           O\nEND\n")
        for operation, parameters in (
                ('sasa', {}),
                ('contacts', dict(selection_a='index 1', selection_b='index 6', cutoff=0.1)),
                ('angle', dict(selection_a='index 1', selection_b='index 2', selection_c='index 3')),
                ('dihedral', dict(selection_a='index 1', selection_b='index 2',
                                  selection_c='index 3', selection_d='index 4'))):
            with self.subTest(operation=operation):
                report = getattr(engine, operation)(self.source, self.out, overwrite=True,
                                                    width=120, height=100, **parameters)
                if operation == 'sasa':
                    self.assertAlmostEqual(report['analysis']['total_area_angstrom2'], 240.43865489959717, places=4)
                if operation == 'contacts':
                    self.assertEqual(report['analysis']['pair_count'], 0)
                with Image.open(self.out / 'scene.png') as image:
                    self.assertGreater(len(image.convert('RGB').getcolors(12000) or []), 20)
                for replay in (False, True):
                    cmd = self.restore(replay)
                    self.assertEqual(cmd.count_atoms('polymer.nucleic'), 6)
                    self.assertEqual(cmd.count_atoms('molecule and (rep sticks or rep spheres or rep lines)'), 6)
                    replay_image = self.base / 'restored.png'
                    cmd.png(str(replay_image), width=120, height=100, ray=1, quiet=1)
                    with Image.open(replay_image) as image:
                        self.assertGreater(len(image.convert('RGB').getcolors(12000) or []), 20)
        # Legacy protein overview rejection is still the default render contract.
        with self.assertRaisesRegex(ValueError, 'protein'):
            engine.render(self.source, self.out, overwrite=True, width=120, height=100)

    def test_export_preserves_view_names_and_labels_old_snapshot_records(self):
        import pymol2
        from molscene.snapshot import scene_fingerprint
        session = self.base / "现场 ';$().pse"
        with pymol2.PyMOL() as pm:
            pm.cmd.load(str(self.source), 'original_name')
            pm.cmd.show('spheres', 'index 2')
            pm.cmd.frame(2)
            pm.cmd.orient()
            pm.cmd.turn('y', 37)
            view = list(pm.cmd.get_view())
            fingerprint = scene_fingerprint(pm.cmd, state=2)
            pm.cmd.save(str(session))
        result = engine.export_session(session, self.out, width=200, height=160,
            background='transparent', analysis_records=[
                {'state': 2, 'source_fingerprint': 'old'},
                {'state': 2, 'source_fingerprint': fingerprint},
                {'state': 1, 'source_fingerprint': fingerprint},
                {'snapshot_id': 'old'}])
        self.assertEqual(result['analysis_records'][0]['snapshot_status'], 'stale')
        self.assertEqual(result['analysis_records'][1]['snapshot_status'], 'current')
        self.assertEqual(result['analysis_records'][2]['snapshot_status'], 'stale')
        self.assertEqual(result['analysis_records'][3]['snapshot_status'], 'unverified')
        from PIL import Image
        with Image.open(self.out / 'scene.png') as image:
            self.assertEqual(image.size, (200, 160))
            self.assertEqual(image.getchannel('A').getextrema(), (0, 255))
        for replay in (False, True):
            cmd = self.restore(replay)
            self.assertEqual(cmd.get_names('objects'), ['original_name'])
            self.assertEqual(cmd.get_state(), 2)
            for got, want in zip(cmd.get_view(), view):
                self.assertAlmostEqual(got, want, places=5)
        with self.assertRaises(ValueError):
            engine.export_session(session, self.base, overwrite=True)
        self.assertTrue(session.exists())

    def test_owned_csv_wrong_type_rejected_without_replacing_old_report(self):
        self.out.mkdir()
        (self.out / 'contacts.csv').mkdir()
        (self.out / 'report.json').write_text('keep')
        with self.assertRaises(ValueError):
            engine.contacts(self.source, self.out, selection_a='all', selection_b='all', overwrite=True)
        self.assertEqual((self.out / 'report.json').read_text(), 'keep')

    def test_all_representations_bind_spatial_membership_and_replay_colors(self):
        from molscene import styles
        self.source.write_text(self.source.read_text(encoding='utf-8').replace('HETATM', 'ATOM  '), encoding='utf-8')
        for index, representation in enumerate(styles.REPRESENTATIONS):
            report = engine.render(self.source, self.out, representation=representation,
                selection='all within 2.1 of index 1', coloring='single', color='salmon',
                state=2, overwrite=True, width=120, height=100)
            for replay in (False, True):
                cmd = self.restore(replay)
                self.assertEqual(cmd.count_atoms('rep ' + representation), 2)
                self.assertEqual(cmd.count_atoms('_molscene_display and index 2'), 0)
                colors = []
                cmd.iterate('_molscene_display', 'colors.append(color)', space={'colors': colors})
                self.assertEqual(set(colors), {cmd.get_color_index('salmon')})

    def test_polymer_without_guide_atoms_is_rejected_by_explicit_cartoon(self):
        self.source.write_text('ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00 20.00           N\nEND\n')
        for representation in ('cartoon', 'ribbon'):
            with self.assertRaisesRegex(ValueError, 'polymer|guide|renderable'):
                engine.render(self.source, self.out, representation=representation, width=120, height=100)

    def test_contacts_export_all_pairs_but_draw_at_most_200_and_accept_zero(self):
        import pymol2
        with pymol2.PyMOL() as pm:
            for i in range(22):
                pm.cmd.pseudoatom('cloud', pos=(i * 0.1, 0, 0), elem='C', name='C', resi=str(i + 1))
            pm.cmd.save(str(self.source))
        report = engine.contacts(self.source, self.out, selection_a='all', selection_b='all',
                                 width=120, height=100)
        self.assertEqual(report['analysis']['pair_count'], 231)
        self.assertEqual(report['analysis']['displayed_pair_count'], 200)
        with (self.out / 'contacts.csv').open(encoding='utf-8', newline='') as handle:
            self.assertEqual(len(list(csv.DictReader(handle))), 231)
        cmd = self.restore()
        coords = cmd.get_session('contacts')['names'][0][5][2][0][1]
        self.assertEqual(len(coords) // 6, 200)
        report = engine.contacts(self.source, self.base / 'zero', selection_a='index 1',
            selection_b='index 22', cutoff=0.01, width=120, height=100)
        self.assertEqual(report['analysis']['pair_count'], 0)
        with (self.base / 'zero' / 'contacts.csv').open(encoding='utf-8', newline='') as handle:
            self.assertEqual(list(csv.DictReader(handle)), [])

    def test_alignment_super_explicit_states_survive_replay_and_invalid_state_preserves_output(self):
        import pymol2
        self.source.write_text(self.source.read_text(encoding='utf-8').replace('HETATM', 'ATOM  '), encoding='utf-8')
        target = self.base / 'target.pdb'
        with pymol2.PyMOL() as pm:
            pm.cmd.load(str(self.source), 'target')
            pm.cmd.alter_state(1, 'target', 'x=x+10')
            pm.cmd.save(str(target), 'target', state=0)
        report = engine.align(self.source, target, self.out, method='super', mobile_state=1,
            target_state=1, cutoff=3, cycles=0, width=120, height=100)
        self.assertLess(report['analysis']['rmsd_angstrom'], 1e-4)
        self.assertEqual(report['analysis']['method'], 'super')
        for replay in (False, True):
            cmd = self.restore(replay)
            for left, right in zip(cmd.get_model('mobile', state=1).atom, cmd.get_model('target', state=1).atom):
                self.assertLess(math.dist(left.coord, right.coord), 1e-4)
        before = (self.out / 'report.json').read_bytes()
        with self.assertRaises(ValueError):
            engine.align(self.source, target, self.out, mobile_state=3, overwrite=True)
        self.assertEqual((self.out / 'report.json').read_bytes(), before)

    def test_semantic_fingerprint_ignores_camera_and_changes_with_source_coordinates(self):
        import pymol2
        from molscene.snapshot import scene_fingerprint
        with pymol2.PyMOL() as pm:
            pm.cmd.load(str(self.source), 'molecule')
            first = scene_fingerprint(pm.cmd, 1)
            pm.cmd.turn('x', 90)
            pm.cmd.zoom('all', 8)
            pm.cmd.color('red')
            self.assertEqual(scene_fingerprint(pm.cmd, 1), first)
            self.assertNotEqual(scene_fingerprint(pm.cmd, 2), first)
            pm.cmd.alter_state(1, 'index 1', 'x=x+1')
            self.assertNotEqual(scene_fingerprint(pm.cmd, 1), first)

    def test_linked_owned_table_and_output_parent_are_rejected(self):
        self.out.mkdir()
        outside = self.base / 'outside.csv'
        outside.write_text('keep')
        try:
            (self.out / 'contacts.csv').symlink_to(outside)
        except OSError as exc:
            self.skipTest('Filesystem cannot create symlinks: ' + str(exc))
        with self.assertRaisesRegex(ValueError, 'link|reparse'):
            engine.contacts(self.source, self.out, selection_a='all', selection_b='all', overwrite=True)
        self.assertEqual(outside.read_text(), 'keep')
        linked = self.base / 'linked'
        linked.symlink_to(self.out, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'link|reparse'):
            engine.inspect(self.source, linked / 'new')

    @unittest.skipUnless(os.name == 'nt', 'Windows junctions require Windows')
    def test_junction_output_is_rejected_lexically(self):
        target = self.base / 'outside'
        target.mkdir()
        junction = self.base / 'junction'
        # Arguments are passed as data through PowerShell's parameter binding.
        script = 'param($link,$target); New-Item -ItemType Junction -Path $link -Value $target -ErrorAction Stop | Out-Null'
        created = subprocess.run(['powershell', '-NoProfile', '-Command', '& { ' + script + ' }',
            str(junction), str(target)], capture_output=True, text=True)
        if created.returncode:
            self.skipTest('Filesystem cannot create junctions')
        try:
            with self.assertRaisesRegex(ValueError, 'link|reparse'):
                engine.inspect(self.source, junction / 'output')
            self.assertEqual(list(target.iterdir()), [])
        finally:
            os.rmdir(junction)

    def test_real_new_cli_workflows(self):
        commands = [
            ['render', '--representation', 'spheres', '--coloring', 'element', '--state', '2'],
            ['angle', '--selection-a', 'index 1', '--selection-b', 'index 2', '--selection-c', 'index 3'],
            ['dihedral', '--selection-a', 'index 1', '--selection-b', 'index 2', '--selection-c', 'index 3', '--selection-d', 'index 4'],
            ['contacts', '--selection-a', 'all', '--selection-b', 'all'],
            ['sasa'], ['inspect'],
        ]
        for index, args in enumerate(commands):
            output = self.base / ('cli-' + str(index))
            dimensions = [] if args[0] == 'inspect' else ['--width', '200', '--height', '160']
            result = subprocess.run([sys.executable, '-B', '-m', 'molscene', args[0], str(self.source),
                '--output', str(output), *args[1:], *dimensions], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((output / 'report.json').read_text(encoding='utf-8'))['schema_version'], 2)

    def test_real_cli_alignment_super_states_and_batch(self):
        self.source.write_text(self.source.read_text(encoding='utf-8').replace('HETATM', 'ATOM  '), encoding='utf-8')
        for method in ('align', 'super'):
            output = self.base / method
            result = subprocess.run([sys.executable, '-B', '-m', 'molscene', 'align',
                str(self.source), str(self.source), '--output', str(output), '--method', method,
                '--mobile-state', '1', '--target-state', '2', '--cycles', '0',
                '--width', '120', '--height', '100'], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['analysis']['mobile_state'], 1)
            self.assertEqual(report['analysis']['target_state'], 2)
            cmd = self.restore(directory=output)
            self.assertEqual(cmd.get_setting_int('state', 'mobile'), 1)
            self.assertEqual(cmd.get_setting_int('state', 'target'), 2)
        inputs = self.base / 'batch-inputs'
        inputs.mkdir()
        shutil.copyfile(self.source, inputs / 'one.pdb')
        shutil.copyfile(self.source, inputs / 'two.pdb')
        output = self.base / 'batch-output'
        result = subprocess.run([sys.executable, '-B', '-m', 'molscene', 'batch', str(inputs),
            '--output', str(output), '--representation', 'spheres', '--state', '2',
            '--width', '120', '--height', '100'], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((output / 'batch.json').read_text(encoding='utf-8'))
        self.assertEqual(report['completed'], 2)
        for item in report['items']:
            self.assertTrue((output / item['report']).is_file())

