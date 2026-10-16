"""Exact live workflows against the real instance-bound PyMOL backend."""
import copy
import importlib.util
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(importlib.util.find_spec('pymol2'), 'requires PyMOL')
class StructureToolsTests(unittest.TestCase):
    def setUp(self):
        from pymol2 import PyMOL
        from molscene.desktop.controller import WorkbenchController
        self.runtime = PyMOL()
        self.runtime.start()
        self.addCleanup(self.runtime.stop)
        self.cmd = self.runtime.cmd
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.controller = WorkbenchController(self.cmd, self.tmp.name)

    def ubiquitin(self):
        self.cmd.load(str(ROOT / 'examples/data/1ubq.pdb'), 'molecule', zoom=0)

    def fingerprint(self, name, states=(1,)):
        # Independent public-model oracle: include properties and topology.
        result = []
        for state in states:
            model = self.cmd.get_model('%' + name, state)
            result.append(([(a.index, a.name, a.resn, a.resi, a.chain, a.segi,
                            a.alt, a.symbol, tuple(a.coord), a.b, a.q, a.vdw,
                            a.formal_charge, a.partial_charge, a.hetatm)
                           for a in model.atom],
                           [(tuple(b.index), b.order) for b in model.bond]))
        return result

    def display(self, name='all'):
        rows = []
        self.cmd.iterate(name, 'rows.append((model,index,reps,color))', space={'rows': rows})
        return rows

    def test_observed_ubiquitin_and_exact_capture_summary(self):
        self.ubiquitin()
        before = self.fingerprint('molecule')
        rows = self.controller.residues('molecule', state=1)
        protein = [row for row in rows if row['polymer'] == 'protein']
        self.assertEqual(len(protein), 76)
        self.assertEqual(''.join(row['one_letter'] for row in protein),
                         'MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG')
        captured = self.controller.select_residues(protein[:1], state=1)
        self.assertEqual(captured['atoms'], protein[0]['atom_count'])
        self.assertEqual(self.cmd.index(captured['selection']),
                         [(a['object'], a['index']) for a in protein[0]['atoms']])
        self.assertEqual(self.controller.selection_summary(captured['selection']),
                         dict(atom_count=protein[0]['atom_count'], residue_count=1,
                              state=1, objects=['molecule']))
        self.assertEqual(before, self.fingerprint('molecule'))

    def test_adversarial_identifiers_order_altloc_state_and_stale_rows(self):
        for obj in ('obj_first', 'obj_second'):
            self.cmd.fragment('ala', obj)
            self.cmd.alter(obj, 'chain=chain_value; segi=segment; resi=residue',
                           space=dict(chain_value='AB', segment='x)y', residue='-4A'))
            self.cmd.alter(obj + ' and index 1', 'alt="B"')
            self.cmd.create(obj, obj, source_state=1, target_state=2)
            self.cmd.translate([10, 3, 4], obj, state=2)
        self.cmd.alter('obj_second', 'chain=""')
        rows = self.controller.residues('all', state=2)
        self.assertEqual([(r['object'], r['chain'], r['segi'], r['resi']) for r in rows],
                         [('obj_first', 'AB', 'x)y', '-4A'), ('obj_second', '', 'x)y', '-4A')])
        selected = self.controller.select_residues(rows[:1], state=2)
        self.assertEqual(selected['atoms'], 10)
        self.assertTrue(all(a['state'] == 2 for a in selected['identities']))
        self.assertEqual({a['alt'] for a in selected['identities']}, {'', 'B'})
        for state in (1, 3, 0, True):
            with self.assertRaises(ValueError):
                self.controller.select_residues(rows, state=state)
        forged = copy.deepcopy(rows)
        forged[0]['resi'] = 'all or all'
        with self.assertRaises(ValueError):
            self.controller.select_residues(forged, state=2)
        self.cmd.alter('obj_first and index 1', 'name="CHANGED"')
        with self.assertRaises(ValueError):
            self.controller.select_residues(rows, state=2)
        rows = self.controller.residues('obj_first', state=2)
        self.cmd.remove('obj_first and index 1')
        with self.assertRaises(ValueError):
            self.controller.select_residues(rows, state=2)
        self.cmd.delete('obj_second')
        with self.assertRaises(ValueError):
            self.controller.select_residues([forged[1]], state=2)

    def test_named_selections_names_collisions_delete_and_session(self):
        self.ubiquitin()
        selection = self.controller.save_selection('my_residue', 'molecule and resi 1')
        self.assertEqual(selection['source'], 'molecule and resi 1')
        original = self.cmd.index('my_residue')
        for name in ('molecule', 'my_residue', '', 'x or all', 'all', 'none',
                     'sele', 'pk1', 'pk2', 'pk3', 'pk4', 'auto', '_molscene_bad',
                     'first', 'last', 'name', 'index', 'b'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.controller.save_selection(name, 'molecule')
        self.assertEqual(original, self.cmd.index('my_residue'))
        with self.assertRaises(ValueError):
            self.controller.delete_selection('molecule')
        with self.assertRaises(ValueError):
            self.controller.delete_selection('pk1')
        path = Path(self.tmp.name) / 'selections.pse'
        self.cmd.save(str(path))
        self.cmd.reinitialize()
        self.cmd.load(str(path))
        self.assertEqual(original, self.cmd.index('my_residue'))
        self.controller.delete_selection('my_residue')
        self.assertNotIn('my_residue', self.cmd.get_names('selections'))

    def test_read_display_and_copy_preserve_existing_selection_visibility(self):
        self.ubiquitin()
        self.cmd.select('kept_visible', 'molecule and resi 1')
        self.cmd.select('kept_hidden', 'molecule and resi 2', enable=0)
        self.cmd.enable('kept_visible')
        original = self.cmd.get_names('selections', enabled_only=1)
        for operation in (lambda: self.controller.residues('molecule'),
                          lambda: self.controller.selection_summary('molecule'),
                          lambda: self.controller.focus_selection('molecule'),
                          lambda: self.controller.hide_context('molecule'),
                          lambda: self.controller.apply_preset('molecule', 'overview', frame=False),
                          lambda: self.controller.prepare_structure('molecule', object_name='clean')):
            operation()
            self.assertEqual(original, self.cmd.get_names('selections', enabled_only=1))
        self.assertEqual(self.cmd.get_names('selections'), ['kept_visible', 'kept_hidden'])

    def test_coordinate_order_partial_residues_discrete_states_and_atomic_rejection(self):
        self.cmd.fragment('ala', 'z_object')
        self.cmd.fragment('gly', 'a_object')
        rows = self.controller.residues('all')
        self.assertEqual([r['object'] for r in rows], ['z_object', 'a_object'])
        partial = self.controller.residues('z_object and name CA')
        captured = self.controller.select_residues(partial)
        self.assertEqual(captured['atoms'], 1)
        self.cmd.create('discrete_obj', 'z_object', source_state=1, target_state=1, discrete=1)
        self.cmd.create('discrete_obj', 'a_object', source_state=1, target_state=2, discrete=1)
        self.cmd.frame(1)
        rows = self.controller.residues('discrete_obj', state=2)
        self.assertEqual({r['resn'] for r in rows}, {'GLY'})
        captured = self.controller.select_residues(rows, state=2)
        self.assertEqual(captured['atoms'], self.cmd.count_atoms('discrete_obj', state=2))
        before = self.cmd.get_names('all')
        for operation in (lambda: self.controller.residues('a_object', state=2),
                          lambda: self.controller.save_selection('bad_state', 'a_object', state=2),
                          lambda: self.controller.select_residues([], state=1)):
            with self.assertRaises(ValueError):
                operation()
        self.assertEqual(before, self.cmd.get_names('all'))

    def test_focus_and_hide_context_preserve_source_and_unselected_display(self):
        self.ubiquitin()
        self.cmd.fragment('ala', 'unrelated')
        self.cmd.show('spheres', 'all')
        self.cmd.orient('all')
        before_view = self.cmd.get_view()
        before_source = self.fingerprint('molecule')
        outside = self.display('unrelated')
        result = self.controller.focus_selection('molecule and resi 1', buffer=0, orient=False)
        self.assertGreater(result['atom_count'], 0)
        self.assertNotEqual(before_view, self.cmd.get_view())
        focused = self.cmd.get_view()
        for buffer in (-1, float('nan'), float('inf'), True, '3'):
            with self.assertRaises(ValueError):
                self.controller.focus_selection('molecule', buffer=buffer)
        self.assertEqual(focused, self.cmd.get_view())
        result = self.controller.hide_context('molecule')
        self.assertGreater(result['solvent_atom_count'], 0)
        self.assertEqual(self.cmd.count_atoms('molecule and solvent and rep spheres'), 0)
        self.assertEqual(outside, self.display('unrelated'))
        self.assertEqual(before_source, self.fingerprint('molecule'))

    def test_overview_surface_are_scoped_and_validate_before_mutation(self):
        self.ubiquitin()
        self.cmd.fragment('ala', 'unrelated')
        self.cmd.color('red', 'all')
        self.cmd.show('spheres', 'all')
        selected = 'molecule and resi 1'
        outside = self.display('not (' + selected + ')')
        source = self.fingerprint('molecule')
        view = self.cmd.get_view()
        result = self.controller.apply_preset(selected, 'overview', frame=False)
        self.assertEqual(result['selected_residue_count'], 1)
        self.assertGreater(self.cmd.count_atoms('(' + selected + ') and rep cartoon'), 0)
        self.assertEqual(view, self.cmd.get_view())
        self.assertEqual(outside, self.display('not (' + selected + ')'))
        self.assertEqual(source, self.fingerprint('molecule'))
        self.cmd.pseudoatom('ligand', resn='LIG', elem='C')
        result = self.controller.apply_preset('ligand', 'surface', frame=False)
        self.assertEqual(result['selected_atom_count'], 1)
        self.assertEqual(self.cmd.count_atoms('ligand and rep surface'), 1)
        display = self.display()
        for kwargs in (dict(preset='bad'), dict(preset='pocket', ligand=None),
                       dict(preset='pocket', ligand='ligand', radius=-1),
                       dict(preset='pocket', ligand='unrelated'),
                       dict(preset='surface', state=2), dict(preset='surface', frame='yes')):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.controller.apply_preset('molecule', **kwargs)
            self.assertEqual(display, self.display())

    @unittest.skipUnless(importlib.util.find_spec('PIL'), 'requires the Pillow test extra')
    def test_overview_renders_sparse_guides_and_unbonded_atoms_at_requested_state(self):
        from PIL import Image
        from molscene.snapshot import scene_fingerprint
        cases = ('full_protein', 'single_met', 'single_ca', 'single_n', 'isolated_met',
                 'isolated_ca', 'disconnected_ca', 'different_chains',
                 'different_objects', 'unbonded_nonprotein')
        for case in cases:
            with self.subTest(case=case):
                self.cmd.reinitialize()
                self.ubiquitin()
                if case in ('full_protein', 'single_met', 'single_ca', 'single_n'):
                    selection = {'full_protein': 'molecule',
                                 'single_met': 'molecule and resi 1',
                                 'single_ca': 'molecule and resi 1 and name CA',
                                 'single_n': 'molecule and resi 1 and name N'}[case]
                else:
                    if case == 'unbonded_nonprotein':
                        self.cmd.pseudoatom('picked', name='C1', elem='C', resn='LIG')
                    elif case == 'different_objects':
                        self.cmd.create('picked', 'molecule and resi 1 and name CA', zoom=0)
                        self.cmd.create('picked_other', 'molecule and resi 2 and name CA', zoom=0)
                    else:
                        scope = {'isolated_met': 'resi 1', 'isolated_ca': 'resi 1 and name CA',
                                 'disconnected_ca': 'resi 1+30 and name CA',
                                 'different_chains': 'resi 1+2 and name CA'}[case]
                        self.cmd.create('picked', 'molecule and (' + scope + ')', zoom=0)
                        if case == 'different_chains':
                            self.cmd.alter('picked and resi 2', 'chain="B"')
                    self.cmd.delete('molecule')
                    selection = 'all'
                for obj in self.cmd.get_object_list('all'):
                    self.cmd.create(obj, '%' + obj, source_state=1, target_state=2, zoom=0)
                    self.cmd.translate([0, 0, 5], '%' + obj, state=2)
                self.cmd.frame(2)
                self.cmd.bg_color('white')
                self.cmd.set('ray_opaque_background', 1)
                self.cmd.set('ray_shadows', 0)
                self.cmd.color('blue', 'all')
                self.cmd.hide('everything', 'all')
                source = {obj: self.fingerprint(obj, (1, 2))
                          for obj in self.cmd.get_object_list('all')}
                fingerprints = [scene_fingerprint(self.cmd, state=s) for s in (1, 2)]
                self.controller.apply_preset(selection, 'overview', state=2, frame=True)
                path = Path(self.tmp.name) / (case + '.png')
                self.cmd.png(str(path), width=320, height=240, ray=1, quiet=1)
                with Image.open(path) as im:
                    colors = im.convert('RGB').getcolors(maxcolors=im.width * im.height)
                    nonwhite = sum(count for count, rgb in colors if min(rgb) < 245)
                self.assertGreater(nonwhite, 15, 'Preset must render visible structure, not only rep flags')
                self.assertEqual(self.cmd.get_state(), 2)
                self.assertEqual(source, {obj: self.fingerprint(obj, (1, 2)) for obj in source})
                self.assertEqual(fingerprints, [scene_fingerprint(self.cmd, state=s) for s in (1, 2)])
                if case == 'full_protein':
                    self.assertEqual(self.cmd.count_atoms('polymer and rep sticks'), 0)
                    self.assertEqual(self.cmd.count_atoms('polymer and rep spheres'), 76)
                    self.assertEqual(self.cmd.count_atoms('polymer and rep cartoon'), 602)

    def test_preset_marker_scale_is_atom_scoped_and_preserves_unselected_styles(self):
        self.ubiquitin()
        self.cmd.fragment('ala', 'unrelated')
        self.cmd.set('sphere_scale', 0.8)
        self.cmd.set('sphere_scale', 0.65, 'molecule')
        self.cmd.set('sphere_scale', 0.55, 'unrelated')
        self.cmd.set('sphere_scale', 0.37, 'molecule and resi 2 and name CA')
        self.cmd.color('red', 'all')
        self.cmd.show('spheres', 'all')
        selection = 'molecule and resi 1'
        def atom_styles(scope):
            rows = []
            self.cmd.iterate(scope, 'rows.append((model,index,reps,color,s.sphere_scale))',
                             space={'rows': rows})
            return rows
        unselected = atom_styles('not (' + selection + ')')
        view = self.cmd.get_view()
        result = self.controller.apply_preset(selection, 'overview', frame=False)
        self.assertEqual(result['display_parameters']['marker_sphere_scale'], 0.2)
        self.assertEqual(result['marker_atom_count'], 1)
        self.assertEqual(unselected, atom_styles('not (' + selection + ')'))
        self.assertAlmostEqual(atom_styles('molecule and resi 1 and name CA')[0][-1], 0.2)
        self.assertAlmostEqual(atom_styles('molecule and resi 1 and name N')[0][-1], 0.65)
        for obj, expected in (('', .8), ('molecule', .65), ('unrelated', .55)):
            self.assertAlmostEqual(self.cmd.get_setting_float('sphere_scale', obj), expected)
        self.assertEqual(view, self.cmd.get_view())
        unrelated = atom_styles('unrelated')
        self.controller.apply_preset('molecule', 'overview', frame=False)
        self.assertEqual(unrelated, atom_styles('unrelated'))
        for obj, expected in (('', .8), ('molecule', .65), ('unrelated', .55)):
            self.assertAlmostEqual(self.cmd.get_setting_float('sphere_scale', obj), expected)

    def test_pocket_matches_independent_requested_state_distance_oracle(self):
        self.ubiquitin()
        self.cmd.create('molecule', 'molecule', source_state=1, target_state=2)
        self.cmd.translate([80, 0, 0], 'molecule', state=2)
        anchor = self.cmd.get_atom_coords('molecule and name CA and resi 1', state=2)
        self.cmd.pseudoatom('ligand', resn='LIG', elem='C', pos=[0, 0, 0])
        self.cmd.create('ligand', 'ligand', source_state=1, target_state=2)
        self.cmd.alter_state(2, 'ligand', '(x,y,z)=point', space={'point': anchor})
        self.cmd.create('outside', 'molecule', source_state=2, target_state=1)
        self.cmd.create('outside', 'outside', source_state=1, target_state=2)
        self.cmd.show('spheres', 'all')
        self.cmd.color('red', 'all')
        outside = self.display('outside')
        source = self.fingerprint('molecule', (1, 2))
        context = '(molecule and resi 1-10) or ligand'
        model = self.cmd.get_model('molecule and polymer and resi 1-10', state=2)
        near = {(a.segi, a.chain, a.resi, a.resn) for a in model.atom
                if math.dist(a.coord, anchor) <= 5}
        expected = {a.index for a in model.atom if (a.segi, a.chain, a.resi, a.resn) in near}
        result = self.controller.apply_preset(context, 'pocket', state=2, ligand='ligand',
                                              radius=5, frame=False)
        self.assertEqual(result['pocket_atom_count'], len(expected))
        self.assertEqual(result['pocket_residue_count'], len(near))
        self.assertEqual(result['ligand_atom_count'], 1)
        self.assertEqual({i for o, i in self.cmd.index('molecule and rep sticks')}, expected)
        self.assertEqual(outside, self.display('outside'))
        self.assertEqual(source, self.fingerprint('molecule', (1, 2)))
        display = self.display()
        with self.assertRaises(ValueError):
            self.controller.apply_preset(context, 'pocket', state=1, ligand='ligand', radius=.01)
        self.assertEqual(display, self.display())

    def cleaning_fixture(self):
        from chempy import Atom, Bond
        from chempy.models import Indexed
        model = Indexed()
        for i, (name, elem, resn, alt) in enumerate((('C1', 'C', 'LIG', ''),
                ('O1', 'O', 'LIG', 'A'), ('OW', 'O', 'HOH', ''),
                ('H1', 'H', 'LIG', ''), ('CB', 'C', 'LIG', 'B'), ('NA', 'Na', 'NA', ''))):
            atom = Atom()
            atom.name, atom.symbol, atom.resn, atom.alt = name, elem, resn, alt
            atom.resi, atom.chain, atom.segi = '-2A', 'AB', 'seg'
            atom.coord, atom.b, atom.q = [float(i), 0., 0.], i + 11., .6
            atom.hetatm = 1
            model.add_atom(atom)
        bond = Bond()
        bond.index, bond.order = [0, 1], 2
        model.add_bond(bond)
        self.cmd.load_model(model, 'source', state=1)
        self.cmd.create('source', 'source', source_state=1, target_state=2)
        self.cmd.translate([0, 5, 10], 'source', state=2)

    def test_clean_copy_exact_subset_preserves_source_all_states_properties_bonds(self):
        self.cleaning_fixture()
        original = self.fingerprint('source', (1, 2))
        original_display, view = self.display(), self.cmd.get_view()
        report = self.controller.prepare_structure('source', object_name='cleaned', state=2,
                                                  remove_hydrogens=True)
        self.assertEqual(report['state'], 1)
        self.assertEqual(report['source_state'], 2)
        self.assertEqual(report['selected_atom_count'], 6)
        self.assertEqual(report['retained_atom_count'], 3)
        self.assertEqual(report['removed'], dict(solvent=1, hydrogens=1, altloc=1))
        self.assertEqual({a['name'] for a in report['identities']}, {'C1', 'O1', 'NA'})
        model = self.cmd.get_model('cleaned', 1)
        self.assertEqual({a.name for a in model.atom}, {'C1', 'O1', 'NA'})
        self.assertEqual({a.alt for a in model.atom}, {'', 'A'})
        expected = {a.name: tuple(a.coord) for a in self.cmd.get_model('source', 2).atom}
        self.assertEqual({a.name: tuple(a.coord) for a in model.atom},
                         {key: expected[key] for key in ('C1', 'O1', 'NA')})
        self.assertEqual([(b.index, b.order) for b in model.bond], [([0, 1], 2)])
        self.assertEqual(self.cmd.count_states('cleaned'), 1)
        self.assertEqual(original, self.fingerprint('source', (1, 2)))
        self.assertEqual(original_display, self.display('source'))
        self.assertEqual(view, self.cmd.get_view())

    def test_clean_validation_empty_and_backend_failure_cleanup(self):
        self.cleaning_fixture()
        self.cmd.select('user_selection', 'source')
        enabled = self.cmd.get_names('selections', enabled_only=1)
        original = self.fingerprint('source', (1, 2))
        names = self.cmd.get_names('all')
        for kwargs in (dict(object_name='source'), dict(object_name='sele'),
                       dict(object_name='bad name'), dict(object_name='clean', state=3),
                       dict(object_name='clean', altloc='highest'),
                       dict(object_name='clean', remove_hydrogens='yes')):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.controller.prepare_structure('source', **kwargs)
            self.assertEqual(names, self.cmd.get_names('all'))
        with self.assertRaises(ValueError):
            self.controller.prepare_structure('source and solvent', object_name='empty')
        real_create = self.cmd.create
        def fail(*args, **kwargs):
            real_create(*args, **kwargs)
            raise RuntimeError('injected backend failure')
        with patch.object(self.cmd, 'create', side_effect=fail), self.assertRaises(RuntimeError):
            self.controller.prepare_structure('source', object_name='partial')
        self.assertEqual(names, self.cmd.get_names('all'))
        self.assertEqual(enabled, self.cmd.get_names('selections', enabled_only=1))
        self.assertEqual(original, self.fingerprint('source', (1, 2)))

    def test_selection_backend_failure_removes_partial_without_touching_existing(self):
        self.ubiquitin()
        self.cmd.select('user_selection', 'molecule and resi 1')
        original = self.cmd.index('user_selection')
        enabled = self.cmd.get_names('selections', enabled_only=1)
        names = self.cmd.get_names('all')
        real_select = self.cmd.select
        def fail(name, *args, **kwargs):
            result = real_select(name, *args, **kwargs)
            if name == 'partial_selection':
                raise RuntimeError('injected select failure')
            return result
        with patch.object(self.cmd, 'select', side_effect=fail), self.assertRaises(RuntimeError):
            self.controller.save_selection('partial_selection', 'molecule')
        self.assertEqual(names, self.cmd.get_names('all'))
        self.assertEqual(enabled, self.cmd.get_names('selections', enabled_only=1))
        self.assertEqual(original, self.cmd.index('user_selection'))

    def test_navigation_workflows_reject_background_thread_before_backend_calls(self):
        import concurrent.futures
        calls = (lambda: self.controller.residues(),
                 lambda: self.controller.select_residues([]),
                 lambda: self.controller.selection_summary('all'),
                 lambda: self.controller.save_selection('saved', 'all'),
                 lambda: self.controller.delete_selection('saved'),
                 lambda: self.controller.focus_selection('all'),
                 lambda: self.controller.hide_context('all'),
                 lambda: self.controller.apply_preset('all', 'overview'),
                 lambda: self.controller.prepare_structure('all', object_name='clean'),
                 lambda: self.controller.update_scene('saved'),
                 lambda: self.controller.rename_scene('saved', 'new_name'))
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            for call in calls:
                with self.assertRaisesRegex(RuntimeError, 'main thread'):
                    pool.submit(call).result()

    def test_clean_all_alternates_and_deuterium_tritium_filter(self):
        self.cleaning_fixture()
        self.cmd.alter('source and name H1', 'elem="D"')
        self.cmd.pseudoatom('source', name='T1', elem='T', resn='LIG', resi='12')
        report = self.controller.prepare_structure('source', object_name='all_conformers',
                                                  remove_solvent=False, remove_hydrogens=True,
                                                  altloc='all')
        self.assertEqual(report['retained_atom_count'], 5)
        self.assertEqual(report['removed'], dict(solvent=0, hydrogens=2, altloc=0))
        self.assertEqual({a.name for a in self.cmd.get_model('all_conformers').atom},
                         {'C1', 'O1', 'OW', 'CB', 'NA'})

    def test_scene_update_rename_preserves_other_scenes_and_reopen(self):
        self.ubiquitin()
        self.cmd.create('molecule', 'molecule', source_state=1, target_state=2)
        self.cmd.orient('all')
        self.controller.store_scene('first')
        first = self.cmd.get_view()
        self.cmd.turn('y', 40)
        self.controller.store_scene('second')
        self.cmd.turn('x', 25)
        self.cmd.frame(2)
        self.cmd.hide('everything', 'all')
        self.cmd.show('sticks', 'all')
        self.cmd.color('blue', 'all')
        expected_view, expected_display = self.cmd.get_view(), self.display()
        self.controller.update_scene('second')
        self.controller.rename_scene('second', 'renamed')
        self.assertEqual(self.controller.scenes(), ['first', 'renamed'])
        for name in ('first', '', '*', 'auto', 'new'):
            with self.assertRaises(ValueError):
                self.controller.rename_scene('renamed', name)
        self.controller.restore_scene('first')
        self.assertEqual(first, self.cmd.get_view())
        path = Path(self.tmp.name) / 'scenes.pse'
        self.cmd.save(str(path))
        self.cmd.reinitialize()
        self.cmd.load(str(path))
        self.controller.restore_scene('renamed')
        self.assertEqual(expected_view, self.cmd.get_view())
        self.assertEqual(expected_display, self.display())
        self.assertEqual(self.cmd.get_state(), 2)


if __name__ == '__main__':
    unittest.main()
