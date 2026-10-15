"""Strict native fixture workflow, screenshots and active-worker window closure."""
import json
from pathlib import Path
import sys
import traceback
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def native_evidence(output):
    from molscene import desktop, analysis
    from pymol.Qt import QtCore, QtWidgets, PYQT_NAME
    QTest = __import__(PYQT_NAME + '.QtTest', fromlist=['QTest']).QTest
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    report = {'passed': False, 'trace': [], 'close_events': 0}
    scheduled = False
    original_attach = desktop.attach_workbench
    def save():
        (output/'native-workbench.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    def attach(parent=None):
        nonlocal scheduled
        dock = original_attach(parent)
        if not scheduled:
            scheduled = True
            QtCore.QTimer.singleShot(700, lambda: exercise(dock))
            QtCore.QTimer.singleShot(90000, lambda: QtWidgets.QApplication.instance().exit(2))
        return dock
    def exercise(dock):
        panel, window = dock.widget(), dock.parentWidget()
        cmd = panel.controller.cmd
        def trace(action):
            report['trace'].append(action)
            save()
        def wait(predicate, timeout=20000):
            for _ in range(timeout//20):
                if predicate():
                    return
                QTest.qWait(20)
            raise AssertionError('Native Qt event timeout: ' + panel.status.text())
        def click(name, tab=None):
            if tab is not None:
                panel.tabs.setCurrentIndex(tab)
            button = panel.findChild(QtWidgets.QPushButton, name)
            scroll = panel.tabs.currentWidget()
            if isinstance(scroll, QtWidgets.QScrollArea):
                scroll.ensureWidgetVisible(button)
            QTest.qWait(20)
            QTest.mouseClick(button, QtCore.Qt.LeftButton)
            trace('click ' + name)
            assert not panel.status.text().startswith('Error'), panel.status.text()
        def enter(entry, value):
            entry.setFocus()
            entry.selectAll()
            QTest.keyClicks(entry, value)
        def select_object(name):
            panel.refresh()
            panel.object_list.clearSelection()
            for index in range(panel.object_list.count()):
                item = panel.object_list.item(index)
                if item.data(QtCore.Qt.UserRole) == name:
                    panel.object_list.setCurrentItem(item)
                    item.setSelected(True)
                    return
            raise AssertionError('Object missing: ' + name)
        try:
            window.resize(1350, 850)
            QTest.qWait(200)
            assert window.pymolwidget.isValid()
            report['gl_valid'] = True
            ubq = panel.controller.objects()[0]['name']
            enter(panel.selection, ubq)
            panel.representation.setCurrentText('cartoon')
            click('show_representation', 0)
            panel.coloring.setCurrentText('chain')
            click('apply_coloring')
            select_object(ubq)
            click('orient_objects')
            panel.background.setCurrentText('white')
            click('apply_appearance', 1)
            panel.tabs.setCurrentIndex(2)
            enter(panel.analysis_selection, ubq + ' and polymer.protein')
            enter(panel.geometry_atoms[0], ubq + ' and name CA and resi 1')
            enter(panel.geometry_atoms[1], ubq + ' and name CA and resi 10')
            click('run_geometry')
            expected = analysis.geometry(cmd, 'distance', [ubq+' and name CA and resi 1', ubq+' and name CA and resi 10'], state=1)
            assert abs(panel.controller.results[-1]['value'] - expected['value']) < 1e-6
            report['ubq_distance_angstrom'] = expected['value']
            click('run_inspect')
            inspection = panel.controller.results[-1]
            assert len(inspection['sequences'][0]['sequence']) == 76
            panel.tabs.setCurrentIndex(3)
            panel.result_tables.setCurrentText('chains')
            with patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=(str(output/'ubq-chains.csv'), 'CSV')):
                click('export_csv')
            with patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=(str(output/'ubq-sequence.fasta'), 'FASTA')):
                click('export_fasta')
            QTest.qWait(150)
            window.grab().save(str(output/'native-1ubq-results.png'))
            framebuffer = window.pymolwidget.grabFramebuffer()
            framebuffer.save(str(output/'native-1ubq-viewport.png'))
            colors = {framebuffer.pixel(x,y) for x in range(0,framebuffer.width(),5) for y in range(0,framebuffer.height(),5)}
            assert len(colors) > 8
            report['framebuffer_colors'] = len(colors)
            with patch.object(QtWidgets.QFileDialog, 'getOpenFileNames', return_value=([str(ROOT/'examples/data/1hsg.pdb')], 'Coordinates')):
                click('load_coordinates', 0)
            hsg = [obj['name'] for obj in panel.controller.objects() if obj['name'] != ubq][0]
            select_object(ubq)
            click('hide_objects')
            enter(panel.selection, hsg + ' and polymer.protein')
            panel.representation.setCurrentText('cartoon')
            click('show_representation')
            enter(panel.selection, hsg + ' and organic')
            panel.representation.setCurrentText('sticks')
            click('show_representation')
            panel.coloring.setCurrentText('single')
            panel.color.setText('orange')
            click('apply_coloring')
            enter(panel.selection, hsg + ' and (byres (polymer.protein within 5 of organic))')
            click('show_representation')
            panel.coloring.setCurrentText('element')
            click('apply_coloring')
            cmd.orient(hsg + ' and organic')
            cmd.zoom(hsg + ' and organic', buffer=7)
            panel.tabs.setCurrentIndex(2)
            enter(panel.analysis_selection, hsg + ' and organic')
            enter(panel.contacts_b, hsg + ' and polymer.protein')
            expected = analysis.contacts(cmd, hsg+' and organic', hsg+' and polymer.protein', state=1)
            click('run_contacts')
            wait(lambda: not panel.jobs.active)
            result = panel.controller.results[-1]
            assert result['pairs'] == expected['pairs'], panel.status.text()
            report['hsg_contact_pairs'] = result['pair_count']
            panel.tabs.setCurrentIndex(3)
            panel.result_tables.setCurrentText('pairs')
            click('annotate_result')
            assert result['displayed_count'] == min(200, result['pair_count'])
            report['hsg_drawn_contacts'] = result['displayed_count']
            QTest.qWait(200)
            window.grab().save(str(output/'native-1hsg-contacts.png'))
            window.pymolwidget.grabFramebuffer().save(str(output/'native-1hsg-viewport.png'))
            before = [(a['object'],a['index'],a['b']) for a in analysis.collect_atoms(cmd,'all',state=1)]
            panel.tabs.setCurrentIndex(2)
            enter(panel.sasa_context, hsg)
            click('run_sasa')
            wait(lambda: not panel.jobs.active)
            assert 'total_area_angstrom2' in panel.controller.results[-1], panel.status.text()
            report['hsg_ligand_sasa_angstrom2'] = panel.controller.results[-1]['total_area_angstrom2']
            assert before == [(a['object'],a['index'],a['b']) for a in analysis.collect_atoms(cmd,'all',state=1)]
            panel.tabs.setCurrentIndex(1)
            panel.export_directory.setText(str(output/"figure 中文';$(literal)"))
            panel.image_width.setValue(640)
            panel.image_height.setValue(480)
            panel.image_dpi.setValue(72)
            panel.background.setCurrentText('transparent')
            view = cmd.get_view()
            click('run_export')
            wait(lambda: not panel.jobs.active)
            assert (output/"figure 中文';$(literal)"/'scene.png').is_file(), panel.status.text()
            assert view == cmd.get_view()
            report['current_view_export'] = True
            # Closing while a real batch runs must wait for cooperative worker exit.
            import shutil
            inputs = output/'batch-inputs'
            inputs.mkdir()
            for index in range(5):
                shutil.copyfile(ROOT/'examples/data/1hsg.pdb', inputs/f'{index}.pdb')
            panel.batch_input.setText(str(inputs))
            panel.batch_output.setText(str(output/'batch-outputs'))
            panel.image_width.setValue(1000)
            panel.image_height.setValue(700)
            def terminal(kind, payload):
                report['batch_terminal'] = kind
                report['batch_terminal_payload'] = payload
                report['worker_stderr'] = panel.jobs._stderr
                report['batch_progress_at_terminal'] = panel.jobs._batch_report()
                path = Path(panel.jobs.job['result_path'])
                if path.is_file():
                    report['batch_envelope'] = json.loads(path.read_text(encoding='utf-8'))
                save()
            panel.jobs.finished.connect(lambda payload: terminal('finished', payload))
            panel.jobs.failed.connect(lambda message: terminal('failed', message))
            panel.jobs.cancelled.connect(lambda payload: terminal('cancelled', payload))
            def cancelled(result):
                try:
                    assert result['cancelled'] and result['completed'] >= 1
                    assert any(item['status']=='pending' for item in result['items'])
                    for item in result['items']:
                        if item['status']=='completed':
                            assert (output/'batch-outputs'/item['report']).is_file()
                    report['active_close_cancelled'] = True
                    report['batch_completed'] = result['completed']
                    report['batch_pending'] = sum(item['status']=='pending' for item in result['items'])
                    report['passed'] = True
                    trace('cooperative worker stopped; native close resumes')
                except BaseException:
                    report['error'] = traceback.format_exc()
                    save()
                    QtWidgets.QApplication.instance().exit(1)
            panel.jobs.cancelled.connect(cancelled)
            class CloseObserver(QtCore.QObject):
                def eventFilter(self, watched, event):
                    if event.type() == QtCore.QEvent.Close:
                        report['close_events'] += 1
                        if not panel.jobs.active:
                            report['closed_after_worker'] = True
                        save()
                    return False
            observer = CloseObserver(window)
            window._molscene_evidence_observer = observer
            window.installEventFilter(observer)
            from molscene.desktop import jobs
            def batch_command(path):
                # This test-only entry calls the real worker and render code.
                # Its first persisted item waits for the normal cancel marker.
                return [sys.executable, '-B', '-u', str(Path(__file__).resolve()), '--batch-worker', str(path)]
            with patch.object(jobs, 'worker_command', side_effect=batch_command):
                click('run_batch')
            barrier = Path(panel._active_job_path).parent/'batch.first-item.ready'
            wait(lambda: barrier.is_file() and panel.jobs.last_result is not None
                 and panel.jobs.last_result.get('completed') == 1, timeout=30000)
            assert panel.jobs.active
            report['batch_barrier_ready'] = json.loads(barrier.read_text(encoding='utf-8'))
            report['batch_progress_before_close'] = panel.jobs._batch_report()
            trace('request native main-window close during real batch')
            window.close()
            assert panel.close_guard.closing
            assert panel.jobs.active
            report['cancel_marker_present'] = Path(panel.jobs.job['parameters']['cancel_file']).is_file()
            report['close_was_deferred'] = True
            save()
        except BaseException:
            report['error'] = traceback.format_exc()
            window.grab().save(str(output/'native-failure.png'))
            save()
            traceback.print_exc()
            QtWidgets.QApplication.instance().exit(1)
    desktop.attach_workbench = attach
    desktop.launch(paths=[ROOT/'examples/data/1ubq.pdb'], workspace=output)
    if not report.get('passed'):
        raise SystemExit(1)


def batch_worker(job_path):
    """Synchronize the native test at a real persisted batch item boundary."""
    import time
    from molscene import batch, worker
    path = Path(job_path)
    job = json.loads(path.read_text(encoding='utf-8'))
    marker = Path(job['parameters']['cancel_file'])
    ready = path.parent/'batch.first-item.ready'
    run_batch = batch.run_batch
    def synchronized(*args, **kwargs):
        prior_progress = kwargs.get('progress')
        def progress(result):
            if prior_progress is not None:
                prior_progress(result)
            if result['completed'] == 1 and not ready.exists():
                staged = ready.with_suffix('.tmp')
                staged.write_text(json.dumps(result), encoding='utf-8')
                staged.replace(ready)
                deadline = time.monotonic() + 30
                while not marker.is_file():
                    if time.monotonic() > deadline:
                        raise RuntimeError('Native test did not request cancellation at the first-item barrier')
                    time.sleep(0.01)
        kwargs['progress'] = progress
        return run_batch(*args, **kwargs)
    with patch.object(batch, 'run_batch', side_effect=synchronized):
        return worker.main([str(path)])


if __name__ == '__main__':
    if '--batch-worker' in sys.argv:
        raise SystemExit(batch_worker(sys.argv[-1]))
    native_evidence(sys.argv[-1])
