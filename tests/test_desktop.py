"""Desktop contract tests; live Qt startup is isolated from unittest lifetime."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HAS_PYMOL = importlib.util.find_spec('pymol2') is not None
HAS_QT = any(importlib.util.find_spec(name) is not None for name in ('PySide6', 'PySide2', 'PyQt6', 'PyQt5'))


class DesktopDependencyTests(unittest.TestCase):
    def test_without_site_packages_import_stays_lazy_and_gui_reports_dependency(self):
        script = "import sys; from molscene import desktop; assert 'pymol' not in sys.modules; desktop.launch()"
        result = subprocess.run([sys.executable, '-S', '-c', script], cwd=ROOT,
                                env=dict(os.environ, PYTHONPATH=str(ROOT)), capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('GUI requires PyMOL and PySide6', result.stderr)

    def test_import_is_lazy_and_missing_gui_has_actionable_diagnostic(self):
        from molscene import desktop
        with patch('importlib.import_module', side_effect=ImportError('missing Qt')):
            with self.assertRaisesRegex(RuntimeError, r'GUI.*PySide6'):
                desktop.launch()


@unittest.skipUnless(HAS_PYMOL, 'requires the user-provided PyMOL interpreter')
class ControllerTests(unittest.TestCase):
    def setUp(self):
        from pymol2 import PyMOL
        from molscene.desktop.controller import WorkbenchController
        self.runtime = PyMOL()
        self.runtime.start()
        self.addCleanup(self.runtime.stop)
        self.cmd = self.runtime.cmd
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.controller = WorkbenchController(self.cmd, self.base)
        self.cmd.pseudoatom('one', pos=[0, 0, 0], name='CA', chain='A')
        self.cmd.pseudoatom('two', pos=[3, 4, 0], name='O', chain='B')

    def test_object_actions_are_explicit_and_refresh_native_changes(self):
        c = self.controller
        self.assertEqual([o['name'] for o in c.objects()], ['one', 'two'])
        with self.assertRaises(ValueError):
            c.delete_objects([])
        c.set_visible(['one'], False)
        self.assertNotIn('one', self.cmd.get_names('objects', enabled_only=1))
        self.assertIn('two', self.cmd.get_names('objects', enabled_only=1))
        c.rename_object('one', 'renamed')
        self.assertEqual(set(self.cmd.get_names('objects')), {'renamed', 'two'})
        with self.assertRaises(ValueError):
            c.rename_object('renamed', 'two')
        self.cmd.set_name('two', 'external')
        self.assertEqual({o['name'] for o in c.objects()}, {'renamed', 'external'})
        c.delete_objects(['renamed'])
        self.assertEqual(self.cmd.get_names('objects'), ['external'])

    def test_selection_capture_rejects_empty_invalid_and_multi_atom(self):
        c = self.controller
        for selection in ('none', 'name ('):
            with self.assertRaises(ValueError):
                c.capture_selection(selection)
        with self.assertRaises(ValueError):
            c.capture_selection('all', single=True)
        self.cmd.select('sele', 'one')
        self.cmd.select('pk1', 'two')
        self.assertEqual(c.capture_current()['atoms'], 1)
        self.assertEqual(c.capture_pick(1)['objects'], ['two'])
        captured = c.capture_selection('one', single=True)
        self.cmd.select('sele', 'two')
        self.assertEqual(self.cmd.count_atoms(captured['selection']), 1)
        self.assertEqual(captured['identities'][0]['object'], 'one')
        for state in (0, 2, True):
            with self.assertRaises(ValueError):
                c.capture_selection('one', state=state)
        # PyMOL singleton state fallback must not validate state 2.
        self.cmd.create('two_states', 'one', 1, 1)
        self.cmd.create('two_states', 'two', 1, 2)
        mixed = c.capture_selection('one or two_states', state=2)
        self.assertEqual(mixed['objects'], ['two_states'])
        self.assertEqual(mixed['atoms'], 1)
        self.assertEqual(c.capture_selection('two_states', state=2)['state'], 2)

    def test_capture_spatial_membership_uses_requested_state_and_preserves_view(self):
        self.cmd.delete('all')
        self.cmd.pseudoatom('moving', pos=[0, 0, 0])
        self.cmd.load_coordset([[10, 0, 0]], 'moving', state=2)
        self.cmd.pseudoatom('reference', pos=[0, 0, 0])
        self.cmd.load_coordset([[0, 0, 0]], 'reference', state=2)
        expression = 'moving within 1 of reference'
        self.cmd.frame(1)
        view = self.cmd.get_view()
        self.assertEqual(self.cmd.select('oracle', expression, state=2), 0)
        names = self.cmd.get_names('all')
        with self.assertRaises(ValueError):
            self.controller.capture_selection(expression, state=2)
        self.assertEqual(self.cmd.get_names('all'), names)
        self.assertEqual(self.cmd.get_state(), 1)
        self.assertEqual(self.cmd.get_view(), view)
        captured = self.controller.capture_selection(expression, state=1, single=True)
        self.assertEqual(captured['objects'], ['moving'])
        self.assertEqual(captured['atoms'], 1)
        self.assertEqual(self.cmd.index(captured['selection']), [('moving', 1)])
        self.assertEqual(list(self.cmd.get_model(captured['selection'], state=1).atom[0].coord), [0., 0., 0.])
        # Resolve a nonempty state-2 spatial branch while state 1 is displayed.
        self.cmd.alter_state(2, 'reference', 'x=10')
        captured = self.controller.capture_selection(expression, state=2, single=True)
        self.assertEqual(self.cmd.index(captured['selection']), [('moving', 1)])
        self.assertEqual(list(self.cmd.get_model(captured['selection'], state=2).atom[0].coord), [10., 0., 0.])
        self.assertEqual(self.cmd.get_state(), 1)
        self.assertEqual(self.cmd.get_view(), view)

    def test_capture_all_omits_absent_state_objects_but_direct_selection_fails(self):
        self.cmd.create('multistate', 'one', 1, 1)
        self.cmd.load_coordset([[3, 4, 0]], 'multistate', state=2)
        captured = self.controller.capture_selection('all', state=2)
        self.assertEqual(captured['objects'], ['multistate'])
        self.assertEqual(captured['atoms'], 1)
        self.assertEqual(self.cmd.index(captured['selection']), [('multistate', 1)])
        self.assertEqual(list(self.cmd.get_model(captured['selection'], state=2).atom[0].coord), [3., 4., 0.])
        for absent in ('one', 'two'):
            with self.subTest(object=absent), self.assertRaises(ValueError):
                self.controller.capture_selection(absent, state=2)

    def test_discrete_capture_identity_and_named_members_match_same_state_atoms(self):
        self.cmd.create('discrete', 'one', 1, 1, discrete=1)
        self.cmd.create('discrete', 'two', 1, 2, discrete=1)
        self.assertEqual(self.cmd.get_model('discrete', state=2).atom[0].index, 2)
        captured = self.controller.capture_selection('discrete', state=2, single=True)
        self.assertEqual(captured['atoms'], 1)
        self.assertEqual(captured['objects'], ['discrete'])
        self.assertEqual([(a['object'], a['index'], a['name'], a['state']) for a in captured['identities']],
                         [('discrete', 2, 'O', 2)])
        self.assertEqual(self.cmd.index(captured['selection']), [('discrete', 2)])
        self.assertEqual(self.cmd.count_atoms(captured['selection']), 1)
        atom = self.cmd.get_model(captured['selection'], state=2).atom[0]
        self.assertEqual((atom.model, atom.index, atom.name), ('discrete', 2, 'O'))
        self.assertEqual(list(atom.coord), [3., 4., 0.])

    def test_background_thread_cannot_mutate_live_objects(self):
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.controller.delete_objects, ['one'])
            with self.assertRaisesRegex(RuntimeError, 'main thread'):
                future.result()
        self.assertEqual(set(self.cmd.get_names('objects')), {'one', 'two'})

    def test_live_state_and_appearance_validation_preserve_other_objects(self):
        c = self.controller
        for opacity in (float('nan'), -0.1, 1.1):
            with self.assertRaises(ValueError):
                c.set_appearance('one', surface_opacity=opacity)
        for radius in (float('inf'), 0, -1):
            with self.assertRaises(ValueError):
                c.set_appearance('one', stick_radius=radius)
        c.set_appearance('one', background='transparent', surface_opacity=0.4, stick_radius=0.31)
        self.assertAlmostEqual(self.cmd.get_setting_float('transparency', 'one'), 0.6, places=5)
        self.assertAlmostEqual(self.cmd.get_setting_float('stick_radius', 'one'), 0.31, places=5)
        self.assertNotAlmostEqual(self.cmd.get_setting_float('stick_radius', 'two'), 0.31, places=5)
        self.assertEqual(self.cmd.get_setting_int('ray_opaque_background'), 0)
        c.set_labels('one', 'name')
        labels = []
        self.cmd.iterate('one', 'labels.append(label)', space={'labels': labels})
        self.assertEqual(labels, ['CA'])
        c.set_labels('one', '')
        labels = []
        self.cmd.iterate('one', 'labels.append(label)', space={'labels': labels})
        self.assertEqual(labels, [''])
        self.cmd.create('states', 'one', 1, 1)
        self.cmd.create('states', 'two', 1, 2)
        c.set_state(2)
        self.assertEqual(self.cmd.get_state(), 2)
        with self.assertRaises(ValueError):
            c.set_state(3)

    def test_load_unique_names_scene_and_session_preserve_view(self):
        c = self.controller
        source = self.base / 'two weird;$(x).pdb'
        self.cmd.save(str(source), 'one')
        first = c.load_coordinates(source)
        second = c.load_coordinates(source)
        self.assertNotEqual(first, second)
        self.assertEqual(len(c.objects()), 4)
        with self.assertRaises(ValueError):
            c.load_coordinates(self.base / 'bad.pse')
        self.cmd.orient('all')
        self.cmd.turn('y', 29)
        view = self.cmd.get_view()
        c.store_scene('camera')
        self.cmd.turn('x', 45)
        c.restore_scene('camera')
        for a, b in zip(view, self.cmd.get_view()):
            self.assertAlmostEqual(a, b, places=4)
        session = self.base / 'view.pse'
        self.cmd.save(str(session))
        self.cmd.reinitialize()
        c.open_session(session)
        self.assertEqual(set(self.cmd.get_names('objects')), {'one', 'two', first, second})
        for a, b in zip(view, self.cmd.get_view()):
            self.assertAlmostEqual(a, b, places=4)
        self.assertIn('camera', c.scenes())
        c.delete_scene('camera')
        self.assertNotIn('camera', c.scenes())


class NativeDesktopTests(unittest.TestCase):
    @unittest.skipUnless(HAS_QT and HAS_PYMOL, 'requires PyMOL and the optional GUI binding')
    def test_native_startup_events_framebuffer_and_shutdown(self):
        self._run_native()

    @unittest.skipUnless(HAS_QT and HAS_PYMOL, 'requires PyMOL and the optional GUI binding')
    def test_native_clean_preferences_startup_and_shutdown(self):
        self._run_native(clean=True)

    def _run_native(self, clean=False):
        with tempfile.TemporaryDirectory() as temporary:
            arguments = [sys.executable, '-B', str(Path(__file__).resolve()), '--native']
            if clean:
                arguments.append('--clean-startup')
            result = subprocess.run(arguments + [temporary],
                                    cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT)), capture_output=True, text=True, timeout=75)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads((Path(temporary) / 'native.json').read_text())
            self.assertTrue(report['gl_valid'])
            self.assertGreater(report['framebuffer_colors'], 8)
            self.assertTrue(report['rotation_changed'])
            self.assertTrue(report['zoom_changed'])
            self.assertEqual(report['picked_atoms'], 1)
            self.assertTrue(report['singleton'])
            self.assertTrue(report['panel_events'])
            self.assertTrue(report['closed'])
            self.assertTrue(report['rename_delete'])
            self.assertTrue(report['scene_events'])
            self.assertTrue(report['native_refresh'])
            self.assertTrue(report['bootstrap_loaded'])
            self.assertTrue(report['preferences_unchanged'])


def grab_native_framebuffer(viewport):
    """Read actual GL pixels; keep this harness usable as an isolated script."""
    for name in ('grabFramebuffer', 'grabFrameBuffer'):
        capture = getattr(viewport, name, None)
        if callable(capture):
            return capture()
    raise AssertionError('Native GL viewport has neither grabFramebuffer nor grabFrameBuffer')


def native_test(output):
    """Real Qt/GL harness starts official launch, never a second QApplication."""
    from molscene import desktop
    from pymol.Qt import QtCore, QtGui, QtWidgets, PYQT_NAME
    QTest = __import__(PYQT_NAME + '.QtTest', fromlist=['QTest']).QTest
    def drag(viewport, button, start, end):
        QTest.mousePress(viewport, button, pos=start)
        QTest.qWait(40)
        # Qt5 QTest.mouseMove warps the OS cursor; deliver the drag directly
        # so the move reaches this widget before the synthetic release.
        move = QtGui.QMouseEvent(
            QtCore.QEvent.MouseMove, QtCore.QPointF(end),
            QtCore.QPointF(viewport.mapTo(viewport.window(), end)),
            QtCore.QPointF(viewport.mapToGlobal(end)),
            QtCore.Qt.NoButton, button, QtCore.Qt.NoModifier)
        QtWidgets.QApplication.sendEvent(viewport, move)
        QTest.mouseRelease(viewport, button, pos=end)
    from pmg_qt import pymol_qt_gui
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    report = {}
    startup = output / 'bootstrap local.pdb'
    startup.write_text('ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\nEND\n')
    from pymol import plugins
    preferences_file = output / 'pymolpluginsrc.py'
    preferences_text = 'import pymol.plugins\npymol.plugins.set_startup_path([], autosave=False)\n'
    preferences_file.write_text(preferences_text)
    plugins.PYMOLPLUGINSRC = str(preferences_file if '--clean-startup' not in sys.argv else output / 'no-preferences.py')
    real_attach = desktop.attach_workbench
    real_show = pymol_qt_gui.PyMOLQtGUI.show
    def no_activate_show(window):
        window.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)
        real_show(window)
        QtCore.QTimer.singleShot(25000, lambda: QtWidgets.QApplication.instance().exit(2))
    pymol_qt_gui.PyMOLQtGUI.show = no_activate_show
    pymol_qt_gui.PyMOLQtGUI.raise_ = lambda self: None
    exercise_scheduled = False
    def attach(parent=None):
        nonlocal exercise_scheduled
        dock = real_attach(parent)
        if not exercise_scheduled:
            exercise_scheduled = True
            QtCore.QTimer.singleShot(700, lambda: exercise(dock))
        return dock
    def exercise(dock):
        try:
            from pymol import cmd
            parent = dock.parent()
            assert parent.pymolwidget.width() >= 350, f'Dock consumed viewport: window={parent.width()}, dock={dock.width()}, viewport={parent.pymolwidget.width()}, hint={dock.minimumSizeHint().width()}'
            parent.resize(1350, 850)
            QTest.qWait(200)
            viewport = parent.pymolwidget
            assert viewport.isValid(), 'native QOpenGLWidget has no valid context'
            report['gl_valid'] = viewport.isValid()
            report['device_pixel_ratio'] = viewport.devicePixelRatioF()
            report['preferences_unchanged'] = preferences_file.read_text() == preferences_text
            saved_preferences = output / 'saved-preferences.py'
            plugins.pref_save(filename=str(saved_preferences))
            if '--clean-startup' not in sys.argv:
                assert plugins.get_startup_path(useronly=True) == []
            assert preferences_file.read_text() == preferences_text
            report['bootstrap_loaded'] = cmd.count_atoms('bootstrap_local*') == 1
            assert report['bootstrap_loaded']
            cmd.reinitialize()
            cmd.fragment('ala', 'native_molecule')
            cmd.show('spheres', 'all')
            cmd.orient('all')
            cmd.zoom('all', buffer=3)
            QTest.qWait(1700)
            report['native_refresh'] = dock.widget().object_list.item(0).data(QtCore.Qt.UserRole) == 'native_molecule'
            assert report['native_refresh']
            image = grab_native_framebuffer(viewport)
            assert not image.isNull(), 'empty framebuffer'
            image.save(str(output / 'native-viewport.png'))
            parent.grab().save(str(output / 'native-molecule-window.png'))
            colors = {image.pixel(x, y) for x in range(0, image.width(), 3)
                      for y in range(0, image.height(), 3)}
            report['framebuffer_colors'] = len(colors)
            view = cmd.get_view()
            center = viewport.rect().center()
            drag(viewport, QtCore.Qt.LeftButton, center, center + QtCore.QPoint(90, 35))
            QTest.qWait(150)
            report['rotation_changed'] = tuple(view) != tuple(cmd.get_view())
            view = cmd.get_view()
            drag(viewport, QtCore.Qt.RightButton, center, center + QtCore.QPoint(0, 65))
            QTest.qWait(150)
            report['zoom_changed'] = tuple(view) != tuple(cmd.get_view())
            # One isolated sphere at the centered origin makes a deterministic native pick.
            cmd.delete('all')
            cmd.pseudoatom('pickable', pos=[0, 0, 0], vdw=1.7)
            cmd.show('spheres', 'pickable')
            cmd.orient('pickable')
            cmd.config_mouse('three_button_viewing')
            QTest.qWait(200)
            center = QtCore.QPoint(int((viewport.width() - 220) / 2), int(viewport.height() / 2))
            QTest.mouseClick(viewport, QtCore.Qt.RightButton, QtCore.Qt.ControlModifier, center)
            QTest.qWait(150)
            report['picked_atoms'] = cmd.count_atoms('pk1')
            if report['picked_atoms'] != 1:
                raise AssertionError('Ctrl-right native pick did not create pk1')
            report['singleton'] = real_attach() is dock
            dock.close()
            actions = [a for a in parent.findChildren(QtWidgets.QAction)
                       if a.objectName() == 'molscene_open_workbench']
            assert len(actions) == 1
            actions[0].trigger()
            assert dock.isVisible()
            panel = dock.widget()
            objects = panel.findChild(QtWidgets.QListWidget, 'objects')
            panel.refresh()
            objects.setCurrentRow(0)
            QTest.mouseClick(panel.findChild(QtWidgets.QPushButton, 'hide_objects'), QtCore.Qt.LeftButton)
            assert 'pickable' not in cmd.get_names('objects', enabled_only=1)
            QTest.mouseClick(panel.findChild(QtWidgets.QPushButton, 'show_objects'), QtCore.Qt.LeftButton)
            assert 'pickable' in cmd.get_names('objects', enabled_only=1)
            entry = panel.findChild(QtWidgets.QLineEdit, 'selection')
            entry.setText('none')
            QTest.mouseClick(panel.findChild(QtWidgets.QPushButton, 'capture_selection'), QtCore.Qt.LeftButton)
            assert 'Error' in panel.status.text()
            entry.setText('pickable')
            QTest.mouseClick(panel.findChild(QtWidgets.QPushButton, 'capture_selection'), QtCore.Qt.LeftButton)
            assert '1 atom' in panel.status.text()
            QTest.mouseClick(panel.findChild(QtWidgets.QPushButton, 'capture_pk1'), QtCore.Qt.LeftButton)
            assert '1 atom' in panel.status.text()
            report['panel_events'] = True
            parent.grab().save(str(output / 'native-window.png'))
            with patch.object(QtWidgets.QInputDialog, 'getText', return_value=('renamed_live', True)):
                QTest.mouseClick(panel.findChild(QtWidgets.QPushButton, 'rename_object'), QtCore.Qt.LeftButton)
            assert cmd.get_names('objects') == ['renamed_live']
            panel.tabs.setCurrentIndex(1)
            panel.scene_name.setText('qt_bookmark')
            saved_view = cmd.get_view()
            QTest.mouseClick(panel.findChild(QtWidgets.QPushButton, 'store_scene'), QtCore.Qt.LeftButton)
            panel.scene_list.setCurrentRow(0)
            cmd.turn('y', 37)
            QTest.mouseClick(panel.findChild(QtWidgets.QPushButton, 'restore_scene'), QtCore.Qt.LeftButton)
            assert max(abs(a - b) for a, b in zip(saved_view, cmd.get_view())) < 1e-4
            QTest.mouseClick(panel.findChild(QtWidgets.QPushButton, 'delete_scene'), QtCore.Qt.LeftButton)
            assert not cmd.get_scene_list()
            report['scene_events'] = True
            panel.tabs.setCurrentIndex(0)
            panel.object_list.setCurrentRow(0)
            QTest.mouseClick(panel.findChild(QtWidgets.QPushButton, 'delete_objects'), QtCore.Qt.LeftButton)
            assert not cmd.get_names('objects')
            report['rename_delete'] = True
            class CloseObserver(QtCore.QObject):
                def eventFilter(self, watched, event):
                    if event.type() == QtCore.QEvent.Close:
                        report['closed'] = True
                        (output / 'native.json').write_text(json.dumps(report, indent=2))
                    return False
            observer = CloseObserver(parent)
            parent.installEventFilter(observer)
            parent.close()
        except BaseException:
            import traceback
            (output / 'failure.txt').write_text(traceback.format_exc())
            dock.parent().grab().save(str(output / 'failure.png'))
            traceback.print_exc()
            QtWidgets.QApplication.instance().exit(1)
    desktop.attach_workbench = attach
    QtCore.QTimer  # timer only created by plugin after QApplication exists
    status = desktop.launch(paths=[startup], workspace=output)
    if status != 0:
        raise SystemExit(1)
    if not (output / 'native.json').exists():
        raise SystemExit(1)


if __name__ == '__main__':
    if '--native' in sys.argv:
        native_test(sys.argv[-1])
    else:
        unittest.main()
