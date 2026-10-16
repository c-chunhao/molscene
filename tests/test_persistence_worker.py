"""Real cooperative worker/controller and CLI persistence boundaries."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(importlib.util.find_spec('pymol2'), 'requires PyMOL')
class PersistenceWorkerTests(unittest.TestCase):
    def setUp(self):
        from pymol2 import PyMOL
        from molscene.desktop.controller import WorkbenchController
        self.pm=PyMOL(); self.pm.start(); self.addCleanup(self.pm.stop)
        tmp=tempfile.TemporaryDirectory(prefix='persistence-worker-'); self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name).resolve(); self.cmd=self.pm.cmd
        self.cmd.pseudoatom('source',name='C',resn='GLY',pos=[1,2,3],state=1)
        self.cmd.pseudoatom('source',name='C',resn='GLY',pos=[4,5,6],state=2)
        self.cmd.select('chosen','source')
        self.controller=WorkbenchController(self.cmd,self.root)

    def run_worker(self,path):
        root=Path(__file__).resolve().parents[1]
        return subprocess.run([sys.executable,'-B','-m','molscene.worker',str(path)],cwd=path.parent,
            env=dict(os.environ,PYTHONPATH=str(root)),capture_output=True,text=True,timeout=60)

    def test_frozen_selection_state_saved_status_and_no_automatic_load(self):
        output=self.root/'out'
        self.cmd.set('session_file',str(self.root/'user.pse'))
        path=self.controller.prepare_job('coordinates',dict(request=dict(selection='chosen',state=2)),output=output)
        job=json.loads(path.read_text()); original=Path(job['input_session']).read_bytes()
        self.assertEqual(self.cmd.get('session_file'),str(self.root/'user.pse'))
        self.assertEqual(job['gui_context']['selection']['state'],2)
        self.cmd.select('chosen','none'); self.cmd.translate([99,0,0],'source',state=2)
        before=self.cmd.get_names('all'); view=self.cmd.get_view()
        result=self.run_worker(path)
        self.assertEqual(result.returncode,0,result.stderr)
        envelope=json.loads(Path(job['result_path']).read_text())
        self.assertTrue(envelope['passed'])
        record=self.controller.load_advanced_result(output/'report.json')
        self.assertEqual(record['snapshot_status'],'saved')
        self.assertEqual(record['result_kind'],'saved_snapshot')
        self.assertEqual(self.controller.result_status(record),'saved')
        self.assertEqual(self.cmd.get_names('all'),before)
        self.assertEqual(self.cmd.get_view(),view)
        self.assertEqual(Path(job['input_session']).read_bytes(),original)
        with self.assertRaisesRegex(ValueError,'(?i)snapshot'):
            self.controller.annotate_result(record)

    def test_snapshot_and_request_binding_failure_envelopes(self):
        for mutation in ('snapshot','request','operation','advanced_operation'):
            with self.subTest(mutation=mutation):
                path=self.controller.prepare_job('coordinates',dict(request=dict(state=2)),output=self.root/mutation)
                job=json.loads(path.read_text())
                if mutation=='snapshot':
                    with Path(job['input_session']).open('ab') as handle: handle.write(b'changed')
                elif mutation=='request': job['parameters']['request']['state']=1
                elif mutation=='operation': job['operation']='session_save'; job['parameters']['request']={}
                else:
                    job['operation']='density'
                    job['parameters']['request']=dict(map_path=str(self.root/'missing.dx'),format='dx')
                path.write_text(json.dumps(job))
                result=self.run_worker(path)
                self.assertEqual(result.returncode,1,result.stderr)
                envelope=json.loads(Path(job['result_path']).read_text())
                self.assertFalse(envelope['passed'])
                if mutation!='snapshot':
                    self.assertIn('operation/request',envelope['error'])
                self.assertFalse((self.root/mutation).exists())

    def test_pre_cancel_and_session_save_worker(self):
        output=self.root/'cancel'
        path=self.controller.prepare_job('session_save',dict(request={}),output=output)
        job=json.loads(path.read_text())
        Path(job['parameters']['cancel_file']).write_text('cancel')
        result=self.run_worker(path)
        self.assertEqual(result.returncode,1,result.stderr)
        envelope=json.loads(Path(job['result_path']).read_text())
        self.assertTrue(envelope['result']['cancelled']); self.assertFalse(output.exists())
        output=self.root/'saved'
        path=self.controller.prepare_job('session_save',dict(request={}),output=output)
        job=json.loads(path.read_text()); result=self.run_worker(path)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((output/'session.pse').read_bytes(),Path(job['input_session']).read_bytes())
        self.assertEqual(self.controller.load_advanced_result(output/'report.json')['snapshot_status'],'saved')

    def test_cli_coordinate_input_and_session_save(self):
        from molscene.cli import main
        source=self.root/'input.cif'; self.cmd.save(str(source),'source',state=2)
        session=self.root/'input.pse'; self.cmd.save(str(session))
        for argv in (['coordinates',str(source),'--selection','all','--state','1','--output',str(self.root/'coords')],
                     ['session-save',str(session),'--output',str(self.root/'session')]):
            stdout=io.StringIO()
            with contextlib.redirect_stdout(stdout): code=main(argv)
            self.assertEqual(code,0,stdout.getvalue())
            report=json.loads(stdout.getvalue())
            self.assertEqual(report['snapshot_status'],'saved')
            if report['operation']=='coordinates':
                self.assertEqual(report['inputs']['source']['sha256'],__import__('hashlib').sha256(source.read_bytes()).hexdigest())


if __name__ == '__main__':
    unittest.main()
