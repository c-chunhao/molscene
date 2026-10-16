"""Actual original native Qt assembly / trajectory acceptance and independent reload."""
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET


def reload_products(root):
    from pymol2 import PyMOL
    root=Path(root)
    assembly=json.loads((root/'assembly/report.json').read_text())
    with PyMOL() as pm:
        pm.cmd.set('cif_use_auth',1)
        for i,row in enumerate(assembly['copies']):
            name='copy'+str(i); pm.cmd.load(str(root/'assembly'/row['path']),name)
            actual=pm.cmd.get_model(name).atom
            assert len(actual)==len(row['source_atoms'])==row['atom_count']
            for atom,expected in zip(actual,row['source_atoms']):
                for key in ('name','resn','resi','chain','segi','alt'): assert getattr(atom,key)==expected[key]
                assert math.dist(atom.coord,expected['coord'])<=.001
        pm.cmd.reinitialize(); pm.cmd.do('@'+str(root/'trajectory/replay.pml'))
        assert pm.cmd.count_states('motion')==6
        for state in range(1,7):
            assert pm.cmd.get_coordset('motion',state).tolist()==[[0.,0.,0.],[float(2*state),0.,0.]]
        pm.cmd.reinitialize(); pm.cmd.do('@'+str(root/'dcd-analysis/replay.pml'))
        from tests.test_dcd import ORACLE
        assert pm.cmd.count_states('topology')==len(ORACLE)
        for state,expected in enumerate(ORACLE,1):
            assert pm.cmd.get_coordset('topology',state).tolist()==expected
    (root/'reload.json').write_text(json.dumps(dict(independent_reload=True)),encoding='utf-8')


def run_native(directory):
    import traceback
    from molscene import desktop,__version__
    desktop._gui_runtime()
    from pymol.Qt import QtCore,QtWidgets,PYQT_NAME
    from pymol import plugins
    from tests.owned_processes import OwnedProcesses,track_qt_jobs,publish_native_owner
    from tests.advanced_native import wait_for
    from tests.test_desktop import grab_native_framebuffer
    QTest=__import__(PYQT_NAME+'.QtTest',fromlist=['QTest']).QTest
    root=Path(directory).resolve(); root.mkdir(parents=True,exist_ok=True)
    publish_native_owner(root)
    plugins.PYMOLPLUGINSRC=str(root/'isolated-preferences.py')
    # Genuine relative UI entries keep public screenshots independent of machine paths.
    shutil.copy2(Path(__file__).resolve().parents[1]/'examples/data/assembly-original.cif',root/'deposited.cif')
    from tests.test_dcd import original_dcd
    (root/'motion.dcd').write_bytes(original_dcd())
    os.chdir(root)
    owned=OwnedProcesses()
    report=dict(version=__version__,owned_pids=[os.getpid()],owned_lifetimes=owned.records,trace=[],closed=False)
    real_attach=desktop.attach_workbench
    scheduled=False; failures=[]
    def step(message):
        print(message,flush=True); report['trace'].append(message)
        (root/'partial-native.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    def attach(parent=None):
        nonlocal scheduled
        dock=real_attach(parent)
        if not scheduled:
            scheduled=True
            QtCore.QTimer.singleShot(150000,dock.parentWidget().close)
            QtCore.QTimer.singleShot(700,lambda:exercise(dock))
        return dock

    def exercise(dock):
        panel,window=dock.widget(),dock.parentWidget(); cmd=panel.controller.cmd
        app=QtWidgets.QApplication.instance(); beats=[0]
        timer=QtCore.QTimer(window); timer.setInterval(10)
        timer.timeout.connect(lambda:beats.__setitem__(0,beats[0]+1)); timer.start()
        track_qt_jobs(panel.jobs,report,owned)
        def page(index):
            panel.page_selector.setCurrentIndex(index); app.processEvents()
            assert panel.tabs.currentIndex()==index
        def reveal(widget,index=None):
            if index is not None: page(index)
            ancestor=widget.parentWidget()
            while ancestor:
                if ancestor.property('disclosure_content'):
                    toggle=ancestor.parentWidget().findChild(QtWidgets.QToolButton)
                    if not toggle.isChecked(): QTest.mouseClick(toggle,QtCore.Qt.LeftButton)
                ancestor=ancestor.parentWidget()
            scroll=panel.tabs.currentWidget()
            if isinstance(scroll,QtWidgets.QScrollArea): scroll.ensureWidgetVisible(widget)
            app.processEvents(); assert widget.isVisibleTo(panel)
        def click(name,index=None):
            widget=panel.findChild(QtWidgets.QPushButton,name)
            assert widget is not None and widget.isEnabled(),name
            reveal(widget,index); QTest.mouseClick(widget,QtCore.Qt.LeftButton); app.processEvents()
            assert not panel.status.text().startswith('Error'),panel.status.text()
        def enter(widget,value,index=None):
            reveal(widget,index); QTest.mouseClick(widget,QtCore.Qt.LeftButton)
            QTest.keyClick(widget,QtCore.Qt.Key_A,QtCore.Qt.ControlModifier)
            QTest.keyClick(widget,QtCore.Qt.Key_Backspace)
            QTest.keyClicks(widget,str(value)); app.processEvents()
            if isinstance(widget,QtWidgets.QLineEdit): assert widget.text()==str(value)
        def worker():
            assert panel.jobs.active,panel.status.text()
            before=beats[0]
            wait_for(app,lambda:not panel.jobs.active,timeout=50,message=panel.status.text())
            assert not panel.status.text().startswith('Error'),panel.status.text()
            assert beats[0]>before; report['worker_responsive']=True
        def sources():
            data={}
            for name in ('asu','motion','topology'):
                states=[]
                for state in range(1,cmd.count_states(name)+1):
                    model=cmd.get_model(name,state)
                    states.append(dict(atoms=[dict(index=a.index,name=a.name,resn=a.resn,resi=a.resi,chain=a.chain,segi=a.segi,
                        alt=a.alt,coord=list(a.coord),b=a.b,q=a.q,vdw=a.vdw,partial_charge=a.partial_charge,
                        formal_charge=a.formal_charge) for a in model.atom],bonds=[dict(index=list(b.index),order=b.order) for b in model.bond],
                        matrix=list(cmd.get_object_matrix(name,state))))
                data[name]=states
            return data
        class CloseObserver(QtCore.QObject):
            def eventFilter(self,watched,event):
                if watched is window and event.type()==QtCore.QEvent.Close and not panel.jobs.active:
                    assert panel.close_guard.closing
                    assert not (root/'close-trajectory').exists()
                    assert panel.controller.results[-1].get('cancelled')
                    assert sources()==original
                    report.update(closed=True,cooperative_close=True,original_sources_preserved=True)
                    report['owned_pids']=sorted(set(report['owned_pids']))
                    assert owned.exited('cooperative-native-close')
                    (root/'molecular-native.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
                return False
        observer=CloseObserver(window); window._molecular_observer=observer; window.installEventFilter(observer)
        try:
            window.resize(1550,1050); window.resizeDocks([dock],[540],QtCore.Qt.Horizontal); QTest.qWait(250)
            assert window.pymolwidget.isValid() and window.pymolwidget.isVisible(); report['gl_valid']=True
            cmd.reinitialize(); cmd.set('cif_use_auth',1); cmd.load('deposited.cif','asu')
            cmd.pseudoatom('motion',name='CA',resi='1',resn='GLY',pos=[0,0,0])
            cmd.pseudoatom('motion',name='CA',resi='2',resn='ALA',pos=[2,0,0])
            for state in range(2,7): cmd.load_coordset([[0.,0.,0.],[float(state*2),0.,0.]],'motion',state=state)
            for i,point in enumerate([[0,0,0],[2,0,0],[0,3,0],[0,0,4]],1): cmd.pseudoatom('topology',name='C'+str(i),resi=str(i),pos=point)
            cmd.hide('everything'); cmd.show('spheres'); cmd.set('sphere_scale',.4); cmd.bg_color('white'); cmd.orient('all')
            panel.refresh(); original=sources(); view=tuple(cmd.get_view()); session_file=cmd.get_setting_text('session_file')
            source_hash=hashlib.sha256((root/'deposited.cif').read_bytes()).hexdigest()
            step('clicked deposited assembly catalog, asynchronous build, complete copies and explicit loading')
            enter(panel.assembly.source,'deposited.cif',0); click('assembly_catalog')
            assert panel.assembly.assembly_id.count()==2
            reveal(panel.assembly.assembly_id)
            QTest.mouseClick(panel.assembly.assembly_id,QtCore.Qt.LeftButton)
            QTest.keyClick(panel.assembly.assembly_id,QtCore.Qt.Key_End)
            QTest.keyClick(panel.assembly.assembly_id,QtCore.Qt.Key_Return)
            assert panel.assembly.assembly_id.currentText()=='cart'
            enter(panel.assembly.output,'assembly'); click('assembly_build')
            assert panel.tabs.currentIndex()==3 and all(not panel.tabs.isTabEnabled(i) for i in (0,1,2,4))
            worker(); assembly=panel.controller.results[-1]
            assert assembly['operation']=='assembly' and assembly['snapshot_status']=='current'
            assert len(panel.controller.results)==1 and set(cmd.get_names('objects'))=={'asu','motion','topology'}
            assert len(assembly['copies'])==2 and assembly['copy_count']==2
            panel.result_tables.setCurrentText('copies'); assert panel._table_model.rowCount()==2
            assert all(not Path(r['path']).is_absolute() for r in assembly['copies'])
            click('assembly_load',0)
            assert len(assembly['loaded'])==2 and len(panel.controller.results)==1
            assert sources()==original and tuple(cmd.get_view())==view and cmd.get_setting_text('session_file')==session_file
            assert panel.controller.result_status(assembly)=='current'; report['assembly_loaded']=True
            QTest.qWait(150); assert window.grab().save(str(root/'assembly-window.png'))
            framebuffer=grab_native_framebuffer(window.pymolwidget); assert not framebuffer.isNull()
            assert framebuffer.save(str(root/'molecular-framebuffer.png'))
            step('clicked multi-state analysis, numerical frame/atom/residue CSV and plot oracles')
            page(2); panel.trajectory.object.setCurrentText('motion')
            reveal(panel.trajectory.align); QTest.mouseClick(panel.trajectory.align,QtCore.Qt.LeftButton,pos=QtCore.QPoint(8,panel.trajectory.align.height()//2))
            assert not panel.trajectory.align.isChecked()
            enter(panel.trajectory.start,'2'); enter(panel.trajectory.stop,'6'); enter(panel.trajectory.stride,'2')
            reveal(panel.trajectory.time_enabled); QTest.mouseClick(panel.trajectory.time_enabled,QtCore.Qt.LeftButton,pos=QtCore.QPoint(8,panel.trajectory.time_enabled.height()//2))
            assert panel.trajectory.time_enabled.isChecked()
            enter(panel.trajectory.time_step,'0.5'); enter(panel.trajectory.time_start,'7')
            enter(panel.trajectory.output,'trajectory'); click('trajectory_analyze'); worker()
            trajectory=panel.controller.results[-1]; assert trajectory['snapshot_status']=='current'
            assert len(panel.controller.results)==2
            assert [r['state'] for r in trajectory['frames']]==[2,4,6]
            assert [r['time_ps'] for r in trajectory['frames']]==[7.5,8.5,9.5]
            assert [r['rg_angstrom'] for r in trajectory['frames']]==[2.,4.,6.]
            assert abs(trajectory['atoms'][1]['rmsf_angstrom']-math.sqrt(32/3))<1e-6
            for table,count in (('frames',3),('atoms',2),('residues',2)):
                panel.result_tables.setCurrentText(table); assert panel._table_model.rowCount()==count
                with (root/'trajectory'/(table+'.csv')).open(newline='') as stream: assert len(list(csv.DictReader(stream)))==count
            for name in ('rmsd','rmsf','rg'):
                path=root/'trajectory'/(name+'.svg'); svg=ET.parse(path)
                labels=' '.join(svg.getroot().itertext()); assert 'angstrom' in labels and ('Time (ps, user supplied)' in labels or 'RMSF' in labels),labels
            assert 'Last checked source' in panel.result_source.text()
            assert sources()==original; report['trajectory_verified']=True
            panel.result_tables.setCurrentText('frames'); QTest.qWait(150)
            assert window.grab().save(str(root/'trajectory-results-window.png'))
            page(2); reveal(panel.trajectory.start); QTest.qWait(100)
            assert window.grab().save(str(root/'trajectory-window.png'))
            step('clicked DCD analysis uses explicit original topology and checks independent replay')
            panel.trajectory.object.setCurrentText('topology'); enter(panel.trajectory.selection,'all')
            enter(panel.trajectory.path,'motion.dcd'); enter(panel.trajectory.output,'dcd-analysis'); click('trajectory_analyze'); worker()
            assert len(panel.controller.results)==3
            assert [r['source_frame'] for r in panel.controller.results[-1]['frames']]==[2,4,6]
            assert sources()==original
            child=subprocess.Popen([sys.executable,'-B','-m','tests.molecular_native','--reload',str(root)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            owned.register('independent-product-reload',child.pid); report['owned_pids'].append(child.pid)
            out,err=child.communicate(timeout=40); (root/'reload.log').write_text(out+err,encoding='utf-8')
            assert child.returncode==0,out+err; report.update(json.loads((root/'reload.json').read_text()))
            step('strict Check source detects original state edits, unrelated copies and camera preserve status')
            page(3); panel.result_list.setCurrentIndex(1); assert trajectory['snapshot_status']=='current'
            cmd.frame(6); cmd.turn('y',5); click('molecular_check_source'); assert trajectory['snapshot_status']=='current'
            cmd.load_coordset([[0.,0.,0.],[13.,0.,0.]],'motion',state=6)
            click('molecular_check_source'); assert trajectory['snapshot_status']=='stale'
            cmd.load_coordset([[0.,0.,0.],[12.,0.,0.]],'motion',state=6)
            click('molecular_check_source'); assert trajectory['snapshot_status']=='current'
            assert sources()==original
            assert hashlib.sha256((root/'deposited.cif').read_bytes()).hexdigest()==source_hash
            report['original_sources_preserved']=True
            assert owned.exited('products-verified')
            step('real molecular cancellation and cooperative native close while worker active')
            page(2); panel.trajectory.object.setCurrentText('motion'); enter(panel.trajectory.path,'')
            enter(panel.trajectory.output,'cancel-trajectory'); click('trajectory_analyze'); click('cancel_job',3)
            wait_for(app,lambda:not panel.jobs.active,timeout=25)
            assert not (root/'cancel-trajectory').exists(); assert panel.controller.results[-1].get('cancelled')
            report['job_cancelled']=True
            page(2); enter(panel.trajectory.output,'close-trajectory'); click('trajectory_analyze')
            assert panel.jobs.active; window.close(); timer.stop()
        except BaseException:
            failure=traceback.format_exc(); failures.append(failure); print(failure,flush=True)
            (root/'failure.txt').write_text(failure,encoding='utf-8'); window.grab().save(str(root/'failure.png'))
            window.removeEventFilter(observer)
            if panel.jobs.active:
                panel.jobs.cancel(); wait_for(app,lambda:not panel.jobs.active,timeout=20)
            timer.stop(); window.close()
    desktop.attach_workbench=attach
    desktop.launch(workspace=root)
    if failures or not (root/'molecular-native.json').is_file(): raise SystemExit(1)


if __name__=='__main__':
    if '--reload' in sys.argv: reload_products(sys.argv[-1])
    else: run_native(sys.argv[-1])
