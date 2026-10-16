from array import array
import copy
import hashlib
import importlib.util
import json
import os
import tempfile
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

from molscene.electrostatics import potential_factor, build_apbs_input, validate_potential_request, export_potential
from molscene.grids import Grid, write_dx, read_dx
from molscene.pqr import read_pqr
from molscene.bundles import CancelledError
from molscene.external_tools import ExternalToolError

HAS_PYMOL = importlib.util.find_spec('pymol2') is not None


def _config(architecture='x64',memory=4096):
    return {'schema_version':1,'tools':{'apbs':dict(argv=[sys.executable],architecture=architecture,
        path_dirs=[],runtime_files=[],timeout_seconds=60,memory_limit_mib=memory)}}


class ElectrostaticsTests(unittest.TestCase):
    def test_exact_si_unit_conversion_and_unknown(self):
        self.assertAlmostEqual(potential_factor('kBT/e', 'mV', 298.15), 1.380649e-23*298.15/1.602176634e-19*1000, places=13)
        self.assertAlmostEqual(potential_factor('V', 'kBT/e', 310), 1.602176634e-19/(1.380649e-23*310))
        self.assertEqual(potential_factor('mV', 'V', 298.15), .001)
        self.assertEqual(potential_factor('unknown', 'unknown', 298.15), 1)
        with self.assertRaises(ValueError):
            potential_factor('unknown', 'V', 298.15)

    def test_request_strict_defaults_independence_and_temperature(self):
        source = {'mode': 'prepared', 'pqr_path': 'ion.pqr'}
        result = validate_potential_request(source)
        self.assertEqual(result['solver']['grid'], [65, 65, 65])
        self.assertEqual(result['units'], 'kBT/e')
        result['solver']['grid'][0] = 33
        self.assertEqual(source, {'mode': 'prepared', 'pqr_path': 'ion.pqr'})
        bad = [dict(source, units='mV'), dict(source, state=True), dict(source, ramp_limit=float('nan')),
               dict(source, solver={'grid': [34, 65, 65]}), dict(source, solver={'oops': 1}),
               dict(source, preparation={'drop_water': 1}), dict(source, unknown=1),
               {'mode': 'prepare'}, {'mode': 'import', 'dx_path': 'x.dx', 'pqr_path': 'x.pqr', 'units': 'V', 'display_units': 'kBT/e'}]
        for request in bad:
            with self.subTest(request=request), self.assertRaises(ValueError):
                validate_potential_request(request)
        with self.assertRaises(ValueError):
            validate_potential_request(dict(source, selection='x'*(8*1024*1024)))
        with self.assertRaises(ValueError):
            validate_potential_request(dict(source, tools={'schema_version':True,'tools':{}}))

    def test_fixed_solver_input_and_coverage_padding(self):
        pqr = {'atoms': [dict(x=2., y=3., z=4., radius=3.)]}
        spec = validate_potential_request({'mode': 'prepared', 'pqr_path': 'x', 'solver': {'grid': [129]*3, 'fine_padding': 32., 'coarse_padding': 80., 'salt_molar': 0}})
        text = build_apbs_input(pqr, spec['solver'], spec['temperature'])
        self.assertIn('mol pqr prepared.pqr', text)
        self.assertIn('dime 129 129 129', text)
        self.assertIn('fgcent 2 3 4', text)
        self.assertNotIn('ion charge', text)
        with self.assertRaises(ValueError):
            build_apbs_input(pqr, dict(spec['solver'], fine_padding=2), 298.15)

    @unittest.skipUnless(HAS_PYMOL, 'PyMOL unavailable')
    def test_import_retains_original_and_converts_display_only(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            pqr = folder/'ion.pqr'
            pqr.write_text('ATOM 1 C ALA 1 0 0 0 1 1.7\n')
            dx = folder/'source.dx'
            write_dx(dx, Grid((3,3,3), (-10.,)*3, ((10.,0.,0.),(0.,10.,0.),(0.,0.,10.)), array('d', [1.]*27), 'V', {}))
            before = hashlib.sha256(dx.read_bytes()).hexdigest()
            report = export_potential(None, folder/'out', {'mode':'import', 'pqr_path':str(pqr), 'dx_path':str(dx), 'units':'V', 'display_units':'mV', 'ramp_limit':2000, 'render':{'width':160,'height':120}})
            self.assertTrue(report['passed'])
            self.assertEqual(hashlib.sha256(dx.read_bytes()).hexdigest(), before)
            self.assertEqual((folder/'out/inputs/potential.dx').read_bytes(), dx.read_bytes())
            self.assertEqual(read_dx(folder/'out/display.dx').values[0], 1000)
            manifest = json.loads((folder/'out/manifest.json').read_text())['files']
            self.assertNotIn('tools.json', manifest)
            self.assertEqual(json.loads((folder/'out/report.json').read_text()), report)
            for name, row in manifest.items():
                self.assertEqual(hashlib.sha256((folder/'out'/name).read_bytes()).hexdigest(), row['sha256'])
            import pymol2
            with pymol2.PyMOL() as replay:
                replay.cmd.do('@'+str(folder/'out/replay.pml'),echo=0)
                record=report['analysis']['display']
                self.assertEqual(replay.cmd.count_atoms(record['molecule']),1)
                self.assertAlmostEqual(replay.cmd.get_model(record['molecule']).atom[0].vdw,1.7,places=6)

    @unittest.skipUnless(HAS_PYMOL, 'PyMOL unavailable')
    def test_existing_molecule_import_replay_preserves_exact_state_coordinates_and_vdw(self):
        import pymol2
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder)
            snapshot=folder/'source.pse'
            dx=folder/'source.dx'
            write_dx(dx,Grid((3,3,3),(-10.,)*3,((10.,0.,0.),(0.,10.,0.),(0.,0.,10.)),array('d',[0.]*27),'unknown',{}))
            with pymol2.PyMOL() as pm:
                pm.cmd.pseudoatom('protein',pos=[1.1234567,2.2345678,3.3456789],vdw=2.7)
                expected=pm.cmd.get_model('protein').atom[0].coord
                pm.cmd.save(str(snapshot))
            report=export_potential(snapshot,folder/'out',dict(mode='import',dx_path=str(dx),selection='protein',render={'width':160,'height':120}))
            self.assertNotIn('display.dx',report['artifact_hashes'])
            with pymol2.PyMOL() as replay:
                replay.cmd.do('@'+str(folder/'out/replay.pml'),echo=0)
                atom=replay.cmd.get_model(report['analysis']['display']['molecule']).atom[0]
                self.assertEqual(atom.coord,expected)
                self.assertAlmostEqual(atom.vdw,2.7,places=6)

    @unittest.skipUnless(HAS_PYMOL, 'PyMOL unavailable')
    def test_cancelled_export_never_publishes_and_preserves_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder)
            pqr=folder/'source.pqr'
            pqr.write_text('ATOM 1 C ALA 1 0 0 0 1 1.7\n')
            dx=folder/'source.dx'
            write_dx(dx,Grid((3,3,3),(-10.,)*3,((10.,0.,0.),(0.,10.,0.),(0.,0.,10.)),array('d',[0.]*27),'unknown',{}))
            before=(pqr.read_bytes(),dx.read_bytes())
            state={'cancelled':False}
            def progress(event):
                if event['stage']=='display':
                    state['cancelled']=True
            with self.assertRaises(CancelledError) as caught:
                export_potential(None,folder/'out',dict(mode='import',pqr_path=str(pqr),dx_path=str(dx)),
                    cancelled=lambda:state['cancelled'],progress=progress)
            self.assertFalse((folder/'out').exists())
            self.assertEqual((pqr.read_bytes(),dx.read_bytes()),before)
            self.assertTrue(Path(caught.exception.failure_path).is_dir())

    @unittest.skipUnless(HAS_PYMOL, 'PyMOL unavailable')
    def test_missing_solver_output_and_nonzero_failure_retain_logs(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            pqr = folder/'ion.pqr'
            pqr.write_text('ATOM 1 C ALA 1 0 0 0 1 1.7\n')
            before = pqr.read_bytes()
            config = _config()
            for failure in (False,True):
                def run(name, config, args, *, cwd, log_prefix, **kwargs):
                    stdout = Path(str(log_prefix)+'.stdout.log')
                    stderr = Path(str(log_prefix)+'.stderr.log')
                    stdout.write_text('solver diagnostic')
                    stderr.write_text('failed input' if failure else '')
                    result = dict(returncode=1 if failure else 0,stdout_path=str(stdout),stderr_path=str(stderr))
                    if failure:
                        raise ExternalToolError('solver failed',result)
                    return result
                output=folder/str(failure)
                with patch('molscene.electrostatics.run_tool',side_effect=run), self.assertRaises((ValueError,ExternalToolError)) as caught:
                    export_potential(None,output,dict(mode='prepared',pqr_path=str(pqr),tools=config))
                self.assertFalse(output.exists())
                evidence=Path(caught.exception.failure_path)
                self.assertEqual((evidence/'logs/apbs.stdout.log').read_text(),'solver diagnostic')
                self.assertFalse(any(evidence.rglob('*.pqr')))
                if failure:
                    self.assertTrue(Path(caught.exception.result['stderr_path']).is_file())
                self.assertEqual(pqr.read_bytes(),before)

    def test_architecture_and_memory_preflight_never_silently_coarsens(self):
        for architecture, grid, memory in [('x86',[161]*3,1536),('x64',[129]*3,256)]:
            with tempfile.TemporaryDirectory() as folder:
                folder=Path(folder)
                pqr=folder/'ion.pqr'
                pqr.write_text('ATOM 1 C ALA 1 0 0 0 1 1.7\n')
                with self.assertRaises(ValueError):
                    export_potential(None,folder/'out',dict(mode='prepared',pqr_path=str(pqr),solver={'grid':grid},
                        tools=_config(architecture,memory)))
                self.assertFalse((folder/'out').exists())

    def test_wrong_solver_geometry_and_convergence_diagnostics_fail_closed(self):
        from molscene.electrostatics import _verify_geometry, _diagnostics
        spec=validate_potential_request(dict(mode='prepared',pqr_path='x'))
        grid=Grid((3,3,3),(-10.,)*3,((10.,0.,0.),(0.,10.,0.),(0.,0.,10.)),array('d',[0.]*27),'kBT/e',{})
        with self.assertRaises(ValueError):
            _verify_geometry(grid,{'atoms':[dict(x=0,y=0,z=0,radius=1)]},spec['solver'])
        for message in ('failed to converge','WARNING: solution not converged','NaN potential'):
            with self.assertRaises(ValueError):
                _diagnostics(message,'')
        self.assertEqual(_diagnostics('APBS support for nanotechnology; boundary potential at infinity',''), [])


if __name__ == '__main__':
    unittest.main()
