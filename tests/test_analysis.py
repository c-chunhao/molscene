"""Real-backend scientific contracts; expected numbers are independent oracles."""
import importlib.util
import math
from pathlib import Path
import unittest
from unittest.mock import patch

try:
    from molscene import analysis
except ImportError:
    analysis = None

HAS_PYMOL = importlib.util.find_spec('pymol2') is not None
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(HAS_PYMOL, 'requires the user-provided PyMOL interpreter')
class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(analysis, 'scientific analysis module must be implemented')
        import pymol2
        self.pm = pymol2.PyMOL()
        self.pm.start()
        self.addCleanup(self.pm.stop)
        self.cmd = self.pm.cmd

    def points(self, coords, object_name='points'):
        for index, coord in enumerate(coords, 1):
            self.cmd.pseudoatom(object_name, name='C' + str(index), elem='C',
                                resn='ALA', resi=str(index), chain='A',
                                pos=coord, vdw=1.7, b=17 + index)

    def test_geometry_distance_angle_and_signed_dihedral(self):
        self.points([(0, 0, 0), (3, 0, 0), (3, 4, 0), (3, 4, 2)])
        result = analysis.geometry(self.cmd, 'distance', ['index 1', 'index 3'])
        self.assertAlmostEqual(result['value'], 5, places=4)
        self.assertEqual(result['units'], 'angstrom')
        result = analysis.geometry(self.cmd, 'angle', ['index 1', 'index 2', 'index 3'])
        self.assertAlmostEqual(result['value'], 90, places=4)
        self.assertEqual(result['units'], 'degree')
        selections = ['index ' + str(i) for i in (1, 2, 3, 4)]
        for z, expected in ((2, 90), (-2, -90)):
            self.cmd.alter_state(1, 'index 4', 'z = value', space={'value': z})
            result = analysis.geometry(self.cmd, 'dihedral', selections)
            self.assertAlmostEqual(result['value'], expected, places=4)
            self.assertAlmostEqual(result['value'], self.cmd.get_dihedral(*selections, state=1), places=4)
            self.assertEqual(result['atoms'][3]['object'], 'points')

    def test_geometry_rejects_invalid_selection_duplicate_points_and_collinearity(self):
        self.points([(0, 0, 0), (1, 0, 0), (2, 0, 0), (2, 0, 0)])
        for kind, sels in [('distance', ['none', 'index 1']),
                           ('distance', ['all', 'index 1']),
                           ('distance', ['index 1', 'index 1']),
                           ('distance', ['index 3', 'index 4']),
                           ('angle', ['index 1', 'index 2']),
                           ('dihedral', ['index 1', 'index 2', 'index 3', 'index 4'])]:
            with self.subTest(kind=kind, selections=sels), self.assertRaises(ValueError):
                analysis.geometry(self.cmd, kind, sels)
        self.assertAlmostEqual(analysis.geometry(self.cmd, 'angle',
            ['index 1', 'index 2', 'index 3'])['value'], 180, places=4)

    def test_explicit_state_changes_geometry_and_rejects_singleton_fallback(self):
        self.points([(0, 0, 0), (1, 0, 0)])
        self.cmd.load_coordset([[0, 0, 0], [6, 0, 0]], 'points', state=2)
        self.points([(0, 0, 0)], 'singleton')
        self.assertEqual(analysis.geometry(self.cmd, 'distance',
            ['points and index 1', 'points and index 2'], state=2)['value'], 6)
        for state in (0, -1, True, 1.5, 3):
            with self.subTest(state=state), self.assertRaises(ValueError):
                analysis.collect_atoms(self.cmd, 'points', state)
        self.assertEqual([(a['object'], a['index'], a['coord'])
                          for a in analysis.collect_atoms(self.cmd, 'all', state=2)],
                         [('points', 1, [0., 0., 0.]), ('points', 2, [6., 0., 0.])])
        with self.assertRaises(ValueError):
            analysis.collect_atoms(self.cmd, 'singleton', state=2)
        rows = analysis.collect_atoms(self.cmd, 'all', state=1)
        self.assertEqual([(r['object'], r['index']) for r in rows],
                         [('points', 1), ('points', 2), ('singleton', 1)])
        self.assertTrue({'object', 'index', 'segi', 'chain', 'resi', 'resn',
                         'name', 'element', 'alt', 'state', 'coord', 'b', 'vdw'} <= rows[0].keys())
        self.cmd.alter_state(1, 'points and index 1', 'x=float("nan")')
        with self.assertRaises(ValueError):
            analysis.collect_atoms(self.cmd, 'points', state=1)

    def test_contacts_match_brute_force_with_overlap_boundary_and_object_identity(self):
        self.points([(0, 0, 0), (4, 0, 0), (2, 2, 0)])
        self.points([(0, 0, 0), (20, 0, 0)], 'other')
        self.cmd.pseudoatom('other', name='H1', elem='H', pos=[0, 0, 1])
        rows = []
        self.cmd.iterate_state(1, 'not elem H',
            'rows.append(((model,index),(x,y,z)))', space={'rows': rows})
        oracle = {(min(a, b), max(a, b)): math.dist(x, y)
                  for a, x in rows for b, y in rows
                  if a != b and math.dist(x, y) <= 4}
        result = analysis.contacts(self.cmd, 'all', 'all')
        actual = {((p['atom_a']['object'], p['atom_a']['index']),
                   (p['atom_b']['object'], p['atom_b']['index'])): p['distance_angstrom']
                  for p in result['pairs']}
        self.assertEqual(actual, oracle)
        self.assertEqual(result['pair_count'], 6)
        self.assertEqual(analysis.contacts(self.cmd, 'all', 'all', max_pairs=6)['pair_count'], 6)
        self.assertEqual(sum(r['atom_pair_count'] for r in result['residue_pairs']), 6)
        self.assertEqual([p['distance_angstrom'] for p in result['pairs']],
                         sorted(p['distance_angstrom'] for p in result['pairs']))
        self.assertGreater(analysis.contacts(self.cmd, 'all', 'all', heavy_only=False)['pair_count'], 6)
        self.assertEqual(analysis.contacts(self.cmd, 'points', 'other and index 2')['pair_count'], 0)
        with self.assertRaises(ValueError):
            analysis.contacts(self.cmd, 'none', 'all')
        with self.assertRaises(ValueError):
            analysis.contacts(self.cmd, 'all', 'all', max_pairs=2)
        for options in ({'cutoff': 0}, {'cutoff': float('inf')}, {'cutoff': float('nan')},
                        {'cutoff': True}, {'max_pairs': 0}, {'max_pairs': True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                analysis.contacts(self.cmd, 'all', 'all', **options)

    def test_sasa_single_sphere_merged_occlusion_subset_and_nonmutation(self):
        self.points([(0, 0, 0)], 'sphere_a')
        single_area = 4 * math.pi * (1.7 + 1.4) ** 2
        result = analysis.sasa(self.cmd, 'sphere_a')
        self.assertAlmostEqual(result['total_area_angstrom2'] / single_area, 1, delta=.01)
        self.points([(20, 0, 0)], 'sphere_b')
        result = analysis.sasa(self.cmd)
        self.assertAlmostEqual(result['total_area_angstrom2'] / (2 * single_area), 1, delta=.01)
        self.cmd.alter_state(1, 'sphere_b', 'x=3')
        for setting, value in [('dot_solvent', 0), ('solvent_radius', 2.1), ('dot_density', 1)]:
            self.cmd.set(setting, value)
        self.cmd.set('dot_density', 2, 'sphere_a')
        before = analysis.collect_atoms(self.cmd)
        settings = [self.cmd.get_setting_tuple(key) for key in ('dot_solvent', 'solvent_radius', 'dot_density')]
        view = self.cmd.get_view()
        names = self.cmd.get_names('all')
        result = analysis.sasa(self.cmd)
        # Two equal spheres separated by d: exposed union = 4*pi*R^2 + 2*pi*R*d.
        expected = single_area + 2 * math.pi * 3.1 * 3
        self.assertAlmostEqual(result['total_area_angstrom2'] / expected, 1, delta=.02)
        subset = analysis.sasa(self.cmd, 'sphere_a', context='all')
        self.assertAlmostEqual(subset['total_area_angstrom2'] * 2,
                               subset['context_area_angstrom2'], delta=1)
        self.assertEqual(subset['selected_atom_count'], 1)
        self.assertEqual(subset['context_atom_count'], 2)
        self.assertAlmostEqual(sum(r['area_angstrom2'] for r in result['residues']), result['total_area_angstrom2'])
        self.assertEqual(analysis.collect_atoms(self.cmd), before)
        self.assertEqual([self.cmd.get_setting_tuple(key) for key in ('dot_solvent', 'solvent_radius', 'dot_density')], settings)
        self.assertEqual(self.cmd.get_setting_int('dot_density', 'sphere_a'), 2)
        self.assertEqual(self.cmd.get_names('all'), names)
        self.assertEqual(self.cmd.get_view(), view)
        with patch.object(self.cmd, 'get_area', side_effect=RuntimeError('area failure')):
            with self.assertRaisesRegex(RuntimeError, 'area failure'):
                analysis.sasa(self.cmd)
        self.assertEqual(analysis.collect_atoms(self.cmd), before)
        self.assertEqual(self.cmd.get_names('all'), names)
        self.assertEqual([self.cmd.get_setting_tuple(key) for key in ('dot_solvent', 'solvent_radius', 'dot_density')], settings)
        with self.assertRaises(ValueError):
            analysis.sasa(self.cmd, 'all', context='sphere_a')

    def test_sasa_default_context_excludes_other_objects_solvent_hydrogen_and_alt_b(self):
        self.points([(0, 0, 0)])
        self.cmd.pseudoatom('points', name='H', elem='H', pos=[0, 0, 1])
        self.cmd.pseudoatom('points', name='O', elem='O', resn='HOH', pos=[1, 0, 0])
        self.cmd.pseudoatom('points', name='CB', elem='C', resn='ALA', resi='2', pos=[0, 0, 0])
        self.cmd.alter('points and name CB', 'alt="B"')
        self.points([(0, 0, 0)], 'unselected')
        result = analysis.sasa(self.cmd, 'points')
        self.assertEqual(result['context_atom_count'], 1)
        self.assertEqual(result['selected_atom_count'], 1)
        self.assertEqual(analysis.sasa(self.cmd, 'points', altloc='all')['context_atom_count'], 2)
        with self.assertRaises(ValueError):
            analysis.sasa(self.cmd, altloc='bad')

    def test_coordinate_sequences_preserve_segi_blank_chain_insertion_alt_and_unknown(self):
        lines = []
        values = [('ALA', 'A', '1', '', 'S1'), ('GLY', 'A', '2A', 'A', 'S1'),
                  ('GLY', 'A', '2A', 'B', 'S1'), ('SER', 'A', '1', '', 'S2'),
                  ('GLY', '', '7', '', 'S2')]
        for index, (resn, chain, resi, alt, segi) in enumerate(values, 1):
            number, insertion = (resi[:-1], resi[-1]) if resi[-1].isalpha() else (resi, ' ')
            lines.append(f'ATOM  {index:5d}  CA {alt or " "}{resn:3s} {chain or " "}{int(number):4d}{insertion}   '
                         f'{float(index):8.3f}{0.:8.3f}{0.:8.3f}  1.00 20.00      {segi:4s} C  ')
        self.cmd.read_pdbstr('\n'.join(lines) + '\nEND\n', 'sequence_model')
        self.cmd.alter('sequence_model and resi 7', 'resn="ZZZ"')
        result = analysis.inspect_structure(self.cmd)
        self.assertEqual(result['atom_count'], 5)
        self.assertEqual(result['residue_site_count'], 4)
        self.assertEqual(result['elements'], {'C': 5})
        sequences = {(s['segi'], s['chain']): s for s in result['sequences']}
        self.assertEqual(sequences['S1', 'A']['sequence'], 'AG')
        self.assertEqual(sequences['S2', 'A']['sequence'], 'S')
        self.assertEqual(sequences['S2', '']['sequence'], 'X')
        self.assertEqual([r['resi'] for r in sequences['S1', 'A']['residue_map']], ['1', '2A'])
        self.assertEqual([r['position'] for r in sequences['S1', 'A']['residue_map']], [1, 2])
        self.assertEqual(len({s['id'] for s in result['sequences']}), 3)

    def test_real_ubiquitin_pdb_cif_inspection_sasa_and_contacts_parity(self):
        expected_sequence = 'MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG'
        results = []
        for extension in ('pdb', 'cif'):
            self.cmd.delete('all')
            self.cmd.load(str(ROOT / ('examples/data/1ubq.' + extension)), 'ubq')
            overview = analysis.inspect_structure(self.cmd)
            self.assertEqual(overview['atom_count'], 660)
            self.assertEqual(overview['residue_site_count'], 134)
            self.assertEqual(overview['sequences'][0]['sequence'], expected_sequence)
            self.assertEqual(len(overview['sequences'][0]['residue_map']), 76)
            surface = analysis.sasa(self.cmd, 'polymer.protein')
            contact = analysis.contacts(self.cmd, 'resi 1', 'resi 2')
            results.append((surface['total_area_angstrom2'], contact['pair_count']))
        self.assertAlmostEqual(results[0][0], results[1][0], places=4)
        self.assertEqual(results[0][1], results[1][1])

    def test_all_scientific_operations_use_requested_state_and_reject_fallback(self):
        self.points([(0, 0, 0), (3, 0, 0)])
        self.cmd.load_coordset([[0, 0, 0], [20, 0, 0]], 'points', state=2)
        self.assertEqual(analysis.validate_state(self.cmd, 'points', 2), 2)
        self.assertEqual(analysis.contacts(self.cmd, 'points', 'points', state=1)['pair_count'], 1)
        self.assertEqual(analysis.contacts(self.cmd, 'points', 'points', state=2)['pair_count'], 0)
        self.assertGreater(analysis.sasa(self.cmd, 'points', state=2)['total_area_angstrom2'],
                           analysis.sasa(self.cmd, 'points', state=1)['total_area_angstrom2'])
        self.assertEqual(analysis.inspect_structure(self.cmd, 'points', state=2)['state'], 2)
        self.points([(0, 0, 0)], 'singleton')
        operations = [lambda: analysis.geometry(self.cmd, 'distance', ['singleton', 'points and index 2'], 2),
                      lambda: analysis.contacts(self.cmd, 'singleton', 'points', state=2),
                      lambda: analysis.sasa(self.cmd, 'singleton', context='all', state=2),
                      lambda: analysis.inspect_structure(self.cmd, 'singleton', state=2)]
        for operation in operations:
            with self.assertRaises(ValueError):
                operation()
        self.assertEqual(analysis.sasa(self.cmd, 'points', context='all', state=2)['context_atom_count'], 2)
        self.assertEqual(analysis.inspect_structure(self.cmd, 'all', state=2)['atom_count'], 2)

    def test_spatial_membership_is_evaluated_only_in_requested_state(self):
        self.points([(0, 0, 0)], 'moving')
        self.cmd.load_coordset([[10, 0, 0]], 'moving', state=2)
        self.points([(10, 0, 0)], 'reference')
        self.cmd.load_coordset([[10, 0, 0]], 'reference', state=2)
        self.points([(0, 0, 0)], 'anchor')
        expression = 'moving within 1 of reference'
        with self.subTest(operation='collect'), self.assertRaises(ValueError):
            analysis.collect_atoms(self.cmd, expression, state=1)
        selected = analysis.collect_atoms(self.cmd, expression, state=2)
        self.assertEqual([(a['object'], a['coord']) for a in selected], [('moving', [10., 0., 0.])])
        with self.subTest(operation='contacts'):
            self.assertEqual(analysis.contacts(self.cmd, '(' + expression + ') or anchor',
                                               'anchor', state=1)['pair_count'], 0)
        with self.subTest(operation='inspect'):
            self.assertEqual(analysis.inspect_structure(self.cmd, '(' + expression + ') or anchor',
                                                        state=1)['atom_count'], 1)

    def test_spatial_resolution_cleanup_preserves_named_selections_and_view(self):
        self.points([(0, 0, 0)], 'moving')
        self.cmd.load_coordset([[10, 0, 0]], 'moving', state=2)
        self.points([(10, 0, 0)], 'reference')
        self.cmd.load_coordset([[10, 0, 0]], 'reference', state=2)
        self.cmd.pseudoatom('later', pos=[10, 0, 0], state=2)
        self.cmd.select('user_selection', 'reference', enable=1)
        self.cmd.frame(2)
        current_state = self.cmd.get_state()
        names = self.cmd.get_names('all')
        enabled = self.cmd.get_names('all', enabled_only=1)
        view = self.cmd.get_view()
        expression = '(moving or later) within 1 of reference'
        with self.assertRaises(ValueError):
            analysis.collect_atoms(self.cmd, expression, state=1)
        with self.assertRaises(ValueError):
            analysis.validate_state(self.cmd, expression, state=1)
        # An object admitted only by another state's spatial predicate must not
        # be validated when this state's nonempty branch selects reference.
        expression_with_members = '(' + expression + ') or reference'
        self.assertEqual(analysis.validate_state(self.cmd, expression_with_members, state=1), 1)
        self.assertEqual([a['object'] for a in analysis.collect_atoms(
            self.cmd, expression_with_members, state=1)], ['reference'])
        with patch.object(self.cmd, 'get_model', side_effect=RuntimeError('collect failure')):
            with self.assertRaisesRegex(ValueError, 'collect failure'):
                analysis.collect_atoms(self.cmd, expression, state=2)
        original_select = self.cmd.select
        def fail_after_creation(*args, **kwargs):
            original_select(*args, **kwargs)
            raise RuntimeError('resolve failure')
        with patch.object(self.cmd, 'select', side_effect=fail_after_creation):
            with self.assertRaisesRegex(ValueError, 'resolve failure'):
                analysis.collect_atoms(self.cmd, expression, state=2)
        self.assertEqual(self.cmd.get_names('all'), names)
        self.assertEqual(self.cmd.get_names('all', enabled_only=1), enabled)
        self.assertEqual(self.cmd.get_view(), view)
        self.assertEqual(self.cmd.get_state(), current_state)
        self.assertEqual(self.cmd.count_atoms('user_selection'), 1)

    def test_real_hsg_ligand_contacts_oracle_and_context_sasa(self):
        self.cmd.load(str(ROOT / 'examples/data/1hsg.pdb'), 'hsg')
        ligand = self.cmd.get_model('resn MK1', state=1).atom
        protein = self.cmd.get_model('polymer.protein and not elem H', state=1).atom
        expected = sum(math.dist(a.coord, b.coord) <= 4 for a in ligand for b in protein)
        result = analysis.contacts(self.cmd, 'resn MK1', 'polymer.protein')
        self.assertEqual(result['pair_count'], expected)
        self.assertGreater(expected, 0)
        complex_area = analysis.sasa(self.cmd, 'resn MK1')
        isolated_area = analysis.sasa(self.cmd, 'resn MK1', context='resn MK1')
        self.assertEqual(complex_area['selected_atom_count'], 45)
        self.assertGreater(complex_area['context_atom_count'], 45)
        self.assertLess(complex_area['total_area_angstrom2'], isolated_area['total_area_angstrom2'])


if __name__ == '__main__':
    unittest.main()
