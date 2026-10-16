"""Interface SASA contracts with independent sphere and membership oracles."""
import importlib.util
import math
import unittest
from unittest.mock import patch

from molscene import analysis


@unittest.skipUnless(importlib.util.find_spec('pymol2'), 'requires PyMOL')
class InterfaceTests(unittest.TestCase):
    def setUp(self):
        import pymol2
        self.pm = pymol2.PyMOL()
        self.pm.start()
        self.addCleanup(self.pm.stop)
        self.cmd = self.pm.cmd

    def atom(self, obj, x, name='C', resi='1', elem='C', resn='ALA'):
        self.cmd.pseudoatom(obj, pos=[x, 0, 0], name=name, resi=resi,
                            resn=resn, elem=elem, chain='A', segi='S', vdw=1.7, b=23, state=1)

    def pair(self, distance=3):
        self.atom('sphere_a', 0)
        self.atom('sphere_b', distance)

    def test_equal_spheres_analytic_area_and_half_sum(self):
        self.pair()
        result = analysis.interface(self.cmd, 'sphere_a', 'sphere_b')
        single = 4 * math.pi * 3.1 ** 2
        buried = 2 * math.pi * 3.1 * (3.1 - 3 / 2)
        for side in ('a', 'b'):
            partner = result['partner_' + side]
            self.assertEqual(partner['selected_atom_count'], 1)
            self.assertAlmostEqual(partner['isolated_area_angstrom2'], single, delta=single * .03)
            self.assertAlmostEqual(partner['complex_area_angstrom2'], single - buried,
                                   delta=(single - buried) * .03)
            self.assertAlmostEqual(partner['buried_area_angstrom2'], buried, delta=buried * .03)
        self.assertEqual(result['kind'], 'interface')
        self.assertEqual(result['state'], 1)
        self.assertAlmostEqual(result['total_buried_area_angstrom2'], 2 * buried, delta=buried * .06)
        self.assertEqual(result['interface_area_angstrom2'], result['total_buried_area_angstrom2'] / 2)
        self.assertTrue(result['definition'])

    def test_separated_spheres_keep_zero_loss_residue_rows(self):
        self.pair(20)
        result = analysis.interface(self.cmd, 'sphere_a', 'sphere_b')
        self.assertEqual(result['total_buried_area_angstrom2'], 0)
        self.assertEqual(result['interface_area_angstrom2'], 0)
        self.assertEqual(len(result['residues']), 2)
        for row in result['residues']:
            self.assertEqual(row['buried_area_angstrom2'], 0)
            self.assertEqual(row['atom_count'], 1)

    def test_exact_union_excludes_same_object_and_other_object_outsiders(self):
        self.atom('mixed', 0, 'CA')
        self.atom('mixed', 3, 'CB', '2')
        self.atom('mixed', 0, 'OUT', '3')
        self.atom('outsider', 3)
        result = analysis.interface(self.cmd, 'mixed and name CA', 'mixed and name CB')
        expected = 2 * math.pi * 3.1 * (3.1 - 1.5)
        for side in ('a', 'b'):
            self.assertAlmostEqual(result['partner_' + side]['buried_area_angstrom2'], expected,
                                   delta=expected * .03)
        self.assertEqual(result['parameters']['context_atom_count'], 2)
        self.assertEqual({r['resi'] for r in result['residues']}, {'1', '2'})

    def test_partner_swap_and_complete_residue_sums_preserve_original_identity(self):
        self.pair()
        self.atom('sphere_a', 40, 'N', '2A')
        self.cmd.alter('sphere_a and resi 2A', 'chain="";segi="other"')
        forward = analysis.interface(self.cmd, 'sphere_a', 'sphere_b')
        reverse = analysis.interface(self.cmd, 'sphere_b', 'sphere_a')
        self.assertEqual(forward['partner_a'], reverse['partner_b'])
        self.assertEqual(forward['partner_b'], reverse['partner_a'])
        self.assertEqual(forward['interface_area_angstrom2'], reverse['interface_area_angstrom2'])
        expected = [('A', 'sphere_a', 'S', 'A', '1', 'ALA', 1),
                    ('A', 'sphere_a', 'other', '', '2A', 'ALA', 1),
                    ('B', 'sphere_b', 'S', 'A', '1', 'ALA', 1)]
        self.assertEqual([tuple(r[k] for k in ('partner', 'object', 'segi', 'chain', 'resi', 'resn', 'state'))
                          for r in forward['residues']], expected)
        for label, side in [('A', 'a'), ('B', 'b')]:
            rows = [r for r in forward['residues'] if r['partner'] == label]
            for area in ('isolated', 'complex', 'buried'):
                field = area + '_area_angstrom2'
                self.assertAlmostEqual(sum(r[field] for r in rows), forward['partner_' + side][field])
            self.assertEqual(sum(r['atom_count'] for r in rows), forward['partner_' + side]['selected_atom_count'])

    def test_split_same_residue_keeps_two_partner_rows(self):
        self.atom('same', 0, 'CA')
        self.atom('same', 3, 'CB')
        result = analysis.interface(self.cmd, 'name CA', 'name CB')
        self.assertEqual([(r['partner'], r['object'], r['resi'], r['atom_count'])
                          for r in result['residues']], [('A', 'same', '1', 1), ('B', 'same', '1', 1)])

    def test_filtering_and_counts_ignore_shared_excluded_atoms(self):
        self.pair(20)
        for element in ('H', 'D', 'T'):
            self.atom('excluded', 0, element, elem=element)
        self.atom('excluded', 0, 'O', resn='HOH', elem='O')
        self.atom('excluded', 0, 'CB', '3')
        self.cmd.alter('excluded and name CB', 'alt="B"')
        self.atom('sphere_a', 40, 'CA', '2')
        self.cmd.alter('sphere_a and name CA', 'alt="A"')
        first, second = 'sphere_a or excluded', 'sphere_b or excluded'
        result = analysis.interface(self.cmd, first, second)
        self.assertEqual(result['partner_a']['selected_atom_count'], 2)
        self.assertEqual(result['partner_b']['selected_atom_count'], 1)
        params = result['parameters']
        for key, expected in dict(selection_a=first, selection_b=second, state=1, altloc='primary',
                                  dot_solvent=1, solvent_radius=1.4, dot_density=3, heavy_only=True,
                                  exclude_solvent=True, partner_a_input_atom_count=7,
                                  partner_b_input_atom_count=6, partner_a_filtered_atom_count=5,
                                  partner_b_filtered_atom_count=5, partner_a_retained_atom_count=2,
                                  partner_b_retained_atom_count=1, context_atom_count=3).items():
            self.assertEqual(params[key], expected, key)
        self.assertEqual(result['total_buried_area_angstrom2'], 0)
        with self.assertRaisesRegex(ValueError, 'overlap'):
            analysis.interface(self.cmd, first, second, altloc='all')
        retained = analysis.interface(self.cmd, first, 'sphere_b', altloc='all')
        self.assertEqual(retained['partner_a']['selected_atom_count'], 3)

    def test_rejects_overlap_empty_filtered_partners_and_invalid_parameters(self):
        self.pair()
        self.atom('only_h', 50, elem='H')
        self.atom('only_b', 50)
        self.cmd.alter('only_b', 'alt="B"')
        for first, second in [('sphere_a', 'sphere_a or sphere_b'), ('none', 'sphere_b'),
                              ('only_h', 'sphere_b'), ('sphere_a', 'only_b')]:
            with self.subTest(first=first, second=second), self.assertRaises(ValueError):
                analysis.interface(self.cmd, first, second)
        for state in (0, -1, True, 1.5, 2):
            with self.subTest(state=state), self.assertRaises(ValueError):
                analysis.interface(self.cmd, 'sphere_a', 'sphere_b', state=state)
        with self.assertRaises(ValueError):
            analysis.interface(self.cmd, 'sphere_a', 'sphere_b', altloc='bad')

    def test_primary_alternate_filter_is_case_sensitive_like_existing_sasa(self):
        self.pair(20)
        self.atom('lowercase_alt', 0)
        self.cmd.alter('lowercase_alt', 'alt="a"')
        result = analysis.interface(self.cmd, 'sphere_a or lowercase_alt',
                                    'sphere_b or lowercase_alt')
        self.assertEqual(result['partner_a']['selected_atom_count'], 1)
        self.assertEqual(result['partner_b']['selected_atom_count'], 1)
        self.assertEqual(result['parameters']['context_atom_count'], 2)
        self.assertEqual(len(result['residues']), 2)

    def test_requested_state_spatial_membership_and_no_singleton_fallback(self):
        self.pair()
        self.cmd.load_coordset([[0, 0, 0]], 'sphere_a', state=2)
        self.cmd.load_coordset([[20, 0, 0]], 'sphere_b', state=2)
        self.cmd.frame(2)
        self.assertGreater(analysis.interface(self.cmd, 'sphere_a', 'sphere_b', state=1)
                           ['interface_area_angstrom2'], 0)
        result = analysis.interface(self.cmd, 'sphere_a', 'sphere_b', state=2)
        self.assertEqual(result['interface_area_angstrom2'], 0)
        self.assertEqual({r['state'] for r in result['residues']}, {2})
        expression = 'sphere_b within 4 of sphere_a'
        self.assertGreater(analysis.interface(self.cmd, 'sphere_a', expression, state=1)
                           ['interface_area_angstrom2'], 0)
        with self.assertRaises(ValueError):
            analysis.interface(self.cmd, 'sphere_a', expression, state=2)
        self.atom('singleton', 3)
        with self.assertRaises(ValueError):
            analysis.interface(self.cmd, 'sphere_a', 'singleton', state=2)

    def test_discrete_state_membership_retains_correct_original_residue(self):
        self.atom('earlier', 0, resi='1')
        self.atom('later', 20, resi='9')
        self.cmd.create('discrete', 'earlier', 1, 1, discrete=1)
        self.cmd.create('discrete', 'later', 1, 2, discrete=1)
        self.atom('partner', 3)
        self.cmd.load_coordset([[3, 0, 0]], 'partner', state=2)
        self.cmd.frame(1)
        result = analysis.interface(self.cmd, 'discrete', 'partner', state=2)
        self.assertEqual(result['interface_area_angstrom2'], 0)
        self.assertEqual([(r['object'], r['resi'], r['state']) for r in result['residues']],
                         [('discrete', '9', 2), ('partner', '1', 2)])

    def test_source_view_settings_and_selection_preserved_on_success_and_failure(self):
        self.pair()
        self.cmd.select('user_selection', 'sphere_a')
        self.cmd.set('dot_density', 1)
        self.cmd.set('solvent_radius', 2.2, 'sphere_a')
        self.cmd.flag('ignore', 'sphere_a', 'set')
        def snapshot():
            return (self.cmd.get_session()['names'], self.cmd.get_view(), self.cmd.get_state(),
                    [self.cmd.get_setting_tuple(key) for key in
                     ('dot_solvent', 'solvent_radius', 'dot_density')],
                    self.cmd.get_names('all'), self.cmd.get_names('all', enabled_only=1))
        # Session names capture all-state atoms/bonds, flags, object settings,
        # styles and selections, excluding unrelated session serialization state.
        before = snapshot()
        analysis.interface(self.cmd, 'sphere_a', 'sphere_b')
        self.assertEqual(snapshot(), before)
        original = self.cmd.get_area
        for failure_at in (1, 2, 3, 4):
            calls = 0
            def fail_area(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == failure_at:
                    raise RuntimeError('injected area failure')
                return original(*args, **kwargs)
            with patch.object(self.cmd, 'get_area', side_effect=fail_area):
                with self.assertRaisesRegex(RuntimeError, 'injected area failure'):
                    analysis.interface(self.cmd, 'sphere_a', 'sphere_b')
            self.assertEqual(snapshot(), before)

    def test_signed_backend_difference_is_not_clamped(self):
        self.pair(20)
        original = self.cmd.get_area
        calls = 0
        def slightly_larger_complex(*args, **kwargs):
            nonlocal calls
            calls += 1
            value = original(*args, **kwargs)
            if self.cmd.count_atoms(args[0]) == 2:
                self.cmd.alter(args[0], 'b=b+0.125')
            return value
        with patch.object(self.cmd, 'get_area', side_effect=slightly_larger_complex):
            result = analysis.interface(self.cmd, 'sphere_a', 'sphere_b')
        self.assertEqual(calls, 4)
        self.assertEqual(result['partner_a']['buried_area_angstrom2'], -.125)
        self.assertEqual(result['partner_b']['buried_area_angstrom2'], -.125)
        self.assertEqual(result['total_buried_area_angstrom2'], -.25)
        self.assertEqual(result['interface_area_angstrom2'], -.125)
        self.assertEqual([r['buried_area_angstrom2'] for r in result['residues']], [-.125, -.125])

    def test_sasa_preserves_selection_visibility_and_auto_hide_setting(self):
        self.pair()
        self.cmd.select('visible_a', 'sphere_a', enable=0)
        self.cmd.select('visible_b', 'sphere_b', enable=0)
        self.cmd.select('hidden_selection', 'sphere_a', enable=0)
        self.cmd.set('auto_hide_selections', 0)
        self.cmd.enable('visible_a')
        self.cmd.enable('visible_b')
        original_load = self.cmd.load_model
        def fail_after_load(*args, **kwargs):
            original_load(*args, **kwargs)
            raise RuntimeError('partial load failure')
        for auto_hide in (0, 1):
            self.cmd.set('auto_hide_selections', auto_hide)
            def snapshot():
                return (self.cmd.get_session()['names'], self.cmd.get_names('all', enabled_only=1),
                        self.cmd.get_setting_tuple('auto_hide_selections'))
            before = snapshot()
            with self.subTest(auto_hide=auto_hide, operation='success'):
                analysis.sasa(self.cmd, 'sphere_a', context='sphere_a or sphere_b')
                self.assertEqual(snapshot(), before)
            for method, failure in [('get_area', RuntimeError('area failure')),
                                    ('load_model', fail_after_load)]:
                with self.subTest(auto_hide=auto_hide, operation=method):
                    with patch.object(self.cmd, method, side_effect=failure):
                        with self.assertRaises(RuntimeError):
                            analysis.sasa(self.cmd, 'sphere_a', context='sphere_a or sphere_b')
                    self.assertEqual(snapshot(), before)


if __name__ == '__main__':
    unittest.main()
