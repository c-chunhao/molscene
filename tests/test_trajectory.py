"""Independent analytic trajectory oracles using public native coordinate states."""
import importlib.util
import math
import subprocess
import sys
import unittest
from unittest.mock import patch

try:
    from molscene import trajectory
except ImportError:
    trajectory = None

HAS_BACKEND = importlib.util.find_spec('pymol2') is not None

class RequestTests(unittest.TestCase):
    def test_validator_exists_and_is_pure(self):
        self.assertIsNotNone(trajectory, 'trajectory service is required')
        code = "from molscene.trajectory import validate_trajectory_request; import sys; r=validate_trajectory_request({'object_name':'m'}); assert r['align']; assert not any(k in sys.modules for k in ('numpy','pymol','pymol2','PySide6'))"
        subprocess.run([sys.executable, '-c', code], check=True)

    def test_defaults_and_invalid_requests(self):
        self.assertIsNotNone(trajectory)
        self.assertEqual(trajectory.validate_trajectory_request({'object_name':'m'})['time_start_ps'], 0.)
        for change in ({'object':'m'}, {'start':True}, {'stride':0}, {'align':1}, {'time_step_ps':0}, {'time_start_ps':math.inf}, {'object_name':'m\nx'}, {'trajectory_path':'x.xtc'}):
            request = dict(object_name='m'); request.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                trajectory.validate_trajectory_request(request)

@unittest.skipUnless(HAS_BACKEND, 'requires user PyMOL scientific interpreter')
class TrajectoryTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(trajectory, 'trajectory service is required')
        import pymol2
        self.pm=pymol2.PyMOL(); self.pm.start(); self.addCleanup(self.pm.stop); self.cmd=self.pm.cmd

    def points(self, states):
        for i, p in enumerate(states[0], 1):
            self.cmd.pseudoatom('m', name='C'+str(i), elem='C', resn='ALA', resi=str((i+1)//2), pos=p, b=13+i)
        for i, coords in enumerate(states[1:], 2): self.cmd.load_coordset(coords, 'm', state=i)

    def run_analysis(self, **kw):
        return trajectory.trajectory_analysis(self.cmd, dict(object_name='m', selection='all', fit_selection='all', **kw))

    def test_rigid_rotation_translation_and_source_immutable(self):
        a=[[0,0,0],[2,0,0],[0,3,0],[0,0,4]]
        b=[[7-y, -2+x, 5+z] for x,y,z in a]
        self.points([a,b]); before=self.cmd.get_session()
        result=self.run_analysis()
        self.assertLess(max(r['rmsd_angstrom'] for r in result['frames']), 1e-6)
        self.assertLess(max(r['rmsf_angstrom'] for r in result['atoms']), 1e-6)
        self.assertAlmostEqual(result['frames'][0]['rg_angstrom'], math.sqrt(87/16), places=6)
        for state, expected in enumerate([a,b],1): self.assertEqual(self.cmd.get_coordset('m',state).tolist(), expected)
        self.assertEqual(self.cmd.get_session()['names'], before['names'])
        self.assertAlmostEqual(result['frames'][1]['fit_determinant'], 1., places=10)

    def test_proper_rotation_retains_reflection_residual(self):
        a=[[0,0,0],[2,0,0],[0,3,0],[0,0,4]]; self.points([a,[[-x,y,z] for x,y,z in a]])
        result=self.run_analysis()
        self.assertGreater(result['frames'][1]['rmsd_angstrom'], .5)
        self.assertAlmostEqual(result['frames'][1]['fit_determinant'], 1., places=10)

    def test_raw_population_fluctuation_rg_residue_and_time(self):
        self.points([[[0,0,0],[2,0,0]], [[0,0,0],[4,0,0]], [[0,0,0],[6,0,0]], [[0,0,0],[8,0,0]], [[0,0,0],[10,0,0]], [[0,0,0],[12,0,0]]])
        result=self.run_analysis(align=False, start=2, stop=6, stride=2, reference_state=1, time_step_ps=.5, time_start_ps=7)
        self.assertEqual([r['state'] for r in result['frames']], [2,4,6])
        self.assertEqual([r['time_ps'] for r in result['frames']], [7.5,8.5,9.5])
        self.assertEqual([r['rg_angstrom'] for r in result['frames']], [2,4,6])
        self.assertAlmostEqual(result['atoms'][1]['rmsf_angstrom'], math.sqrt(32/3), places=10)
        self.assertAlmostEqual(result['residues'][0]['rmsf_angstrom'], math.sqrt(16/3), places=10)
        self.assertIsNone(result['frames'][0]['fitted_rmsd_angstrom'])

    def test_align_false_ignores_empty_fit(self):
        self.points([[[0,0,0]],[[1,0,0]]])
        result=trajectory.trajectory_analysis(self.cmd, dict(object_name='m',selection='all',fit_selection='none',align=False))
        self.assertEqual(result['frames'][1]['rmsd_angstrom'], 1.)

    def test_invalid_geometry_states_nonfinite_and_cancellation(self):
        self.points([[[0,0,0],[1,0,0],[2,0,0]],[[0,0,0],[1,0,0],[3,0,0]]])
        with self.assertRaisesRegex(ValueError,'noncollinear|degenerate'): self.run_analysis()
        for kw in ({'reference_state':3},{'stop':3},{'start':2}):
            with self.subTest(kw=kw), self.assertRaises(ValueError): self.run_analysis(align=False, **kw)
        from molscene.bundles import CancelledError
        with self.assertRaises(CancelledError): trajectory.trajectory_analysis(self.cmd, dict(object_name='m'), cancelled=lambda:True)
        self.cmd.alter_state(2,'m and index 1','x=float("nan")')
        with self.assertRaisesRegex(ValueError,'finite'): self.run_analysis(align=False)

    def test_membership_frozen_at_reference_and_budget_before_iteration(self):
        self.points([[[0,0,0],[2,0,0]],[[4,0,0],[0,0,0]]])
        result=trajectory.trajectory_analysis(self.cmd, dict(object_name='m',selection='x < 1',align=False))
        self.assertEqual([a['index'] for a in result['atoms']], [1])
        self.assertEqual(result['frames'][1]['rmsd_angstrom'], 4.)
        with patch.object(trajectory,'MAX_SAMPLES',3), self.assertRaisesRegex(ValueError,'budget|limit'):
            self.run_analysis(align=False)

    def test_fit_scaffold_independent_of_measurement_motion(self):
        # Scaffold does not move; C4 moves 0,2,4 while C5 moves 0,4,8.
        a=[[0,0,0],[2,0,0],[0,3,0],[0,0,1],[0,0,5]]
        b=[p[:] for p in a]; b[3][2]+=2; b[4][2]+=4
        c=[p[:] for p in a]; c[3][2]+=4; c[4][2]+=8
        self.points([a,b,c])
        result=trajectory.trajectory_analysis(self.cmd,dict(object_name='m',selection='index 4+5',fit_selection='index 1+2+3'))
        self.assertAlmostEqual(result['frames'][2]['rmsd_angstrom'],math.sqrt(40),places=6)
        self.assertAlmostEqual(result['atoms'][0]['rmsf_angstrom'],math.sqrt(8/3),places=6)
        self.assertAlmostEqual(result['atoms'][1]['rmsf_angstrom'],math.sqrt(32/3),places=6)

    def test_rejects_discrete_identity_and_full_topology_cap(self):
        self.points([[[0,0,0],[1,0,0],[0,1,0]],[[0,0,0],[1,0,0],[0,1,0]]])
        with patch.object(trajectory,'MAX_ATOMS',2), self.assertRaisesRegex(ValueError,'topology'):
            self.run_analysis(align=False)
        self.cmd.create('discrete','m',source_state=0,target_state=0,discrete=1)
        with self.assertRaisesRegex(ValueError,'Discrete|topology'):
            trajectory.trajectory_analysis(self.cmd,dict(object_name='discrete',selection='all',align=False))

    def test_degenerate_moving_fit_and_midway_cancellation(self):
        self.points([[[0,0,0],[1,0,0],[0,1,0]],[[0,0,0],[1,0,0],[2,0,0]]])
        with self.assertRaisesRegex(ValueError,'degenerate'): self.run_analysis()
        from molscene.bundles import CancelledError
        stopped=[]
        with self.assertRaises(CancelledError):
            trajectory.trajectory_analysis(self.cmd,dict(object_name='m',selection='all',align=False),cancelled=lambda:bool(stopped),progress=lambda message:stopped.append(message))
        self.assertEqual(len(stopped),1)

class IgnoredFitAndNumericValidationTests(unittest.TestCase):
    def test_empty_fit_is_ignored_when_alignment_disabled(self):
        self.assertEqual(trajectory.validate_trajectory_request(dict(object_name='m',align=False,fit_selection=''))['fit_selection'],'')

    def test_extreme_integer_time_is_rejected_as_value_error(self):
        with self.assertRaises(ValueError): trajectory.validate_trajectory_request(dict(object_name='m',time_start_ps=10**10000))
