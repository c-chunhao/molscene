"""Independent identity/geometry oracles for selected coordinate and PSE saves."""
import csv
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


class PersistenceReportBoundsTests(unittest.TestCase):
    def test_supported_200000_atom_frozen_report_is_readable_with_existing_bound(self):
        from molscene.advanced import read_json, validate_bundle
        from molscene.artifacts import sha256, write_json
        from molscene.bundles import Bundle
        from molscene.desktop.controller import WorkbenchController
        # Exercise real controller serialization with a synthetic maximum-size
        # membership. Native membership/snapshot fidelity has a separate test.
        members=[dict(object='protein_chain_A',index=index) for index in range(1,200001)]
        context=dict(schema_version=1,session_epoch=0,members={},maps={},files={},timeline=None,
            display_snapshot=None,selection=dict(state=2,members=members),verified=True)
        with tempfile.TemporaryDirectory(prefix='persistence-report-bound-') as directory:
            root=Path(directory).resolve(); output=root/'large'
            controller=WorkbenchController(None,root)
            with patch('molscene.advanced_snapshot.capture_identity',return_value=context), \
                    patch.object(controller,'_save_snapshot',side_effect=lambda path: path.write_bytes(b'captured PSE fixture')):
                job_path=controller.prepare_job('coordinates',dict(request=dict(selection='chosen',state=2)),output=output)
            job=json.loads(job_path.read_text(encoding='utf-8'))
            request=job['parameters']['request']
            self.assertEqual(job['gui_context']['parameters'],request)
            names=('objects/0001.cif','source.pse','membership.csv','replay.pml','report.json','manifest.json')
            with Bundle(output,protected=(),inventory=names,new_directory_only=True) as bundle:
                (bundle.stage/'objects').mkdir()
                for name in names[:-2]: (bundle.stage/name).write_text('synthetic payload\n',encoding='utf-8')
                inputs=dict(session=dict(path='source.pse',sha256=sha256(bundle.stage/'source.pse'),bytes=18))
                report=dict(schema_version=2,tool='molscene',tool_version='test',pymol_version='test',
                    created_at='2026-10-16T00:00:00+00:00',operation='coordinates',passed=True,
                    result_kind='saved_snapshot',snapshot_status='saved',output_path=str(output),
                    snapshot_id=inputs['session']['sha256'],inputs=inputs,parameters=request,
                    object_files=[dict(number=1,path='objects/0001.cif',source_object='protein_chain_A',
                        source_state=2,atom_count=200000,source_indices=list(range(1,200001)),
                        source_object_matrix=[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1])],
                    reload_verification=dict(passed=True,atom_count=200000,core_identity=['element','name','resn','resi','chain','segi','alt'],
                        max_euclidean_error_angstrom=0.0,method='synthetic report-size fixture',
                        files=[dict(path='objects/0001.cif',atom_count=200000,passed=True,reload_state=1,
                            core_identity=['element','name','resn','resi','chain','segi','alt'],max_euclidean_error_angstrom=0.0)]),
                    artifacts=dict(report='report.json',manifest='manifest.json',replay='replay.pml',session='source.pse',membership='membership.csv'),
                    limitations=['Synthetic payloads establish report serialization bounds only.'],
                    replay_contract='Relative files and public PyMOL calls; no MolScene dependency',
                    artifact_hashes={name:sha256(bundle.stage/name) for name in names[:-2]})
                write_json(bundle.stage/'report.json',report)
                bundle.publish()
            report_bytes=(output/'report.json').stat().st_size
            print('200,000-atom full-shaped report bytes:',report_bytes)
            loaded=validate_bundle(output/'report.json')
            self.assertEqual(loaded['parameters'],request)
            self.assertEqual(loaded['object_files'][0]['source_indices'],list(range(1,200001)))
            self.assertLessEqual((output/'report.json').stat().st_size,8*1024*1024)
            oversized=root/'oversized.json'; oversized.write_bytes(b' '*(8*1024*1024+1))
            with self.assertRaisesRegex(ValueError,'bounded'):
                read_json(oversized)


class PersistenceValidationTests(unittest.TestCase):
    def test_service_exists_and_validation_stays_lazy(self):
        from molscene import persistence
        self.assertEqual(persistence.validate_persistence_request('coordinates', {}),
                         dict(selection='all', state=1, format='cif'))
        self.assertEqual(persistence.validate_persistence_request('session_save', {}), {})
        for operation, request in [('oops', {}), ('coordinates', None), ('session_save', {'state': 1}),
                ('coordinates', {'state': True}), ('coordinates', {'state': 0}),
                ('coordinates', {'format': 'xyz'}), ('coordinates', {'selection': 'all\nquit'}),
                ('coordinates', {'unknown': 1})]:
            with self.subTest(request=request), self.assertRaises(ValueError):
                persistence.validate_persistence_request(operation, request)

    def test_plain_help_and_validation_without_site_packages(self):
        code = ('import sys; from molscene.persistence import validate_persistence_request; '
                'validate_persistence_request("coordinates", {}); '
                'assert "pymol" not in sys.modules and "pymol2" not in sys.modules; '
                'from molscene.cli import main; '
                'assert main(["coordinates", "--help"]) == 0; '
                'assert main(["session-save", "--help"]) == 0')
        result = subprocess.run([sys.executable, '-S', '-B', '-c', code], capture_output=True,
                                text=True, cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(result.returncode, 0, result.stderr)


@unittest.skipUnless(importlib.util.find_spec('pymol2'), 'requires PyMOL')
class PersistenceTests(unittest.TestCase):
    def setUp(self):
        from pymol2 import PyMOL
        self.pm = PyMOL()
        self.pm.start()
        self.addCleanup(self.pm.stop)
        self.cmd = self.pm.cmd
        temporary = tempfile.TemporaryDirectory(prefix='persistence-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def fixture(self, wide=False):
        from chempy import Atom, Bond
        from chempy.models import Indexed
        model = Indexed()
        # Identity duplicates across objects, insertion and alternate conformers
        # have fixed independently specified source and expected coordinates.
        for name, resi, chain, segi, alt, coord in [
                ('CA', '12A', '', 'SG1', 'A', [1.23456, 2.34567, 3.45678]),
                ('CA', '12A', '', 'SG1', 'B', [2.11111, 3.22222, 4.33333]),
                ('N', '-3', 'B', 'SG2', '', [-1.23456, -2.34567, -3.45678])]:
            atom = Atom()
            atom.name, atom.resi, atom.chain, atom.segi, atom.alt = name, resi, chain, segi, alt
            atom.resn, atom.symbol, atom.coord = 'GLY', 'N' if name == 'N' else 'C', coord
            atom.b, atom.q, atom.id = 12.3456, 0.87654, len(model.atom) + 501
            model.atom.append(atom)
        bond = Bond()
        bond.index, bond.order = [0, 2], 1
        model.bond.append(bond)
        for name in ('source_a', 'source_b'):
            self.cmd.load_model(model, name, state=1)
            native = self.cmd.get_model(name, state=1)
            self.cmd.create(name, name, source_state=1, target_state=2, zoom=0)
            self.cmd.load_coords([[a.coord[0], a.coord[1]+8, a.coord[2]] for a in native.atom], name, state=2)
        self.cmd.transform_object('source_a', [1,0,0,10,0,1,0,20,0,0,1,30,0,0,0,1],
                                  state=0, homogenous=1, transpose=0)
        if wide:
            self.cmd.alter('source_a', "name='LONGNAME';resn='LONGRES';chain='LONG';segi='LONGSEGI';resi='123456A'")
        self.cmd.frame(1)
        self.cmd.set('session_file', str(self.root/'user.pse'))
        self.cmd.scene('captured', 'store')
        path = self.root/'input.pse'
        old = self.cmd.get('session_file')
        self.cmd.save(str(path))
        self.cmd.set('session_file', old)
        return path

    def preserved(self):
        # Direct public calls, deliberately independent of product collectors.
        cmd = self.cmd
        return dict(view=cmd.get_view(), state=cmd.get_state(), session_file=cmd.get('session_file'),
            scenes=cmd.get_scene_list(), objects=cmd.get_names('objects'),
            models=[[(a.name, a.resi, a.segi, a.chain, a.alt, a.symbol, a.coord, a.b, a.q)
                     for a in cmd.get_model(name, state=state).atom]
                    for name in ('source_a', 'source_b') for state in (1, 2)],
            matrices=[cmd.get_object_matrix(name, state=state) for name in ('source_a', 'source_b') for state in (1,2)],
            bonds=[[(b.index,b.order) for b in cmd.get_model(name, state=1).bond] for name in ('source_a','source_b')])

    def test_transformed_state_two_duplicate_identity_and_independent_reload(self):
        from molscene.persistence import export_persistence
        from molscene.advanced import validate_bundle
        source = self.fixture()
        original, before = source.read_bytes(), self.preserved()
        native_members={(name,a.index):(a.id,(a.symbol,a.name,a.resn,a.resi,a.chain,a.segi,a.alt),a.coord)
            for name in ('source_a','source_b') for a in self.cmd.get_model(name,state=2).atom}
        expected = [('C','CA','GLY','12A','','SG1','A'), ('C','CA','GLY','12A','','SG1','B'),
                    ('N','N','GLY','-3','B','SG2','')]
        for fmt in ('cif', 'pdb'):
            output = self.root/fmt
            report = export_persistence(source, output, 'coordinates', dict(selection='all',state=2,format=fmt))
            self.assertEqual(report, validate_bundle(output/'report.json'))
            self.assertEqual(report['result_kind'], 'saved_snapshot')
            self.assertEqual(report['snapshot_status'], 'saved')
            self.assertEqual(report['output_path'], str(output))
            self.assertEqual([r['path'] for r in report['object_files']], [f'objects/0001.{fmt}', f'objects/0002.{fmt}'])
            self.assertEqual([r['source_object'] for r in report['object_files']], ['source_a','source_b'])
            with (output/'membership.csv').open(newline='',encoding='utf-8') as handle:
                members = list(csv.DictReader(handle))
            self.assertEqual(len(members), 6)
            self.assertEqual({(r['source_object'],int(r['source_index'])) for r in members},
                             {('source_a',1),('source_a',2),('source_a',3),('source_b',1),('source_b',2),('source_b',3)})
            self.assertEqual({r['source_state'] for r in members}, {'2'})
            for row in members:
                source_id,identity,coord=native_members[row['source_object'],int(row['source_index'])]
                self.assertEqual(int(row['source_id']),source_id)
                self.assertEqual(tuple(row[key] for key in ('element','name','resn','resi','chain','segi','alt')),identity)
                self.assertEqual([float(row[key]) for key in ('world_x','world_y','world_z')],coord)
            from pymol2 import PyMOL
            for number, name in enumerate(('source_a','source_b'), 1):
                with PyMOL() as reload:
                    reload.cmd.load(str(output/f'objects/{number:04d}.{fmt}'), 'loaded')
                    atoms = reload.cmd.get_model('all', state=1).atom
                    atoms = sorted(atoms,key=lambda a:(a.name,a.alt))
                    self.assertEqual([(a.symbol,a.name,a.resn,a.resi,a.chain,a.segi,a.alt) for a in atoms],expected)
                    delta = (10,28,30) if name=='source_a' else (0,8,0)
                    bases = [[1.23456,2.34567,3.45678],[2.11111,3.22222,4.33333],[-1.23456,-2.34567,-3.45678]]
                    for atom, base in zip(atoms,bases):
                        self.assertLessEqual(math.dist(atom.coord,[base[i]+delta[i] for i in range(3)]),0.001)
                selected = [r for r in members if r['source_object']==name]
                self.assertEqual({(r['element'],r['name'],r['resn'],r['resi'],r['chain'],r['segi'],r['alt']) for r in selected}, set(expected))
            self.assertEqual(report['reload_verification']['atom_count'],6)
            self.assertLessEqual(report['reload_verification']['max_euclidean_error_angstrom'],0.001)
            moved=self.root/(fmt+'-relocated'); output.rename(moved)
            with PyMOL() as replay:
                replay.cmd.do('@'+str(moved/'replay.pml'))
                self.assertEqual(set(replay.cmd.get_names('objects')),{'saved_0001','saved_0002'})
                self.assertEqual(replay.cmd.count_atoms('all'),6)
                self.assertEqual(replay.cmd.count_states('all'),1)
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(self.preserved(), before)

    def test_wide_cif_and_pdb_width_refusal_preserves_old_output(self):
        from molscene.persistence import export_persistence
        source = self.fixture(wide=True)
        report = export_persistence(source,self.root/'wide-cif','coordinates',dict(selection='source_a',state=2))
        self.assertEqual(report['reload_verification']['atom_count'],3)
        with self.assertRaisesRegex(ValueError, '(?i)pdb'):
            export_persistence(source,self.root/'rejected','coordinates',dict(format='pdb'))
        self.assertFalse((self.root/'rejected').exists())
        old = self.root/'old'; old.mkdir(); (old/'report.json').write_text('previous valid result')
        with self.assertRaises(ValueError):
            export_persistence(source,old,'session_save',{})
        self.assertEqual((old/'report.json').read_text(),'previous valid result')

    def test_pdb_rejects_each_text_numeric_and_coordinate_width(self):
        from molscene.persistence import export_persistence
        for field, value in [('name','ABCDE'),('resn','ABCD'),('chain','AB'),('segi','ABCDE'),
                             ('resi','10000'),('resi','-1000'),('name','Cα')]:
            self.cmd.reinitialize()
            self.cmd.pseudoatom('source_a',name='C',resn='GLY',pos=[0,0,0])
            self.cmd.alter('source_a',field+'='+repr(value))
            source=self.root/'width.pse'; self.cmd.save(str(source))
            with self.subTest(field=field,value=value), self.assertRaisesRegex(ValueError,'(?i)pdb'):
                export_persistence(source,self.root/'refused','coordinates',dict(format='pdb'))
            self.assertFalse((self.root/'refused').exists())
        self.cmd.alter('source_a',"name='C'")
        self.cmd.load_coords([[10000.,0,0]],'source_a')
        self.cmd.save(str(source))
        with self.assertRaisesRegex(ValueError,'(?i)pdb'):
            export_persistence(source,self.root/'refused','coordinates',dict(format='pdb'))

    def test_missing_requested_state_never_uses_singleton_fallback(self):
        from molscene.persistence import export_persistence
        self.cmd.pseudoatom('single',pos=[1,2,3])
        source=self.root/'single.pse'; self.cmd.save(str(source))
        with self.assertRaisesRegex(ValueError,'state 2'):
            export_persistence(source,self.root/'bad','coordinates',dict(state=2))
        self.assertFalse((self.root/'bad').exists())

    def test_full_session_byte_preservation_maps_scenes_labels_and_public_replay(self):
        from array import array
        from molscene.persistence import export_persistence
        from molscene.grids import Grid
        from molscene.native_maps import native_dx_text
        from pymol2 import PyMOL
        source = self.fixture()
        self.cmd.label('source_a', '"Saved label"')
        self.cmd.set('label_size',18,'source_a',state=2)
        self.cmd.load_raw(native_dx_text(Grid((2,2,2),(0,0,0),((1,0,0),(0,1,0),(0,0,1)),array('d',range(8)),'unknown',{})), 'dx','valid_map')
        self.cmd.scene('labelled', 'store')
        self.cmd.save(str(source))
        original=source.read_bytes()
        output=self.root/'saved'
        report=export_persistence(source,output,'session_save',{})
        self.assertEqual((output/'session.pse').read_bytes(),original)
        self.assertEqual(source.read_bytes(),original)
        # Move the bundle, execute its public replay; no MolScene calls involved.
        moved=self.root/'relocated'; output.rename(moved)
        with PyMOL() as reload:
            reload.cmd.do('@'+str(moved/'replay.pml'))
            self.assertEqual(reload.cmd.count_states('source_a'),2)
            self.assertEqual(reload.cmd.get_scene_list(),['captured','labelled'])
            self.assertEqual(reload.cmd.get_type('valid_map'),'object:map')
            labels=[]; reload.cmd.iterate('source_a','labels.append(label)',space=dict(labels=labels))
            self.assertEqual(labels,['Saved label']*3)
            self.assertEqual(reload.cmd.get_setting_float('label_size','source_a',state=2),18)
        self.assertEqual(report['result_kind'],'saved_snapshot')
        self.assertEqual(report['snapshot_status'],'saved')

    def test_relocated_replay_with_unicode_and_apostrophe_path(self):
        from molscene.persistence import export_persistence
        from pymol2 import PyMOL
        source=self.fixture(); output=self.root/'ordinary'
        export_persistence(source,output,'coordinates',dict(selection='source_a and alt A',state=2))
        moved=self.root/"已保存's coordinates"; output.rename(moved)
        with PyMOL() as replay:
            replay.cmd.do('@'+str(moved/'replay.pml'))
            atoms=replay.cmd.get_model('all',state=1).atom
            self.assertEqual(len(atoms),1)
            atom=atoms[0]
            self.assertEqual((atom.symbol,atom.name,atom.resn,atom.resi,atom.chain,atom.segi,atom.alt),
                             ('C','CA','GLY','12A','','SG1','A'))
            self.assertLessEqual(math.dist(atom.coord,[11.23456,30.34567,33.45678]),0.001)

    def test_grouped_frozen_membership_native_subset_parity(self):
        from molscene.desktop.controller import WorkbenchController
        from molscene.worker import run_job
        self.fixture()
        expression='(source_a and (alt A or name N)) or (source_b and alt B)'
        self.cmd.select('chosen_group',expression,state=2)
        expected=set(self.cmd.index('chosen_group'))
        self.assertEqual(len(expected),3)
        controller=WorkbenchController(self.cmd,self.root); output=self.root/'grouped'
        job_path=controller.prepare_job('coordinates',dict(request=dict(selection='chosen_group',state=2)),output=output)
        job=json.loads(job_path.read_text(encoding='utf-8'))
        frozen=job['parameters']['request']['selection']
        self.cmd.select('frozen_check',frozen,state=2)
        self.assertEqual(set(self.cmd.index('frozen_check')),expected)
        self.cmd.select('chosen_group','none'); self.cmd.delete('frozen_check')
        self.assertEqual(run_job(job_path),0)
        with (output/'membership.csv').open(newline='',encoding='utf-8') as handle:
            rows=list(csv.DictReader(handle))
        self.assertEqual({(row['source_object'],int(row['source_index'])) for row in rows},expected)
        self.assertEqual({row['source_state'] for row in rows},{'2'})
        loaded=controller.load_advanced_result(output/'report.json')
        self.assertEqual(loaded['parameters'],job['gui_context']['parameters'])
        self.assertEqual(loaded['snapshot_id'],job['gui_context']['snapshot_id'])

    def test_protected_collision_pre_cancel_and_cancel_between_object_files(self):
        from molscene.persistence import export_persistence
        from molscene.bundles import CancelledError
        source=self.fixture(); original=source.read_bytes()
        for output in (source,self.root,self.root/'empty'):
            if output.name=='empty':
                output.mkdir()
            with self.assertRaises(ValueError):
                export_persistence(source,output,'session_save',{},protected=(output,))
        pre=self.root/'pre'
        with self.assertRaises(CancelledError):
            export_persistence(source,pre,'session_save',{},cancelled=lambda:True)
        self.assertFalse(pre.exists())
        flag=[False]
        def progress(row):
            if row.get('stage')=='coordinates' and row.get('completed')==1:
                flag[0]=True
        output=self.root/'between'
        with self.assertRaises(CancelledError):
            export_persistence(source,output,'coordinates',{},cancelled=lambda:flag[0],progress=progress)
        self.assertFalse(output.exists())
        self.assertEqual(source.read_bytes(),original)
        self.assertTrue(list(self.root.glob('.molscene-failure-*/failure.json')))
        self.assertFalse(list(self.root.glob('.molscene-stage-*')))

    def test_hardlink_alias_rejected(self):
        from molscene.persistence import export_persistence
        source=self.fixture(); alias=self.root/'alias.pse'
        try:
            os.link(source,alias)
        except OSError as exc:
            self.skipTest('filesystem does not support hardlink creation: '+str(exc))
        with self.assertRaises(ValueError):
            export_persistence(alias,self.root/'out','session_save',{})

    def test_linked_ancestor_rejected(self):
        from molscene.persistence import export_persistence
        source=self.fixture(); target=self.root/'real'; target.mkdir(); link=self.root/'link'
        try:
            link.symlink_to(target,target_is_directory=True)
        except OSError as exc:
            self.skipTest('filesystem does not support directory symlink creation: '+str(exc))
        with self.assertRaises(ValueError):
            export_persistence(source,link/'out','session_save',{})

    def test_strict_directory_publication_rejects_preexisting_and_raced_empty_directory(self):
        from molscene.persistence import export_persistence
        from molscene.bundles import Bundle
        source=self.fixture()
        output=self.root/'empty'; output.mkdir()
        with self.assertRaises(ValueError):
            export_persistence(source,output,'session_save',{})
        self.assertEqual(list(output.iterdir()),[])
        raced=self.root/'raced'
        def progress(row):
            if row['stage']=='publish': raced.mkdir()
        with self.assertRaises(ValueError):
            export_persistence(source,raced,'session_save',{},progress=progress)
        self.assertEqual(list(raced.iterdir()),[])
        # Established Bundle callers still accept an empty directory.
        with Bundle(output,protected=(source,),inventory=('report.json','manifest.json')) as bundle:
            (bundle.stage/'report.json').write_text('{}')
            bundle.publish()
        self.assertTrue((output/'manifest.json').is_file())

    def test_object_names_are_metadata_and_never_output_paths(self):
        from molscene.persistence import export_persistence
        self.cmd.pseudoatom('odd_name',name='C',resn='GLY',pos=[1,2,3])
        self.cmd.set_name('odd_name','data_report.json')
        source=self.root/'odd.pse'; self.cmd.save(str(source))
        output=self.root/'numbered'
        report=export_persistence(source,output,'coordinates',{})
        self.assertEqual(report['object_files'][0]['source_object'],'data_report.json')
        self.assertEqual(report['object_files'][0]['path'],'objects/0001.cif')
        self.assertEqual([path.name for path in (output/'objects').iterdir()],['0001.cif'])

    def test_real_atom_and_object_caps_refuse_before_output(self):
        from chempy import Atom
        from chempy.models import Indexed
        from molscene.persistence import export_persistence
        atom=Atom(); atom.name='C'; atom.symbol='C'; atom.resn='GLY'; atom.resi='1'; atom.coord=[0,0,0]
        model=Indexed(); model.atom=[atom]*200_001
        self.cmd.load_model(model,'large')
        self.assertEqual(self.cmd.count_atoms('all'),200_001)
        source=self.root/'large.pse'; self.cmd.save(str(source))
        with self.assertRaisesRegex(ValueError,'200,000'):
            export_persistence(source,self.root/'too-many-atoms','coordinates',{})
        self.assertFalse((self.root/'too-many-atoms').exists())
        self.cmd.delete('all')
        for number in range(1001):
            self.cmd.pseudoatom('source_'+str(number),name='C',resn='GLY',pos=[0,0,0])
        self.cmd.save(str(source))
        with self.assertRaisesRegex(ValueError,'1,000'):
            export_persistence(source,self.root/'too-many-objects','coordinates',{})
        self.assertFalse((self.root/'too-many-objects').exists())

    def test_changed_original_provenance_fails_before_publication(self):
        from molscene.persistence import export_persistence
        source=self.fixture(); original=self.root/'original.cif'; original.write_text('changed source')
        with self.assertRaisesRegex(ValueError,'changed'):
            export_persistence(source,self.root/'changed','coordinates',{},
                source_input=dict(path=str(original),sha256='a'*64))
        self.assertFalse((self.root/'changed').exists())


if __name__ == '__main__':
    unittest.main()
