"""Isolated actual Qt controls and the allocated native molecular workflow."""
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
ROOT = Path(__file__).resolve().parents[1]

class ControlCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from molscene.desktop import _gui_runtime
        _, cls.qt = _gui_runtime()
        cls.app = cls.qt.QtWidgets.QApplication.instance() or cls.qt.QtWidgets.QApplication([])

    def setUp(self):
        from pymol2 import PyMOL
        from molscene.desktop.controller import WorkbenchController
        from molscene.desktop.panel import WorkbenchPanel
        self.pm = PyMOL(); self.pm.start(); self.addCleanup(self.pm.stop)
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.pm.cmd.pseudoatom('source',name='CA')
        self.panel = WorkbenchPanel(WorkbenchController(self.pm.cmd,Path(self.temp.name)))
        self.addCleanup(self.dispose)

    def dispose(self):
        self.panel.refresh_timer.stop(); self.panel.close(); self.panel.deleteLater()
        self.qt.QtCore.QCoreApplication.sendPostedEvents(self.panel,self.qt.QtCore.QEvent.DeferredDelete)

    def test_controls_defaults_catalog_and_dispatch(self):
        p=self.panel
        self.assertTrue(hasattr(p,'assembly'), 'Structure needs biological assembly controls')
        a,t=p.assembly,p.trajectory
        self.assertEqual(t.object.currentText(),'source')
        self.assertEqual((t.selection.text(),t.fit_selection.text()),('name CA','name CA'))
        self.assertTrue(t.align.isChecked()); self.assertFalse(t.time_enabled.isChecked())
        self.assertFalse(t.time_step.isEnabled()); self.assertEqual(t.stop.value(),0)
        self.assertFalse(p.findChild(self.qt.QtWidgets.QToolButton,'assembly_section').isChecked())
        self.assertFalse(p.findChild(self.qt.QtWidgets.QToolButton,'trajectory_section').isChecked())
        a.source.setText(str(ROOT/'examples/data/assembly-original.cif')); a.catalog()
        self.assertEqual([a.assembly_id.itemText(i) for i in range(a.assembly_id.count())],['1','cart'])
        self.assertIn('copies',a.metadata.text())
        a.assembly_id.setCurrentText('cart'); a.output.setText('new-assembly')
        with patch.object(p,'_launch_job') as launch:
            a.build(); self.assertEqual(launch.call_args.args[0],'assembly')
            self.assertEqual(launch.call_args.args[1]['request']['assembly_id'],'cart')
            self.assertEqual(launch.call_args.kwargs['output'],'new-assembly')
            t.output.setText('new-trajectory'); t.analyze()
            req=launch.call_args.args[1]['request']
            self.assertEqual(launch.call_args.args[0],'trajectory'); self.assertIsNone(req['stop'])
            self.assertIsNone(req['time_step_ps']); self.assertNotIn('trajectory_path',req)
            t.time_enabled.setChecked(True); t.time_step.setValue(.5); t.path.setText('local.dcd'); t.analyze()
            self.assertEqual(launch.call_args.args[1]['request']['trajectory_path'],'local.dcd')
            self.assertEqual(launch.call_args.args[1]['request']['time_step_ps'],.5)

    def test_large_results_are_bounded_without_losing_table_data(self):
        p=self.panel
        atoms=[dict(index=i,coord=[1,2,3]) for i in range(10000)]
        rows=[dict(object_name='copy',source_atoms=atoms)]
        record=dict(operation='assembly',copies=rows,analysis=dict(trajectory=dict(atoms=atoms)),snapshot_status='current')
        p._current_record=record; p._populate_result(record); p.result_tables.setCurrentText('copies')
        self.assertLess(len(p.result_details.toPlainText()),6000)
        col=p._table_model.columns.index('source_atoms')
        for role in (self.qt.QtCore.Qt.DisplayRole,self.qt.QtCore.Qt.ToolTipRole):
            self.assertLess(len(p._table_model.data(p._table_model.index(0,col),role)),2000)
        self.assertIs(p._table_model.rows[0]['source_atoms'],atoms)
        self.assertEqual(len(record['analysis']['trajectory']['atoms']),10000)
        destination=Path(self.temp.name)/'full.csv'
        p.controller.export_table(record,'copies',destination)
        self.assertIn('9999',destination.read_text())
        from molscene.desktop.panel import ResultTableModel
        small=[{'matrix':[1,0,0,1],'operators':[{'id':'R'}]}]
        model=ResultTableModel(small)
        self.assertEqual(json.loads(model.data(model.index(0,1))),[{'id':'R'}])
        nested=[{str(i):'x'*1999 for i in range(64)} for unused in range(16)]
        model=ResultTableModel([{'nested':nested}])
        self.assertLess(len(model.data(model.index(0,0))),2000)
        p._populate_result(dict(operation='trajectory',output_path='important-output-location',
            frames=nested,analysis={'trajectory':{'frames':nested}},snapshot_status='current'))
        self.assertIn('important-output-location',p.result_details.toPlainText())

    def test_trajectory_timer_uses_last_checked_status_and_explicit_check(self):
        p=self.panel
        record=dict(operation='trajectory',snapshot_status='current',frames=[])
        p._current_record=record; p._populate_result(record); p.tabs.setCurrentIndex(3)
        with patch.object(p.controller,'result_status',return_value='current') as check:
            p.refresh(); self.assertEqual(check.call_count,0)
            p._check_result_source(); self.assertEqual(check.call_count,1)
        self.assertIn('Last checked source',p.result_source.text())

    def test_narrow_paths_browsers_and_job_locking(self):
        p=self.panel; Q=self.qt.QtWidgets
        long='F:/'+('测量 long directory/'*30)+'source.cif'
        for entry in (p.assembly.source,p.assembly.output,p.trajectory.path,p.trajectory.output): entry.setText(long)
        for width in (360,420,540,800):
            p.resize(width,900); p.show(); self.app.processEvents()
            for index,name in ((0,'assembly_section'),(2,'trajectory_section')):
                p.page_selector.setCurrentIndex(index); p.findChild(Q.QToolButton,name).setChecked(True); self.app.processEvents()
                scroll=p.tabs.currentWidget()
                self.assertLessEqual(scroll.widget().minimumSizeHint().width(),360)
                self.assertEqual(scroll.horizontalScrollBar().maximum(),0)
            self.assertLessEqual(p.minimumSizeHint().width(),360)
        with patch.object(Q.QFileDialog,'getOpenFileName',return_value=('','')):
            p.findChild(Q.QPushButton,'browse_assembly_source').click()
        self.assertEqual(p.assembly.source.text(),long)
        p._job_started(dict(operation='trajectory',output='result',parameters={'request':{'reference_state':7}}))
        self.assertEqual(p._job_state,7)
        self.assertEqual(p.tabs.count(),5); self.assertEqual(p.tabs.currentIndex(),3)
        self.assertTrue(all(not p.tabs.isTabEnabled(i) for i in (0,1,2,4)))
        self.assertTrue(p.cancel_job.isEnabled()); p._job_idle()
        self.assertTrue(all(p.tabs.isTabEnabled(i) for i in range(5)))

@unittest.skipUnless(HAS_GUI,'requires PyMOL and Qt')
class MolecularGuiTests(unittest.TestCase):
    def test_isolated_molecular_controls(self):
        with tempfile.TemporaryDirectory(prefix='molecular-controls-') as directory:
            path=Path(directory)/'result.json'
            child=subprocess.run([sys.executable,'-B','-m','tests.test_molecular_gui','--controls-child',str(path)],cwd=ROOT,
                env=dict(os.environ,PYTHONPATH=str(ROOT),QT_QPA_PLATFORM='offscreen'),capture_output=True,text=True,timeout=180)
            print(child.stdout+child.stderr,flush=True)
            self.assertEqual(child.returncode,0,child.stdout+child.stderr)
            data=json.loads(path.read_text()); self.assertEqual(data['tests_run'],CONTROL_CASE_COUNT)
            self.assertEqual(data['skips'],0)

@unittest.skipUnless(os.environ.get('MOLSCENE_REQUIRE_ADVANCED')=='1' or os.environ.get('MOLSCENE_REQUIRE_MOLECULAR')=='1','requires allocated native desktop focus')
class NativeMolecularTests(unittest.TestCase):
    def test_native_assemblies_trajectory_and_close(self):
        self.assertTrue(HAS_GUI,'Required native molecular gate needs actual backend/Qt')
        artifacts=os.environ.get('MOLSCENE_TEST_ARTIFACTS')
        if artifacts: Path(artifacts).mkdir(parents=True,exist_ok=True)
        root=Path(tempfile.mkdtemp(prefix='molecular-',dir=artifacts))
        if not artifacts: self.addCleanup(__import__('shutil').rmtree,root)
        print('Molecular native evidence: '+str(root),flush=True)
        env=dict(os.environ,PYTHONPATH=str(ROOT)); env.pop('QT_QPA_PLATFORM',None)
        from tests.owned_processes import run_native_child,verify_native_report
        child,out,err,pid=run_native_child([sys.executable,'-B','-m','tests.molecular_native',str(root)],root,timeout=180,cwd=ROOT,env=env)
        (root/'process.log').write_text(out+err,encoding='utf-8')
        self.assertEqual(child.returncode,0,out+err)
        self.assertTrue((root/'molecular-native.json').is_file(),out+err)
        report=json.loads((root/'molecular-native.json').read_text())
        for key in ('closed','gl_valid','worker_responsive','assembly_loaded','trajectory_verified','original_sources_preserved','independent_reload','job_cancelled','cooperative_close'):
            self.assertTrue(report[key],key)
        verify_native_report(root,'molecular-native.json',pid,child.returncode)

if '--controls-child' not in sys.argv:
    CONTROL_CASE_COUNT = len(unittest.defaultTestLoader.getTestCaseNames(ControlCases))
    del ControlCases

if __name__=='__main__':
    if '--controls-child' in sys.argv:
        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ControlCases))
        Path(sys.argv[-1]).write_text(json.dumps(dict(tests_run=result.testsRun,skips=len(result.skipped))))
        raise SystemExit(not result.wasSuccessful() or bool(result.skipped))
    # Internal real-Qt cases execute only in their isolated QApplication child.
    unittest.main()
