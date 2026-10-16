"""Closed assembly exports, independent coordinate reload and relocated replay."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

try:
    from molscene.assembly_export import export_assembly
except ImportError:
    export_assembly=None
from molscene.artifacts import sha256
from molscene.bundles import CancelledError

DATA=Path(__file__).resolve().parents[1]/'examples/data'
NATIVE=importlib.util.find_spec('pymol2') is not None


class AssemblyExportValidationTests(unittest.TestCase):
    def test_service_exists_and_session_is_rejected_before_backend(self):
        self.assertIsNotNone(export_assembly,'assembly export service is required')
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                export_assembly('source.pse',Path(td)/'out',{'source_path':str(DATA/'assembly-original.cif')})

    def test_source_change_between_catalog_and_copy_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/'source.cif'; source.write_bytes((DATA/'assembly-original.cif').read_bytes())
            out=Path(td)/'out'
            def progress(row):
                if row['stage']=='validate': source.write_text(source.read_text().replace('1 2 3 1 10','8 2 3 1 10'))
            with self.assertRaisesRegex(ValueError,'changed'):
                export_assembly(None,out,{'source_path':str(source)},progress=progress)
            self.assertFalse(out.exists())


@unittest.skipUnless(NATIVE,'PyMOL backend unavailable')
class NativeAssemblyExportTests(unittest.TestCase):
    def test_bundle_manifest_reload_and_relocated_standalone_replay(self):
        from molscene.analysis import collect_atoms
        import pymol2
        source=DATA/'assembly-original.cif'; digest=sha256(source)
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)/'bundle'
            report=export_assembly(None,out,dict(source_path=str(source),assembly_id='cart'))
            self.assertEqual(report['operation'],'assembly')
            self.assertEqual(report['parameters']['state'],1)
            self.assertEqual(report['inputs']['source']['sha256'],digest)
            self.assertEqual(report['atom_count'],4)
            manifest=json.loads((out/'manifest.json').read_text())['files']
            self.assertEqual(set(manifest),{p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file()}-{'manifest.json'})
            for name,row in manifest.items(): self.assertEqual(sha256(out/name),row['sha256'])
            self.assertEqual(set(report['artifact_hashes']),set(manifest)-{'report.json'})
            moved=Path(td)/'relocated'; shutil.move(str(out),moved)
            with pymol2.PyMOL() as pm:
                for copy in report['copy_files']:
                    pm.cmd.load(str(moved/copy['path']),copy['object_name'],zoom=0)
                    atoms=sorted(collect_atoms(pm.cmd,copy['object_name']),key=lambda a:a['name'])
                    self.assertEqual([(a['segi'],a['chain']) for a in atoms],[('X','A'),('X','A')])
                    expected=[(-2,11,3),(-4,12,6)] if copy==report['copy_files'][0] else [(-2,1,3),(-4,2,6)]
                    for atom,coord in zip(atoms,expected):
                        for x,y in zip(atom['coord'],coord): self.assertLessEqual(abs(x-y),.001)
                pm.cmd.reinitialize()
                pm.cmd.do('@'+str(moved/'replay.pml'))
                self.assertEqual(pm.cmd.count_atoms('all'),4)
                self.assertEqual(set(pm.cmd.get_names('objects')),{r['object_name'] for r in report['copies']})
                pm.cmd.set('cif_use_auth',0); pm.cmd.set('assembly','1')
                pm.cmd.do('@'+str(moved/'replay.pml'))
                self.assertEqual(pm.cmd.count_atoms('all'),8)
                self.assertEqual(pm.cmd.get_setting_int('cif_use_auth'),0)
                self.assertEqual(pm.cmd.get_setting_text('assembly'),'1')
                self.assertEqual({a['chain'] for a in collect_atoms(pm.cmd,'all')},{'A'})
            self.assertEqual(sha256(source),digest)

    def test_real_3c70_bundle_reloads_all_world_geometry(self):
        source=DATA/'3c70.cif'; digest=sha256(source)
        with tempfile.TemporaryDirectory() as td:
            report=export_assembly(None,Path(td)/'real',{'source_path':str(source)})
            self.assertEqual(report['copy_count'],2)
            self.assertLessEqual(report['reload_validation']['max_coordinate_error_angstrom'],.001)
            self.assertEqual(report['reload_validation']['verified_atom_count'],report['atom_count'])
            self.assertEqual(sha256(source),digest)

    def test_collision_cancel_protection_publish_no_success(self):
        source=DATA/'assembly-original.cif'; digest=sha256(source)
        request={'source_path':str(source)}
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); (root/'exists').mkdir()
            for output,options in [(root/'exists',{}),(source,{}),(root/'guard',{'protected':(root/'guard',)})]:
                with self.subTest(output=output),self.assertRaises(ValueError): export_assembly(None,output,request,**options)
            with self.assertRaises(CancelledError): export_assembly(None,root/'cancel',request,cancelled=lambda:True)
            self.assertFalse((root/'cancel').exists())
            def progress(row):
                if row['stage']=='export_copies': cancel[0]=True
            cancel=[False]
            with self.assertRaises(CancelledError): export_assembly(None,root/'midcancel',request,cancelled=lambda:cancel[0],progress=progress)
            self.assertFalse((root/'midcancel').exists())
            self.assertEqual(sha256(source),digest)
