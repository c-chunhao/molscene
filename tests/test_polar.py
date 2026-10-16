"""Typed polar candidates against independent bonded fragment geometry."""
import importlib.util
import math
import unittest
from unittest.mock import patch

from molscene import analysis

HAS_PYMOL = importlib.util.find_spec('pymol2') is not None


def fragments(cmd):
    from chempy import Atom, Bond
    from chempy.models import Indexed
    specs = [
        ('don', [('N', 'N', (0, 0, 0)), ('H', 'H', (1, 0, 0)),
                 ('C1', 'C', (-.7, 1.212, 0)), ('C2', 'C', (-.7, -1.212, 0))],
         [(0, 1, 1), (0, 2, 1), (0, 3, 1)]),
        ('acc', [('O', 'O', (3, 0, 0)), ('C', 'C', (4.23, 0, 0)),
                 ('C1', 'C', (4.93, 1.212, 0)), ('H', 'H', (4.78, -.953, 0))],
         [(0, 1, 2), (1, 2, 1), (1, 3, 1)])]
    for name, atoms, bonds in specs:
        model = Indexed()
        for label, element, xyz in atoms:
            atom = Atom()
            atom.name, atom.symbol, atom.coord = label, element, list(xyz)
            atom.resn, atom.resi, atom.chain = 'TST', '12A', 'A'
            model.atom.append(atom)
        for left, right, order in bonds:
            bond = Bond()
            bond.index, bond.order = [left, right], order
            model.bond.append(bond)
        cmd.load_model(model, name, zoom=0)


def hydroxyls(cmd):
    """Two explicit hydroxyl fragments, each with both native polar roles."""
    from chempy import Atom, Bond
    from chempy.models import Indexed
    for name, x, direction in (('water_a', 0, 1), ('water_b', 3, -1)):
        model = Indexed()
        for label, element, xyz in [('O','O',[x,10,0]), ('H1','H',[x-.9455*direction,10-.3256*direction,0]),
                                    ('C','C',[x,10+direction,0])]:
            atom = Atom()
            atom.name, atom.symbol, atom.coord = label, element, xyz
            atom.resn, atom.resi = 'TST', '1'
            model.atom.append(atom)
        for index in (1, 2):
            bond = Bond()
            bond.index, bond.order = [0, index], 1
            model.bond.append(bond)
        cmd.load_model(model, name, zoom=0)


@unittest.skipUnless(HAS_PYMOL, 'requires the user-provided PyMOL interpreter')
class PolarTests(unittest.TestCase):
    def setUp(self):
        import pymol2
        self.pm = pymol2.PyMOL()
        self.pm.start()
        self.addCleanup(self.pm.stop)
        self.cmd = self.pm.cmd
        fragments(self.cmd)

    def polar(self, a='don', b='acc', **options):
        return analysis.polar_contacts(self.cmd, a, b, **options)

    def test_typed_fragment_geometry_and_nonpolar_negative(self):
        result = self.polar()
        self.assertEqual(result['pair_count'], 1)
        pair = result['pairs'][0]
        self.assertEqual((pair['donor']['name'], pair['acceptor']['name']), ('N', 'O'))
        self.assertEqual(pair['distance_angstrom'], 3.)
        self.assertEqual(pair['donor_membership'], ['A'])
        self.assertEqual(pair['acceptor_membership'], ['B'])
        self.cmd.pseudoatom('carbon', elem='C', pos=[0, 0, 0])
        self.assertGreater(analysis.contacts(self.cmd, 'carbon', 'acc and name O')['pair_count'], 0)
        self.assertEqual(self.polar('carbon', 'acc')['pair_count'], 0)
        with self.assertRaises(ValueError):
            self.polar('none', 'acc')

    def test_native_strict_cutoff_and_angle_fixture(self):
        original = {name: self.cmd.get_model(name) for name in ('don', 'acc')}
        def rotate(name, degrees):
            theta = math.radians(degrees)
            center = 0 if name == 'don' else 3
            xyz = [[(a.coord[0]-center)*math.cos(theta)-a.coord[1]*math.sin(theta)+center,
                    (a.coord[0]-center)*math.sin(theta)+a.coord[1]*math.cos(theta), a.coord[2]]
                   for a in original[name].atom]
            self.cmd.load_coords(xyz, name, 1)
        rotate('don', 90)
        for degrees, angle, expected in ((44,45,1), (46,45,0), (46,60,1), (90,90,0)):
            rotate('acc', degrees)
            self.assertEqual(self.polar(angle=angle)['pair_count'], expected)
        rotate('don', 0)
        rotate('acc', 0)
        for x, expected in ((3.499, 1), (3.5, 0), (3.501, 0)):
            self.cmd.load_coords([[a.coord[0]+x-3, a.coord[1], a.coord[2]] for a in original['acc'].atom], 'acc', 1)
            self.assertEqual(self.polar()['pair_count'], expected)

    def test_reciprocal_overlap_dedup_and_direct_bonds(self):
        hydroxyls(self.cmd)
        result = self.polar('water_a or water_b', 'water_a or water_b')
        self.assertEqual(result['candidate_count'], 1)
        self.assertEqual(result['pair_count'], 2)
        self.assertTrue(all(p['donor_membership'] == ['A', 'B'] for p in result['pairs']))
        self.cmd.create('combined', 'don or acc', zoom=0)
        self.cmd.bond('combined and name N', 'combined and name O')
        donor_id = self.cmd.index('combined and name N')[0]
        acceptor_id = self.cmd.index('combined and name O')[0]
        # Force the coarse API to offer the covalent pair, proving our explicit
        # bond exclusion instead of relying on the backend to happen to omit it.
        with patch.object(self.cmd, 'find_pairs', return_value=[(donor_id, acceptor_id)]):
            direct = self.polar('combined and name N', 'combined and name O')
        self.assertEqual(direct['candidate_count'], 1)
        self.assertEqual(direct['pair_count'], 0)

    def test_primary_altloc_and_requested_state_membership(self):
        self.cmd.alter('don', 'alt="B"')
        self.assertEqual(self.polar()['pair_count'], 0)
        self.cmd.alter('acc', 'alt="A"')
        self.assertEqual(self.polar(altloc='all')['pair_count'], 1)
        self.cmd.create('combined', 'don or acc', zoom=0)
        self.assertEqual(self.polar('combined and name N', 'combined and name O', altloc='all')['pair_count'], 0)
        self.cmd.alter('don or acc', 'alt=""')
        for name in ('don', 'acc'):
            self.cmd.create(name, name, source_state=1, target_state=2, zoom=0)
        self.cmd.translate([10, 0, 0], 'acc', state=2)
        self.cmd.pseudoatom('unrelated_singleton', pos=[100, 0, 0])
        self.cmd.frame(1)
        self.assertEqual(self.polar(state=2)['pair_count'], 0)
        self.assertEqual(self.polar('don within 3.2 of acc', 'acc', state=1)['pair_count'], 1)
        with self.assertRaises(ValueError):
            self.polar('unrelated_singleton', 'acc', state=2)

    def test_candidate_and_directed_caps_fail_without_truncation(self):
        self.cmd.create('acc2', 'acc', zoom=0)
        with patch.object(self.cmd, 'find_pairs', wraps=self.cmd.find_pairs) as query:
            with self.assertRaisesRegex(ValueError, 'max_pairs'):
                self.polar('don', 'acc or acc2', max_pairs=1)
            query.assert_not_called()
        hydroxyls(self.cmd)
        with self.assertRaisesRegex(ValueError, 'max_pairs'):
            self.polar('water_a or water_b', 'water_a or water_b', max_pairs=1)

    def test_invalid_options(self):
        for options in ({'state':True}, {'state':0}, {'state':3}, {'cutoff':.09}, {'cutoff':11},
                        {'cutoff':float('nan')}, {'angle':-1}, {'angle':91}, {'angle':True},
                        {'angle':float('inf')}, {'altloc':'other'}, {'max_pairs':0}, {'max_pairs':True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.polar(**options)

    def test_source_camera_selection_and_chemistry_preserved(self):
        from molscene.polar import chemistry_fingerprint
        self.cmd.select('user_selection', 'don')
        self.cmd.color('red', 'don')
        self.cmd.turn('y', 17)
        before = chemistry_fingerprint(self.cmd)
        atoms = analysis.collect_atoms(self.cmd)
        session = self.cmd.get_session()['names']
        view = self.cmd.get_view()
        selected = self.cmd.index('user_selection')
        self.polar()
        self.assertEqual(chemistry_fingerprint(self.cmd), before)
        self.assertEqual(analysis.collect_atoms(self.cmd), atoms)
        self.assertEqual(self.cmd.get_session()['names'], session)
        self.assertEqual(self.cmd.get_view(), view)
        self.assertEqual(self.cmd.index('user_selection'), selected)
