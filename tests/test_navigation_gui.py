"""Real Qt navigation, exact membership and native workflow acceptance."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HAS_BACKEND = importlib.util.find_spec('pymol2') is not None
HAS_QT = any(importlib.util.find_spec(name) for name in ('PySide6', 'PySide2', 'PyQt6', 'PyQt5'))


@unittest.skipUnless(HAS_BACKEND and HAS_QT, 'requires PyMOL and Qt')
class NavigationQtTests(unittest.TestCase):
    def test_open_session_replaces_same_count_residue_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), '--session-qt', directory],
                cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT), QT_QPA_PLATFORM='offscreen'),
                capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_exact_navigation_handoff_presets_and_full_result_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), '--qt', directory],
                cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT), QT_QPA_PLATFORM='offscreen'),
                capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipUnless(HAS_BACKEND and HAS_QT, 'requires PyMOL and Qt')
class NativeNavigationTests(unittest.TestCase):
    def test_native_navigation_presets_and_interface_workflow(self):
        def check(directory):
            result = subprocess.run([sys.executable, '-B', str(ROOT / 'tests/navigation_native.py'), str(directory)],
                cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT)), capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads((Path(directory) / 'native-navigation.json').read_text(encoding='utf-8'))
            self.assertTrue(report['passed'], report)
            self.assertTrue(report['gl_valid'])
            self.assertGreater(report['framebuffer_colors'], 8)
            self.assertEqual(report['ubq_selected_atoms'], 17)
            self.assertGreater(report['interface_rows'], 0)
            self.assertEqual(report['csv_rows'], report['interface_rows'])
            self.assertTrue(report['source_unchanged'])
            self.assertGreater(report['pocket_framebuffer_colors'], 8)
            self.assertTrue(report['closed'])
        evidence = os.environ.get('MOLSCENE_TEST_ARTIFACTS')
        if evidence:
            Path(evidence).mkdir(parents=True, exist_ok=True)
            check(tempfile.mkdtemp(prefix='native-navigation-', dir=evidence))
        else:
            with tempfile.TemporaryDirectory() as directory:
                check(directory)


def qt_session_replacement(directory):
    from unittest.mock import patch
    from pymol.Qt import QtCore, QtWidgets, PYQT_NAME
    from pymol2 import PyMOL
    from molscene.desktop.controller import WorkbenchController
    from molscene.desktop.panel import WorkbenchPanel
    QTest = __import__(PYQT_NAME + '.QtTest', fromlist=['QTest']).QTest
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    with PyMOL() as runtime:
        cmd = runtime.cmd
        cmd.load(str(ROOT / 'examples/data/1ubq.pdb'), 'ubq')
        cmd.alter('ubq and resi 1', 'resn="MSE"')
        session = Path(directory) / 'replacement.pse'
        cmd.save(str(session))
        cmd.alter('ubq and resi 1', 'resn="MET"')
        controller = WorkbenchController(cmd, directory)
        panel = WorkbenchPanel(controller)
        panel.resize(360, 720)
        panel.show()
        app.processEvents()
        def click(name):
            button = panel.findChild(QtWidgets.QPushButton, name)
            assert button is not None, name
            panel.tabs.currentWidget().ensureWidgetVisible(button)
            app.processEvents()
            assert button.isVisible() and button.isEnabled(), name
            QTest.mouseClick(button, QtCore.Qt.LeftButton)
            app.processEvents()
            assert not panel.status.text().startswith('Error:'), panel.status.text()
        nav = panel.navigation
        nav.scope.setCurrentIndex(nav.scope.findData('ubq'))
        nav.chain.setCurrentIndex(nav.chain.findData('A'))
        nav.search.setText('ubq')
        app.processEvents()
        assert nav.proxy.index(0, 1).data() == 'MET'
        nav.browser.selectRow(0)
        click('use_residues')
        assert '8 atoms' in panel.selection_summary.text()
        assert panel.selection_summary.toolTip()
        nav.browser.selectRow(0)
        record = controller.inspect('ubq')
        panel._show_record(record)
        assert record['snapshot_status'] == 'current'
        assert panel.tabs.currentIndex() == 0
        before = controller.objects()
        result_model = panel.result_table.model()
        with patch.object(controller, 'residues', wraps=controller.residues) as scan:
            with patch.object(QtWidgets.QFileDialog, 'getOpenFileName', return_value=(str(session), '')) as dialog:
                click('open_session')
                dialog.assert_called_once()
            assert controller.objects() == before, 'Replacement must retain names, counts and states'
            assert cmd.get_model('ubq and resi 1').atom[0].resn == 'MSE'
            assert nav.proxy.index(0, 1).data() == 'MSE', 'Open PSE must refresh same-count residue identities'
            assert scan.call_count == 1, 'Explicit load must rescan exactly once'
            assert not nav.browser.selectionModel().selectedRows(), 'Old actionable row selection must be cleared'
            assert 'preview required' in panel.selection_summary.text()
            assert not panel.selection_summary.toolTip(), 'Old captured source details must be invalidated'
            assert record['snapshot_status'] == 'stale' and 'stale' in panel.result_source.text()
            assert panel.result_table.model() is result_model
            assert nav.scope.currentData() == 'ubq' and nav.chain.currentData() == 'A'
            assert nav.search.text() == 'ubq' and nav.polymer.isChecked()
            nav.browser.selectRow(0)
            click('use_residues')
            assert cmd.count_atoms(panel.selection.text()) == 8
            assert {a.resn for a in cmd.get_model(panel.selection.text()).atom} == {'MSE'}
            assert '8 atoms' in panel.selection_summary.text()
            scan.reset_mock()
            assert panel.refresh_timer.interval() == 1500
            QTest.qWait(3200)
            assert scan.call_count == 0, 'Unchanged timer ticks must not rescan residues'
            assert len(nav.browser.selectionModel().selectedRows()) == 1
            with patch.object(QtWidgets.QFileDialog, 'getOpenFileName', return_value=('', '')):
                click('open_session')
            assert scan.call_count == 0, 'Cancelled dialog must not invalidate the source'
            assert '8 atoms' in panel.selection_summary.text()
        panel.refresh_timer.stop()
        panel.close()
    print('Same-count PSE replacement Qt passed: one rescan, MSE handoff, stale result, no timer rescans')


def qt_navigation(directory):
    import csv
    from unittest.mock import patch
    from pymol.Qt import QtCore, QtWidgets, PYQT_NAME
    from pymol2 import PyMOL
    from molscene.desktop.controller import WorkbenchController
    from molscene.desktop.panel import WorkbenchPanel
    QTest = __import__(PYQT_NAME + '.QtTest', fromlist=['QTest']).QTest
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    with PyMOL() as runtime:
        cmd = runtime.cmd
        cmd.load(str(ROOT / 'examples/data/1ubq.pdb'), 'ubq')
        controller = WorkbenchController(cmd, directory)
        panel = WorkbenchPanel(controller)
        panel.resize(360, 720)
        panel.show()
        app.processEvents()
        def click(name):
            button = panel.findChild(QtWidgets.QPushButton, name)
            assert button is not None, 'Missing navigation action: ' + name
            parent = button.parentWidget()
            while parent:
                if parent.property('disclosure_content'):
                    toggle = parent.parentWidget().findChild(QtWidgets.QToolButton)
                    if not toggle.isChecked():
                        panel.tabs.currentWidget().ensureWidgetVisible(toggle)
                        QTest.mouseClick(toggle, QtCore.Qt.LeftButton)
                parent = parent.parentWidget()
            page = panel.tabs.currentWidget()
            if isinstance(page, QtWidgets.QScrollArea):
                page.ensureWidgetVisible(button)
            app.processEvents()
            QTest.mouseClick(button, QtCore.Qt.LeftButton)
            app.processEvents()
            assert not panel.status.text().startswith('Error:'), panel.status.text()
        browser = panel.findChild(QtWidgets.QTableView, 'residue_browser')
        assert browser is not None, 'Missing observed-residue navigator'
        assert browser.model().rowCount() == 76
        assert not panel.findChild(QtWidgets.QPushButton, 'delete_named_selection').isEnabled(), 'Deletion requires explicit named choice'
        cmd.alter('ubq and resi 1', 'resn="MSE"')
        scope = panel.findChild(QtWidgets.QComboBox, 'residue_object')
        scope.setCurrentIndex(scope.findData('ubq'))
        app.processEvents()
        assert browser.model().index(0,1).data() == 'MSE', 'A scope change must refresh observed identities'
        cmd.alter('ubq and resi 1', 'resn="MET"')
        click('residue_refresh')
        browser.selectRow(0)
        click('use_residues')
        assert cmd.count_atoms(panel.selection.text()) == 8
        assert {a.resi for a in cmd.get_model(panel.selection.text()).atom} == {'1'}
        click('send_analysis_a')
        assert cmd.count_atoms(panel.analysis_selection.text()) == 8
        browser.selectRow(1)
        click('use_residues')
        click('send_analysis_b')
        assert {a.resi for a in cmd.get_model(panel.contacts_b.text()).atom} == {'2'}
        assert {a.resi for a in cmd.get_model(panel.analysis_selection.text()).atom} == {'1'}
        panel.selection.setText('ubq')
        browser.selectRow(0)
        click('use_preset_ligand')
        assert panel.selection.text() == 'ubq', 'Ligand handoff must preserve pocket context'
        assert cmd.count_atoms(panel.findChild(QtWidgets.QLineEdit, 'preset_ligand').text()) == 8
        # Timer refresh must retain local search/selection without scanning coordinates again.
        search = panel.findChild(QtWidgets.QLineEdit, 'residue_filter')
        search.setText('MET')
        app.processEvents()
        assert browser.model().rowCount() == 1
        browser.selectRow(0)
        with patch.object(controller, 'residues', wraps=controller.residues) as scan:
            panel.refresh()
            panel.refresh()
            assert scan.call_count == 0
        assert search.text() == 'MET' and len(browser.selectionModel().selectedRows()) == 1
        panel.state.setValue(2)
        app.processEvents()
        assert browser.model().rowCount() == 0, 'Invalid state must clear actionable cached rows'
        assert 'preview required' in panel.selection_summary.text()
        panel.state.setValue(1)
        app.processEvents()
        assert browser.model().rowCount() == 1
        search.clear()
        panel.findChild(QtWidgets.QLineEdit, 'named_selection_name').setText('saved_sites')
        click('save_named_selection')
        assert cmd.count_atoms('saved_sites') == cmd.count_atoms('ubq')
        panel.findChild(QtWidgets.QComboBox, 'preset_kind').setCurrentText('surface')
        view = cmd.get_view()
        click('apply_preset')
        assert cmd.get_view() == view
        assert '660 atoms' in panel.selection_summary.text() and 'state 1' in panel.selection_summary.text()
        assert cmd.count_atoms('ubq and rep surface') == 660
        panel.findChild(QtWidgets.QLineEdit, 'clean_name').setText('cleaned')
        click('clean_copy')
        assert cmd.count_atoms('cleaned') == 602
        assert cmd.count_atoms('ubq') == 660
        panel.tabs.setCurrentIndex(1)
        panel.scene_name.setText('bookmark')
        click('store_scene')
        panel.scene_list.setCurrentRow(0)
        cmd.turn('y', 20)
        updated = cmd.get_view()
        click('scene_update')
        panel.scene_name.setText('other_bookmark')
        click('store_scene')
        panel.scene_list.setCurrentRow(0)
        with patch.object(QtWidgets.QInputDialog, 'getText', return_value=('renamed', True)):
            click('scene_rename')
        assert set(controller.scenes()) == {'renamed', 'other_bookmark'}
        assert panel.scene_list.currentItem() and panel.scene_list.currentItem().text() == 'renamed', 'Keep the renamed scene selected'
        cmd.turn('y', 20)
        click('restore_scene')
        assert all(abs(a-b) < 1e-5 for a,b in zip(cmd.get_view(), updated))
        # Real scientific result rows: lossless filtering and numeric sorting.
        from molscene import analysis
        record = controller.record_result('sasa', analysis.sasa(cmd, 'ubq'), state=1,
                                          source_fingerprint=controller._fingerprint(1))
        panel._show_record(record)
        panel.tabs.setCurrentIndex(3)
        panel.result_tables.setCurrentText('residues')
        total = panel.result_table.model().rowCount()
        assert total > 0
        panel.findChild(QtWidgets.QLineEdit, 'result_filter').setText('MET')
        app.processEvents()
        assert 0 < panel.result_table.model().rowCount() < total
        model = panel.result_table.model()
        panel.result_table.selectRow(0)
        cmd.alter_state(1, 'ubq and index 1', 'x=x+1')
        panel.refresh()
        assert 'stale' in panel.result_source.text()
        assert panel.result_table.model() is model
        assert len(panel.result_table.selectionModel().selectedRows()) == 1
        output = Path(directory) / 'full.csv'
        with patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=(str(output), 'CSV')):
            click('export_csv')
        with output.open(encoding='utf-8', newline='') as handle:
            assert len(list(csv.DictReader(handle))) == total
        panel.result_filter.clear()
        # Numeric data must sort by raw values, including signed areas.
        panel._populate_result({'rows':[{'area_angstrom2':10.}, {'area_angstrom2':-2.}, {'area_angstrom2':3.}]})
        panel._current_record = {'rows':[{'area_angstrom2':10.}, {'area_angstrom2':-2.}, {'area_angstrom2':3.}]}
        panel._table_selected('rows')
        panel.result_table.sortByColumn(0, QtCore.Qt.AscendingOrder)
        assert [panel.result_table.model().index(i,0).data() for i in range(3)] == ['-2','3','10']
        panel._job_started({'operation':'interface','output':'unused','parameters':{'state':1}})
        assert not panel.findChild(QtWidgets.QPushButton, 'annotate_result').isEnabled()
        assert panel.result_filter.isEnabled()
        panel._job_idle()
        assert panel.findChild(QtWidgets.QPushButton, 'annotate_result').isEnabled()
        long_name = 'long_molecule_' + 'segment_' * 24
        cmd.set_name('cleaned', long_name)
        panel.refresh()
        for tab in range(3):
            panel.tabs.setCurrentIndex(tab)
            for toggle in panel.tabs.currentWidget().findChildren(QtWidgets.QToolButton):
                if toggle.isCheckable() and not toggle.isChecked():
                    panel.tabs.currentWidget().ensureWidgetVisible(toggle)
                    QTest.mouseClick(toggle, QtCore.Qt.LeftButton)
            app.processEvents()
            for form in panel.tabs.currentWidget().findChildren(QtWidgets.QFormLayout):
                for row_number in range(form.rowCount()):
                    item = form.itemAt(row_number, QtWidgets.QFormLayout.LabelRole)
                    if item and item.widget() and item.widget().isVisible():
                        assert item.widget().width() > 0, 'Form label hidden: ' + item.widget().text()
            assert panel.tabs.widget(tab).horizontalScrollBar().maximum() == 0, (tab, panel.width(), [(type(w).__name__, w.minimumSize().width(), [w.itemAt(i).widget().objectName() for i in range(w.count()) if w.itemAt(i).widget()]) for w in panel.tabs.widget(tab).findChildren(QtWidgets.QLayout) if w.minimumSize().width() > 310])
        assert panel.minimumSizeHint().width() <= 380, panel.minimumSizeHint().width()
        assert panel.width() <= 380, (panel.width(), [(w.objectName(), w.minimumSizeHint().width(), w.width()) for w in panel.findChildren(QtWidgets.QWidget) if w.minimumSizeHint().width() > 350])
        panel.refresh_timer.stop()
        panel.close()
    print('Navigation Qt passed')


if __name__ == '__main__':
    if '--session-qt' in sys.argv:
        qt_session_replacement(sys.argv[-1])
    elif '--qt' in sys.argv:
        qt_navigation(sys.argv[-1])
    else:
        unittest.main()
