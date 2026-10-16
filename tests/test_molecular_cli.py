"""Backend-free molecular command boundaries and original input provenance."""
import contextlib
import io
import json
import importlib.util
from pathlib import Path
import tempfile
import unittest
from molscene.cli import main

DATA = Path(__file__).resolve().parents[1] / 'examples/data'

class MolecularCLITests(unittest.TestCase):
    def test_help_and_catalog_are_backend_free(self):
        for command in ('assemblies', 'assembly', 'trajectory'):
            with self.subTest(command=command), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([command, '--help']), 0)
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            self.assertEqual(main(['assemblies', str(DATA/'assembly-original.cif')]), 0)
        self.assertIn('assemblies', json.loads(stream.getvalue()))

    def test_invalid_trajectory_request_precedes_backend(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['trajectory', str(DATA/'assembly-original.cif'), '--stride', '0', '--output', 'unused']), 1)

@unittest.skipUnless(importlib.util.find_spec('pymol2'), 'requires PyMOL')
class NativeMolecularCLITests(unittest.TestCase):
    def test_coordinate_default_object_original_provenance_and_actual_pse_object(self):
        import pymol2
        from molscene.artifacts import sha256
        with tempfile.TemporaryDirectory(prefix='molecular-cli-') as td:
            root=Path(td); coordinate=root/'ensemble.pdb'; session=root/'input.pse'
            with pymol2.PyMOL() as pm:
                for i,p in enumerate([[0,0,0],[2,0,0],[0,3,0]],1):
                    pm.cmd.pseudoatom('actual',name='CA',resn='GLY',resi=str(i),pos=p)
                pm.cmd.load_coordset([[1,0,0],[3,0,0],[1,3,0]],'actual',state=2)
                pm.cmd.save(str(coordinate),'actual',state=0); pm.cmd.save(str(session))
            for source,extra in [(coordinate,[]),(session,['--object','actual'])]:
                output=root/('out-'+source.suffix[1:]); stream=io.StringIO()
                with contextlib.redirect_stdout(stream):
                    code=main(['trajectory',str(source),*extra,'--output',str(output)])
                self.assertEqual(code,0,stream.getvalue()); report=json.loads(stream.getvalue())
                self.assertEqual(report['parameters']['object_name'],'trajectory_source' if source==coordinate else 'actual')
                self.assertEqual(report['frame_count'],2)
                if source==coordinate:
                    self.assertEqual(report['inputs']['original']['sha256'],sha256(coordinate))
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['trajectory',str(session),'--output',str(root/'wrong-object')]),1)
            self.assertFalse((root/'wrong-object').exists())

    def test_assembly_cli_and_output_input_preflight(self):
        with tempfile.TemporaryDirectory(prefix='molecular-cli-') as td:
            output=Path(td)/'assembly'; stream=io.StringIO()
            with contextlib.redirect_stdout(stream):
                code=main(['assembly',str(DATA/'assembly-original.cif'),'--assembly-id','cart','--output',str(output)])
            self.assertEqual(code,0,stream.getvalue()); self.assertEqual(json.loads(stream.getvalue())['copy_count'],2)
            from unittest.mock import patch
            with patch('molscene.engine._runtime',side_effect=AssertionError('backend reached')),contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['trajectory',str(DATA/'assembly-original.cif'),'--output',str(DATA)]),1)
