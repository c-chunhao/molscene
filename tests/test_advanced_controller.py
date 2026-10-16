import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


@unittest.skipUnless(importlib.util.find_spec('pymol2'), 'requires PyMOL')
class AdvancedControllerTests(unittest.TestCase):
    def setUp(self):
        from pymol2 import PyMOL
        from molscene.desktop.controller import WorkbenchController
        self.pm = PyMOL()
        self.pm.start()
        self.addCleanup(self.pm.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cmd = self.pm.cmd
        self.cmd.pseudoatom('source', pos=[0, 0, 0], state=1)
        self.cmd.pseudoatom('source', pos=[2, 0, 0], state=2)
        self.controller = WorkbenchController(self.cmd, self.root)

    def test_selection_identity_binds_originals_ignores_camera_and_owned_copies(self):
        from molscene.advanced_snapshot import capture_identity, result_status
        identity = capture_identity(self.cmd, selection='all', state=1, session_epoch=3)
        self.cmd.create('owned', 'source', 1, 1)
        self.cmd.turn('y', 25)
        self.assertEqual(result_status(self.cmd, identity, session_epoch=3), 'current')
        self.assertEqual(result_status(self.cmd, identity, session_epoch=4), 'stale')
        self.cmd.alter('source', 'vdw=3.5')
        self.assertEqual(result_status(self.cmd, identity, session_epoch=3), 'stale')
        self.assertEqual(result_status(self.cmd, {}, session_epoch=3), 'unverified')

    def test_partial_or_boolean_identity_cannot_authorize_currentness(self):
        from molscene.advanced_snapshot import capture_identity, result_status
        self.assertEqual(result_status(self.cmd,dict(schema_version=1,session_epoch=0,verified=True),
                                       session_epoch=0),'unverified')
        identity=capture_identity(self.cmd,selection='all',session_epoch=0)
        identity['schema_version']=True
        self.assertEqual(result_status(self.cmd,identity,session_epoch=0),'unverified')
        with self.assertRaises(ValueError):
            capture_identity(self.cmd,selection='all',session_epoch=True)

    def test_set_state_preserves_native_movie_mapping(self):
        self.cmd.mset('1 1 2')
        self.cmd.frame(2)
        self.controller.set_state(2)
        self.assertEqual(self.cmd.get_state(), 2)
        self.assertEqual(self.cmd.get_movie_length(), 3)
        self.cmd.frame(2)
        self.assertEqual(self.cmd.get_state(), 1)

    def test_tools_job_never_saves_snapshot(self):
        path = self.controller.prepare_job('tools', {'tools': {'schema_version': 1, 'tools': {}}})
        job = json.loads(path.read_text())
        self.assertNotIn('input_session', job)
        self.assertEqual(job['output'], job['result_path'])
        self.assertFalse(list(path.parent.glob('*.pse')))

    def test_timeline_scene_rename_updates_references_and_delete_is_guarded(self):
        document = self.controller.timeline_new()
        baseline = document['baseline_scene']
        self.controller.rename_scene(baseline, 'new_baseline')
        self.assertEqual(self.controller.advanced_inventory()['timeline']['baseline_scene'], 'new_baseline')
        with self.assertRaises(ValueError):
            self.controller.delete_scene('new_baseline')
        self.cmd.scene('new_baseline', 'clear')
        with self.assertRaises(ValueError):
            self.controller.timeline_seek(1)

    def _map(self):
        path = self.root / 'map.dx'
        path.write_text('object 1 class gridpositions counts 2 2 2\norigin 0 0 0\ndelta 1 0 0\ndelta 0 1 0\ndelta 0 0 1\nobject 2 class gridconnections counts 2 2 2\nobject 3 class array type double rank 0 items 8 data follows\n0 1 2 3 4 5 6 7\n')
        return path

    def test_map_identity_checks_values_matrix_bytes_and_unassociated_map(self):
        from molscene.advanced_snapshot import capture_identity, result_status
        record = self.controller.density_load(self._map(), format='dx')
        identity = capture_identity(self.cmd, maps=[record], session_epoch=0)
        self.assertEqual(result_status(self.cmd, identity, session_epoch=0), 'current')
        self.cmd.transform_object(record['name'], [1,0,0,2,0,1,0,0,0,0,1,0,0,0,0,1], homogenous=1)
        self.assertEqual(result_status(self.cmd, identity, session_epoch=0), 'stale')
        session = self.root / 'same.pse'
        self.cmd.save(str(session))
        self.controller.open_session(session)
        inventory = self.controller.advanced_inventory()
        self.assertFalse(inventory['maps'][0]['verified'])
        self.assertEqual(result_status(self.cmd, identity, session_epoch=1), 'stale')

    def test_map_raw_values_and_timeline_document_changes_stale_identity(self):
        from molscene.advanced_snapshot import capture_identity, result_status
        record = self.controller.density_load(self._map(), format='dx')
        identity = capture_identity(self.cmd, maps=[record], session_epoch=0)
        field = self.cmd.get_volume_field(record['name'], state=1, copy=0)
        field[0, 0, 0] = 99
        self.assertEqual(result_status(self.cmd, identity, session_epoch=0), 'stale')
        document = self.controller.timeline_new()
        identity = capture_identity(self.cmd, timeline=document, session_epoch=0)
        self.assertEqual(result_status(self.cmd, identity, timeline=document, session_epoch=0), 'unverified')
        document['fps'] = 25
        self.assertEqual(result_status(self.cmd, identity, timeline=document, session_epoch=0), 'stale')

    def test_bound_source_removal_is_stale_even_with_equal_owned_copy(self):
        from molscene.advanced_snapshot import capture_identity, result_status
        identity = capture_identity(self.cmd, states=(1,), session_epoch=0)
        self.cmd.create('copy', 'source', 1, 1)
        self.assertEqual(result_status(self.cmd, identity, session_epoch=0), 'current')
        self.cmd.delete('source')
        self.assertEqual(result_status(self.cmd, identity, session_epoch=0), 'stale')

    def test_density_result_copy_preserves_source_and_bundle(self):
        from molscene.worker import run_job
        source = self._map()
        original = self.controller.density_load(source, format='dx', name='original_map')
        output = self.root / 'density-result'
        job = self.controller.prepare_job('density', {'request': dict(map_path=str(source), format='dx',
            map_name=original['name'], render={'width': 32, 'height': 32})}, output=output)
        self.assertEqual(run_job(job), 0)
        frozen = {name: (output / name).read_bytes() for name in ('report.json', 'manifest.json')}
        self.cmd.pseudoatom(self.cmd.get_unused_name('molscene_density') + '_mesh', pos=[9,9,9])
        count = self.cmd.count_atoms('source')
        loaded = self.controller.load_advanced_result(output / 'report.json')
        self.assertEqual(self.cmd.count_atoms('source'), count)
        self.assertEqual(self.controller.result_status(loaded), 'current')
        self.assertNotEqual(loaded['loaded']['record']['name'], original['name'])
        for name, content in frozen.items():
            self.assertEqual((output / name).read_bytes(), content)
        source.write_text(source.read_text() + '# changed\n')
        with self.assertRaises(ValueError):
            self.controller.load_advanced_result(output / 'report.json')

    def test_timeline_project_open_replaces_session_and_restores_document(self):
        from molscene.worker import run_job
        document = self.controller.timeline_new()
        output = self.root / 'project'
        path = self.controller.prepare_job('timeline_project', {'timeline': document}, output=output)
        self.assertEqual(run_job(path), 0)
        self.cmd.pseudoatom('later', pos=[8, 8, 8])
        self.controller.timeline_open_project(output)
        self.assertNotIn('later', self.cmd.get_names('objects'))
        self.assertEqual(self.controller.advanced_inventory()['timeline'], document)
        self.assertEqual(self.controller.advanced_inventory()['session_epoch'], 1)

    def test_worker_detects_modified_private_session_before_export(self):
        from molscene.worker import run_job
        output = self.root/'modified-snapshot'
        path = self.controller.prepare_job('timeline_project', {'timeline':self.controller.timeline_new()},output=output)
        job = json.loads(path.read_text())
        with Path(job['input_session']).open('ab') as handle:
            handle.write(b'changed')
        self.assertEqual(run_job(path),1)
        self.assertIn('snapshot',json.loads(Path(job['result_path']).read_text())['error'].lower())
        self.assertFalse(output.exists())

    def test_aligned_density_copy_uses_replay_transform_and_original_members(self):
        from molscene.worker import run_job
        self.cmd.set('matrix_mode', 1)
        points = [(0,0,0),(1,2,0),(3,1,2),(4,4,1),(6,3,4),(7,6,2)]
        for name, coords in [('mobile', points), ('target', [(-y+11,x-7,z+3) for x,y,z in points])]:
            pdb = '\n'.join('ATOM  %5d  CA  ALA A%4d    %8.3f%8.3f%8.3f  1.00 20.00           C' % (i+1,i+1,*point)
                            for i, point in enumerate(coords)) + '\nEND\n'
            self.cmd.read_pdbstr(pdb, name)
        self.cmd.transform_object('mobile', [1,0,0,3,0,1,0,4,0,0,1,1,0,0,0,1], homogenous=1)
        original = self.cmd.get_coords('mobile', state=1).copy()
        path = self._map()
        output = self.root / 'aligned-result'
        request = dict(map_path=str(path), format='dx', render={'width':32,'height':32},
            registration=dict(kind='align',mobile='mobile',target='target',model_copy_name='aligned',
                              copy_name='registered',cycles=0))
        job = self.controller.prepare_job('density', {'request': request}, output=output)
        self.assertEqual(run_job(job), 0)
        result = self.controller.load_advanced_result(output / 'report.json')
        self.assertEqual(self.controller.result_status(result), 'current')
        self.assertTrue((self.cmd.get_coords('mobile',state=1) == original).all())
        import numpy as np
        self.assertTrue(np.allclose(self.cmd.get_coords(result['loaded']['model_copy'],state=1),
                                    self.cmd.get_coords('target',state=1), atol=1e-4))

    def test_result_bundle_wrong_snapshot_cannot_reuse_private_context(self):
        from unittest.mock import patch
        from molscene.worker import run_job
        source = self._map()
        output = self.root / 'result'
        job = self.controller.prepare_job('density', {'request': dict(map_path=str(source),format='dx',render={'width':32,'height':32})}, output=output)
        self.assertEqual(run_job(job), 0)
        from molscene.advanced import validate_bundle
        report = validate_bundle(output / 'report.json')
        report['snapshot_id'] = '0' * 64
        with patch('molscene.advanced.validate_bundle', return_value=report):
            with self.assertRaises(ValueError):
                self.controller.load_advanced_result(output / 'report.json')

    def _potential_map(self):
        from array import array
        from molscene.grids import Grid, write_dx
        path = self.root / 'potential.dx'
        write_dx(path, Grid((3,3,3),(-10.,)*3,((10.,0.,0.),(0.,10.,0.),(0.,0.,10.)),
                           array('d',[-8.,0.,8.]*9),'kBT/e',{}))
        return path

    def test_potential_selected_copy_and_ramp_keep_original_all_identity(self):
        from molscene.worker import run_job
        output = self.root / 'potential-result'
        job = self.controller.prepare_job('potential', {'request':dict(mode='import', dx_path=str(self._potential_map()),
            units='kBT/e', selection='all', render=dict(width=32,height=32))}, output=output)
        self.assertEqual(run_job(job),0)
        before = {name:(output/name).read_bytes() for name in ('report.json','manifest.json')}
        first = self.controller.load_advanced_result(output/'report.json')
        self.cmd.turn('x',30)
        self.assertEqual(self.controller.result_status(first),'current')
        second = self.controller.load_advanced_result(output/'report.json')
        self.assertEqual(self.cmd.count_atoms(second['loaded']['molecule']),1)
        self.assertNotEqual(first['loaded']['molecule'],second['loaded']['molecule'])
        changed = self.controller.potential_ramp(first,ramp_limit=10)
        self.assertEqual(changed['loaded']['ramp_values'],[-10.,0.,10.])
        self.assertEqual(changed['loaded']['clipped_grid_fraction'],0)
        self.cmd.alter('source','vdw=4')
        with self.assertRaises(ValueError):
            self.controller.potential_ramp(first,ramp_limit=3)
        for name,content in before.items():
            self.assertEqual((output/name).read_bytes(),content)

    def test_potential_pqr_import_job_has_no_snapshot_and_loads_owned_copy(self):
        from molscene.worker import run_job
        pqr = self.root/'ion.pqr'
        pqr.write_text('ATOM 1 C ALA 1 0 0 0 1 2.5\n')
        output = self.root/'pqr-import'
        path = self.controller.prepare_job('potential', {'request':dict(mode='import',pqr_path=str(pqr),
            dx_path=str(self._potential_map()),units='kBT/e',render=dict(width=32,height=32))},output=output)
        self.assertNotIn('input_session',json.loads(path.read_text()))
        self.assertFalse(list(path.parent.glob('*.pse')))
        self.assertEqual(run_job(path),0)
        original_count = self.cmd.count_atoms('source')
        record = self.controller.load_advanced_result(output/'report.json')
        self.assertEqual(self.cmd.get_model(record['loaded']['molecule']).atom[0].vdw,2.5)
        self.assertEqual(self.cmd.count_atoms('source'),original_count)
        pqr.write_text(pqr.read_text()+'REMARK changed\n')
        with self.assertRaises(ValueError):
            self.controller.potential_ramp(record,ramp_limit=3)


@unittest.skipUnless(importlib.util.find_spec('pymol') and any(importlib.util.find_spec(name)
    for name in ('PySide6', 'PySide2', 'PyQt6', 'PyQt5')), 'requires Qt and PyMOL')
class AdvancedJobsTests(unittest.TestCase):
    def setUp(self):
        from pymol.Qt import QtCore
        from molscene.desktop.jobs import JobManager
        self.QtCore = QtCore
        self.app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
        self.manager = JobManager()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_late_cancel_retains_confirmed_success(self):
        result = self.root / 'result.json'
        result.write_text(json.dumps({'passed': True, 'result': {'operation': 'density', 'passed': True}}))
        self.manager.job = dict(operation='density', result_path=str(result))
        self.manager.process = self.QtCore.QProcess()
        self.manager._cancelled = True
        completed, cancelled = [], []
        self.manager.finished.connect(completed.append)
        self.manager.cancelled.connect(cancelled.append)
        self.manager._done(0, self.QtCore.QProcess.NormalExit)
        self.assertEqual(len(completed), 1)
        self.assertFalse(cancelled)

    def test_tools_preparation_needs_no_live_backend(self):
        import sys
        from unittest.mock import patch
        from molscene.desktop.controller import WorkbenchController
        controller = WorkbenchController(object(), self.root)
        config=dict(schema_version=1,tools={'apbs':dict(argv=[sys.executable],architecture='x64',path_dirs=[],
                    runtime_files=[],timeout_seconds=15,memory_limit_mib=512)})
        with patch('molscene.artifacts.sha256',side_effect=RuntimeError('Tool hashing belongs in asynchronous worker')):
            path = controller.prepare_job('tools', {'tools': config})
        self.assertNotIn('input_session', json.loads(path.read_text()))

    def test_before_commit_cancel_without_envelope_is_cancelled(self):
        self.manager.job = dict(operation='density', result_path=str(self.root / 'missing.json'))
        self.manager.process = self.QtCore.QProcess()
        self.manager._cancelled = True
        cancelled = []
        self.manager.cancelled.connect(cancelled.append)
        self.manager._done(1, self.QtCore.QProcess.CrashExit)
        self.assertTrue(cancelled[0]['cancelled'])

    def test_stdout_partial_line_is_bounded(self):
        from unittest.mock import Mock
        process = Mock()
        process.readAllStandardOutput.return_value = b'x' * 1000000
        self.manager.process = process
        self.manager._read_stdout()
        self.assertLessEqual(len(self.manager._stdout), 65536)

    def test_slow_tools_is_async_cancellable_without_session(self):
        import sys
        import time
        script = self.root / 'slow_probe.py'
        script.write_text('import time\ntime.sleep(30)\nprint("APBS 3.4.1")\n')
        config = dict(schema_version=1, tools={'apbs': dict(argv=[sys.executable, '-B', str(script)],
            architecture='x64', path_dirs=[], runtime_files=[dict(path=str(script), role='package_metadata')],
            timeout_seconds=60, memory_limit_mib=512)})
        job = self.root / 'job.json'
        result = self.root / 'result.json'
        job.write_text(json.dumps(dict(schema_version=1, operation='tools', output=str(result),
            result_path=str(result), parameters=dict(tools=config, cancel_file=str(self.root / 'cancel.request')))))
        ready, cancelled, failed = [], [], []
        def progress(value):
            if value.get('stage') == 'tool_ready':
                ready.append(value)
                self.manager.cancel()
        self.manager.progress.connect(progress)
        self.manager.cancelled.connect(cancelled.append)
        self.manager.failed.connect(failed.append)
        start = time.monotonic()
        self.manager.start(job)
        self.assertLess(time.monotonic() - start, 1)
        deadline = start + 15
        while self.manager.active and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        if self.manager.active:
            self.manager.process.kill()
            self.manager.process.waitForFinished(5000)
        self.assertFalse(failed, failed)
        self.assertTrue(ready)
        self.assertTrue(cancelled)
        self.assertFalse(cancelled[0]['tools']['apbs']['available'])
        self.assertTrue(cancelled[0]['tools']['apbs']['error'])
        self.assertFalse(list(self.root.glob('*.pse')))
