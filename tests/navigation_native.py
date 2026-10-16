"""Standalone real native navigation acceptance; also runnable outside checkout -I."""
import csv
import json
from pathlib import Path
import sys
import traceback
from unittest.mock import patch


def fixture(name):
    candidates = (Path(__file__).resolve().parents[1] / 'examples/data' / name,
                  Path(sys.prefix) / 'share/molscene/examples/data' / name)
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError('Bundled fixture missing: ' + name)


def framebuffer(viewport):
    for name in ('grabFramebuffer', 'grabFrameBuffer'):
        method = getattr(viewport, name, None)
        if callable(method):
            return method()
    raise AssertionError('Native GL framebuffer capture unavailable')


def native_navigation(output):
    from molscene import desktop
    from pymol.Qt import QtCore, QtGui, QtWidgets, PYQT_NAME
    QTest = __import__(PYQT_NAME + '.QtTest', fromlist=['QTest']).QTest
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = dict(passed=False, trace=[])
    original_attach = desktop.attach_workbench
    scheduled = False
    def save():
        (output / 'native-navigation.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    def attach(parent=None):
        nonlocal scheduled
        dock = original_attach(parent)
        if not scheduled:
            scheduled = True
            QtCore.QTimer.singleShot(700, lambda: exercise(dock))
            QtCore.QTimer.singleShot(150000, lambda: QtWidgets.QApplication.instance().exit(2))
        return dock
    def exercise(dock):
        panel, window = dock.widget(), dock.parentWidget()
        cmd = panel.controller.cmd
        def wait(predicate, timeout=45000):
            for _ in range(timeout // 20):
                QTest.qWait(20)
                if predicate():
                    return
            raise AssertionError('Native navigation timeout: ' + panel.status.text())
        def reveal(widget):
            ancestor = widget.parentWidget()
            while ancestor:
                if ancestor.property('disclosure_content'):
                    toggle = ancestor.parentWidget().findChild(QtWidgets.QToolButton)
                    if not toggle.isChecked():
                        page = panel.tabs.currentWidget()
                        if isinstance(page, QtWidgets.QScrollArea):
                            page.ensureWidgetVisible(toggle)
                        QTest.mouseClick(toggle, QtCore.Qt.LeftButton)
                ancestor = ancestor.parentWidget()
            page = panel.tabs.currentWidget()
            if isinstance(page, QtWidgets.QScrollArea):
                page.ensureWidgetVisible(widget)
            QTest.qWait(30)
            assert widget.isVisible(), widget.objectName() + ' must be visible for mouse events'
        def click(name, tab=None):
            if tab is not None:
                panel.tabs.setCurrentIndex(tab)
            button = panel.findChild(QtWidgets.QPushButton, name)
            assert button is not None, name
            reveal(button)
            QTest.mouseClick(button, QtCore.Qt.LeftButton)
            QTest.qWait(30)
            report['trace'].append('click ' + name)
            save()
            assert not panel.status.text().startswith('Error'), panel.status.text()
        def enter(entry, value):
            reveal(entry)
            entry.setFocus()
            entry.selectAll()
            QTest.keyClicks(entry, value)
        def select_row(browser, number, modifier=QtCore.Qt.NoModifier):
            reveal(browser)
            index = browser.model().index(number, 0)
            browser.scrollTo(index)
            QTest.mouseClick(browser.viewport(), QtCore.Qt.LeftButton, modifier, browser.visualRect(index).center())
        def drag(button, offset):
            viewport = window.pymolwidget
            start = QtCore.QPoint(max(40, (viewport.width() - 220) // 2), viewport.height() // 2)
            end = start + offset
            QTest.mousePress(viewport, button, pos=start)
            event = QtGui.QMouseEvent(QtCore.QEvent.MouseMove, QtCore.QPointF(end),
                QtCore.QPointF(viewport.mapTo(window, end)), QtCore.QPointF(viewport.mapToGlobal(end)),
                QtCore.Qt.NoButton, button, QtCore.Qt.NoModifier)
            QtWidgets.QApplication.sendEvent(viewport, event)
            QTest.mouseRelease(viewport, button, pos=end)
            QTest.qWait(50)
        def source_signature(name):
            model = cmd.get_model('%' + name, state=1)
            metadata = []
            cmd.iterate('%' + name, 'metadata.append((index,name,resn,resi,segi,chain,elem,alt,b,q,color))',
                        space={'metadata': metadata})
            return (metadata, [tuple(atom.coord) for atom in model.atom],
                    [(tuple(bond.index), bond.order) for bond in model.bond])
        try:
            window.resize(1280, 720)
            window.resizeDocks([dock], [360], QtCore.Qt.Horizontal)
            QTest.qWait(250)
            assert window.pymolwidget.isValid()
            report['gl_valid'] = True
            assert window.pymolwidget.width() >= 350
            assert dock.width() <= 420, f'Dock too wide: {dock.width()}'
            ubq = panel.controller.objects()[0]['name']
            browser = panel.findChild(QtWidgets.QTableView, 'residue_browser')
            assert browser.model().rowCount() == 76
            select_row(browser, 0)
            select_row(browser, 1, QtCore.Qt.ControlModifier)
            click('use_residues')
            atoms = cmd.get_model(panel.selection.text()).atom
            assert len(atoms) == 17 and {a.resi for a in atoms} == {'1', '2'}
            report['ubq_selected_atoms'] = len(atoms)
            indexes = {a.index for a in atoms}
            click('send_analysis_a')
            assert {a.index for a in cmd.get_model(panel.analysis_selection.text()).atom} == indexes
            select_row(browser, 2)
            click('use_residues')
            click('send_analysis_b')
            assert {a.resi for a in cmd.get_model(panel.contacts_b.text()).atom} == {'3'}
            assert {a.index for a in cmd.get_model(panel.analysis_selection.text()).atom} == indexes
            enter(panel.selection, ubq)
            panel.findChild(QtWidgets.QComboBox, 'preset_kind').setCurrentText('overview')
            click('apply_preset')
            click('focus_selection')
            view = cmd.get_view()
            drag(QtCore.Qt.LeftButton, QtCore.QPoint(70, 25))
            assert view != cmd.get_view(), 'Native rotation did not change camera'
            view = cmd.get_view()
            drag(QtCore.Qt.RightButton, QtCore.QPoint(0, 50))
            assert view != cmd.get_view(), 'Native zoom did not change camera'
            reveal(browser)
            window.grab().save(str(output / 'navigation-1280x720.png'))
            report['small_geometry'] = dict(window=[window.width(), window.height()], dock=dock.width(), viewport=window.pymolwidget.width())
            image = framebuffer(window.pymolwidget)
            assert not image.isNull()
            image.save(str(output / 'ubq-viewport.png'))
            colors = {image.pixel(x,y) for x in range(0,image.width(),5) for y in range(0,image.height(),5)}
            report['framebuffer_colors'] = len(colors)
            assert len(colors) > 8
            with patch.object(QtWidgets.QFileDialog, 'getOpenFileNames', return_value=([str(fixture('1hsg.pdb'))], 'Coordinates')):
                click('load_coordinates')
            hsg = [obj['name'] for obj in panel.controller.objects() if obj['name'] != ubq][0]
            source_before = source_signature(hsg)
            cmd.disable(ubq)
            nav = panel.navigation
            nav.scope.setCurrentIndex(nav.scope.findData(hsg))
            nav.polymer.setChecked(False)
            enter(nav.search, 'MK1')
            assert browser.model().rowCount() == 1
            select_row(browser, 0)
            enter(panel.selection, hsg)
            click('use_preset_ligand')
            assert panel.selection.text() == hsg
            assert cmd.count_atoms(nav.ligand.text()) == 45
            nav.preset.setCurrentText('pocket')
            nav.radius.setValue(5)
            nav.frame.setChecked(True)
            # Independent coordinate oracle for the actual 1HSG pocket.
            ligand = cmd.get_model(hsg + ' and organic').atom
            polymer = cmd.get_model(hsg + ' and polymer.protein').atom
            sites = {(a.segi,a.chain,a.resi,a.resn) for a in polymer
                     if any(sum((x-y)**2 for x,y in zip(a.coord,b.coord)) <= 25 for b in ligand)}
            expected_pocket = {a.index for a in polymer if (a.segi,a.chain,a.resi,a.resn) in sites}
            click('apply_preset')
            actual = {a.index for a in cmd.get_model(hsg + ' and polymer.protein and rep sticks').atom}
            assert actual == expected_pocket and len(sites) > 0
            report['pocket_residues'] = len(sites)
            enter(nav.save_name, 'pocket_context')
            click('save_named_selection')
            assert cmd.count_atoms('pocket_context') == cmd.count_atoms(hsg)
            panel.tabs.setCurrentIndex(1)
            enter(panel.scene_name, 'pocket_view')
            click('store_scene')
            panel.scene_list.setCurrentRow(0)
            cmd.turn('y', 12)
            view = cmd.get_view()
            click('scene_update')
            with patch.object(QtWidgets.QInputDialog, 'getText', return_value=('edited_view', True)):
                click('scene_rename')
            cmd.turn('x', 30)
            reveal(panel.scene_list)
            rect = panel.scene_list.visualItemRect(panel.scene_list.item(0))
            # Qt's item view recognizes the double-click after its first press;
            # QTest.mouseDClick alone supplies only the double-click event.
            QTest.mouseClick(panel.scene_list.viewport(), QtCore.Qt.LeftButton, pos=rect.center())
            QTest.mouseDClick(panel.scene_list.viewport(), QtCore.Qt.LeftButton, pos=rect.center())
            QTest.qWait(50)
            assert max(abs(a-b) for a,b in zip(view,cmd.get_view())) < 1e-4
            report['scene_edited'] = True
            panel.tabs.setCurrentIndex(0)
            source_count = cmd.count_atoms(hsg)
            enter(nav.clean_name, 'hsg_clean')
            nav.clean_h.setChecked(True)
            click('clean_copy')
            assert cmd.count_atoms(hsg) == source_count
            assert cmd.count_atoms('hsg_clean and solvent') == 0
            assert cmd.count_atoms('hsg_clean and organic') == 45
            assert cmd.count_states('hsg_clean') == 1
            assert source_signature(hsg) == source_before, 'Preset/scene/clean copy changed source atom data'
            cmd.disable('hsg_clean')
            report['clean_copy_atoms'] = cmd.count_atoms('hsg_clean')
            panel.tabs.setCurrentIndex(2)
            enter(panel.analysis_selection, hsg + ' and polymer.protein and chain A')
            enter(panel.contacts_b, hsg + ' and polymer.protein and chain B')
            click('run_interface')
            assert panel.jobs.active, panel.status.text()
            assert not panel.findChild(QtWidgets.QPushButton, 'annotate_result').isEnabled()
            wait(lambda: not panel.jobs.active)
            result = panel.controller.results[-1]
            assert result['operation'] == 'interface', panel.status.text()
            assert result['snapshot_status'] == 'current'
            assert source_signature(hsg) == source_before, 'Interface worker changed live source atom data'
            assert result['interface_area_angstrom2'] > 0
            assert abs(2 * result['interface_area_angstrom2'] - result['total_buried_area_angstrom2']) < 1e-6
            assert len(result['residues']) == 198
            assert {row['partner'] for row in result['residues']} == {'A','B'}
            for label, key in (('A','partner_a'), ('B','partner_b')):
                assert abs(sum(row['buried_area_angstrom2'] for row in result['residues'] if row['partner'] == label) - result[key]['buried_area_angstrom2']) < 1e-3
            report['interface_area_angstrom2'] = result['interface_area_angstrom2']
            report['interface_rows'] = len(result['residues'])
            panel.tabs.setCurrentIndex(3)
            panel.result_tables.setCurrentText('residues')
            enter(panel.result_filter, 'MET')
            assert 0 < panel.result_table.model().rowCount() < report['interface_rows']
            column = panel._table_model.columns.index('buried_area_angstrom2')
            panel.result_table.sortByColumn(column, QtCore.Qt.DescendingOrder)
            values = [float(panel.result_table.model().index(i,column).data()) for i in range(panel.result_table.model().rowCount())]
            assert values == sorted(values, reverse=True)
            with patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=(str(output / 'interface-full.csv'), 'CSV')):
                click('export_csv')
            with (output / 'interface-full.csv').open(encoding='utf-8', newline='') as handle:
                rows = list(csv.DictReader(handle))
            report['csv_rows'] = len(rows)
            assert len(rows) == 198
            window.resize(1920, 1080)
            window.resizeDocks([dock], [360], QtCore.Qt.Horizontal)
            QTest.qWait(150)
            assert dock.width() <= 420 and window.pymolwidget.width() >= 350
            window.grab().save(str(output / 'interface-1920x1080.png'))
            report['large_geometry'] = dict(window=[window.width(), window.height()], dock=dock.width(), viewport=window.pymolwidget.width())
            pocket_image = framebuffer(window.pymolwidget)
            pocket_image.save(str(output / 'pocket-viewport.png'))
            report['pocket_framebuffer_colors'] = len({pocket_image.pixel(x,y) for x in range(0,pocket_image.width(),5) for y in range(0,pocket_image.height(),5)})
            assert report['pocket_framebuffer_colors'] > 8
            panel.tabs.setCurrentIndex(1)
            enter(panel.export_directory, str(output / 'current-view'))
            panel.image_width.setValue(640)
            panel.image_height.setValue(480)
            panel.image_ray.setChecked(False)
            view = cmd.get_view()
            click('run_export')
            assert panel.jobs.active
            wait(lambda: not panel.jobs.active)
            assert (output / 'current-view/scene.png').is_file(), panel.status.text()
            exported = json.loads((output / 'current-view/report.json').read_text(encoding='utf-8'))
            science = [row for row in exported['analysis_records'] if row['operation'] == 'interface']
            assert len(science) == 1 and len(science[0]['residues']) == 198
            assert view == cmd.get_view()
            assert source_signature(hsg) == source_before, 'Export changed live source atom data'
            report['source_unchanged'] = True
            report['current_view_export'] = True
            report['passed'] = True
            class Observer(QtCore.QObject):
                def eventFilter(self, watched, event):
                    if event.type() == QtCore.QEvent.Close:
                        report['closed'] = True
                        save()
                    return False
            observer = Observer(window)
            window.installEventFilter(observer)
            window.close()
        except BaseException:
            report['error'] = traceback.format_exc()
            save()
            traceback.print_exc()
            QtWidgets.QApplication.instance().exit(1)
    with patch.object(desktop, 'attach_workbench', side_effect=attach):
        desktop.launch([fixture('1ubq.pdb')], workspace=output)


if __name__ == '__main__':
    native_navigation(sys.argv[-1])
