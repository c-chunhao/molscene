"""Guided labels against real headless PyMOL, including literal text and PSE."""
import importlib
import importlib.util
import math
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

try:
    annotations = importlib.import_module('molscene.annotations')
except ModuleNotFoundError as exc:
    if exc.name != 'molscene.annotations':
        raise
    annotations = None

HAS_PYMOL = importlib.util.find_spec('pymol2') is not None


def load_atoms(cmd, name, specs):
    """Each spec is name, element, resn, resi, chain, segment, alternate."""
    from chempy import Atom, Bond
    from chempy.models import Indexed
    model = Indexed()
    for i, (atom_name, element, resn, resi, chain, segi, alt) in enumerate(specs):
        atom = Atom()
        atom.name, atom.symbol, atom.resn, atom.resi = atom_name, element, resn, resi
        atom.chain, atom.segi, atom.alt = chain, segi, alt
        atom.coord, atom.b, atom.q = [i * 2., 0., 0.], 10. + i, .75
        atom.formal_charge = 1 if i == 0 else 0
        model.atom.append(atom)
    if len(specs) > 1:
        bond = Bond()
        bond.index, bond.order = [0, 1], 1
        model.bond.append(bond)
    cmd.load_model(model, name, zoom=0)


@unittest.skipUnless(HAS_PYMOL, 'requires the user-provided PyMOL interpreter')
class AnnotationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(annotations, 'guided label backend is not implemented')
        from pymol2 import PyMOL
        self.pm = PyMOL()
        self.pm.start()
        self.addCleanup(self.pm.stop)
        self.cmd = self.pm.cmd
        load_atoms(self.cmd, 'molecule', [
            ('N', 'N', 'GLY', '12A', 'A', 'S1', ''),
            ('CA', 'C', 'GLY', '12A', 'A', 'S1', 'B'),
            ('CA', 'C', 'GLY', '12A', 'A', 'S1', 'A'),
            ('CA', 'C', 'GLY', '12A', 'A', 'S1', ''),
            ('P', 'P', 'DA', '13', '', 'S1', ''),
            ('O', 'O', 'DA', '13', '', 'S1', ''),
            ('C1', 'C', 'LIG', '14', '', 'S1', ''),
            ('H1', 'H', 'LIG', '14', '', 'S1', ''),
            ('H1', 'H', 'HOH', '15', '', 'S1', ''),
        ])
        self.cmd.pseudoatom('unrelated', name='X', pos=[30, 0, 0])
        self.cmd.label('all', repr('original'))
        self.cmd.set('label_size', 11, 'all')
        self.cmd.set('label_color', 'green', 'all')
        self.cmd.set('label_color', 'red', 'molecule and name N')
        self.cmd.set('label_color', 'blue', 'unrelated and name X')
        self.cmd.load_coordset([[a.coord[0], 5., 0.] for a in self.cmd.get_model('molecule').atom],
                               'molecule', state=2)
        self.cmd.frame(2)
        self.cmd.turn('y', 17)
        self.cmd.select('visible_selection', 'unrelated', enable=1)

    def labels(self):
        rows = []
        self.cmd.iterate('all', 'rows.append((model,index,label))', space={'rows': rows})
        return {(obj, index): text for obj, index, text in rows}

    def snapshot(self, *, labels=True):
        models = []
        for obj in self.cmd.get_object_list('all'):
            for state in range(1, self.cmd.count_states(obj) + 1):
                model = self.cmd.get_model('%' + obj, state=state)
                models.append((obj, state,
                    [(a.index, a.name, a.resn, a.resi, a.segi, a.chain, a.alt,
                      a.symbol, tuple(a.coord), a.b, a.q, a.formal_charge) for a in model.atom],
                    [(tuple(b.index), b.order) for b in model.bond]))
        result = dict(models=models, frame=self.cmd.get_state(), view=self.cmd.get_view(),
                      selections=self.cmd.get_names('selections'),
                      enabled=self.cmd.get_names('selections', enabled_only=1),
                      settings=self.cmd.get_setting_tuple('label_size'),
                      color=self.cmd.get_setting_tuple('label_color'),
                      session_file=self.cmd.get('session_file'))
        if labels:
            result['labels'] = self.labels()
            result['styles'] = self.styles()
        return result

    def styles(self, state=1):
        rows = []
        self.cmd.iterate('all', 'rows.append((model,index,s.label_color))', space={'rows': rows})
        return {(obj, index): (self.cmd.get_setting_float('label_size', obj, state=state),
                              color if color is not None else self.cmd.get_setting_int('label_color', obj))
                for obj, index, color in rows}

    def test_exact_recipe_strings_and_selected_anchor_preference(self):
        before = self.snapshot()
        result = annotations.label_plan(self.cmd, 'molecule')
        self.assertEqual(result['count'], 4)
        self.assertEqual({(a['name'], a['alt'], a['text']) for a in result['targets']},
                         {('CA', '', 'GLY12A'), ('P', '', 'DA13'),
                          ('C1', '', 'LIG14'), ('H1', '', 'HOH15')})
        chain = annotations.label_plan(self.cmd, 'molecule', template='chain_residue')
        self.assertEqual({a['text'] for a in chain['targets']},
                         {'A:GLY12A', '(blank):DA13', '(blank):LIG14', '(blank):HOH15'})
        atom = annotations.label_plan(self.cmd, 'molecule', template='atom')
        self.assertEqual(atom['count'], 9)
        self.assertEqual([a['text'] for a in atom['targets']],
                         [a.name for a in self.cmd.get_model('molecule', state=1).atom])
        partial = annotations.label_plan(self.cmd, 'molecule and name N+O')
        self.assertEqual({(a['name'], a['text']) for a in partial['targets']},
                         {('N', 'GLY12A'), ('O', 'DA13')})
        alt_a = annotations.label_plan(self.cmd, 'molecule and not alt ""')
        self.assertEqual([(a['name'], a['alt']) for a in alt_a['targets']], [('CA', 'A')])
        self.assertEqual(self.snapshot(), before)

    def test_residue_identity_keeps_object_segment_chain_insertion_and_resn(self):
        load_atoms(self.cmd, 'identities', [
            ('CA', 'C', 'GLY', '12', '', '', ''),
            ('CA', 'C', 'GLY', '12A', '', '', ''),
            ('CA', 'C', 'GLY', '12A', 'A', '', ''),
            ('CA', 'C', 'GLY', '12A', 'A', 'S2', ''),
            ('CA', 'C', 'ALA', '12A', 'A', 'S2', ''),
        ])
        result = annotations.label_plan(self.cmd, 'identities or (molecule and name CA)')
        self.assertEqual(result['count'], 6)
        keys = {(a['object'], a['segi'], a['chain'], a['resi'], a['resn'])
                for a in result['targets']}
        self.assertEqual(len(keys), 6)
        self.assertIn(('identities', '', '', '12', 'GLY'), keys)
        self.assertIn(('identities', 'S2', 'A', '12A', 'ALA'), keys)

    def test_literal_quotes_backslashes_and_unicode_cannot_execute(self):
        text = 'CA\'"\\β'
        self.cmd.alter('unrelated', 'name=value', space={'value': text})
        plan = annotations.label_plan(self.cmd, 'unrelated', template='atom')
        self.assertEqual(plan['targets'][0]['text'], text)
        annotations.apply_labels(self.cmd, 'unrelated', template='atom')
        key = self.cmd.index('unrelated')[0]
        self.assertEqual(self.labels()[key], text)
        resn, resi = "L'β\"\\", '12A'
        self.cmd.alter('unrelated', 'resn=r; resi=i', space={'r': resn, 'i': resi})
        native = self.cmd.get_model('unrelated').atom[0]
        self.assertEqual((native.resn, native.resi), (resn, resi))
        annotations.apply_labels(self.cmd, 'unrelated', template='residue')
        self.assertEqual(self.labels()[key], resn + resi)

    def test_custom_preview_never_evaluates_expression_and_apply_is_native(self):
        before = self.snapshot()
        expression = '1/0'
        result = annotations.label_plan(self.cmd, 'molecule', template='custom', expression=expression)
        self.assertEqual(result['expression'], expression)
        self.assertEqual(result['count'], 9)
        self.assertTrue(all('text' not in target for target in result['targets']))
        self.assertEqual(self.snapshot(), before)
        result = annotations.apply_labels(self.cmd, 'molecule and name N', template='custom',
                                          expression='resn+resi+":"+name')
        self.assertEqual(result['count'], 1)
        self.assertEqual(self.labels()[self.cmd.index('molecule and name N')[0]], 'GLY12A:N')

    def test_apply_and_clear_affect_exact_targets_only_and_preserve_scene(self):
        before = self.snapshot(labels=False)
        labels, styles = self.labels(), self.styles()
        result = annotations.apply_labels(self.cmd, 'molecule', size=18.5, color='yellow')
        targets = {(a['object'], a['index']): a['text'] for a in result['targets']}
        self.assertEqual(result['count'], 4)
        self.assertEqual(result.get('size_scope'), 'object_state')
        self.assertEqual(result['size_objects'], ['molecule'])
        for key, text in self.labels().items():
            self.assertEqual(text, targets.get(key, labels[key]))
            expected_size = 18.5 if key[0] == 'molecule' else styles[key][0]
            expected_color = self.cmd.get_color_index('yellow') if key in targets else styles[key][1]
            self.assertEqual(self.styles()[key], (expected_size, expected_color))
        self.assertEqual(self.cmd.get_setting_float('label_size', 'molecule', state=2), 11.)
        self.assertEqual(self.cmd.get_setting_float('label_size', 'molecule'), 11.)
        self.assertEqual(self.snapshot(labels=False), before)
        clear_key = self.cmd.index('molecule and name N')[0]
        cleared = annotations.clear_labels(self.cmd, 'molecule and name N')
        self.assertEqual(cleared['count'], 1)
        self.assertEqual(self.labels()[clear_key], '')
        self.assertEqual(self.styles()[clear_key], (18.5, styles[clear_key][1]))
        self.assertEqual(self.labels()[self.cmd.index('unrelated')[0]], 'original')
        self.assertEqual(self.snapshot(labels=False), before)

    def test_validation_is_nonmutating_and_cap_rejects_without_truncation(self):
        before = self.snapshot()
        cases = [dict(selection=s) for s in ('none', 'name (', '', 'all\n')]
        cases += [dict(state=s) for s in (0, -1, True, 3, 1.5)]
        cases += [dict(size=s) for s in (True, '14', 0, 7.9, 48.1, math.nan, math.inf, 10**1000)]
        cases += [dict(max_labels=s) for s in (0, True, 501, 1.5)]
        cases += [dict(color=s) for s in ('no_such_native_color', '', None, True)]
        cases += [dict(template='unknown'), dict(template='custom', expression=None),
                  dict(template='atom', max_labels=8)]
        for options in cases:
            with self.subTest(options=options), self.assertRaises(ValueError):
                annotations.apply_labels(self.cmd, **dict(dict(selection='molecule'), **options))
            self.assertEqual(self.snapshot(), before)
        with self.assertRaises(ValueError):
            annotations.clear_labels(self.cmd, 'molecule', state=3)
        self.assertEqual(self.snapshot(), before)

    def test_state_specific_spatial_and_discrete_membership(self):
        self.cmd.pseudoatom('reference', pos=[0, 5, 0])
        self.cmd.load_coordset([[0., 5., 0.]], 'reference', state=2)
        self.cmd.frame(1)
        result = annotations.apply_labels(self.cmd, 'molecule within 1 of reference',
                                          template='atom', state=2)
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['targets'][0]['state'], 2)
        self.assertEqual(self.cmd.get_state(), 1)
        self.cmd.create('discrete', 'molecule and name N', 1, 1, discrete=1)
        self.cmd.create('discrete', 'molecule and name P', 1, 2, discrete=1)
        result = annotations.apply_labels(self.cmd, 'discrete', template='atom', state=2)
        self.assertEqual([(a['index'], a['name'], a['text']) for a in result['targets']], [(2, 'P', 'P')])
        self.assertEqual(self.labels()[('discrete', 1)], 'original')
        self.assertEqual(self.labels()[('discrete', 2)], 'P')

    def test_native_limit_accepts_500_and_rejects_501_without_truncation(self):
        load_atoms(self.cmd, 'many_atoms', [('X', 'C', 'LIG', str(i), '', '', '')
                                            for i in range(1, 502)])
        self.cmd.label('many_atoms', repr('before cap'))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'max_labels'):
            annotations.apply_labels(self.cmd, 'many_atoms', template='atom')
        self.assertEqual(self.snapshot(), before)
        result = annotations.apply_labels(self.cmd, 'many_atoms and index 1-500',
                                          template='atom', size=8, color='black')
        self.assertEqual(result['count'], 500)
        self.assertEqual(sum(text == 'X' for (obj, _), text in self.labels().items()
                             if obj == 'many_atoms'), 500)
        self.assertEqual(self.labels()[('many_atoms', 501)], 'before cap')
        self.assertEqual(self.cmd.get_setting_float('label_size', 'many_atoms', state=1), 8.)

    def test_controller_wrappers_and_real_pse_label_style_persistence(self):
        from molscene.desktop.controller import WorkbenchController
        with tempfile.TemporaryDirectory() as directory:
            controller = WorkbenchController(self.cmd, directory)
            preview = controller.preview_labels('molecule', template='chain_residue')
            result = controller.apply_label_recipe('molecule', template='chain_residue', size=21, color='cyan')
            self.assertEqual(result['targets'], preview['targets'])
            controller.clear_labels('molecule and name N')
            before = self.snapshot()
            other_state_size = self.cmd.get_setting_float('label_size', 'molecule', state=2)
            path = Path(directory) / 'labeled.pse'
            controller._save_snapshot(path)
            self.assertEqual(self.snapshot(), before)
            self.cmd.reinitialize()
            self.cmd.load(str(path), format='pse')
            self.assertEqual(self.labels(), before['labels'])
            self.assertEqual(self.styles(), before['styles'])
            self.assertEqual(self.cmd.get_setting_float('label_size', 'molecule', state=2), other_state_size)
            self.assertEqual(self.cmd.get_state(), before['frame'])
            for a, b in zip(self.cmd.get_view(), before['view']):
                self.assertAlmostEqual(a, b, places=5)
            controller.set_labels('molecule and name N', 'name')
            self.assertEqual(self.labels()[self.cmd.index('molecule and name N')[0]], 'N')
            with ThreadPoolExecutor(max_workers=1) as pool:
                with self.assertRaisesRegex(RuntimeError, 'main thread'):
                    pool.submit(controller.apply_label_recipe, 'molecule').result()


if __name__ == '__main__':
    unittest.main()
