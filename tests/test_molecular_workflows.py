"""Guarded molecular workers and explicit controller completion/loading."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from molscene.advanced import validate_request
from molscene.worker import run_job

DATA=Path(__file__).resolve().parents[1]/'examples/data'

class MolecularBoundaryTests(unittest.TestCase):
    def test_closed_nonobject_report_rejects_as_validation_error(self):
        from molscene.advanced import validate_bundle
        from molscene.bundles import Bundle
        from molscene.artifacts import write_json
        with tempfile.TemporaryDirectory() as td:
            output=Path(td)/'invalid'
            with Bundle(output,protected=(),inventory=('report.json','manifest.json'),new_directory_only=True) as bundle:
                write_json(bundle.stage/'report.json',[]); bundle.publish()
            with self.assertRaises(ValueError): validate_bundle(output/'report.json')

    def test_pure_request_registration(self):
        self.assertEqual(validate_request('assembly',dict(request=dict(source_path=str(DATA/'assembly-original.cif'))))['request']['state'],1)
        self.assertTrue(validate_request('trajectory',dict(request=dict(object_name='source')))['request']['align'])

    def test_raw_inputs_protected_even_with_invalid_request(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for operation,key in [('assembly','source_path'),('trajectory','trajectory_path')]:
                source=root/(key+'.cif'); source.write_bytes(b'original')
                job=root/'job.json'; job.write_text(json.dumps(dict(schema_version=1,operation=operation,
                    output=str(root/'out'),result_path=str(source),parameters=dict(request={key:str(source),'invalid':True}))))
                self.assertEqual(run_job(job),1)
                self.assertEqual(source.read_bytes(),b'original')

    def test_closed_molecular_reports_have_a_scoped_larger_json_cap(self):
        from molscene.advanced import read_json, validate_bundle
        from molscene.bundles import Bundle
        from molscene.artifacts import write_json
        with tempfile.TemporaryDirectory() as td:
            for operation in ('assembly','trajectory','density'):
                output=Path(td)/operation
                with Bundle(output,protected=(),inventory=('report.json','manifest.json'),new_directory_only=True) as bundle:
                    write_json(bundle.stage/'report.json',dict(schema_version=2,operation=operation,passed=True,
                        artifact_hashes={},padding='x'*(8*1024*1024)))
                    bundle.publish()
                with self.assertRaisesRegex(ValueError,'bounded'):
                    read_json(output/'report.json')
                if operation=='density':
                    with self.assertRaisesRegex(ValueError,'bounded'): validate_bundle(output/'report.json')
                else: self.assertEqual(validate_bundle(output/'report.json')['operation'],operation)

@unittest.skipUnless(importlib.util.find_spec('pymol2'),'requires PyMOL')
class MolecularWorkflowTests(unittest.TestCase):
    def setUp(self):
        import pymol2
        from molscene.desktop.controller import WorkbenchController
        self.pm=pymol2.PyMOL(); self.pm.start(); self.addCleanup(self.pm.stop); self.cmd=self.pm.cmd
        temporary=tempfile.TemporaryDirectory(prefix='molecular-workflow-'); self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve(); self.controller=WorkbenchController(self.cmd,self.root)
        for i,p in enumerate([[0,0,0],[2,0,0],[0,3,0],[0,0,4]],1):
            self.cmd.pseudoatom('source',name='C'+str(i),pos=p,resi=str(i))
        self.cmd.load_coordset([[1,0,0],[3,0,0],[1,3,0],[1,0,4]],'source',state=2)

    def worker(self,path):
        job=json.loads(path.read_text())
        result=subprocess.run([sys.executable,'-B','-m','molscene.worker',str(path)],cwd=path.parent,
            env=dict(os.environ,PYTHONPATH=str(DATA.parents[1])),capture_output=True,text=True,timeout=90)
        self.assertEqual(result.returncode,0,result.stderr)
        return job,json.loads(Path(job['result_path']).read_text())['result']

    def test_assembly_completion_then_explicit_unique_copies(self):
        source=self.root/'deposited.cif'; source.write_bytes((DATA/'assembly-original.cif').read_bytes())
        output=self.root/'assembly'
        path=self.controller.prepare_job('assembly',dict(request=dict(source_path=str(source),assembly_id='cart')),output=output)
        self.assertNotIn('input_session',json.loads(path.read_text()))
        job,report=self.worker(path); before=self.cmd.get_names('objects'); view=self.cmd.get_view()
        source_coords=[self.cmd.get_coordset('source',state=i).tolist() for i in (1,2)]
        record=self.controller.complete_advanced_result(output/'report.json')
        self.assertEqual(self.cmd.get_names('objects'),before); self.assertEqual(record['snapshot_status'],'current')
        Path(job['original_inputs'][0]['copy']).write_bytes(b'irrelevant private copy change after export')
        self.assertEqual(self.controller.result_status(record),'current')
        self.cmd.set('session_file','user.pse')
        first=self.controller.load_assembly_result(output/'report.json'); second=self.controller.load_assembly_result(output/'report.json')
        self.assertTrue(set(first).isdisjoint(second)); self.assertEqual(self.cmd.get_view(),view)
        self.assertEqual(self.cmd.get('session_file'),'user.pse'); self.assertEqual(len(self.controller.results),0)
        self.assertEqual([self.cmd.get_coordset('source',state=i).tolist() for i in (1,2)],source_coords)
        self.assertEqual(self.controller.result_status(record),'current')
        self.controller._session_epoch+=1; self.assertEqual(self.controller.result_status(record),'stale')
        self.controller._session_epoch-=1
        source.write_bytes(b'changed'); self.assertEqual(self.controller.result_status(record),'stale')

    def test_trajectory_compact_frozen_members_and_strict_status(self):
        self.cmd.select('chosen','source'); self.cmd.set('session_file','user.pse')
        output=self.root/'trajectory'
        path=self.controller.prepare_job('trajectory',dict(request=dict(object_name='source',selection='chosen',fit_selection='chosen')),output=output)
        job=json.loads(path.read_text()); self.assertLess(path.stat().st_size,12000)
        self.cmd.select('chosen','none'); self.assertEqual(self.cmd.get('session_file'),'user.pse')
        self.worker(path); record=self.controller.complete_advanced_result(output/'report.json')
        self.assertEqual(record['snapshot_status'],'current')
        self.cmd.pseudoatom('unrelated',pos=[20,20,20]); self.cmd.turn('y',25)
        self.cmd.load_coordset([[9,0,0],[11,0,0],[9,3,0],[9,0,4]],'source',state=3)
        self.assertEqual(self.controller.result_status(record),'current')
        self.cmd.translate([3,0,0],'source',state=2); self.assertEqual(self.controller.result_status(record),'stale')

    def test_worker_rejects_operation_request_and_original_mutations(self):
        for mutation in ('request','copy','original','roles'):
            with self.subTest(mutation=mutation):
                source=self.root/(mutation+'.cif'); source.write_bytes((DATA/'assembly-original.cif').read_bytes())
                path=self.controller.prepare_job('assembly',dict(request=dict(source_path=str(source))),output=self.root/('out-'+mutation))
                job=json.loads(path.read_text())
                if mutation=='request': job['parameters']['request']['assembly_id']='cart'
                elif mutation=='copy': Path(job['original_inputs'][0]['copy']).write_bytes(b'changed')
                elif mutation=='original': source.write_bytes(b'changed')
                else: job['original_inputs']=[]
                path.write_text(json.dumps(job)); self.assertEqual(run_job(path),1)
                self.assertFalse((self.root/('out-'+mutation)).exists())

    def test_context_evidence_cannot_omit_a_required_state(self):
        from molscene.molecular_snapshot import result_status
        self.cmd.load_coordset([[2,0,0],[4,0,0],[2,3,0],[2,0,4]],'source',state=3)
        output=self.root/'three'
        path=self.controller.prepare_job('trajectory',dict(request=dict(object_name='source',selection='all',fit_selection='all')),output=output)
        job=json.loads(path.read_text()); context=job['gui_context']
        self.controller._advanced_contexts[str(output/'report.json')]=context
        self.assertEqual(result_status(self.cmd,context,session_epoch=0),'current')
        del context['trajectory']['states']['2']
        self.assertEqual(result_status(self.cmd,context,session_epoch=0),'unverified')

    def test_record_status_rejects_retargeted_public_parameters(self):
        output=self.root/'status-binding'
        path=self.controller.prepare_job('trajectory',dict(request=dict(object_name='source',selection='all',fit_selection='all')),output=output)
        self.worker(path); record=self.controller.complete_advanced_result(output/'report.json')
        self.assertEqual(self.controller.result_status(record),'current')
        record['parameters']['reference_state']=2
        self.assertEqual(self.controller.result_status(record),'unverified')

    def test_dcd_context_captures_only_original_topology_state_one(self):
        from tests.test_dcd import original_dcd
        self.cmd.delete_states('source','2')
        dcd=self.root/'input.dcd'; dcd.write_bytes(original_dcd())
        output=self.root/'dcd'
        request=dict(object_name='source',selection='all',fit_selection='all',align=False,trajectory_path=str(dcd),reference_state=5,start=2,stop=6,stride=2)
        path=self.controller.prepare_job('trajectory',dict(request=request),output=output)
        job=json.loads(path.read_text()); self.assertEqual(set(job['gui_context']['trajectory']['states']),{'1'})
        self.worker(path); record=self.controller.complete_advanced_result(output/'report.json')
        self.assertEqual(record['snapshot_status'],'current')
        self.assertEqual([r['state'] for r in record['frames']],[2,4,6])
        dcd.write_bytes(b'changed'); self.assertEqual(self.controller.result_status(record),'stale')

    def test_pre_cancel_publishes_no_bundle(self):
        for operation,request in [('assembly',dict(source_path=str(DATA/'assembly-original.cif'))),
                                  ('trajectory',dict(object_name='source',selection='all',fit_selection='all'))]:
            output=self.root/('cancel-'+operation)
            path=self.controller.prepare_job(operation,dict(request=request),output=output)
            job=json.loads(path.read_text()); Path(job['parameters']['cancel_file']).write_text('cancel')
            self.assertEqual(run_job(path),1); self.assertFalse(output.exists())
            self.assertTrue(json.loads(Path(job['result_path']).read_text())['result']['cancelled'])

    def test_explicit_load_rolls_back_only_owned_names(self):
        from unittest.mock import patch
        output=self.root/'rollback'
        path=self.controller.prepare_job('assembly',dict(request=dict(source_path=str(DATA/'assembly-original.cif'),assembly_id='cart')),output=output)
        self.worker(path); self.cmd.set('session_file','user.pse'); before=self.cmd.get_names('objects'); view=self.cmd.get_view()
        original=self.cmd.load_model; calls=[]
        def broken(*args,**kwargs):
            original(*args,**kwargs); calls.append(args[1])
            if len(calls)==2: raise ValueError('controlled load failure')
        with patch.object(self.cmd,'load_model',side_effect=broken),self.assertRaisesRegex(ValueError,'controlled'):
            self.controller.load_assembly_result(output/'report.json')
        self.assertEqual(self.cmd.get_names('objects'),before); self.assertEqual(self.cmd.get_view(),view)
        self.assertEqual(self.cmd.get('session_file'),'user.pse')

    def test_closed_but_retargeted_reports_reject_before_live_mutation(self):
        from molscene.artifacts import sha256
        output=self.root/'retargeted'
        path=self.controller.prepare_job('assembly',dict(request=dict(source_path=str(DATA/'assembly-original.cif'),assembly_id='cart')),output=output)
        self.worker(path); report_path=output/'report.json'; original=report_path.read_text()
        manifest_path=output/'manifest.json'; manifest=json.loads(manifest_path.read_text())
        before=self.cmd.get_names('objects')
        for mutation in ('request','role','payload','operation','geometry'):
            with self.subTest(mutation=mutation):
                report=json.loads(original)
                if mutation=='request': report['parameters']['assembly_id']='1'
                elif mutation=='role': report['inputs']['trajectory']=report['inputs'].pop('source')
                elif mutation=='payload': report['inputs']['source']['path']='../outside.cif'
                elif mutation=='operation': report['operation']='trajectory'
                else: report['copies'][0]['source_atoms'][0]['coord'][0]+=5
                report_path.write_text(json.dumps(report),encoding='utf-8')
                manifest['files']['report.json']=dict(sha256=sha256(report_path),bytes=report_path.stat().st_size)
                manifest_path.write_text(json.dumps(manifest),encoding='utf-8')
                with self.assertRaises(ValueError): self.controller.load_assembly_result(report_path)
                self.assertEqual(self.cmd.get_names('objects'),before)

    def test_copy_role_cannot_be_retargeted_to_a_source_cif(self):
        from unittest.mock import patch
        from molscene.artifacts import sha256
        output=self.root/'wrong-copy-role'
        path=self.controller.prepare_job('assembly',dict(request=dict(source_path=str(DATA/'assembly-original.cif'),assembly_id='cart')),output=output)
        self.worker(path); report_path=output/'report.json'; report=json.loads(report_path.read_text())
        source=report['inputs']['source']; report['copies'][0]['path']=source['path']
        report['copy_files'][0].update(source)
        report_path.write_text(json.dumps(report),encoding='utf-8')
        manifest_path=output/'manifest.json'; manifest=json.loads(manifest_path.read_text())
        manifest['files']['report.json']=dict(sha256=sha256(report_path),bytes=report_path.stat().st_size)
        manifest_path.write_text(json.dumps(manifest),encoding='utf-8')
        with patch('molscene.engine._runtime',side_effect=AssertionError('source CIF reached native copy reader')):
            with self.assertRaises(ValueError): self.controller.load_assembly_result(report_path)

    def test_preflight_output_and_invalid_envelope_are_disjoint_from_inputs(self):
        source=self.root/'protected.cif'; source.write_bytes((DATA/'assembly-original.cif').read_bytes()); original=source.read_bytes()
        for output in (source,self.root):
            with self.assertRaises(ValueError):
                self.controller.prepare_job('assembly',dict(request=dict(source_path=str(source))),output=output)
        path=self.controller.prepare_job('assembly',dict(request=dict(source_path=str(source))),output=self.root/'safe')
        job=json.loads(path.read_text()); copied=Path(job['original_inputs'][0]['copy']); copy_bytes=copied.read_bytes()
        for envelope in (source,copied,path):
            changed=dict(job,result_path=str(envelope)); path.write_text(json.dumps(changed))
            self.assertEqual(run_job(path),1); self.assertEqual(source.read_bytes(),original); self.assertEqual(copied.read_bytes(),copy_bytes)
        self.assertFalse((self.root/'safe').exists())
