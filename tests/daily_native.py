"""Independently runnable real desktop daily acceptance (no source-path injection)."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def reload_saved(root):
    """Fresh headless process verifies both products independently of the live UI."""
    from pymol2 import PyMOL
    root = Path(root)
    report = json.loads((root / 'coordinates' / 'report.json').read_text(encoding='utf-8'))
    expected = json.loads((root / 'expected-atoms.json').read_text(encoding='utf-8'))
    with PyMOL() as runtime:
        cmd = runtime.cmd
        for number, item in enumerate(report['object_files'], 1):
            cmd.load(str(root / 'coordinates' / item['path']), 'reload_' + str(number))
        model = cmd.get_model('all', state=1)
        actual = sorted((a.symbol,a.name,a.resn,a.resi,a.chain,a.segi,a.alt,tuple(a.coord)) for a in model.atom)
        assert len(actual) == len(expected) == 8
        for left,right in zip(actual,expected):
            assert list(left[:7]) == right[:7], (left,right)
            assert sum((a-b)**2 for a,b in zip(left[7],right[7])) ** .5 <= .001
        cmd.reinitialize()
        cmd.load(str(root / 'session' / 'session.pse'))
        assert cmd.count_atoms('don or acc') == 8
        labels=[]
        cmd.iterate('literal', 'labels.append(label)', space={'labels':labels})
        assert labels == ["B:Q'R7"], labels
        assert 'daily_view' in cmd.get_scene_list()
        assert cmd.get_setting_float('label_size','literal',state=1) == 18
    (root / 'reload.json').write_text(json.dumps(dict(pid=os.getpid(),coordinates_reloaded=True,session_reloaded=True)),encoding='utf-8')


def run_native(directory):
    from unittest.mock import patch
    import traceback
    from molscene import desktop, __version__
    desktop._gui_runtime()
    from pymol.Qt import QtCore, QtWidgets, PYQT_NAME
    from pymol import plugins
    from tests.test_desktop import grab_native_framebuffer
    from tests.test_polar import fragments
    from tests.owned_processes import OwnedProcesses, track_qt_jobs, publish_native_owner
    from tests.advanced_native import wait_for
    QTest=__import__(PYQT_NAME+'.QtTest',fromlist=['QTest']).QTest
    root=Path(directory).resolve()
    root.mkdir(parents=True,exist_ok=True)
    publish_native_owner(root)
    plugins.PYMOLPLUGINSRC=str(root/'isolated-preferences.py')
    import molscene
    owned=OwnedProcesses()
    report=dict(version=__version__,package_origin=str(Path(molscene.__file__).resolve()),owned_pids=[os.getpid()],owned_lifetimes=owned.records,closed=False,trace=[])
    real_attach=desktop.attach_workbench
    scheduled=False
    failed=[]

    def step(value):
        report['trace'].append(value)
        print(value,flush=True)
        (root/'partial-native.json').write_text(json.dumps(report,indent=2),encoding='utf-8')

    def attach(parent=None):
        nonlocal scheduled
        dock=real_attach(parent)
        if not scheduled:
            scheduled=True
            QtCore.QTimer.singleShot(120000,dock.parentWidget().close)
            QtCore.QTimer.singleShot(700,lambda:exercise(dock))
        return dock

    def exercise(dock):
        panel,window=dock.widget(),dock.parentWidget()
        cmd=panel.controller.cmd
        app=QtWidgets.QApplication.instance()
        beats=[0]
        timer=QtCore.QTimer(window)
        timer.setInterval(10)
        timer.timeout.connect(lambda:beats.__setitem__(0,beats[0]+1))
        timer.start()
        track_qt_jobs(panel.jobs,report,owned)
        def track():
            process=panel.jobs.process
            if process is not None:
                process.started.connect(lambda: report['owned_pids'].append(int(process.processId())))
                if int(process.processId()):report['owned_pids'].append(int(process.processId()))
        def page(index):
            panel.page_selector.setCurrentIndex(index)
            app.processEvents()
            assert panel.tabs.currentIndex()==index
        def click(name,index=None):
            if index is not None:page(index)
            widget=panel.findChild(QtWidgets.QPushButton,name)
            assert widget is not None and widget.isEnabled(), name+': unavailable'
            ancestor=widget.parentWidget()
            while ancestor:
                if ancestor.property('disclosure_content'):
                    toggle=ancestor.parentWidget().findChild(QtWidgets.QToolButton)
                    if not toggle.isChecked():toggle.setChecked(True)
                ancestor=ancestor.parentWidget()
            scroll=panel.tabs.currentWidget()
            if isinstance(scroll,QtWidgets.QScrollArea):scroll.ensureWidgetVisible(widget)
            app.processEvents()
            assert widget.isVisibleTo(panel),name+': hidden'
            QTest.mouseClick(widget,QtCore.Qt.LeftButton)
            app.processEvents()
            assert not panel.status.text().startswith('Error'),panel.status.text()
        def worker():
            assert panel.jobs.active,panel.status.text()
            track()
            before=beats[0]
            wait_for(app,lambda:not panel.jobs.active,timeout=40,message=panel.status.text())
            assert not panel.status.text().startswith('Error'),panel.status.text()
            assert beats[0]>before,'Qt heartbeat did not advance'
            report['worker_responsive']=True
        def sources():
            data={}
            for name in ('don','acc'):
                model=cmd.get_model(name,state=1)
                data[name]=dict(atoms=[dict(index=a.index,name=a.name,resn=a.resn,resi=a.resi,chain=a.chain,segi=a.segi,alt=a.alt,
                    coord=list(a.coord),formal_charge=a.formal_charge,partial_charge=a.partial_charge,b=a.b,q=a.q,vdw=a.vdw) for a in model.atom],
                    bonds=[dict(index=list(b.index),order=b.order) for b in model.bond],matrix=list(cmd.get_object_matrix(name,state=1)))
            return data
        class CloseObserver(QtCore.QObject):
            def eventFilter(self,watched,event):
                if watched is window and event.type()==QtCore.QEvent.Close and not panel.jobs.active:
                    assert panel.close_guard.closing, 'Active close did not pass through the cooperative guard'
                    assert not (root/'close-session').exists(), 'Close published a cancelled save'
                    assert panel.controller.results[-1].get('cancelled'), 'Close did not retain cancelled result'
                    report['cooperative_close']=True
                    report['closed']=True
                    report['owned_pids']=sorted(set(pid for pid in report['owned_pids'] if pid>0))
                    assert owned.exited('cooperative-native-close'), 'Owned worker survived close'
                    for key in ('gl_valid','original_sources_preserved','coordinates_reloaded','session_reloaded','worker_responsive','job_cancelled','narrow_navigation','dialog_cancelled','literal_labels'):
                        assert report.get(key) is True,key
                    (root/'daily-native.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
                return False
        observer=CloseObserver(window)
        window._daily_observer=observer
        window.installEventFilter(observer)
        try:
            window.resize(1280,860)
            window.resizeDocks([dock],[360],QtCore.Qt.Horizontal)
            QTest.qWait(200)
            report['gl_valid']=window.pymolwidget.isValid() and window.pymolwidget.isVisible()
            assert report['gl_valid']
            cmd.reinitialize()
            fragments(cmd)
            cmd.pseudoatom('literal',name='CA',resn="Q'R",resi='7',chain='B',pos=[1,2,0])
            cmd.hide('everything')
            cmd.show('sticks','don or acc')
            cmd.show('spheres','don or acc')
            cmd.set('sphere_scale',.25)
            cmd.bg_color('white')
            cmd.orient('don or acc')
            cmd.zoom('don or acc',buffer=3)
            panel.refresh()
            original=sources()
            view=tuple(cmd.get_view())
            (root/'original-before.json').write_text(json.dumps(original,indent=2),encoding='utf-8')
            expected=sorted((a.symbol,a.name,a.resn,a.resi,a.chain,a.segi,a.alt,tuple(a.coord)) for a in cmd.get_model('don or acc',state=1).atom)
            (root/'expected-atoms.json').write_text(json.dumps(expected),encoding='utf-8')
            step('real narrow page selector and dialog cancellation')
            assert 340<=panel.width()<=540,panel.width()
            assert panel.page_selector.isVisible() and not panel.tabs.tabBar().isVisible()
            assert [panel.page_selector.itemText(i) for i in range(5)]==['Structure','Figure','Analysis','Results','Advanced']
            for i in range(5):page(i)
            report['narrow_navigation']=True
            before=panel.advanced.density.path.text()
            with patch.object(QtWidgets.QFileDialog,'getOpenFileName',return_value=('','')):
                click('browse_density_path',4)
            assert panel.advanced.density.path.text()==before
            report['dialog_cancelled']=True
            step('bonded polar calculation, complete rows and live annotation')
            panel.analysis_selection.setText('don')
            panel.contacts_b.setText('acc')
            click('run_polar',2)
            assert all(not panel.tabs.isTabEnabled(i) for i in (0,1,2,4))
            assert panel.cancel_job.isEnabled() and panel.tabs.currentIndex()==3
            panel.page_selector.setCurrentIndex(1)
            assert panel.tabs.currentIndex()==3 and panel.page_selector.currentIndex()==3
            worker()
            record=panel.controller.results[-1]
            assert record['operation']=='polar' and record['pair_count']==1,record
            assert abs(record['pairs'][0]['distance_angstrom']-3)<1e-6
            panel.result_tables.setCurrentText('pairs')
            assert panel._table_model.rowCount()==1
            click('annotate_result',3)
            assert record['displayed_count']==1
            report['polar_pairs']=record['pair_count']
            step('literal guided label preview/apply and clear')
            panel.selection.setText('literal')
            panel.label_recipe.setCurrentText('chain_residue')
            panel.label_size.setValue(18)
            panel.label_color.setText('black')
            click('preview_labels',1)
            assert "B:Q'R7" in panel.label_preview.toPlainText()
            click('apply_label_recipe')
            labels=[]
            cmd.iterate('literal','labels.append(label)',space={'labels':labels})
            assert labels==["B:Q'R7"]
            click('clear_labels')
            labels=[]
            cmd.iterate('literal','labels.append(label)',space={'labels':labels})
            assert labels==['']
            click('apply_label_recipe')
            report['literal_labels']=True
            cmd.scene('daily_view','store')
            step('asynchronous coordinate and complete-session saves')
            panel.selection.setText('don or acc')
            panel.coordinates_output.setText(str(root/'coordinates'))
            click('save_coordinates')
            worker()
            assert panel.controller.results[-1]['snapshot_status']=='saved'
            assert 'Saved snapshot' in panel.result_summary.text()
            assert not panel.annotate_button.isEnabled()
            page(1)
            panel.session_output.setText(str(root/'session'))
            click('save_session')
            worker()
            assert not panel.annotate_button.isEnabled()
            reload_process=subprocess.Popen([sys.executable,'-B','-m','tests.daily_native','--reload',str(root)],
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            report['owned_pids'].append(reload_process.pid)
            owned.register('saved-products-reload',reload_process.pid)
            out,err=reload_process.communicate(timeout=30)
            (root/'reload.log').write_text(out+err,encoding='utf-8')
            assert reload_process.returncode==0,out+err
            report.update(json.loads((root/'reload.json').read_text(encoding='utf-8')))
            del report['pid']
            after=sources()
            (root/'original-after.json').write_text(json.dumps(after,indent=2),encoding='utf-8')
            assert original==after
            assert max(abs(a-b) for a,b in zip(view,cmd.get_view()))<1e-5
            report['original_sources_preserved']=True
            step('actual framebuffer and readable private-path-free UI capture')
            panel.coordinates_output.setText('new-coordinates')
            panel.session_output.setText('new-session')
            panel.status.setText('Ready · polar candidates, labels and saved snapshots verified')
            page(2)
            # Show the real polar controls at a useful scroll position.
            toggle=panel.findChild(QtWidgets.QToolButton,'contacts_section')
            toggle.setChecked(False)
            panel.tabs.currentWidget().ensureWidgetVisible(panel.findChild(QtWidgets.QPushButton,'run_polar'))
            cmd.refresh()
            QTest.qWait(300)
            framebuffer=grab_native_framebuffer(window.pymolwidget)
            assert not framebuffer.isNull()
            assert framebuffer.save(str(root/'daily-framebuffer.png'))
            from PIL import Image
            with Image.open(root/'daily-framebuffer.png') as image:
                extrema=image.convert('RGB').getextrema()
                assert any(hi-lo>40 for lo,hi in extrema),extrema
            assert window.grab().save(str(root/'daily-window.png'))
            page(1)
            for name in ('appearance_section','guided_labels_section','persistence_section'):
                panel.findChild(QtWidgets.QToolButton,name).setChecked(name=='guided_labels_section')
            panel.tabs.currentWidget().ensureWidgetVisible(panel.findChild(QtWidgets.QToolButton,'guided_labels_section'))
            QTest.qWait(100)
            assert window.grab().save(str(root/'daily-labels-window.png'))
            report['dock_width']=dock.width()
            report['panel_width']=panel.width()
            report['framebuffer_sha256']=hashlib.sha256((root/'daily-framebuffer.png').read_bytes()).hexdigest()
            assert owned.exited('saved-products-cleaned'), 'Owned process survived saved products'
            step('cooperative worker cancellation and normal native close')
            panel.session_output.setText(str(root/'cancel-session'))
            click('save_session',1)
            track()
            click('cancel_job')
            wait_for(app,lambda:not panel.jobs.active,timeout=20)
            assert not (root/'cancel-session').exists()
            assert panel.controller.results[-1].get('cancelled')
            assert 'Cancelled save' in panel.result_summary.text()
            report['job_cancelled']=True
            page(1)
            panel.session_output.setText(str(root/'close-session'))
            click('save_session')
            track()
            assert panel.jobs.active
            panel.advanced.timeline.timer.start()
            # The real close guard requests cancellation, rejects this close,
            # then reissues normal native close only after the worker stops.
            window.close()
            assert not panel.advanced.timeline.timer.isActive()
            timer.stop()
        except BaseException:
            failure=traceback.format_exc()
            failed.append(failure)
            print(failure,flush=True)
            (root/'failure.txt').write_text(failure,encoding='utf-8')
            (root/'partial-native.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            window.grab().save(str(root/'failure.png'))
            window.removeEventFilter(observer)
            if panel.jobs.active:
                panel.jobs.cancel()
                wait_for(app,lambda:not panel.jobs.active,timeout=15)
            timer.stop()
            window.close()
    desktop.attach_workbench=attach
    desktop.launch(workspace=root)
    if failed or not (root/'daily-native.json').is_file():raise SystemExit(1)


if __name__=='__main__':
    if '--reload' in sys.argv:reload_saved(sys.argv[-1])
    else:run_native(sys.argv[-1])
