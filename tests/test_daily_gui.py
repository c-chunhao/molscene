"""Compact daily controls in isolated Qt children; visible native gate is opt in."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HAS_GUI = importlib.util.find_spec('pymol2') is not None and any(importlib.util.find_spec(n) for n in ('PySide6','PySide2','PyQt6','PyQt5'))

@unittest.skipUnless(HAS_GUI, 'requires PyMOL and Qt')
class DailyControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from molscene.desktop import _gui_runtime
        _, cls.qt = _gui_runtime()
        cls.app = cls.qt.QtWidgets.QApplication.instance() or cls.qt.QtWidgets.QApplication([])

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
        self.cmd.pseudoatom('source', name='CA', resn='GLY', resi='12', chain='A')
        self.controller = WorkbenchController(self.cmd, self.root)
        self.panel = WorkbenchPanel(self.controller)
        self.addCleanup(self.dispose)

    def dispose(self):
        self.panel.refresh_timer.stop()
        self.panel.close()
        self.panel.deleteLater()
        self.qt.QtCore.QCoreApplication.sendPostedEvents(self.panel, self.qt.QtCore.QEvent.DeferredDelete)

    def test_complete_names_and_bidirectional_navigation_at_compact_and_wide_widths(self):
        p = self.panel
        names = ['Structure','Figure','Analysis','Results','Advanced']
        self.assertEqual([p.page_selector.itemText(i) for i in range(5)], names)
        for width in (360,420,540,800,360):
            p.resize(width,720)
            p.show()
            self.app.processEvents()
            self.assertEqual(p.page_selector.isVisible(), width <= 540)
            self.assertEqual(p.tabs.tabBar().isVisible(), width > 540)
            for i in range(5):
                p.page_selector.setCurrentIndex(i)
                self.assertEqual(p.tabs.currentIndex(), i)
                p.tabs.setCurrentIndex((i+1)%5)
                self.assertEqual(p.page_selector.currentIndex(), (i+1)%5)
            self.assertLessEqual(p.minimumSizeHint().width(), 360)

    def test_every_job_locks_both_navigation_routes_and_keeps_results_cancel(self):
        p = self.panel
        cases = [('contacts',{}),('polar',{}),('sasa',{}),('interface',{}),('export',{}),('batch',{}),
            ('tools',{}),('coordinates',{'request':{'state':1}}),('session_save',{'request':{}}),
            ('density',{'request':{'source_state':1}}),('potential',{'request':{'state':1}}),
            ('timeline_project',{'timeline':{'keyframes':[{'state':1}]}}),('animation',{'timeline':{'keyframes':[{'state':1}]}})]
        for operation, parameters in cases:
            p._job_started(dict(operation=operation,parameters=parameters,output='new-result'))
            for i in (0,1,2,4):
                self.assertFalse(p.tabs.isTabEnabled(i))
                self.assertFalse(p.page_selector.model().item(i).isEnabled())
                p.page_selector.setCurrentIndex(i)
                self.assertEqual(p.tabs.currentIndex(),3)
                p.tabs.setCurrentIndex(i)
                self.assertEqual(p.tabs.currentIndex(),3)
            self.assertTrue(p.cancel_job.isEnabled())
            self.assertEqual(p.page_selector.currentIndex(),3)
            p._job_idle()
            self.assertTrue(all(p.tabs.isTabEnabled(i) for i in range(5)))

    def test_browse_unicode_cancel_new_directory_and_existing_link_refusal(self):
        from molscene.desktop.ui_helpers import browse_input, browse_new_directory
        entry = self.panel.advanced.density.path
        value = str(self.root / "数据 O'Brien.dx")
        with patch.object(self.qt.QtWidgets.QFileDialog,'getOpenFileName',return_value=(value,'DX')):
            browse_input(self.panel, entry, 'Map', 'DX (*.dx)')
        self.assertEqual(entry.text(),value)
        with patch.object(self.qt.QtWidgets.QFileDialog,'getOpenFileName',return_value=('','')):
            browse_input(self.panel, entry, 'Map', 'DX (*.dx)')
        self.assertEqual(entry.text(),value)
        with patch.object(self.qt.QtWidgets.QFileDialog,'getExistingDirectory',return_value=str(self.root)), patch.object(self.qt.QtWidgets.QInputDialog,'getText',return_value=('新结果',True)):
            browse_new_directory(self.panel,entry)
        destination = self.root / '新结果'
        self.assertEqual(entry.text(),str(destination))
        self.assertFalse(destination.exists())
        destination.mkdir()
        with patch.object(self.qt.QtWidgets.QFileDialog,'getExistingDirectory',return_value=str(self.root)), patch.object(self.qt.QtWidgets.QInputDialog,'getText',return_value=('新结果',True)):
            with self.assertRaises(ValueError): browse_new_directory(self.panel,entry)
        link = self.root / 'linked'
        try: link.symlink_to(destination,target_is_directory=True)
        except OSError as exc: print('Optional real symlink branch unavailable: '+str(exc),flush=True)
        else:
            with patch.object(self.qt.QtWidgets.QFileDialog,'getExistingDirectory',return_value=str(self.root)), patch.object(self.qt.QtWidgets.QInputDialog,'getText',return_value=('linked',True)):
                with self.assertRaises(ValueError): browse_new_directory(self.panel,entry)
        with patch.object(self.qt.QtWidgets.QFileDialog,'getExistingDirectory',return_value=''):
            browse_new_directory(self.panel,entry)
        self.assertEqual(entry.text(),str(destination))

    def test_browse_rows_hide_label_field_and_button_with_mode(self):
        p = self.panel.advanced.potential
        for mode,pqr,dx in [('prepared',True,False),('prepare',False,False),('import',True,True)]:
            p.mode.setCurrentText(mode)
            for field,visible in ((p.pqr,pqr),(p.dx,dx)):
                self.assertEqual(field._path_row.isVisibleTo(p),visible)
                self.assertEqual(field._path_label.isVisibleTo(p),visible)
                self.assertEqual(field._path_browse.isVisibleTo(p),visible)
        for name in ('browse_density_path','browse_density_output','browse_potential_tools_path','browse_potential_output','browse_timeline_output'):
            self.assertIsNotNone(self.panel.findChild(self.qt.QtWidgets.QPushButton,name),name)

    def test_tool_summary_is_bounded_and_keeps_real_status_and_full_details(self):
        view = self.panel.advanced.potential
        huge = 'F:/' + 'long directory/'*200
        view.show_tools_result(dict(tools={'apbs':dict(available=True,manifest={'version':'3.4.1','architecture':'x64','argv':[huge+'apbs.exe']},error=None),
            'pdb2pqr':dict(available=False,manifest=None,error='missing runtime '+huge)}))
        self.assertLess(len(view.tools_status.text()),450)
        self.assertIn('3.4.1',view.tools_status.text())
        self.assertIn('x64',view.tools_status.text())
        self.assertIn('unavailable',view.tools_status.text())
        self.assertIn(huge,view.tools_status.toolTip())
        self.assertIn(huge,view.tools_details.toPlainText())
        self.assertFalse(view.tools_details.isVisibleTo(view))

    def test_density_summary_retains_units_shape_origin_with_collapsed_matrix_details(self):
        d = self.panel.advanced.density
        d.refresh([dict(name='map',state=1,verified=True,world_matrix=list(range(16)),load_matrix=list(range(16)),
            grid={'shape':[3,4,5],'origin_angstrom':[1,2,3],'value_unit':'raw','normalization':'none','basis_angstrom':[[1,0,0]],'statistics':{}},source_sha256='a'*64,format='dx',interpretation='auto')],['source'])
        self.assertIn('raw',d.metadata.text())
        self.assertIn('[3, 4, 5]',d.metadata.text())
        self.assertIn('[1, 2, 3]',d.metadata.text())
        self.assertNotIn('World matrix:',d.metadata.text())
        self.assertIn('world_matrix',d.metadata_details.toPlainText())
        self.assertFalse(d.metadata_details.isVisibleTo(d))

    def test_daily_dispatch_and_literal_label_preview_apply_clear(self):
        p = self.panel
        self.assertEqual(p.polar_cutoff.value(),3.5)
        self.assertEqual(p.polar_angle.value(),45)
        p.analysis_selection.setText('source')
        p.contacts_b.setText('source')
        with patch.object(p,'_launch_job') as launch:
            p._polar()
            self.assertEqual(launch.call_args.args[0],'polar')
            self.assertEqual(launch.call_args.args[1]['altloc'],'primary')
            p.coordinates_output.setText('new-coordinates')
            p._save_coordinates()
            self.assertEqual(launch.call_args.args[0],'coordinates')
            self.assertEqual(launch.call_args.args[1]['request'],dict(selection='all',state=1,format='cif'))
            p.session_output.setText('new-session')
            p._save_session()
            self.assertEqual(launch.call_args.args[0],'session_save')
        p.label_recipe.setCurrentText('chain_residue')
        p._preview_labels()
        self.assertIn('A:GLY12',p.label_preview.toPlainText())
        p._apply_label_recipe()
        rows=[]
        self.cmd.iterate('source','rows.append(label)',space={'rows':rows})
        self.assertEqual(rows,['A:GLY12'])
        self.assertIn('same object/state',p.label_scope.text())
        p._clear_labels()
        rows=[]
        self.cmd.iterate('source','rows.append(label)',space={'rows':rows})
        self.assertEqual(rows,[''])

    def test_saved_results_are_readable_complete_and_cannot_annotate(self):
        p = self.panel
        record=dict(operation='coordinates',kind='saved_snapshot',result_kind='saved_snapshot',state=1,
            snapshot_status='saved',object_files=[{'path':'objects/0001.cif','atoms':1}],artifacts=[{'path':'report.json'}],output_path='new-coordinates')
        p._populate_result(record)
        self.assertIn('Saved snapshot',p.result_summary.text())
        self.assertFalse(p.annotate_button.isEnabled())
        self.assertIn('object_files',[p.result_tables.itemText(i) for i in range(p.result_tables.count())])
        self.assertIn('new-coordinates',p.result_details.toPlainText())
        p._populate_result(dict(record,cancelled=True,passed=False))
        self.assertIn('Cancelled save',p.result_summary.text())
        self.assertNotIn('Saved snapshot',p.result_summary.text())


def isolated_controls(original):
    methods = unittest.defaultTestLoader.getTestCaseNames(original)
    @classmethod
    def setup(cls):
        with tempfile.TemporaryDirectory(prefix='daily-controls-') as folder:
            report = Path(folder)/'result.json'
            root = Path(__file__).resolve().parents[1]
            child = subprocess.run([sys.executable,'-B','-m','tests.test_daily_gui','--daily-controls-child',str(report)],cwd=root,
                env=dict(os.environ,PYTHONPATH=str(root),QT_QPA_PLATFORM='offscreen'),capture_output=True,text=True,timeout=180)
            print(child.stdout+child.stderr,end='',flush=True)
            if child.returncode: raise AssertionError('Daily controls child exited '+str(child.returncode))
            data=json.loads(report.read_text())
            if data['tests_run']!=len(methods) or data['skips']: raise AssertionError(str(data))
            cls.successes=data['successes']
            print('DAILY_CONTROLS '+json.dumps(data),flush=True)
    def passed(self): self.assertIn('__main__.DailyControlTests.'+self._testMethodName,self.successes)
    return unittest.skipUnless(HAS_GUI,'requires PyMOL and Qt')(type('DailyControlTests',(unittest.TestCase,),dict(__module__=__name__,setUpClass=setup,**{m:passed for m in methods})))

if '--daily-controls-child' not in sys.argv:
    DailyControlTests=isolated_controls(DailyControlTests)

@unittest.skipUnless(os.environ.get('MOLSCENE_DAILY_NATIVE')=='1' or os.environ.get('MOLSCENE_REQUIRE_DAILY')=='1' or os.environ.get('MOLSCENE_REQUIRE_ADVANCED')=='1','requires allocated native desktop focus')
class NativeDailyTests(unittest.TestCase):
    def test_native_daily_polar_labels_exports_and_narrow_navigation(self):
        self.assertTrue(HAS_GUI,'Native daily gate requires actual backend/Qt')
        artifacts=os.environ.get('MOLSCENE_TEST_ARTIFACTS')
        if artifacts:
            Path(artifacts).mkdir(parents=True,exist_ok=True)
            root=Path(tempfile.mkdtemp(prefix='daily-',dir=artifacts))
        else:
            temporary=tempfile.TemporaryDirectory(prefix='daily-')
            self.addCleanup(temporary.cleanup)
            root=Path(temporary.name)
        print('Daily native evidence: '+str(root),flush=True)
        env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        env.pop('QT_QPA_PLATFORM',None)
        from tests.owned_processes import run_native_child
        child,stdout,stderr,native_pid=run_native_child([sys.executable,'-B','-m','tests.daily_native',str(root)],root,timeout=180,
            cwd=Path(__file__).resolve().parents[1],env=env)
        (root/'process.log').write_text(stdout+stderr,encoding='utf-8')
        self.assertEqual(child.returncode,0,stdout+stderr)
        report=json.loads((root/'daily-native.json').read_text())
        for key in ('closed','gl_valid','original_sources_preserved','coordinates_reloaded','session_reloaded','worker_responsive','job_cancelled','narrow_navigation','dialog_cancelled','literal_labels'):
            self.assertTrue(report[key],key)
        from tests.owned_processes import verify_native_report
        verify_native_report(root,'daily-native.json',native_pid,child.returncode)

if __name__=='__main__':
    if '--daily-controls-child' in sys.argv:
        class Result(unittest.TextTestResult):
            def __init__(self,*args,**kwargs):
                super().__init__(*args,**kwargs)
                self.successes=[]
            def addSuccess(self,test):
                super().addSuccess(test)
                self.successes.append(test.id())
        result=unittest.TextTestRunner(verbosity=2,resultclass=Result).run(unittest.defaultTestLoader.loadTestsFromTestCase(DailyControlTests))
        Path(sys.argv[-1]).write_text(json.dumps(dict(tests_run=result.testsRun,successes=result.successes,skips=len(result.skipped),pid=os.getpid())),encoding='utf-8')
        raise SystemExit(not result.wasSuccessful() or bool(result.skipped))
    unittest.main()
