"""Advanced controls and the separately launched real native acceptance gate."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys
from unittest.mock import patch

HAS_GUI = importlib.util.find_spec('pymol2') is not None and any(
    importlib.util.find_spec(name) is not None
    for name in ('PySide6', 'PySide2', 'PyQt6', 'PyQt5'))


@unittest.skipUnless(HAS_GUI, 'requires PyMOL and its Qt binding')
class AdvancedControlTests(unittest.TestCase):
    """Construct real controls without showing a window or requesting native GL."""

    @classmethod
    def setUpClass(cls):
        from molscene.desktop import _gui_runtime
        _, qt = _gui_runtime()
        cls.qt = qt
        cls.app = qt.QtWidgets.QApplication.instance() or qt.QtWidgets.QApplication([])

    def setUp(self):
        from pymol2 import PyMOL
        from molscene.desktop.controller import WorkbenchController
        from molscene.desktop.panel import WorkbenchPanel
        self.runtime = PyMOL()
        self.runtime.start()
        self.addCleanup(self.runtime.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cmd = self.runtime.cmd
        self.cmd.pseudoatom('source', pos=[1, 2, 3])
        self.controller = self.build_controller(WorkbenchController)
        self.panel = WorkbenchPanel(self.controller)
        self.addCleanup(self.dispose_panel)

    def build_controller(self, cls):
        return cls(self.cmd, self.root)

    def dispose_panel(self):
        self.panel.refresh_timer.stop()
        self.panel.close()
        self.panel.deleteLater()
        self.qt.QtCore.QCoreApplication.sendPostedEvents(
            self.panel, self.qt.QtCore.QEvent.DeferredDelete)

    def advanced(self):
        self.assertEqual(self.panel.tabs.count(), 5,
                         'Advanced must be appended without moving the existing tabs')
        self.assertEqual([self.panel.tabs.tabText(i) for i in range(5)],
                         ['Structure', 'Figure', 'Analysis', 'Results', 'Advanced'])
        return self.panel.advanced

    def test_fifth_tab_preserves_existing_workflows_and_exposes_named_controls(self):
        advanced = self.advanced()
        names = '''advanced_panel advanced_timeline advanced_density advanced_potential
            timeline_new timeline_open_project timeline_save_project timeline_load_json
            timeline_table timeline_frame timeline_state timeline_scene timeline_label
            timeline_interpolation timeline_capture timeline_update timeline_remove
            timeline_move_up timeline_move_down timeline_fps timeline_seek timeline_previous
            timeline_next timeline_play timeline_pause timeline_position timeline_output
            timeline_width timeline_height timeline_ray timeline_mp4 timeline_export
            density_path density_format density_interpretation density_load density_maps
            density_state density_metadata density_kind density_level density_color
            density_opacity density_volume_ramp density_visible density_apply_display
            density_matrix density_mobile density_target density_mobile_state
            density_target_state density_alignment_selection density_registration_mode
            density_register density_output density_export potential_mode potential_pqr
            potential_dx potential_selection potential_state potential_units
            potential_display_units potential_temperature potential_force_field potential_ph
            potential_protonation potential_drop_water potential_grid_x potential_grid_y
            potential_grid_z potential_solute_dielectric potential_solvent_dielectric
            potential_salt potential_ion_radius potential_probe_radius potential_coarse_padding
            potential_fine_padding potential_ramp_limit potential_tools_path
            potential_tools_check potential_tools_status potential_output potential_compute
            potential_load_result potential_diagnostics potential_ramp_apply'''.split()
        actual = [advanced.objectName()] + [obj.objectName() for obj in advanced.findChildren(self.qt.QtCore.QObject)]
        for name in names:
            with self.subTest(name=name):
                self.assertEqual(actual.count(name), 1)
        self.assertFalse(self.panel.jobs.active)

    def test_density_volume_uses_explicit_transfer_ramp_and_disables_isolevel(self):
        density = self.advanced().density
        self.assertEqual(density.display_request(), dict(kind='mesh', level=1.0,
            color='cyan', opacity=1.0, visible=True, volume_ramp=[]))
        density.kind.setCurrentText('volume')
        self.assertFalse(density.level.isEnabled())
        with self.assertRaisesRegex(ValueError, 'ramp'):
            density.display_request()
        density.volume_ramp.setText('[0,0,0,1,0, 2,1,0,0,0.8]')
        self.assertEqual(density.display_request()['volume_ramp'],
                         [0, 0, 0, 1, 0, 2, 1, 0, 0, 0.8])
        density.kind.setCurrentText('surface')
        self.assertTrue(density.level.isEnabled())

    def test_potential_parameters_keep_units_states_and_model_assumptions_explicit(self):
        potential = self.advanced().potential
        request = potential.request_parameters()
        self.assertEqual(request['state'], 1)
        self.assertEqual(request['temperature'], 298.15)
        self.assertEqual(request['units'], 'kBT/e')
        self.assertEqual(request['display_units'], 'kBT/e')
        self.assertEqual(request['preparation'], dict(force_field='PARSE', ph=7.0,
            protonation='propka', drop_water=False))
        self.assertEqual(request['solver'], dict(grid=[65, 65, 65], solute_dielectric=2.0,
            solvent_dielectric=78.5, salt_molar=0.15, ion_radius=2.0, probe_radius=1.4,
            coarse_padding=40.0, fine_padding=20.0))
        potential.mode.setCurrentText('import')
        potential.units.setCurrentText('mV')
        potential.display_units.setCurrentText('V')
        potential.state.setValue(3)
        self.assertEqual(potential.request_parameters()['units'], 'mV')
        self.assertEqual(potential.request_parameters()['state'], 3)
        self.assertFalse(potential.solver_group.isVisibleTo(potential))
        self.assertTrue(potential.dx.isVisibleTo(potential))
        potential.mode.setCurrentText('prepare')
        self.assertEqual(potential.units.currentText(), 'kBT/e')
        self.assertFalse(potential.units.isEnabled())
        self.assertTrue(potential.preparation_group.isVisibleTo(potential))

    def test_timeline_render_defaults_scene_inheritance_and_independent_cursor(self):
        timeline = self.advanced().timeline
        self.assertEqual(timeline.scene.itemText(0), 'Keep previous display')
        self.assertIsNone(timeline.scene.itemData(0))
        self.assertEqual(timeline.render_parameters(), dict(width=800, height=600, dpi=150, ray=True))
        self.assertEqual(timeline.fps.value(), 24)
        timeline.frame.setValue(30)
        self.assertEqual(timeline.seek.value(), 1)
        self.assertFalse(timeline.timer.isActive())

    def test_advanced_worker_lock_leaves_results_and_cancel_reachable(self):
        advanced = self.advanced()
        self.panel._job_started(dict(operation='tools', output='test-result.json', parameters={}))
        for index in (0, 1, 2, 4):
            self.assertFalse(self.panel.tabs.widget(index).isEnabled())
        self.assertTrue(self.panel.tabs.widget(3).isEnabled())
        self.assertTrue(self.panel.cancel_job.isEnabled())
        self.assertFalse(advanced.timeline.timer.isActive())
        self.panel._job_idle()
        self.assertTrue(advanced.isEnabled())

    def test_panel_close_stops_preview_without_resetting_position(self):
        timeline = self.advanced().timeline
        timeline.seek.setRange(1, 50)
        timeline.seek.setValue(12)
        timeline.timer.start()
        self.assertTrue(timeline.timer.isActive())
        self.panel.close()
        self.assertFalse(timeline.timer.isActive())
        self.assertEqual(timeline.seek.value(), 12)

    def test_tools_path_prefill_is_existing_only_and_parsing_is_explicit(self):
        from molscene.desktop.potential_panel import PotentialPanel
        advanced = self.advanced()
        config = self.root / 'tools.json'
        config.write_text(json.dumps(dict(schema_version=1, tools={})), encoding='utf-8')
        with patch.dict(os.environ, MOLSCENE_TOOLS_CONFIG=str(config)):
            view = PotentialPanel(advanced)
        self.addCleanup(view.deleteLater)
        self.assertEqual(view.tools_path.text(), str(config))
        self.assertIn('not checked', view.tools_status.text())
        self.assertFalse(self.panel.jobs.active)
        self.assertTrue(callable(getattr(view, 'tools_configuration', None)),
                        'Explicit tools configuration parsing is required before dispatch')
        self.assertEqual(view.tools_configuration(), dict(schema_version=1, tools={}))
        config.write_text('{"schema_version": 2, "tools": {}}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'schema_version'):
            view.tools_configuration()
        with patch.dict(os.environ, MOLSCENE_TOOLS_CONFIG=str(self.root / 'missing.json')):
            empty = PotentialPanel(advanced)
        self.addCleanup(empty.deleteLater)
        self.assertEqual(empty.tools_path.text(), '')
        with self.assertRaisesRegex(ValueError, 'configuration'):
            empty.tools_configuration()
        self.assertEqual(self.cmd.get_names('objects'), ['source'])

    def test_dock_close_stops_preview_without_needing_a_native_window(self):
        from molscene.desktop.panel import WorkbenchPanel
        window = self.qt.QtWidgets.QMainWindow()
        dock = self.qt.QtWidgets.QDockWidget(window)
        panel = WorkbenchPanel(self.controller, dock)
        dock.setWidget(panel)
        window.addDockWidget(self.qt.QtCore.Qt.RightDockWidgetArea, dock)
        panel.advanced.timeline.timer.start()
        self.assertTrue(panel.advanced.timeline.timer.isActive())
        dock.close()
        self.assertFalse(panel.advanced.timeline.timer.isActive())
        panel.refresh_timer.stop()
        window.deleteLater()
        self.qt.QtCore.QCoreApplication.sendPostedEvents(window, self.qt.QtCore.QEvent.DeferredDelete)
        self.assertEqual(len(self.controller._timeline_timers), 1)

    def test_missing_tool_config_is_an_actionable_error_without_starting_a_worker(self):
        view = self.advanced().potential
        view.tools_path.clear()
        view.actions['potential_tools_check'].click()
        self.assertIn('configuration', self.panel.status.text())
        self.assertTrue(self.panel.status.text().startswith('Error'))
        self.assertFalse(self.panel.jobs.active)
        self.assertEqual(self.cmd.get_names('objects'), ['source'])

    def test_prepared_pqr_request_does_not_require_a_hidden_live_selection(self):
        from molscene.electrostatics import validate_potential_request
        view = self.advanced().potential
        view.pqr.setText(str(self.root / 'source.pqr'))
        request = view.request_parameters()
        self.assertEqual(request['selection'], 'all')
        self.assertEqual(validate_potential_request(request)['pqr_path'], str(self.root / 'source.pqr'))

    def test_failed_tools_worker_retains_each_diagnostic_in_tools_and_results(self):
        view = self.advanced().potential
        result = dict(operation='tools', passed=False, cancelled=False, tools={
            'apbs': dict(available=True, manifest={'version': '3.4.1', 'architecture': 'x64',
                'argv': ['F:/tools/apbs.exe', '--version'], 'runtime_files': []}, error=None),
            'pdb2pqr': dict(available=False, manifest=None, error='Python 3.11 runtime missing')})
        self.panel.jobs.job = dict(operation='tools', parameters={'tools': {'schema_version': 1, 'tools': {}}})
        self.panel.jobs.last_result = result
        self.panel._job_started(dict(operation='tools', output='tools-result.json', parameters={}))
        self.panel._job_failed('Some configured tools are unavailable')
        self.assertIn('Python 3.11 runtime missing', view.tools_status.text())
        self.assertIn('3.4.1', view.tools_status.text())
        self.assertIn('pdb2pqr', self.panel.result_details.toPlainText())
        self.assertTrue(view.isEnabled())

    def test_structured_tool_status_survives_reusing_the_same_configuration(self):
        view = self.advanced().potential
        config = dict(schema_version=1, tools={'apbs': dict(argv=['F:/tools/apbs.exe'],
            architecture='x64', runtime_files=[], path_dirs=[], timeout_seconds=120,
            memory_limit_mib=1024)})
        view.tools_path.setText('F:/tools/config.json')
        with patch('molscene.external_tools.load_tools', return_value=config):
            view.tools_configuration()
            view.show_tools_result(dict(tools={'apbs': dict(available=True,
                manifest={'version': '3.4.1', 'architecture': 'x64'}, error=None)}))
            view.tools_configuration()
        row = view.tools_table.topLevelItem(0)
        self.assertIn('3.4.1', row.text(1))
        self.assertEqual({row.child(i).text(0) for i in range(row.childCount())},
            {'argv', 'architecture', 'runtime_files', 'path_dirs', 'timeout_seconds',
             'memory_limit_mib', 'actual_version', 'status'})

    def test_advanced_job_history_uses_explicit_request_state_and_keeps_analysis_default(self):
        self.panel.analysis_state.blockSignals(True)
        self.panel.analysis_state.setValue(2)
        self.panel.analysis_state.blockSignals(False)
        for operation, parameters, state in (
            ('potential', {'request': {'state': 3}}, 3),
            ('density', {'request': {'source_state': 4}}, 4),
            ('tools', {'tools': {}}, 1),
            ('contacts', {}, 2),
        ):
            self.panel._job_started(dict(operation=operation, parameters=parameters, output='result.json'))
            self.assertEqual(self.panel._job_state, state, operation)
            self.panel._job_idle()

    def test_narrow_advanced_forms_do_not_create_hidden_horizontal_overflow(self):
        advanced = self.advanced()
        self.panel.tabs.setCurrentIndex(4)
        for name in ('advanced_density', 'advanced_potential'):
            advanced.findChild(self.qt.QtWidgets.QToolButton, name).setChecked(True)
        advanced.potential.tools_status.setText('argv: ' + 'F:/runtime/' + 'long_runtime_directory/' * 20 + 'apbs.exe')
        advanced.potential.diagnostics.setText('Owned surface: ' + 'molecular_copy_' * 25)
        self.panel.resize(360, 720)
        self.panel.show()
        self.app.processEvents()
        advanced.ensureWidgetVisible(advanced.density.actions['density_register'])
        self.assertEqual(advanced.horizontalScrollBar().maximum(), 0)
        self.assertLessEqual(advanced.widget().width(), advanced.viewport().width())
        for layout in advanced.findChildren(self.qt.QtWidgets.QFormLayout):
            for row in range(layout.rowCount()):
                item = layout.itemAt(row, self.qt.QtWidgets.QFormLayout.LabelRole)
                if item and item.widget().isVisibleTo(advanced):
                    self.assertGreater(item.widget().width(), 20, item.widget().text())


class AdvancedWorkflowTests(AdvancedControlTests):
    """Real reviewed controller and core behavior exercised through Qt widgets."""

    def test_capture_edit_reorder_and_remove_change_the_real_timeline(self):
        view = self.advanced().timeline
        view.actions['timeline_new'].click()
        self.assertEqual(view.table.rowCount(), 1)
        self.cmd.turn('y', 45)
        view.frame.setValue(25)
        view.label.setText('second')
        view.actions['timeline_capture'].click()
        self.assertEqual(view.table.rowCount(), 2)
        self.assertEqual(self.controller.advanced_inventory()['timeline']['keyframes'][1]['label'], 'second')
        view.table.selectRow(1)
        view.label.setText('edited')
        view.actions['timeline_update'].click()
        self.assertEqual(self.controller.advanced_inventory()['timeline']['keyframes'][1]['label'], 'edited')
        view.actions['timeline_move_up'].click()
        self.assertEqual(self.controller.advanced_inventory()['timeline']['keyframes'][0]['label'], 'edited')
        self.assertEqual(view.frame.value(), 1)
        self.assertEqual(view.label.text(), 'edited')
        view.actions['timeline_remove'].click()
        self.assertEqual(view.table.rowCount(), 1)
        self.assertEqual(self.controller.advanced_inventory()['timeline']['frame_count'], 1)

    def test_preview_uses_elapsed_clock_and_stops_at_last_frame_without_movie_changes(self):
        view = self.advanced().timeline
        view.actions['timeline_new'].click()
        first = tuple(self.cmd.get_view())
        self.cmd.turn('y', 60)
        last = tuple(self.cmd.get_view())
        view.frame.setValue(25)
        view.actions['timeline_capture'].click()
        self.assertEqual(view.table.rowCount(), 2)
        self.cmd.mset('1 x7')
        native_frames = self.cmd.count_frames()
        view.seek.setValue(1)
        with patch('molscene.desktop.timeline_panel.time.monotonic', return_value=100.0):
            view.actions['timeline_play'].click()
        self.assertTrue(view.timer.isActive())
        with patch('molscene.desktop.timeline_panel.time.monotonic', return_value=100.5):
            view.timer.timeout.emit()
        self.assertEqual(view.seek.value(), 13)
        self.assertEqual(view.frame.value(), 25)
        self.assertNotEqual(tuple(self.cmd.get_view()), first)
        with patch('molscene.desktop.timeline_panel.time.monotonic', return_value=102.0):
            view.timer.timeout.emit()
        self.assertEqual(view.seek.value(), 25)
        self.assertFalse(view.timer.isActive())
        for expected, actual in zip(last, self.cmd.get_view()):
            self.assertAlmostEqual(expected, actual, places=5)
        self.assertEqual(self.cmd.count_frames(), native_frames)

    def test_epoch_change_and_failed_seek_stop_preview_before_further_mutation(self):
        view = self.advanced().timeline
        view.actions['timeline_new'].click()
        view.frame.setValue(25)
        view.actions['timeline_capture'].click()
        view.actions['timeline_play'].click()
        self.assertTrue(view.timer.isActive())
        saved = self.root / 'replacement.pse'
        self.cmd.save(str(saved))
        self.controller.open_session(saved)
        self.panel.refresh()
        self.assertFalse(view.timer.isActive())
        view.actions['timeline_new'].click()
        view.frame.setValue(25)
        view.actions['timeline_capture'].click()
        view.actions['timeline_play'].click()
        self.cmd.scene(self.controller.advanced_inventory()['timeline']['baseline_scene'], 'delete')
        view.timer.timeout.emit()
        self.assertFalse(view.timer.isActive())
        self.assertIn('Error', self.panel.status.text())

    def test_density_widget_load_display_and_registration_preserve_source(self):
        from array import array
        from molscene.grids import Grid, write_dx
        from molscene.density import map_fingerprint
        view = self.advanced().density
        source = self.root / 'asymmetric.dx'
        write_dx(source, Grid((3, 3, 3), (2, 3, 4), ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                              array('d', range(27)), 'unknown', {}))
        view.path.setText(str(source))
        view.actions['density_load'].click()
        self.assertEqual(view.maps.count(), 1, self.panel.status.text())
        original = self.controller.advanced_inventory()['maps'][0]
        before = map_fingerprint(self.cmd, original)
        view.level.setValue(8)
        view.actions['density_apply_display'].click()
        self.assertTrue(any(self.cmd.get_type(name) == 'object:mesh' for name in self.cmd.get_names('objects')))
        view.matrix.setText('[0,-1,0,10, 1,0,0,-2, 0,0,1,3, 0,0,0,1]')
        view.actions['density_register'].click()
        self.assertEqual(view.maps.count(), 2, self.panel.status.text())
        self.assertEqual(map_fingerprint(self.cmd, original), before)
        self.assertIn('verified', view.metadata.text().lower())
        self.assertIn('Registered / transformed placement', view.metadata.text())
        selected_copy, selected_state = view.maps.currentData()
        self.assertNotEqual(selected_copy, original['name'])
        self.assertEqual(selected_state, 1)
        view.output.setText(str(self.root / 'registered-export'))
        with patch.object(self.panel.jobs, 'start') as start:
            view.actions['density_export'].click()
            self.assertEqual(start.call_count, 1, self.panel.status.text())
            request = json.loads(Path(start.call_args.args[0]).read_text())['parameters']['request']
        self.assertEqual(request['map_name'], selected_copy)
        self.assertIsNone(request['registration'], 'Export must not reapply the registration controls')
        names = self.cmd.get_names('objects')
        view.path.setText(str(self.root / 'missing.dx'))
        view.actions['density_load'].click()
        self.assertTrue(self.panel.status.text().startswith('Error'))
        self.assertEqual(self.cmd.get_names('objects'), names)

    def test_density_two_state_selection_refresh_display_registration_and_export(self):
        from tests.advanced_map_fixture import two_state_map
        from molscene.density import map_fingerprint
        name, paths = two_state_map(self.controller, self.root)
        originals = self.controller.advanced_inventory()['maps']
        fingerprints = [map_fingerprint(self.cmd, r) for r in originals]
        source_bytes = [p.read_bytes() for p in paths]
        view = self.advanced().density
        self.panel.refresh()
        for state in (1, 2):
            with self.subTest(state=state):
                view.maps.setCurrentIndex(state - 1)
                self.assertEqual(view.state.value(), state)
                self.assertEqual(view._selected_record()['state'], state)
                self.assertEqual(view._selected_record()['grid']['origin_angstrom'], [state * 3, 2, 1])
                self.panel.refresh()
                self.assertEqual(tuple(view.maps.currentData()), (name, state))
                self.assertEqual(view.state.value(), state)
                display = view.apply_display()
                self.assertEqual((display['source_state'], display['state']), (state, 1))
                view.output.setText(str(self.root / f'export-state-{state}'))
                with patch.object(self.panel.jobs, 'start') as start:
                    view.actions['density_export'].click()
                    self.assertEqual(start.call_count, 1, self.panel.status.text())
                    job = json.loads(Path(start.call_args.args[0]).read_text())
                request = job['parameters']['request']
                self.assertEqual((request['map_name'], request['source_state']), (name, state))
                self.assertEqual(Path(request['map_path']).read_bytes(), source_bytes[state - 1])
                registered = view.register()['record']
                self.assertEqual(registered['state'], 1)
                self.assertEqual(self.cmd.get_volume_field(registered['name'], copy=1).tolist(),
                                 self.cmd.get_volume_field(name, state=state, copy=1).tolist())
                # Register chooses its new copy at the next refresh; reset the
                # preference so the next loop explicitly exercises the source.
                view._preferred_identity = None
        self.assertEqual([map_fingerprint(self.cmd, r) for r in originals], fingerprints)
        self.assertEqual([p.read_bytes() for p in paths], source_bytes)

    def test_timeline_project_and_animation_dispatch_use_the_document_and_output(self):
        view = self.advanced().timeline
        view.actions['timeline_new'].click()
        view.output.setText(str(self.root / 'project'))
        with patch.object(self.panel.jobs, 'start') as start:
            view.actions['timeline_save_project'].click()
            self.assertEqual(start.call_count, 1)
            job = json.loads(Path(start.call_args.args[0]).read_text())
            self.assertEqual(job['operation'], 'timeline_project')
            self.assertEqual(job['parameters']['timeline'], self.controller.advanced_inventory()['timeline'])
            self.assertEqual(job['output'], str(self.root / 'project'))
            view.width.setValue(128)
            view.height.setValue(96)
            view.ray.setChecked(False)
            view.actions['timeline_export'].click()
            job = json.loads(Path(start.call_args.args[0]).read_text())
            self.assertEqual(job['operation'], 'animation')
            self.assertEqual(job['parameters']['render'], dict(width=128, height=96, dpi=150, ray=False))
            self.assertFalse(job['parameters']['mp4'])
            self.assertNotIn('tools', job['parameters'])

    def test_import_potential_dispatch_preserves_explicit_state_and_needs_no_tools(self):
        view = self.advanced().potential
        view.mode.setCurrentText('import')
        view.selection.setText('source')
        from array import array
        from molscene.grids import Grid, write_dx
        path = self.root / 'potential.dx'
        write_dx(path, Grid((3,3,3), (-10,-10,-10), ((10,0,0),(0,10,0),(0,0,10)), array('d', range(27)), 'unknown', {}))
        self.cmd.create('source', 'source', 1, 3)
        view.dx.setText(str(path))
        view.units.setCurrentText('unknown')
        view.display_units.setCurrentText('unknown')
        view.state.setValue(3)
        view.output.setText(str(self.root / 'potential-output'))
        self.panel.analysis_state.setValue(2)
        with patch.object(self.panel.jobs, 'start') as start:
            view.actions['potential_compute'].click()
            self.assertEqual(start.call_count, 1, self.panel.status.text())
            job = json.loads(Path(start.call_args.args[0]).read_text())
        self.assertEqual(job['operation'], 'potential')
        self.panel._job_started(job)
        self.assertEqual(self.panel._job_state, 3)
        self.panel._job_idle()
        self.assertEqual(job['parameters']['request']['state'], 3)
        self.assertEqual(job['parameters']['request']['tools'], {})
        self.assertEqual(self.cmd.count_atoms(job['parameters']['request']['selection'], state=3), 1)

    def test_load_json_uses_controller_validation_before_replacing_timeline(self):
        view = self.advanced().timeline
        view.actions['timeline_new'].click()
        document = self.controller.advanced_inventory()['timeline'].copy()
        document['fps'] = 30
        path = self.root / 'timeline.json'
        path.write_text(json.dumps(document), encoding='utf-8')
        with patch.object(self.qt.QtWidgets.QFileDialog, 'getOpenFileName', return_value=(str(path), 'JSON')):
            view.actions['timeline_load_json'].click()
        self.assertEqual(view.fps.value(), 30)
        path.write_text('{}', encoding='utf-8')
        with patch.object(self.qt.QtWidgets.QFileDialog, 'getOpenFileName', return_value=(str(path), 'JSON')):
            view.actions['timeline_load_json'].click()
        self.assertTrue(self.panel.status.text().startswith('Error'))
        self.assertEqual(self.controller.advanced_inventory()['timeline']['fps'], 30)

    def test_preview_tick_does_not_rebuild_or_hash_the_entire_live_inventory(self):
        view = self.advanced().timeline
        view.actions['timeline_new'].click()
        view.frame.setValue(25)
        view.actions['timeline_capture'].click()
        view.actions['timeline_play'].click()
        with patch.object(self.controller, 'advanced_inventory', wraps=self.controller.advanced_inventory) as inventory:
            view.timer.timeout.emit()
        self.assertEqual(inventory.call_count, 0, 'Preview ticks must leave full inventory refresh to the existing refresh timer')

    def wait_job(self):
        import time
        from pymol.Qt import PYQT_NAME
        QTest = __import__(PYQT_NAME + '.QtTest', fromlist=['QTest']).QTest
        deadline = time.monotonic() + 45
        while self.panel.jobs.active and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(10)
        self.assertFalse(self.panel.jobs.active, self.panel.status.text())
        self.assertFalse(self.panel.status.text().startswith('Error'), self.panel.status.text())

    def test_project_open_validates_bundle_then_replaces_session_and_stops_timer(self):
        view = self.advanced().timeline
        view.actions['timeline_new'].click()
        output = self.root / 'project'
        view.output.setText(str(output))
        view.actions['timeline_save_project'].click()
        self.wait_job()
        self.cmd.pseudoatom('later', pos=[5, 5, 5])
        self.panel.refresh()
        view.timer.start()
        with patch.object(self.qt.QtWidgets.QFileDialog, 'getExistingDirectory', return_value=str(output)):
            view.actions['timeline_open_project'].click()
        self.assertNotIn('later', self.cmd.get_names('objects'))
        self.assertFalse(view.timer.isActive())
        self.assertEqual(view.table.rowCount(), 1)
        self.cmd.pseudoatom('preserve_after_invalid_open', pos=[8, 8, 8])
        (output / 'timeline.json').write_text('{}')
        with patch.object(self.qt.QtWidgets.QFileDialog, 'getExistingDirectory', return_value=str(output)):
            view.actions['timeline_open_project'].click()
        self.assertIn('preserve_after_invalid_open', self.cmd.get_names('objects'))
        self.assertTrue(self.panel.status.text().startswith('Error'))

    def test_potential_worker_loads_owned_copy_and_stale_source_blocks_ramp(self):
        from array import array
        from molscene.grids import Grid, write_dx
        advanced = self.advanced()
        view = advanced.potential
        path = self.root / 'potential.dx'
        write_dx(path, Grid((3,3,3), (-10,-10,-10), ((10,0,0),(0,10,0),(0,0,10)), array('d', range(-13,14)), 'unknown', {}))
        view.mode.setCurrentText('import')
        view.selection.setText('source')
        view.dx.setText(str(path))
        view.output.setText(str(self.root / 'potential'))
        advanced.timeline.width.setValue(64)
        advanced.timeline.height.setValue(48)
        view.actions['potential_compute'].click()
        self.wait_job()
        report = self.root / 'potential' / 'report.json'
        before = self.cmd.get_atom_coords('source')
        with patch.object(self.qt.QtWidgets.QFileDialog, 'getOpenFileName', return_value=(str(report), 'JSON')):
            view.actions['potential_load_result'].click()
        self.assertGreater(len(self.cmd.get_names('objects')), 1, self.panel.status.text())
        self.assertEqual(self.cmd.get_atom_coords('source'), before)
        from molscene.desktop.controller import WorkbenchController
        unverified_controller = WorkbenchController(self.cmd, self.root)
        names = self.cmd.get_names('objects')
        with patch.object(advanced, 'controller', unverified_controller), patch.object(
                self.qt.QtWidgets.QFileDialog, 'getOpenFileName', return_value=(str(report), 'JSON')):
            view.actions['potential_load_result'].click()
        self.assertTrue(self.panel.status.text().startswith('Error'))
        self.assertEqual(self.cmd.get_names('objects'), names)
        view.ramp_limit.setValue(3)
        view.actions['potential_ramp_apply'].click()
        loaded = self.controller.results[-1]
        self.assertEqual(loaded['loaded']['ramp_values'], [-3, 0, 3])
        self.cmd.alter('source', 'vdw=vdw+0.2')
        view.ramp_limit.setValue(4)
        view.actions['potential_ramp_apply'].click()
        self.assertTrue(self.panel.status.text().startswith('Error'))
        self.assertEqual(loaded['loaded']['ramp_values'], [-3, 0, 3])
        names = self.cmd.get_names('objects')
        with patch.object(self.qt.QtWidgets.QFileDialog, 'getOpenFileName', return_value=(str(report), 'JSON')):
            view.actions['potential_load_result'].click()
        self.assertEqual(self.cmd.get_names('objects'), names)

    def test_density_export_worker_and_result_loading_preserve_original(self):
        from array import array
        from molscene.grids import Grid, write_dx
        view = self.advanced().density
        path = self.root / 'density.dx'
        write_dx(path, Grid((3,3,3), (-5,-5,-5), ((5,0,0),(0,5,0),(0,0,5)), array('d', range(27)), 'unknown', {}))
        view.path.setText(str(path))
        view.actions['density_load'].click()
        original, original_state = view.maps.currentData()
        self.assertEqual(original_state, 1)
        self.panel.advanced.timeline.width.setValue(64)
        self.panel.advanced.timeline.height.setValue(48)
        view.output.setText(str(self.root / 'density'))
        view.actions['density_export'].click()
        self.assertTrue(self.panel.jobs.active, self.panel.status.text())
        self.wait_job()
        report = self.root / 'density' / 'report.json'
        with patch.object(self.qt.QtWidgets.QFileDialog, 'getOpenFileName', return_value=(str(report), 'JSON')):
            self.panel.advanced.potential.actions['potential_load_result'].click()
        self.assertEqual(view.maps.count(), 2, self.panel.status.text())
        self.assertIn(original, self.cmd.get_names('objects'))


def _isolated_controls(case):
    """Keep each public case identity while running its QWidget body in a child.

    Discovery may already have created a process-global QCoreApplication. A
    fresh child owns QApplication for the entire original class, and the
    parent accepts only its complete actual successful TestResult identities.
    """
    methods = unittest.defaultTestLoader.getTestCaseNames(case)
    name = case.__name__

    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory(prefix='advanced-controls-') as folder:
            report_path = Path(folder) / 'result.json'
            root = Path(__file__).resolve().parents[1]
            process = subprocess.run([sys.executable, '-B', '-m', 'tests.test_advanced_gui',
                '--advanced-controls-child', name, str(report_path)], cwd=root,
                env=dict(os.environ, PYTHONPATH=str(root), QT_QPA_PLATFORM='offscreen'),
                capture_output=True, text=True, timeout=180)
            # Retain all backend and child status diagnostics in the raw log.
            print(process.stdout + process.stderr, end='', flush=True)
            if process.returncode != 0:
                raise AssertionError(f'{name} child exited {process.returncode}')
            report = json.loads(report_path.read_text(encoding='utf-8'))
            expected = [f'__main__.{name}.{method}' for method in methods]
            if report['successes'] != expected or report['tests_run'] != len(expected):
                raise AssertionError(f'Incomplete isolated controls: {report}')
            cls.child_successes = report['successes']
            print('ADVANCED_CONTROLS ' + json.dumps(report), flush=True)

    def passed(self):
        self.assertIn(f'__main__.{name}.{self._testMethodName}', self.child_successes)

    isolated = type(name, (unittest.TestCase,), dict(__module__=__name__,
        setUpClass=setUpClass, **{method: passed for method in methods}))
    return unittest.skipUnless(HAS_GUI, 'requires PyMOL and its Qt binding')(isolated)


if '--advanced-controls-child' not in sys.argv:
    AdvancedControlTests = _isolated_controls(AdvancedControlTests)
    AdvancedWorkflowTests = _isolated_controls(AdvancedWorkflowTests)


@unittest.skipUnless(os.environ.get('MOLSCENE_REQUIRE_ADVANCED') == '1' or
                     os.environ.get('MOLSCENE_ADVANCED_NATIVE') == '1', 'requires allocated native desktop focus')
class AdvancedNativeTests(unittest.TestCase):
    def test_native_advanced_timeline_density_potential_and_close(self):
        from tests import advanced_native
        self.assertTrue(callable(getattr(advanced_native, 'run_native', None)), 'Complete native Advanced harness is required')
        self.assertTrue(HAS_GUI, 'Native Advanced gate requires actual PyMOL/Qt')
        self.assertTrue(os.environ.get('MOLSCENE_TOOLS_CONFIG'), 'Real APBS tools config is required')
        artifacts = os.environ.get('MOLSCENE_TEST_ARTIFACTS')
        if artifacts:
            Path(artifacts).mkdir(parents=True, exist_ok=True)
            root = Path(tempfile.mkdtemp(prefix='advanced-', dir=artifacts))
        else:
            temporary = tempfile.TemporaryDirectory(prefix='advanced-')
            self.addCleanup(temporary.cleanup)
            root = Path(temporary.name)
        print('Advanced native evidence: ' + str(root), flush=True)
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        env.pop('QT_QPA_PLATFORM', None)
        from tests.owned_processes import run_native_child
        process, stdout, stderr, native_pid = run_native_child(
            [sys.executable, '-B', '-m', 'tests.advanced_native', str(root)], root, timeout=300,
            cwd=Path(__file__).resolve().parents[1], env=env)
        (root / 'process.log').write_text(stdout + stderr, encoding='utf-8')
        self.assertEqual(process.returncode, 0, stdout + stderr)
        self.assertTrue((root / 'advanced-native.json').is_file(), stdout + stderr)
        report = json.loads((root / 'advanced-native.json').read_text())
        self.assertTrue(report['closed'])
        self.assertTrue(report['original_sources_preserved'])
        self.assertTrue(report['cancel_descendants_cleaned'])
        self.assertTrue(report['potential_numeric_calibration'])
        from tests.owned_processes import verify_native_report
        verify_native_report(root, 'advanced-native.json', native_pid, process.returncode)


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--advanced-controls-child':
        name, report_path = sys.argv[2:]
        if name not in ('AdvancedControlTests', 'AdvancedWorkflowTests'):
            raise ValueError('Unknown control suite')

        class ControlResult(unittest.TextTestResult):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.successes = []

            def addSuccess(self, test):
                super().addSuccess(test)
                self.successes.append(test.id())

        suite = unittest.defaultTestLoader.loadTestsFromTestCase(globals()[name])
        result = unittest.TextTestRunner(verbosity=2, resultclass=ControlResult).run(suite)
        Path(report_path).write_text(json.dumps(dict(pid=os.getpid(), parent_pid=os.getppid(),
            tests_run=result.testsRun, successes=result.successes)), encoding='utf-8')
        raise SystemExit(not result.wasSuccessful() or bool(result.skipped))
    else:
        unittest.main()
