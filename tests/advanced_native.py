"""Advanced acceptance through the official desktop launcher and real workers."""
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time


REQUIRED_VISUAL_COMPARISONS = (
    'timeline_first_last', 'density_mesh_levels', 'density_surface_levels',
    'density_registered_world_transform', 'density_volume_visible_hidden',
    'potential_calibrated_surface',
)


def capture_framebuffer(viewport, destination):
    """Read the presented native GL framebuffer, never cmd.png/ray output."""
    if not viewport.isVisible() or not viewport.isValid():
        raise AssertionError('Native viewport must be visible with a valid GL context')
    from tests.test_desktop import grab_native_framebuffer
    image = grab_native_framebuffer(viewport)
    if image.isNull() or image.width() < 32 or image.height() < 32:
        raise AssertionError('Native framebuffer is empty or unexpectedly small')
    if not image.save(str(destination)):
        raise AssertionError('Could not persist native framebuffer evidence')
    return Path(destination)


def visual_difference(before, after):
    """Quantify actual visible changes; owned object existence is insufficient."""
    import numpy as np
    from PIL import Image
    with Image.open(before) as first, Image.open(after) as second:
        if first.size != second.size:
            raise AssertionError('Framebuffer size changed during visual comparison')
        left = np.asarray(first.convert('RGB'), dtype=np.int16)
        right = np.asarray(second.convert('RGB'), dtype=np.int16)
    magnitude = np.abs(left - right)
    changed = magnitude.max(axis=2) > 12
    return dict(changed_pixels=int(changed.sum()), changed_fraction=float(changed.mean()),
                mean_channel_difference=float(magnitude.mean()),
                before_sha256=hashlib.sha256(Path(before).read_bytes()).hexdigest(),
                after_sha256=hashlib.sha256(Path(after).read_bytes()).hexdigest())


def wait_for(app, predicate, timeout=30, message='Native workflow timed out'):
    from pymol.Qt import PYQT_NAME
    QTest = __import__(PYQT_NAME + '.QtTest', fromlist=['QTest']).QTest
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        QTest.qWait(10)
    raise AssertionError(message)


def source_state(cmd, names, states=(1,)):
    """Capture original atoms/radii/matrices for before/after preservation checks."""
    result = {}
    for name in names:
        records = {}
        for state in states:
            model = cmd.get_model(name, state=state)
            records[str(state)] = dict(
                atoms=[dict(index=atom.index, coord=list(atom.coord), vdw=atom.vdw,
                            partial_charge=atom.partial_charge, name=atom.name,
                            resi=atom.resi, resn=atom.resn) for atom in model.atom],
                matrix=list(cmd.get_object_matrix(name, state=state)))
        result[name] = records
    raw = json.dumps(result, sort_keys=True, allow_nan=False).encode('utf-8')
    return dict(sha256=hashlib.sha256(raw).hexdigest(), objects=result)


def write_assertions(directory, report):
    """A draft or object-only run cannot accidentally publish a passing gate."""
    required = (
        'closed', 'worker_responsive', 'cancel_descendants_cleaned',
        'original_sources_preserved', 'potential_numeric_calibration',
        'potential_radius_convention', 'result_copy_preserves_session',
    )
    for name in required:
        if report.get(name) is not True:
            raise AssertionError('Missing native acceptance assertion: ' + name)
    for name in REQUIRED_VISUAL_COMPARISONS:
        evidence = report.get('visual', {}).get(name, {})
        if evidence.get('changed_pixels', 0) < 20 or evidence.get('changed_fraction', 0) < 0.001:
            raise AssertionError('No demonstrated native visible geometry change: ' + name)
    destination = Path(directory) / 'advanced-native.json'
    destination.write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
    return destination


def run_native(directory):
    """Exercise real widgets, native presented frames, solver and worker lifecycle."""
    from array import array
    from unittest.mock import patch
    import traceback
    import numpy as np
    from PIL import Image
    from molscene import desktop
    from molscene.grids import Grid, write_dx, read_dx, grid_point
    from molscene.density import map_fingerprint
    from tests.test_native_render import process_alive
    desktop._gui_runtime()
    from pymol.Qt import QtCore, QtWidgets, PYQT_NAME
    from pmg_qt import pymol_qt_gui
    from pymol import plugins
    QTest = __import__(PYQT_NAME + '.QtTest', fromlist=['QTest']).QTest
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    plugins.PYMOLPLUGINSRC = str(root / 'isolated-preferences.py')
    report = dict(visual={}, owned_pids=[os.getpid()], closed=False)
    real_attach = desktop.attach_workbench
    scheduled = False
    failed = []

    def step(message):
        with (root / 'steps.txt').open('a', encoding='utf-8') as handle:
            handle.write(message + '\n')
        print(message, flush=True)

    def attach(parent=None):
        nonlocal scheduled
        dock = real_attach(parent)
        if not scheduled:
            scheduled = True
            QtCore.QTimer.singleShot(240000, dock.parentWidget().close)
            QtCore.QTimer.singleShot(700, lambda: exercise(dock))
        return dock

    def exercise(dock):
        window = dock.parentWidget()
        panel = dock.widget()
        cmd = panel.controller.cmd
        app = QtWidgets.QApplication.instance()
        timeline, density, potential = panel.advanced.timeline, panel.advanced.density, panel.advanced.potential
        viewport = window.pymolwidget
        events, heartbeat = [], [0]
        beat = QtCore.QTimer(window)
        beat.setInterval(10)
        beat.timeout.connect(lambda: heartbeat.__setitem__(0, heartbeat[0] + 1))
        beat.start()
        panel.jobs.progress.connect(events.append)
        panel.jobs.progress.connect(lambda event: report['owned_pids'].append(int(event['pid']))
            if event.get('stage') == 'tool_ready' and type(event.get('pid')) is int else None)
        def click(name):
            widget = panel.findChild(QtWidgets.QPushButton, name)
            assert widget is not None and widget.isEnabled(), name + ': unavailable; ' + panel.status.text()
            panel.advanced.ensureWidgetVisible(widget)
            QTest.mouseClick(widget, QtCore.Qt.LeftButton)
            app.processEvents()
            assert not panel.status.text().startswith('Error'), panel.status.text()
        def section(name):
            toggle = panel.findChild(QtWidgets.QToolButton, name)
            if not toggle.isChecked():
                QTest.mouseClick(toggle, QtCore.Qt.LeftButton)
        def image(name):
            cmd.refresh()
            QTest.qWait(180)
            return capture_framebuffer(viewport, root / (name + '.png'))
        def difference(name, first, second):
            report['visual'][name] = visual_difference(first, second)
            assert report['visual'][name]['changed_pixels'] >= 20, name + ': no visible geometry change'
        def worker():
            assert panel.jobs.active, panel.status.text()
            count = heartbeat[0]
            wait_for(app, lambda: int(panel.jobs.process.processId()) > 0 if panel.jobs.active else True)
            if panel.jobs.active:
                report['owned_pids'].append(int(panel.jobs.process.processId()))
            wait_for(app, lambda: not panel.jobs.active, timeout=90, message='Worker did not complete: ' + panel.status.text())
            assert not panel.status.text().startswith('Error'), panel.status.text()
            assert heartbeat[0] - count >= 2, 'Qt heartbeat stopped during worker'
            report['worker_responsive'] = True
        def load_result(path):
            with patch.object(QtWidgets.QFileDialog, 'getOpenFileName', return_value=(str(path), 'JSON')):
                click('potential_load_result')
        class CloseObserver(QtCore.QObject):
            def eventFilter(self, watched, event):
                if watched is window and event.type() == QtCore.QEvent.Close:
                    report['closed'] = True
                    write_assertions(root, report)
                return False
        observer = CloseObserver(window)
        window._advanced_test_observer = observer
        window.installEventFilter(observer)
        try:
            step('native startup and compact layout')
            window.resize(1280, 820)
            window.resizeDocks([dock], [360], QtCore.Qt.Horizontal)
            panel.tabs.setCurrentIndex(4)
            QTest.qWait(200)
            assert viewport.isValid() and viewport.isVisible()
            assert panel.tabs.tabText(4) == 'Advanced'
            assert viewport.width() >= 350, 'Advanced form consumed native viewport'
            assert dock.minimumSizeHint().width() < 480, 'Advanced fields force an oversized dock'
            assert panel.advanced.horizontalScrollBar().maximum() == 0, 'Advanced form has clipped horizontal overflow'
            report['dock_width'] = dock.width()
            report['viewport_width'] = viewport.width()
            cmd.reinitialize()
            cmd.load(str(Path(__file__).resolve().parents[1] / 'examples/data/1ubq.pdb'), 'original')
            cmd.hide('everything')
            cmd.show('cartoon', 'original')
            cmd.color('green', 'original')
            cmd.orient('original')
            cmd.bg_color('black')
            panel.refresh()
            original_before = source_state(cmd, ['original'])
            (root / 'original-before.json').write_text(json.dumps(original_before, indent=2), encoding='utf-8')
            step('timeline widgets and presented frames')
            click('timeline_new')
            cmd.turn('y', 65)
            timeline.frame.setValue(25)
            timeline.label.setText('Rotated')
            click('timeline_capture')
            click('timeline_previous')
            first = image('timeline-first')
            timeline.seek.setValue(25)
            last = image('timeline-last')
            difference('timeline_first_last', first, last)
            timeline.seek.setValue(1)
            click('timeline_play')
            QTest.qWait(180)
            assert timeline.seek.value() > 1
            click('timeline_pause')
            assert not timeline.timer.isActive()
            timeline.output.setText(str(root / 'project'))
            click('timeline_save_project')
            worker()
            assert (root / 'project/source.pse').is_file()
            step('density mesh, surface, rigid registration and visible volume')
            cmd.disable('original')
            section('advanced_density')
            values = array('d', [math.exp(-((i*.5-6)/2)**2-((j*.5-6)/2.5)**2-((k*.5-6)/3)**2)
                + .35*math.exp(-((i*.5-8)/1.1)**2-((j*.5-5)/1.3)**2-((k*.5-6)/1.2)**2)
                for i in range(25) for j in range(25) for k in range(25)])
            dx = root / 'asymmetric.dx'
            write_dx(dx, Grid((25,25,25), (-6,-5,-4), ((.5,0,0),(0,.5,0),(0,0,.5)), values, 'unknown', {}))
            density.path.setText(str(dx))
            click('density_load')
            original_map = panel.controller.advanced_inventory()['maps'][0]
            map_before = map_fingerprint(cmd, original_map)
            density.level.setValue(.15)
            click('density_apply_display')
            cmd.zoom(original_map['name'], buffer=0)
            first = image('mesh-low')
            density.level.setValue(.65)
            click('density_apply_display')
            difference('density_mesh_levels', first, image('mesh-high'))
            density.visible.setChecked(False)
            click('density_apply_display')
            density.kind.setCurrentText('surface')
            density.visible.setChecked(True)
            density.level.setValue(.15)
            click('density_apply_display')
            first = image('surface-low')
            density.level.setValue(.65)
            click('density_apply_display')
            difference('density_surface_levels', first, image('surface-high'))
            first = image('registration-source')
            density.visible.setChecked(False)
            click('density_apply_display')
            density.matrix.setText('[0,-1,0,2, 1,0,0,-1, 0,0,1,1, 0,0,0,1]')
            click('density_register')
            density.visible.setChecked(True)
            click('density_apply_display')
            difference('density_registered_world_transform', first, image('registration-copy'))
            assert map_fingerprint(cmd, original_map) == map_before
            density.visible.setChecked(False)
            click('density_apply_display')
            original_index = next((i for i in range(density.maps.count())
                if tuple(density.maps.itemData(i) or ()) == (original_map['name'], original_map['state'])), -1)
            assert original_index >= 0, 'Original map state is missing from density inventory'
            density.maps.setCurrentIndex(original_index)
            density.kind.setCurrentText('volume')
            density.volume_ramp.setText('[0,0,0,1,0, 0.15,0,1,1,0.15, 0.4,0,1,0,0.5, 0.8,1,0.5,0,0.8, 1.4,1,0,0,1]')
            density.visible.setChecked(True)
            click('density_apply_display')
            first = image('volume-visible')
            density.visible.setChecked(False)
            click('density_apply_display')
            difference('density_volume_visible_hidden', first, image('volume-hidden'))
            density.visible.setChecked(True)
            click('density_apply_display')
            timeline.width.setValue(128)
            timeline.height.setValue(96)
            timeline.ray.setChecked(False)
            section('advanced_potential')
            step('real APBS through widgets with numeric and visible sign calibration')
            cmd.disable('all')
            configuration = json.loads(Path(os.environ['MOLSCENE_TOOLS_CONFIG']).read_text(encoding='utf-8-sig'))
            configuration['tools'] = {'apbs': configuration['tools']['apbs']}
            config_path = root / 'tools.json'
            config_path.write_text(json.dumps(configuration), encoding='utf-8')
            potential.tools_path.setText(str(config_path))
            click('potential_tools_check')
            worker()
            assert '3.4.1' in potential.tools_status.text()
            pqr = root / 'dipole.pqr'
            pqr.write_text('ATOM 1 P ION 1 -3.0 0.0 0.0 1.0 1.5\nATOM 2 N ION 2 3.0 0.0 0.0 -1.0 1.5\n')
            pqr_bytes = pqr.read_bytes()
            potential.pqr.setText(str(pqr))
            potential.output.setText(str(root / 'potential'))
            for axis in potential.grid:
                axis.setCurrentText('65')
            potential.solute_dielectric.setValue(80)
            potential.solvent_dielectric.setValue(80)
            potential.salt.setValue(0)
            potential.coarse_padding.setValue(48)
            potential.fine_padding.setValue(24)
            potential.ramp_limit.setValue(5)
            timeline.ray.setChecked(True)
            click('potential_compute')
            worker()
            load_result(root / 'potential/report.json')
            loaded = potential.loaded_record['loaded']
            assert loaded['ramp_values'] == [-5, 0, 5] and loaded['ramp_colors'] == ['red', 'white', 'blue']
            assert loaded['units'] == 'kBT/e'
            assert loaded['radius_convention'] == 'PQR electrostatic radii (vdw=elec_radius)'
            assert all(abs(atom.vdw-1.5) < 1e-6 for atom in cmd.get_model(loaded['molecule']).atom)
            assert cmd.get_setting_int('surface_ramp_above_mode', loaded['molecule']) == 0
            report['potential_radius_convention'] = True
            grid = read_dx(root / 'potential/potential.dx')
            samples = []
            for x in (-7, 7):
                indices = [round((p-o)/grid.basis[a][a]) for a,(p,o) in enumerate(zip((x,0,0),grid.origin))]
                point = grid_point(grid, *indices)
                distances = [math.sqrt(sum((a-b)**2 for a,b in zip(point, center))) for center in ((-3,0,0),(3,0,0))]
                expected = (1.602176634e-19)**2/(4*math.pi*8.8541878128e-12*80*1e-10*1.380649e-23*298.15) * (1/distances[0]-1/distances[1])
                i,j,k = indices
                actual = grid.values[(i*grid.shape[1]+j)*grid.shape[2]+k]
                samples.append(dict(point=point, actual=actual, expected=expected, relative_error=abs(actual-expected)/abs(expected)))
            assert max(sample['relative_error'] for sample in samples) < .12, samples
            assert samples[0]['actual'] > 0 > samples[1]['actual']
            report['potential_numeric_calibration'] = True
            report['potential_samples'] = samples
            cmd.orient(loaded['molecule'])
            cmd.zoom(loaded['molecule'], buffer=3)
            first = image('potential-calibrated')
            with Image.open(first) as surface:
                rgb = np.asarray(surface.convert('RGB'), dtype=np.int16)
            red = int(((rgb[:,:,0] > rgb[:,:,2]+30) & (rgb[:,:,0] > 70)).sum())
            blue = int(((rgb[:,:,2] > rgb[:,:,0]+30) & (rgb[:,:,2] > 70)).sum())
            assert min(red, blue) > 30, (red, blue)
            report['potential_sign_pixels'] = dict(red=red, blue=blue)
            cmd.disable(loaded['molecule'])
            difference('potential_calibrated_surface', first, image('potential-hidden'))
            cmd.enable(loaded['molecule'])
            potential.ramp_limit.setValue(3)
            click('potential_ramp_apply')
            assert potential.loaded_record['loaded']['ramp_values'] == [-3,0,3]
            pqr.write_bytes(pqr_bytes + b'# changed source\n')
            potential.ramp_limit.setValue(4)
            QTest.mouseClick(potential.actions['potential_ramp_apply'], QtCore.Qt.LeftButton)
            assert panel.status.text().startswith('Error')
            assert potential.loaded_record['loaded']['ramp_values'] == [-3,0,3]
            pqr.write_bytes(pqr_bytes)
            step('responsive native renderer cancellation and descendant cleanup')
            cmd.disable('all')
            cmd.enable(original_map['name'] + '_volume')
            cmd.zoom(original_map['name'], buffer=0)
            click('timeline_new')
            timeline.frame.setValue(500)
            cmd.turn('y', 40)
            click('timeline_capture')
            timeline.output.setText(str(root / 'cancel-animation'))
            timeline.ray.setChecked(False)
            events.clear()
            click('timeline_export')
            wait_for(app, lambda: panel.jobs.active and int(panel.jobs.process.processId()) > 0)
            report['owned_pids'].append(int(panel.jobs.process.processId()))
            wait_for(app, lambda: any(row.get('stage') == 'render' for row in events) or not panel.jobs.active, timeout=60)
            assert panel.jobs.active, 'Animation completed before cancellation could be exercised'
            QTest.mouseClick(panel.cancel_job, QtCore.Qt.LeftButton)
            wait_for(app, lambda: not panel.jobs.active, timeout=30)
            assert not (root / 'cancel-animation').exists()
            report['owned_pids'].extend(int(row['pid']) for row in events if row.get('stage') == 'tool_ready')
            failures = list(root.glob('.molscene-failure-*/logs/native.result.log'))
            assert failures, 'No retained renderer cancellation process evidence'
            for path in failures:
                outcome = json.loads(path.read_text())
                report['owned_pids'].extend([outcome['native_pid'], outcome['parent_pid']])
            for path in (root / 'potential/report.json',):
                rendering = json.loads(path.read_text()).get('analysis', {}).get('rendering', {})
                report['owned_pids'].extend(rendering.get('process', {}).get(key) for key in ('native_pid','parent_pid','guardian_pid')
                                          if rendering.get('process', {}).get(key))
            report['owned_pids'] = sorted(set(report['owned_pids']))
            wait_for(app, lambda: not any(process_alive(pid) for pid in report['owned_pids'] if pid != os.getpid()), timeout=10)
            report['cancel_descendants_cleaned'] = True
            step('mandatory associated-volume export and fresh result copy')
            density.output.setText(str(root / 'density'))
            click('density_export')
            worker()
            density_report = json.loads((root / 'density/report.json').read_text())
            assert density_report['analysis']['rendering']['backend'] == 'native_opengl'
            rendering_process = density_report['analysis']['rendering']['process']
            report['owned_pids'].extend(rendering_process[key] for key in
                ('native_pid', 'parent_pid', 'guardian_pid') if rendering_process.get(key))
            report['owned_pids'] = sorted(set(report['owned_pids']))
            wait_for(app, lambda: not any(process_alive(pid) for pid in report['owned_pids']
                if pid != os.getpid()), timeout=10)
            load_result(root / 'density/report.json')
            assert panel.controller.results[-1]['loaded']['record']['name'] != original_map['name']
            assert cmd.count_atoms('original') == len(original_before['objects']['original']['1']['atoms'])
            report['result_copy_preserves_session'] = True
            original_after = source_state(cmd, ['original'])
            (root / 'original-after.json').write_text(json.dumps(original_after, indent=2), encoding='utf-8')
            assert original_before == original_after
            assert map_fingerprint(cmd, original_map) == map_before
            report['original_sources_preserved'] = True
            report['map_fingerprint_before'] = map_before
            report['map_fingerprint_after'] = map_fingerprint(cmd, original_map)
            report['events'] = events
            panel.tabs.setCurrentIndex(4)
            app.processEvents()
            assert panel.advanced.horizontalScrollBar().maximum() == 0, 'Populated results clipped the Advanced form'
            assert panel.advanced.widget().width() <= panel.advanced.viewport().width()
            for layout in panel.advanced.findChildren(QtWidgets.QFormLayout):
                for row in range(layout.rowCount()):
                    item = layout.itemAt(row, QtWidgets.QFormLayout.LabelRole)
                    if item and item.widget().isVisibleTo(panel.advanced):
                        assert item.widget().width() > 20, 'Collapsed field label: ' + item.widget().text()
            window.grab().save(str(root / 'advanced-window.png'))
            step('normal dock and native-window close')
            timeline.timer.start()
            dock.close()
            assert not timeline.timer.isActive()
            beat.stop()
            window.close()
        except BaseException:
            failure = traceback.format_exc()
            failed.append(failure)
            report['passed'] = False
            (root / 'partial-native.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            (root / 'failure.txt').write_text(failure, encoding='utf-8')
            window.grab().save(str(root / 'failure.png'))
            print(failure, flush=True)
            if panel.jobs.active:
                panel.jobs.cancel()
                wait_for(app, lambda: not panel.jobs.active, timeout=15)
            window.removeEventFilter(observer)
            beat.stop()
            window.close()
    desktop.attach_workbench = attach
    desktop.launch(workspace=root)
    if failed or not (root / 'advanced-native.json').is_file():
        raise SystemExit(1)


if __name__ == '__main__':
    run_native(sys.argv[-1])
