"""Original analytic deposited-assembly fixtures; expectations are hand-derived."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

try:
    from molscene import assembly
    from molscene.cif_tables import read_tables
except ImportError:
    assembly = read_tables = None

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'examples/data'
NATIVE = importlib.util.find_spec('pymol2') is not None


class AssemblyTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(assembly, 'assembly service is required')

    def test_request_defaults_and_strict_types(self):
        value = assembly.validate_assembly_request({'source_path': str(DATA/'assembly-original.cif')})
        self.assertEqual(value, dict(source_path=str((DATA/'assembly-original.cif').resolve()), assembly_id='1', state=1))
        for changes in ({'state': True}, {'state': 0}, {'state': 1.0}, {'assembly_id': 1},
                        {'assembly_id': ''}, {'unexpected': 2}, {'source_path': 'x\n.cif'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                assembly.validate_assembly_request(dict(source_path='x.cif', **changes) if 'source_path' not in changes else changes)

    def test_catalog_has_literal_composition_and_label_namespace(self):
        value = assembly.assembly_catalog(DATA/'assembly-original.cif')
        self.assertEqual(value['chain_namespace'], 'label_asym_id')
        self.assertEqual([r['assembly_id'] for r in value['assemblies']], ['1', 'cart'])
        cart = value['assemblies'][1]
        self.assertEqual(cart['copy_count'], 2)
        self.assertEqual(cart['generations'][0]['copies'][0]['operator_ids'], ['R', 'T'])
        self.assertEqual(cart['generations'][0]['copies'][0]['matrix'],
                         [0.,-1.,0.,0.,1.,0.,0.,10.,0.,0.,1.,0.,0.,0.,0.,1.])

    def test_pdb_null_chain_and_continuation(self):
        cat = assembly.assembly_catalog(DATA/'assembly-original.pdb')
        self.assertEqual(cat['assemblies'][0]['generations'][0]['asym_ids'], ['', 'A'])
        self.assertEqual(cat['assemblies'][0]['copy_count'], 2)

    def test_cif_lexing_and_rejection(self):
        self.assertEqual(read_tables("data_a\n_x.a 'loop_'\n_x.b\n;hello\nworld\n;\n# done\n")['_x'],
                         [{'a': 'loop_', 'b': 'hello\nworld'}])
        bad = ("data_a\nloop_\n_x.a\n_x.b\n1\n", "data_a\n_x.a 1\n_x.a 2",
               "data_a\n_x.a 'no", 'data_a\nsave_x\n', 'data_a\n_x.a 1\ndata_b\n_x.a 2')
        for text in bad:
            with self.subTest(text=text), self.assertRaises(ValueError): read_tables(text)

    def test_expressions_are_bounded_data(self):
        for expression in ('__import__(os)', '(1)(1-999999999)', '(1,)', '()', '(2-1)', '(1(2))', '1;quit'):
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                assembly.expand_operations(expression, {'1': [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]})
        self.assertEqual([x['operator_ids'] for x in assembly.expand_operations('(1-2)(1,2)',
            {str(n): [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1] for n in (1,2)})],
            [['1','1'], ['1','2'], ['2','1'], ['2','2']])

    def test_missing_duplicate_incomplete_metadata(self):
        text = (DATA/'assembly-original.cif').read_text()
        replacements = [('R 0 -1 0 0 1 0 0 0 0 0 1 0', 'R 2 -1 0 0 1 0 0 0 0 0 1 0'),
                        ('T 1 0 0 10 0 1 0 0 0 0 1 0', 'I 1 0 0 10 0 1 0 0 0 0 1 0'),
                        ('1 I X,Y', '1 missing X,Y'), ('1 I X,Y', '1 I Z'),
                        ('_pdbx_struct_oper_list.vector[3]', '_pdbx_struct_oper_list.absent')]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'bad.cif'
            for old,new in replacements:
                path.write_text(text.replace(old,new))
                with self.subTest(old=old), self.assertRaises(ValueError): assembly.assembly_catalog(path)
            path.write_text('data_no\n_entry.id no\n')
            with self.assertRaises(ValueError): assembly.assembly_catalog(path)

    def test_backend_free_import(self):
        result = subprocess.run([sys.executable,'-B','-S','-c',
            'import sys; import molscene.assembly, molscene.assembly_export; '
            'assert not any(x.startswith(("pymol","numpy","PySide","PyQt")) for x in sys.modules)'],
            cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_geometry_verifier_uses_euclidean_angstrom_error(self):
        atom=dict(index=1,segi='X',chain='A',resi='1',resn='GLY',name='CA',element='C',alt='',coord=[0,0,0])
        with self.assertRaises(ValueError):
            assembly._verify_atoms([dict(atom,coord=[.0007,.0007,.0007])],[atom])

    def test_parser_rejects_empty_later_loop_and_incomplete_pdb(self):
        with self.assertRaises(ValueError): read_tables('data_a\nloop_\n_x.a\n1\nloop_\n_y.a\n')
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'bad.pdb'
            text=(DATA/'assembly-original.pdb').read_text()
            for bad in (text.replace('REMARK 350   BIOMT3   2','REMARK 351   BIOMT3   2'),
                        text.replace('REMARK 350   BIOMT3   2','REMARK 350   BIOMT2   2')):
                path.write_text(bad)
                with self.assertRaises(ValueError): assembly.assembly_catalog(path)

    def test_source_byte_bound_precedes_parsing(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'large.cif'
            with path.open('wb') as handle: handle.truncate(20*1024*1024+1)
            with self.assertRaises(ValueError): assembly.assembly_catalog(path)


@unittest.skipUnless(NATIVE, 'PyMOL backend unavailable')
class NativeAssemblyTests(unittest.TestCase):
    def test_analytic_label_membership_transform_source_immutable(self):
        from molscene.artifacts import sha256
        from molscene.analysis import collect_atoms
        import pymol2
        source=DATA/'assembly-original.cif'; digest=sha256(source)
        with pymol2.PyMOL() as pm:
            cmd=pm.cmd
            cmd.pseudoatom('existing', pos=[7,8,9]); before=collect_atoms(cmd,'existing')
            cmd.set('cif_use_auth',0); cmd.set('assembly','1')
            view=cmd.get_view(); session=cmd.get_setting_text('session_file')
            setting=cmd.get_setting_text('assembly')
            result=assembly.build_assembly(cmd,source,assembly_id='cart')
            self.assertEqual(result['atom_count'],4)
            self.assertEqual(len(result['copies']),2)
            first=sorted(collect_atoms(cmd,result['copies'][0]['object_name']),key=lambda x:x['name'])
            self.assertEqual([(a['segi'],a['chain'],a['name']) for a in first], [('X','A','CA'),('X','A','N')])
            for actual,expected in zip(first, [(-2,11,3),(-4,12,6)]):
                for a,b in zip(actual['coord'],expected): self.assertAlmostEqual(a,b,places=4)
            self.assertEqual(collect_atoms(cmd,'existing'),before)
            self.assertEqual(cmd.get_setting_text('assembly'),setting)
            self.assertEqual(cmd.get_setting_int('cif_use_auth'),0)
            self.assertEqual(cmd.get_view(),view)
            self.assertEqual(cmd.get_setting_text('session_file'),session)
            self.assertEqual(sha256(source),digest)
            self.assertEqual(set(cmd.get_names('objects')), {'existing'} | {r['object_name'] for r in result['copies']})

    def test_native_pdb_null_and_invalid_state_rollback(self):
        import pymol2
        from molscene.analysis import collect_atoms
        with pymol2.PyMOL() as pm:
            result=assembly.build_assembly(pm.cmd,DATA/'assembly-original.pdb')
            self.assertEqual(result['atom_count'],4)
            second=sorted(collect_atoms(pm.cmd,result['copies'][1]['object_name']),key=lambda a:a['index'])
            self.assertEqual([a['chain'] for a in second],['','A'])
            for atom,expected in zip(second,[(11,-2,-3),(12,-4,-6)]):
                for actual,want in zip(atom['coord'],expected): self.assertAlmostEqual(actual,want,places=4)
            names=set(pm.cmd.get_names('all'))
            with self.assertRaises(ValueError): assembly.build_assembly(pm.cmd,DATA/'assembly-original.cif',state=2)
            self.assertEqual(set(pm.cmd.get_names('all')),names)

    def test_requested_multimodel_state_and_native_bonds_are_preserved(self):
        import pymol2
        from molscene.analysis import collect_atoms
        text=(DATA/'assembly-original.pdb').read_text()
        header='\n'.join(line for line in text.splitlines() if line.startswith(('HEADER','REMARK')))+'\n'
        rows=[line for line in text.splitlines() if line.startswith('ATOM')]
        # Two bonded atoms share chain A, with identical topology in two models.
        def atom_row(line,x,y,z): return line[:21]+'A'+line[22:30]+f'{x:8.3f}{y:8.3f}{z:8.3f}'+line[54:]
        first=[atom_row(rows[0],1,2,3),atom_row(rows[1],2.3,2,3)]
        second=[atom_row(rows[0],11,12,13),atom_row(rows[1],12.3,12,13)]
        header=header.replace('NULL,','A,').replace('REMARK 350                    AND CHAINS: A\n','')
        with tempfile.TemporaryDirectory() as td, pymol2.PyMOL() as pm:
            path=Path(td)/'models.pdb'
            path.write_text(header+'MODEL        1\n'+'\n'.join(first)+'\nENDMDL\nMODEL        2\n'+'\n'.join(second)+'\nENDMDL\nEND\n')
            result=assembly.build_assembly(pm.cmd,path,state=2)
            self.assertEqual(result['state'],2)
            copy=result['copies'][0]['object_name']
            self.assertEqual(len(pm.cmd.get_model(copy).bond),1)
            actual=sorted(collect_atoms(pm.cmd,copy),key=lambda a:a['name'])
            for atom,expected in zip(actual,[(11,12,13),(12.3,12,13)]):
                for x,y in zip(atom['coord'],expected): self.assertAlmostEqual(x,y,places=3)

    def test_atom_budget_and_midbuild_cancel_rollback_only_owned(self):
        import pymol2
        from molscene.bundles import CancelledError
        with tempfile.TemporaryDirectory() as td, pymol2.PyMOL() as pm:
            source=Path(td)/'large.cif'
            text=(DATA/'3c70.cif').read_text()
            # This source has 1,2 on its generation expression. Repeated
            # right-to-left products generate 64 copies, over 200000 atoms.
            import re
            text,count=re.subn(r'(?m)^(_pdbx_struct_assembly_gen.oper_expression\s+)1,2\s*$',
                r'\g<1>(1,2)(1,2)(1,2)(1,2)(1,2)(1,2)',text)
            self.assertEqual(count,1)
            source.write_text(text)
            pm.cmd.pseudoatom('retained'); names=set(pm.cmd.get_names('all'))
            with self.assertRaisesRegex(ValueError,'200000'): assembly.build_assembly(pm.cmd,source)
            self.assertEqual(set(pm.cmd.get_names('all')),names)
            cancel=[False]
            def progress(row):
                if row['stage']=='copy' and row['completed']==1: cancel[0]=True
            with self.assertRaises(CancelledError):
                assembly.build_assembly(pm.cmd,DATA/'assembly-original.cif',assembly_id='cart',
                    cancelled=lambda:cancel[0],progress=progress)
            self.assertEqual(set(pm.cmd.get_names('all')),names)

    def test_real_deposited_3c70_nonidentity(self):
        import pymol2
        from molscene.analysis import collect_atoms
        from molscene.artifacts import sha256
        source=DATA/'3c70.cif'
        self.assertEqual(sha256(source),'a3e5e2bc4997710ba916b6f8b6fd01acd85d9e6912fd063b2ac79b4d581fbb88')
        with pymol2.PyMOL() as pm:
            result=assembly.build_assembly(pm.cmd,source)
            self.assertEqual(len(result['copies']),2)
            self.assertEqual(result['source_atom_count'],5205)
            self.assertEqual(result['atom_count'],10410)
            self.assertEqual([r['atom_count'] for r in result['copies']],[5205,5205])
            self.assertEqual(result['copies'][1]['matrix'],[1.,0.,0.,0.,0.,-1.,0.,106.344,0.,0.,-1.,0.,0.,0.,0.,1.])
            first,second=[sorted(collect_atoms(pm.cmd,r['object_name']),key=lambda a:a['index']) for r in result['copies']]
            self.assertEqual(len(first),len(second))
            for actual,want in zip(first[0]['coord'],[27.832,25.704,5.521]): self.assertAlmostEqual(actual,want,places=3)
            for actual,want in zip(second[0]['coord'],[27.832,80.640,-5.521]): self.assertAlmostEqual(actual,want,places=3)
            for a,b in zip(first,second):
                for x,y in zip(b['coord'],[a['coord'][0],106.344-a['coord'][1],-a['coord'][2]]): self.assertAlmostEqual(x,y,places=3)
