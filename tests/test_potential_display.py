from array import array
import copy
import importlib.util
import tempfile
import unittest
from pathlib import Path

from molscene.grids import Grid, write_dx
from molscene.potential_display import load_potential_copy, update_potential_ramp


@unittest.skipUnless(importlib.util.find_spec('pymol2'), 'PyMOL unavailable')
class PotentialDisplayTests(unittest.TestCase):
    def setUp(self):
        import pymol2
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pm = pymol2.PyMOL()
        self.pm.start()
        self.addCleanup(self.pm.stop)
        self.cmd = self.pm.cmd
        self.pqr = self.root/'ion.pqr'
        self.pqr.write_text('ATOM 1 C ALA 1 0 0 0 1 2.5\n')
        self.dx = self.root/'potential.dx'
        write_dx(self.dx, Grid((3,3,3), (-10.,)*3, ((10.,0.,0.),(0.,10.,0.),(0.,0.,10.)), array('d', [-8.,0.,8.]*9), 'kBT/e', {}))

    def load(self, **kwargs):
        return load_potential_copy(self.cmd, dx_path=self.dx, units='kBT/e', temperature=298.15, ramp_limit=5., **kwargs)

    def test_pqr_radius_fixed_ramp_legend_and_owned_update(self):
        record = self.load(pqr_path=self.pqr)
        name = record['molecule']
        self.assertEqual(self.cmd.get_model(name).atom[0].vdw, 2.5)
        self.assertEqual(self.cmd.get_model(name).atom[0].elec_radius, 2.5)
        self.assertEqual(self.cmd.get_setting_int('surface_solvent', name), 0)
        self.assertEqual(self.cmd.get_setting_int('surface_ramp_above_mode', name), 0)
        self.assertAlmostEqual(self.cmd.get_setting_float('solvent_radius', name), 1.4, places=6)
        self.assertEqual(record['clipped_grid_fraction'], 2/3)
        self.assertIn('grid fraction', record['legend_text'])
        before = copy.deepcopy(record)
        changed = update_potential_ramp(self.cmd, record, ramp_limit=10)
        self.assertEqual(record, before)
        self.assertEqual(changed['clipped_grid_fraction'], 0)
        self.assertEqual(changed['ramp_values'], [-10., 0., 10.])
        self.cmd.delete(record['ramp'])
        with self.assertRaises(ValueError):
            update_potential_ramp(self.cmd, record, ramp_limit=3)

    def test_existing_molecule_uses_explicit_state_and_preserves_vdw_settings(self):
        self.cmd.pseudoatom('source', pos=[0,0,0], vdw=4, state=1)
        self.cmd.create('source', 'source', 1, 2)
        self.cmd.alter_state(2, 'source', 'x=1')
        self.cmd.set('surface_solvent', 1, 'source')
        record = self.load(selection='source', state=2)
        self.assertEqual(self.cmd.get_model(record['molecule']).atom[0].coord, [1.,0.,0.])
        self.assertEqual(self.cmd.get_model(record['molecule']).atom[0].vdw, 4.)
        self.assertEqual(self.cmd.get_setting_int('surface_solvent', 'source'), 1)
        self.assertEqual(record['radius_convention'], 'existing molecule vdw radii')
        self.assertEqual(self.cmd.get_model('source', state=1).atom[0].coord, [0.,0.,0.])

    def test_insufficient_surface_coverage_fails_without_owned_leaks(self):
        self.cmd.pseudoatom('source', pos=[9,0,0], vdw=2)
        before = set(self.cmd.get_names('all'))
        with self.assertRaises(ValueError):
            self.load(selection='source')
        self.assertEqual(set(self.cmd.get_names('all')), before)

    def test_finite_float64_that_overflows_native_map_is_rejected(self):
        write_dx(self.dx, Grid((3,3,3), (-10.,)*3, ((10.,0.,0.),(0.,10.,0.),(0.,0.,10.)), array('d', [1e100]*27), 'V', {}))
        with self.assertRaises(ValueError):
            self.load(pqr_path=self.pqr)
        self.assertEqual(self.cmd.get_names('objects'), [])

    def test_native_asymmetric_values_preserve_axes_and_field(self):
        record=self.load(pqr_path=self.pqr)
        values=self.cmd.get_volume_field(record['map'],copy=1)
        self.assertEqual(list(values.shape),[3,3,3])
        self.assertEqual(values[0,0,:].tolist(),[-8.,0.,8.])
        self.assertIn('native_value_sha256',record)

    def test_pqr_radii_remain_attached_to_identity_when_atoms_are_unsorted(self):
        self.pqr.write_text('ATOM 1 O ALA 1 0 0 0 -1 1.4\nATOM 2 C ALA 1 1 0 0 1 2.7\nATOM 3 N ALA 1 2 0 0 0 1.8\n')
        record=self.load(pqr_path=self.pqr)
        actual={a.name:(a.vdw,a.elec_radius,a.partial_charge) for a in self.cmd.get_model(record['molecule']).atom}
        for name,radius,charge in [('O',1.4,-1.),('C',2.7,1.),('N',1.8,0.)]:
            self.assertAlmostEqual(actual[name][0],radius,places=6)
            self.assertAlmostEqual(actual[name][1],radius,places=6)
            self.assertEqual(actual[name][2],charge)

    def test_long_precision_dx_values_are_not_silently_misparsed_by_native_loader(self):
        values=[4.9326540340094691e-05, -2.6192531018203062e-05, 3.1253969105056972e-05]*9
        write_dx(self.dx,Grid((3,3,3),(-10.,)*3,((10.,0.,0.),(0.,10.,0.),(0.,0.,10.)),array('d',values),'V',{}))
        record=self.load(pqr_path=self.pqr)
        field=self.cmd.get_volume_field(record['map'],copy=1)
        self.assertAlmostEqual(float(field[0,0,0]),values[0],places=10)


if __name__ == '__main__':
    unittest.main()
