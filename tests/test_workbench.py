"""Workbench scientific behavior and real Qt process integration."""
import csv
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HAS_PYMOL = importlib.util.find_spec('pymol2') is not None
HAS_QT = any(importlib.util.find_spec(name) is not None for name in ('PySide6', 'PySide2', 'PyQt6', 'PyQt5'))


@unittest.skipUnless(HAS_PYMOL, 'requires the user-provided PyMOL interpreter')
class WorkbenchControllerTests(unittest.TestCase):
    def setUp(self):
        from pymol2 import PyMOL
        from molscene.desktop.controller import WorkbenchController
        self.runtime = PyMOL()
        self.runtime.start()
        self.addCleanup(self.runtime.stop)
        self.cmd = self.runtime.cmd
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.controller = WorkbenchController(self.cmd, self.root)
        for name, point in [('a', [0, 0, 0]), ('atom_b', [3, 0, 0]), ('c', [3, 4, 0]), ('d', [3, 4, 5])]:
            self.cmd.pseudoatom(name, pos=point, name='CA', resn='ALA', resi='1')

    def test_live_geometry_records_requested_state_and_actual_annotation(self):
        result = self.controller.geometry('angle', ['a', 'atom_b', 'c'], state=1, annotate=True)
        self.assertAlmostEqual(result['value'], 90.)
        self.assertEqual(result['units'], 'degree')
        self.assertEqual(result['parameters']['selections'], ['a', 'atom_b', 'c'])
        self.assertEqual(len(self.controller.results), 1)
        self.assertEqual(self.cmd.get_type(result['annotation']), 'object:measurement')
        self.assertIn('calculated_at', result)
        self.assertEqual(result['snapshot_status'], 'current')
        self.assertEqual(len(result['source_fingerprint']), 64)
        with self.assertRaises(ValueError):
            self.controller.geometry('distance', ['a', 'all'], state=1)
        self.assertEqual(len(self.controller.results), 1)

    def test_inspection_has_full_rows_and_safe_csv_fasta_exports(self):
        self.cmd.fragment('ala', 'peptide_fixture')
        result = self.controller.inspect('peptide_fixture', state=1)
        self.assertEqual(result['atom_count'], 10)
        table = self.root / '中文 summary;$(x).csv'
        self.controller.export_table(result, 'chains', table)
        with table.open(encoding='utf-8', newline='') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]['object'], 'peptide_fixture')
        fasta = self.root / 'sequence.fasta'
        self.controller.export_fasta(result, fasta)
        self.assertIn('\nA\n', fasta.read_text())
        with self.assertRaisesRegex(ValueError, 'overwrite'):
            self.controller.export_fasta(result, fasta)
        self.assertIn('\nA\n', fasta.read_text())

    def test_alignment_transforms_live_mobile_and_has_explicit_states(self):
        self.cmd.delete('all')
        self.cmd.load(str(ROOT / 'examples/data/1ubq.pdb'), 'target')
        self.cmd.create('mobile', 'target')
        self.cmd.translate([4, 5, 6], 'mobile', state=1)
        before = self.cmd.get_atom_coords('mobile and name CA and resi 1', state=1)
        result = self.controller.align('mobile', 'target', algorithm='super', selection='all',
                                       mobile_state=1, target_state=1, cycles=0, cutoff=2.)
        after = self.cmd.get_atom_coords('mobile and name CA and resi 1', state=1)
        self.assertGreater(sum((a-b)**2 for a,b in zip(before, after)), 1.)
        self.assertAlmostEqual(result['rmsd_angstrom'], 0, places=4)
        self.assertGreater(result['aligned_atoms'], 0)
        self.assertEqual(result['parameters']['mobile_state'], 1)
        with self.assertRaises(ValueError):
            self.controller.align('mobile', 'target', mobile_state=2, target_state=1)

    def test_alignment_tracks_both_states_in_live_and_exported_records(self):
        from molscene import engine
        self.cmd.delete('all')
        self.cmd.load(str(ROOT / 'examples/data/1ubq.pdb'), 'target')
        self.cmd.create('target', 'target', source_state=1, target_state=2)
        self.cmd.create('mobile', 'target', source_state=0, target_state=0)
        self.cmd.translate([4, 5, 6], 'mobile', state=1)
        result = self.controller.align('mobile', 'target', mobile_state=1, target_state=2)
        self.assertEqual(self.controller.result_status(result), 'current')
        self.cmd.turn('y', 37)
        self.cmd.color('red', 'target')
        self.assertEqual(self.controller.result_status(result), 'current')
        self.cmd.frame(2)
        self.assertEqual(self.controller.result_status(result), 'current')
        self.cmd.frame(1)
        session = self.root / 'alignment.pse'

        def exported_status():
            self.cmd.save(str(session))
            report = engine.export_session(session, self.root / 'export', overwrite=True,
                width=120, height=100, analysis_records=[result])
            return report['analysis_records'][0]['snapshot_status']

        self.assertEqual(exported_status(), 'current')
        for obj, state in (('target', 2), ('mobile', 1)):
            with self.subTest(obj=obj, state=state):
                self.cmd.alter_state(state, obj + ' and index 1', 'x=x+100')
                self.assertEqual(self.controller.result_status(result), 'stale')
                self.assertEqual(exported_status(), 'stale')
                self.cmd.alter_state(state, obj + ' and index 1', 'x=x-100')
                # Recapture after floating-point roundoff from the reversible edit.
                result = self.controller.align('mobile', 'target', mobile_state=1, target_state=2)
        for obj, state in (('target', 2), ('mobile', 1)):
            with self.subTest(deleted_object=obj, deleted_state=state):
                coordinates = self.cmd.get_coordset(obj, state, copy=1)
                self.cmd.delete_states(obj, str(state))
                self.assertEqual(self.controller.result_status(result), 'stale')
                self.assertEqual(exported_status(), 'stale')
                self.cmd.load_coordset(coordinates, obj, state=state)
                result = self.controller.align('mobile', 'target', mobile_state=1, target_state=2)

    def test_legacy_mixed_state_alignment_cannot_verify_missing_target_provenance(self):
        from molscene import engine
        record = dict(operation='align', state=1, source_fingerprint=self.controller._fingerprint(1),
                      parameters=dict(mobile_state=1, target_state=2))
        self.assertNotEqual(self.controller.result_status(record), 'current')
        session = self.root / 'legacy.pse'
        self.cmd.save(str(session))
        report = engine.export_session(session, self.root / 'export', width=120, height=100,
                                       analysis_records=[record])
        self.assertNotEqual(report['analysis_records'][0]['snapshot_status'], 'current')
        record['source_fingerprints'] = {'1': record['source_fingerprint']}
        self.assertEqual(self.controller.result_status(record), 'unverified')






    def test_job_snapshot_is_immutable_and_explicit_result_path(self):
        path = self.controller.prepare_job('contacts', dict(selection_a='a', selection_b='atom_b', state=1,
                                           cutoff=4., heavy_only=True, max_pairs=100000), state=1)
        job = json.loads(path.read_text())
        self.assertEqual(job['schema_version'], 1)
        self.assertEqual(job['output'], job['result_path'])
        self.assertTrue(Path(job['input_session']).is_file())
        self.cmd.translate([30, 0, 0], 'a')
        from pymol2 import PyMOL
        with PyMOL() as snapshot:
            snapshot.cmd.load(job['input_session'])
            self.assertEqual(snapshot.cmd.get_atom_coords('a', state=1), [0., 0., 0.])
        with self.assertRaises(ValueError):
            self.controller.prepare_job('contacts', dict(selection_a='a', selection_b='atom_b', state=2), state=2)

    def test_relative_batch_input_survives_worker_directory_change(self):
        inputs = self.root / 'UI inputs 中文'
        inputs.mkdir()
        (inputs / '1ubq.pdb').write_bytes((ROOT / 'examples/data/1ubq.pdb').read_bytes())
        parameters = dict(input_dir=inputs.name, render_options=dict(width=120, height=100))
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                previous = Path.cwd()
                try:
                    os.chdir(self.root)
                    path = self.controller.prepare_job('batch', parameters, output='output-' + str(cancel))
                finally:
                    os.chdir(previous)
                job = json.loads(path.read_text(encoding='utf-8'))
                if cancel:
                    Path(job['parameters']['cancel_file']).touch()
                process = subprocess.run([sys.executable, '-B', '-m', 'molscene.worker', str(path)],
                    cwd=path.parent, env=dict(os.environ, PYTHONPATH=str(ROOT)),
                    capture_output=True, text=True, timeout=60)
                envelope = json.loads(Path(job['result_path']).read_text(encoding='utf-8'))
                self.assertIn('result', envelope, process.stdout + process.stderr + str(envelope))
                report = envelope['result']
                self.assertEqual(process.returncode, 1 if cancel else 0, envelope)
                self.assertEqual(envelope['passed'], not cancel)
                self.assertEqual(report['cancelled'], cancel)
                self.assertEqual(report['completed'], 0 if cancel else 1)
                self.assertEqual(report['failed'], 0)
                self.assertEqual(report['items'][0]['input'], '1ubq.pdb')
                self.assertEqual(report['items'][0]['status'], 'pending' if cancel else 'completed')
                output = Path(job['output'])
                self.assertEqual(json.loads((output / 'batch.json').read_text(encoding='utf-8')), report)
                if not cancel:
                    self.assertTrue((output / report['items'][0]['output'] / 'scene.png').is_file())
                self.assertEqual(Path(job['parameters']['input_dir']), inputs.resolve())
                self.assertEqual(parameters['input_dir'], inputs.name)

    def test_batch_input_requires_existing_directory_before_creating_job(self):
        regular_file = self.root / 'input.pdb'
        regular_file.write_text('not a directory')
        for value in (None, '', str(self.root / 'missing'), str(regular_file)):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'existing directory'):
                    self.controller.prepare_job('batch', dict(input_dir=value), output=self.root / 'output')
                self.assertFalse((self.root / '.molscene-jobs').exists())

    def test_batch_input_rejects_links_before_resolving_directory(self):
        target = self.root / 'target'
        (target / 'inputs').mkdir(parents=True)
        link = self.root / 'linked'
        try:
            link.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest('Directory symlinks unavailable: ' + str(exc))
        for value in (link, link / 'inputs'):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'links or reparse points'):
                    self.controller.prepare_job('batch', dict(input_dir=str(value)), output=self.root / 'output')
                self.assertFalse((self.root / '.molscene-jobs').exists())

    def test_labels_resolve_spatial_selection_in_requested_state(self):
        self.cmd.load_coordset([[20, 0, 0]], 'a', state=2)
        self.cmd.load_coordset([[3, 0, 0]], 'atom_b', state=2)
        self.cmd.load_coordset([[3, 4, 0]], 'c', state=2)
        self.cmd.frame(1)
        self.controller.set_labels('(a or c) within 4 of atom_b', 'name', state=2)
        labels = {}
        self.cmd.iterate('a or c', 'labels[model] = label', space={'labels': labels})
        self.assertEqual(labels, {'a':'', 'c':'CA'})

    def test_result_fingerprint_excludes_camera_and_rejects_changed_coordinates_or_state(self):
        result = self.controller.geometry('distance', ['a', 'atom_b'], state=1)
        self.cmd.turn('y', 19)
        self.assertEqual(self.controller.result_status(result), 'current')
        self.cmd.load_coordset([[0, 0, 0]], 'a', state=2)
        self.cmd.load_coordset([[6, 0, 0]], 'atom_b', state=2)
        state2 = self.controller.geometry('distance', ['a', 'atom_b'], state=2, annotate=True)
        self.assertEqual(state2['value'], 6.)
        self.assertEqual(state2['snapshot_status'], 'stale')
        self.assertNotIn('annotation', state2)
        self.cmd.frame(2)
        self.assertEqual(self.controller.result_status(state2), 'current')
        self.assertEqual(self.controller.result_status(result), 'stale')
        self.cmd.translate([1, 0, 0], 'atom_b', state=2)
        self.assertEqual(self.controller.result_status(state2), 'stale')
        names = self.cmd.get_names('objects')
        with self.assertRaisesRegex(ValueError, 'stale'):
            self.controller.annotate_result(state2)
        self.assertEqual(names, self.cmd.get_names('objects'))

    def test_sasa_retains_request_selections_alongside_scientific_parameters(self):
        from molscene import analysis
        raw = analysis.sasa(self.cmd, 'a', context='all', state=1)
        result = self.controller.record_result('sasa', raw, dict(selection='a', context='all', state=1),
                                               state=1, source_fingerprint=self.controller._fingerprint(1))
        self.assertEqual(result['parameters']['dot_density'], 3)
        self.assertEqual(result['request_parameters']['selection'], 'a')
        self.assertEqual(result['request_parameters']['context'], 'all')

    def test_contact_annotations_are_capped_but_csv_retains_every_pair(self):
        from molscene import analysis
        self.cmd.delete('all')
        for index in range(21):
            self.cmd.pseudoatom('cloud', pos=[0.01*index, 0, 0], resi=str(index+1))
        raw = analysis.contacts(self.cmd, 'cloud', 'cloud', state=1)
        self.assertEqual(raw['pair_count'], 210)
        result = self.controller.record_result('contacts', raw, state=1, source_fingerprint=self.controller._fingerprint(1))
        with patch.object(self.cmd, 'distance', wraps=self.cmd.distance) as draw:
            self.controller.annotate_result(result)
            self.assertEqual(draw.call_count, 200)
        self.assertEqual(self.cmd.get_type(result['annotation']), 'object:measurement')
        path = self.root/'contacts.csv'
        self.controller.export_table(result, 'pairs', path)
        with path.open(encoding='utf-8', newline='') as handle:
            self.assertEqual(len(list(csv.DictReader(handle))), 210)


@unittest.skipUnless(HAS_PYMOL and HAS_QT, 'requires PyMOL and a Qt binding')
class QtWorkbenchTests(unittest.TestCase):
    def test_repeated_export_history_is_bounded_and_preserves_science(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), '--repeat-exports', directory],
                                    cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT), QT_QPA_PLATFORM='offscreen'),
                                    capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_qt_controls_and_process_failure_cancel_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), '--qt', directory],
                                    cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT), QT_QPA_PLATFORM='offscreen'),
                                    capture_output=True, text=True, timeout=150)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((Path(directory) / 'qt-passed.json').is_file())

    def test_native_fixtures_exports_and_cooperative_window_close(self):
        def check(directory):
            result = subprocess.run([sys.executable, '-B', str(ROOT/'tests/workbench_native.py'), str(directory)],
                                    cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT)),
                                    capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads((Path(directory)/'native-workbench.json').read_text(encoding='utf-8'))
            self.assertTrue(report['passed'], report.get('error') or report)
            self.assertTrue(report['gl_valid'])
            self.assertGreater(report['framebuffer_colors'], 8)
            self.assertEqual(report['hsg_contact_pairs'], 97)
            self.assertEqual(report['hsg_drawn_contacts'], 97)
            self.assertTrue(report['close_was_deferred'])
            self.assertTrue(report['closed_after_worker'])
            self.assertTrue(report['active_close_cancelled'])
            self.assertEqual(report['batch_terminal'], 'cancelled')
            self.assertEqual(report['batch_progress_before_close']['completed'], 1)
            self.assertEqual(report['batch_barrier_ready']['completed'], 1)
            self.assertTrue(report['cancel_marker_present'])
        evidence = os.environ.get('MOLSCENE_TEST_ARTIFACTS')
        if evidence:
            root = Path(evidence)
            root.mkdir(parents=True, exist_ok=True)
            check(tempfile.mkdtemp(prefix='native-workbench-', dir=root))
        else:
            with tempfile.TemporaryDirectory() as directory:
                check(directory)


def qt_test(directory):
    from pymol.Qt import QtCore, QtWidgets, PYQT_NAME
    QTest = __import__(PYQT_NAME + '.QtTest', fromlist=['QTest']).QTest
    from pymol2 import PyMOL
    from molscene.desktop.controller import WorkbenchController
    from molscene.desktop.panel import WorkbenchPanel
    from molscene.desktop.jobs import JobManager
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    root = Path(directory)
    def wait(predicate, timeout=7000):
        for _ in range(timeout // 10):
            app.processEvents()
            if predicate():
                return
            QTest.qWait(10)
        raise AssertionError('Qt event timeout')
    with PyMOL() as runtime:
        cmd = runtime.cmd
        cmd.pseudoatom('point_first', pos=[0, 0, 0])
        cmd.pseudoatom('second', pos=[3, 4, 0])
        controller = WorkbenchController(cmd, root)
        panel = WorkbenchPanel(controller)
        panel.show()
        assert panel.tabs.count() == 4
        panel.tabs.setCurrentIndex(2)
        panel.geometry_atoms[0].setText('point_first')
        panel.geometry_atoms[1].setText('second')
        QTest.mouseClick(panel.findChild(QtWidgets.QPushButton, 'run_geometry'), QtCore.Qt.LeftButton)
        assert len(controller.results) == 1, panel.status.text()
        assert controller.results[0]['value'] == 5
        panel.tabs.setCurrentIndex(3)
        assert panel.result_table.model().rowCount() == 2
        assert 'angstrom' in panel.result_details.toPlainText()
        panel.geometry_atoms[1].setText('none')
        panel.tabs.setCurrentIndex(2)
        QTest.mouseClick(panel.findChild(QtWidgets.QPushButton, 'run_geometry'), QtCore.Qt.LeftButton)
        assert 'Error' in panel.status.text()
        assert panel.findChild(QtWidgets.QPushButton, 'run_geometry').isEnabled()
        # External worker is replaced only at the executable boundary; QProcess,
        # signals, event delivery, JSON reading, cancellation and recovery are real.
        script = root / 'boundary worker.py'
        script.write_text("import json,sys,time\nfrom pathlib import Path\np=Path(sys.argv[1]); j=json.loads(p.read_text()); mode=j['parameters']['mode']\nprint(json.dumps({'progress': {'message':'working'}}), flush=True)\nif mode=='wait': time.sleep(30)\nr={'passed':mode=='ok','result':{'value':7},'error':'deliberate worker failure'}\nif mode=='malformed': r=[]\nPath(j['result_path']).write_text(json.dumps(r))\n")
        manager = panel.jobs
        failures, completed, cancelled, progress = [], [], [], []
        manager.failed.connect(failures.append)
        manager.finished.connect(completed.append)
        manager.cancelled.connect(cancelled.append)
        manager.progress.connect(progress.append)
        def job(mode):
            path = root / (mode + '.json')
            path.write_text(json.dumps(dict(schema_version=1, operation='contacts', output=str(root/(mode+'.result.json')),
                                            result_path=str(root/(mode+'.result.json')), parameters={'mode':mode})))
            return path
        with patch('molscene.desktop.jobs.worker_command', side_effect=lambda path: [sys.executable, '-B', '-u', str(script), str(path)]):
            manager.start(job('fail'))
            assert manager.active
            wait(lambda: bool(failures))
            assert 'deliberate' in failures[-1]
            assert not manager.active
            assert panel.findChild(QtWidgets.QPushButton, 'run_geometry').isEnabled()
            manager.start(job('malformed'))
            wait(lambda: len(failures) == 2)
            assert not manager.active
            manager.start(job('wait'))
            wait(lambda: len(progress) >= 2)
            manager.cancel()
            wait(lambda: bool(cancelled))
            assert cancelled[-1]['cancelled'] is True
            assert not manager.active
            manager.start(job('ok'))
            wait(lambda: bool(completed))
            assert completed[-1]['value'] == 7
        # Real worker integration against the immutable saved native session.
        from molscene import analysis
        from PIL import Image
        import shutil
        cmd.delete('all')
        cmd.load(str(ROOT / 'examples/data/1ubq.pdb'), 'ubq')
        cmd.hide('everything')
        cmd.show('cartoon', 'ubq')
        cmd.orient('ubq')
        panel.refresh()
        panel.analysis_selection.setText('ubq and resi 1-10')
        panel.contacts_b.setText('ubq and resi 11-20')
        expected = analysis.contacts(cmd, 'ubq and resi 1-10', 'ubq and resi 11-20', state=1)
        panel.tabs.setCurrentIndex(2)
        QTest.mouseClick(panel.findChild(QtWidgets.QPushButton, 'run_contacts'), QtCore.Qt.LeftButton)
        assert manager.active, panel.status.text()
        cmd.turn('y', 17)  # view-only changes must not make molecular data stale
        wait(lambda: not manager.active)
        contacts = controller.results[-1]
        assert contacts['pairs'] == expected['pairs'], panel.status.text()
        assert contacts['snapshot_status'] == 'current'
        assert contacts['snapshot_id'] != contacts['source_fingerprint']
        assert panel.result_table.model().rowCount() == expected['pair_count']
        # Every successful row is kept by a lazy model, including a 100k table.
        from molscene.desktop.panel import ResultTableModel
        large = ResultTableModel([{'distance_angstrom':1.}] * 100000)
        assert large.rowCount() == 100000
        assert large.data(large.index(99999, 0)) == '1'
        panel.analysis_selection.setText('ubq and polymer.protein')
        panel.sasa_context.setText('ubq')
        expected_sasa = analysis.sasa(cmd, 'ubq and polymer.protein', context='ubq', state=1)
        QTest.mouseClick(panel.findChild(QtWidgets.QPushButton, 'run_sasa'), QtCore.Qt.LeftButton)
        assert manager.active
        wait(lambda: not manager.active)
        assert abs(controller.results[-1]['total_area_angstrom2'] - expected_sasa['total_area_angstrom2']) < 1e-5, panel.status.text()
        assert controller.results[-1]['snapshot_status'] == 'current'
        # Native coordinate edits while a process runs produce a stale record.
        panel.analysis_selection.setText('ubq and resi 1-10')
        QTest.mouseClick(panel.findChild(QtWidgets.QPushButton, 'run_contacts'), QtCore.Qt.LeftButton)
        cmd.translate([0.2, 0, 0], 'ubq and resi 1', state=1)
        wait(lambda: not manager.active)
        stale = controller.results[-1]
        assert stale['snapshot_status'] == 'stale', panel.status.text()
        measurement_names = cmd.get_names('objects')
        try:
            controller.annotate_result(stale)
            raise AssertionError('Stale annotations were accepted')
        except ValueError:
            pass
        assert cmd.get_names('objects') == measurement_names
        # A real worker validation error leaves all controls usable.
        panel.analysis_selection.setText('ubq and resi 1')
        panel.sasa_context.setText('ubq and resi 2')
        fail_count = len(failures)
        QTest.mouseClick(panel.findChild(QtWidgets.QPushButton, 'run_sasa'), QtCore.Qt.LeftButton)
        wait(lambda: not manager.active)
        assert len(failures) == fail_count + 1 and 'subset' in failures[-1]
        assert panel.findChild(QtWidgets.QPushButton, 'run_sasa').isEnabled()
        panel.tabs.setCurrentIndex(1)
        destination = root / "中文 figure';$(literal)"
        panel.export_directory.setText(str(destination))
        panel.image_width.setValue(320)
        panel.image_height.setValue(240)
        panel.image_dpi.setValue(72)
        panel.image_ray.setChecked(False)
        panel.background.setCurrentText('transparent')
        camera = cmd.get_view()
        QTest.mouseClick(panel.findChild(QtWidgets.QPushButton, 'run_export'), QtCore.Qt.LeftButton)
        assert manager.active, panel.status.text()
        wait(lambda: not manager.active)
        assert (destination/'report.json').is_file(), panel.status.text()
        assert cmd.get_view() == camera
        with Image.open(destination/'scene.png') as image:
            assert image.size == (320, 240)
        export = json.loads((destination/'report.json').read_text(encoding='utf-8'))
        assert export['analysis_records'][-1]['snapshot_status'] == 'stale'
        first_png = (destination/'scene.png').read_bytes()
        QTest.mouseClick(panel.findChild(QtWidgets.QPushButton, 'run_export'), QtCore.Qt.LeftButton)
        wait(lambda: not manager.active)
        assert 'Error' in panel.status.text()
        assert (destination/'scene.png').read_bytes() == first_png
        # Real cooperative cancellation retains completed item directories.
        inputs = root/'batch inputs'
        inputs.mkdir()
        for index in range(5):
            shutil.copyfile(ROOT/'examples/data/1hsg.pdb', inputs/f'{index}.pdb')
        panel.batch_input.setText(str(inputs))
        panel.batch_output.setText(str(root/'batch outputs'))
        panel.image_width.setValue(1000)
        panel.image_height.setValue(700)
        QTest.mouseClick(panel.findChild(QtWidgets.QPushButton, 'run_batch'), QtCore.Qt.LeftButton)
        assert manager.active, panel.status.text()
        wait(lambda: manager.last_result is not None and manager.last_result.get('completed', 0) >= 1, timeout=20000)
        assert manager.active, 'Batch finished before a cooperative cancel could be tested'
        cancel_count = len(cancelled)
        QTest.mouseClick(panel.cancel_job, QtCore.Qt.LeftButton)
        assert manager.active  # cooperative stop allows the running item to finish
        wait(lambda: not manager.active, timeout=20000)
        assert len(cancelled) == cancel_count + 1, panel.status.text()
        report = controller.results[-1]
        assert report['cancelled'] and report['completed'] >= 1
        assert any(item['status']=='pending' for item in report['items'])
        for item in report['items']:
            if item['status']=='completed':
                assert (root/'batch outputs'/item['report']).is_file()

        panel.close()
    (root / 'qt-passed.json').write_text(json.dumps({'controls':True, 'failure':True, 'cancel':True, 'recovery':True}))

def repeated_export_test(directory):
    from pymol.Qt import QtCore, QtWidgets, PYQT_NAME
    QTest = __import__(PYQT_NAME + '.QtTest', fromlist=['QTest']).QTest
    from pymol2 import PyMOL
    from molscene import analysis
    from molscene.desktop.controller import WorkbenchController
    from molscene.desktop.panel import WorkbenchPanel
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    root = Path(directory)
    with PyMOL() as runtime:
        cmd = runtime.cmd
        for index in range(46):
            cmd.pseudoatom('cloud', pos=[index * 0.01, 0, 0], resi=str(index+1))
        cmd.show('spheres', 'cloud')
        cmd.orient('cloud')
        controller = WorkbenchController(cmd, root)
        panel = WorkbenchPanel(controller)
        panel.show()
        def wait():
            for _ in range(1000):
                app.processEvents()
                if not panel.jobs.active:
                    assert not panel.status.text().startswith('Error'), panel.status.text()
                    return
                QTest.qWait(10)
            raise AssertionError('Export worker did not finish')
        panel.tabs.setCurrentIndex(2)
        panel.analysis_selection.setText('cloud')
        panel.contacts_b.setText('cloud')
        QTest.mouseClick(panel.findChild(QtWidgets.QPushButton, 'run_contacts'), QtCore.Qt.LeftButton)
        wait()
        science = controller.results[0]
        assert science['pair_count'] == 1035
        assert science['pairs'] == analysis.contacts(cmd, 'cloud', 'cloud', state=1)['pairs']
        original_science = json.dumps(science, sort_keys=True)
        batch = controller.record_result('batch', dict(schema_version=2, passed=True,
            items=[dict(input='cloud.pdb', output='0001-cloud', status='completed', report='0001-cloud/report.json')],
            completed=1, failed=0, cancelled=False), dict(input_dir=str(root/'inputs'), overwrite=False))
        assert batch['items'][0]['report'] == '0001-cloud/report.json'
        assert len(json.dumps(batch)) < 1024
        panel.tabs.setCurrentIndex(1)
        panel.image_width.setValue(128)
        panel.image_height.setValue(96)
        panel.image_dpi.setValue(72)
        panel.image_ray.setChecked(False)
        sizes = []
        for index in range(4):
            destination = root / f'export{index}'
            panel.export_directory.setText(str(destination))
            QTest.mouseClick(panel.findChild(QtWidgets.QPushButton, 'run_export'), QtCore.Qt.LeftButton)
            wait()
            report_path = destination / 'report.json'
            report = json.loads(report_path.read_text(encoding='utf-8'))
            assert len(report['analysis_records']) == 1, 'Export recursively embedded earlier job history'
            exported_science = report['analysis_records'][0]
            assert exported_science['pairs'] == science['pairs']
            assert exported_science['snapshot_id'] == science['snapshot_id']
            assert exported_science['source_fingerprint'] == science['source_fingerprint']
            history = controller.results[-1]
            assert 'analysis_records' not in history, 'Export job history retains complete embedded science'
            assert 'analysis_records' not in history['request_parameters'], 'Request history duplicates scientific rows'
            assert history['artifacts']['report'] == 'report.json'
            assert history['output_path'] == str(destination)
            assert history['source_fingerprint'] == science['source_fingerprint']
            assert len(json.dumps(history)) < 4096, 'Export history is not compact metadata'
            sizes.append(report_path.stat().st_size)
        assert max(sizes) - min(sizes) < 1024, sizes
        assert json.dumps(science, sort_keys=True) == original_science
        assert len(json.dumps(controller.results)) < len(original_science) + 4 * 4096 + 1024
        (root/'repeated-export.json').write_text(json.dumps(dict(
            pair_count=science['pair_count'], report_sizes=sizes,
            export_history_sizes=[len(json.dumps(row)) for row in controller.results if row['operation']=='export'],
            scientific_records_per_export=1, export_count=4)), encoding='utf-8')
        panel.close()
        panel.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(panel, QtCore.QEvent.DeferredDelete)


if __name__ == '__main__':
    if '--qt' in sys.argv:
        qt_test(sys.argv[-1])
    elif '--repeat-exports' in sys.argv:
        repeated_export_test(sys.argv[-1])
    else:
        unittest.main()
