"""Qt-free, state-bound live structure workflows using public PyMOL commands.

Residue labels are data, never selection-language fragments. Every display or
copy operation freezes original object/index membership before acting on it.
"""
from collections import defaultdict
from contextlib import contextmanager
import math
import re

from molscene.analysis import collect_atoms, _RESIDUE_CODES


IDENTITY_FIELDS = ('object', 'index', 'segi', 'chain', 'resi', 'resn', 'name',
                   'element', 'alt', 'state')
RESIDUE_FIELDS = ('object', 'segi', 'chain', 'resi', 'resn')
_RESERVED = frozenset(('all none sele auto new same origin center enabled visible '
                       'present polymer protein nucleic solvent organic inorganic '
                       'hydrogens hetatm guide backbone sidechain byres byobject '
                       'and or not pk1 pk2 pk3 pk4 pkset pkresi pkchain pkobject').split())


def _identity(atom):
    return {key: atom[key] for key in IDENTITY_FIELDS}


def _key(atom):
    return atom['object'], atom['index']


def _residue_key(atom):
    return tuple(atom[key] for key in RESIDUE_FIELDS)


def _state(state):
    if type(state) is not int or state < 1:
        raise ValueError('State must be an explicit positive integer')


def _boolean(value, name):
    if type(value) is not bool:
        raise ValueError(name + ' must be a boolean')


def _number(value, name, *, positive=False):
    if (isinstance(value, bool) or not isinstance(value, (float, int))
            or not math.isfinite(value) or (value <= 0 if positive else value < 0)):
        raise ValueError(name + ' must be finite and ' + ('positive' if positive else 'nonnegative'))


def _name(cmd, name):
    if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name)
            or name.lower() in _RESERVED or name.lower().startswith('_molscene')
            or cmd.get_legal_name(name) != name):
        raise ValueError('Use a nonreserved name containing letters, digits or underscores, starting with a letter or underscore')
    if name in cmd.get_names('all'):
        raise ValueError('That object or selection name already exists')


@contextmanager
def _bound(cmd, atoms, state):
    """Use select_list for object identifiers, including imported unusual names."""
    name = cmd.get_unused_name('_molscene_structure')
    part = cmd.get_unused_name('_molscene_members')
    enabled = cmd.get_names('selections', enabled_only=1)
    groups = defaultdict(list)
    for atom in atoms:
        groups[atom['object']].append(atom['index'])
    try:
        cmd.select(name, 'none', enable=0, quiet=1)
        for obj, indices in groups.items():
            cmd.select_list(part, obj, indices, state=state, mode='index', quiet=1)
            cmd.disable(part)
            cmd.select(name, '%' + name + ' or %' + part, state=state, enable=0, quiet=1)
        yield name
    finally:
        cmd.delete(part)
        cmd.delete(name)
        # select_list/create can trigger PyMOL's auto_hide_selections. Restore
        # existing selection visibility without changing that global setting.
        existing = set(cmd.get_names('selections'))
        for selection in enabled:
            if selection in existing:
                cmd.enable(selection)


def _matching(cmd, bound, predicate):
    return set(cmd.index('(%' + bound + ') and (' + predicate + ')'))


def _summary(atoms, state):
    return dict(atom_count=len(atoms), residue_count=len({_residue_key(a) for a in atoms}),
                state=state, objects=sorted({a['object'] for a in atoms}))


def residues(cmd, selection='all', *, state=1):
    atoms = collect_atoms(cmd, selection, state)
    with _bound(cmd, atoms, state) as bound:
        protein = _matching(cmd, bound, 'polymer.protein')
        nucleic = _matching(cmd, bound, 'polymer.nucleic')
        guides = _matching(cmd, bound, 'guide')
    # Object source order and atom coordinate order, never lexical residue order.
    order = {name: i for i, name in enumerate(cmd.get_object_list('all'))}
    groups = {}
    for atom in sorted(atoms, key=lambda a: (order[a['object']], a['index'])):
        groups.setdefault(_residue_key(atom), []).append(atom)
    rows = []
    for key, members in groups.items():
        keys = {_key(a) for a in members}
        kind = 'protein' if keys & protein else 'nucleic' if keys & nucleic else 'other'
        rows.append(dict(zip(RESIDUE_FIELDS, key), state=state, atom_count=len(members),
                         polymer=kind, one_letter=_RESIDUE_CODES.get(key[-1].upper(), '')
                         if kind != 'other' and keys & guides else '',
                         atoms=[_identity(a) for a in members]))
    return rows


def _capture(cmd, name, atoms, source, state):
    try:
        with _bound(cmd, atoms, state) as bound:
            cmd.select(name, '%' + bound, state=state, enable=1, quiet=1)
    except Exception:
        cmd.delete(name)
        raise
    return dict(selection=name, source=source, state=state, atoms=len(atoms),
                objects=sorted({a['object'] for a in atoms}), identities=[_identity(a) for a in atoms])


def select_residues(cmd, rows, *, state=1):
    _state(state)
    rows = list(rows)
    if not rows:
        raise ValueError('Choose one or more observed residue rows')
    wanted = {}
    try:
        for row in rows:
            if row['state'] != state or row['atom_count'] != len(row['atoms']) or not row['atoms']:
                raise ValueError('Residue rows must match the requested state and original membership')
            for atom in row['atoms']:
                if (atom['state'] != state or _residue_key(atom) != _residue_key(row)
                        or type(atom['index']) is not int or atom['index'] < 1):
                    raise ValueError('Residue row identity does not match its atoms')
                identity = _identity(atom)
                if _key(atom) in wanted and wanted[_key(atom)] != identity:
                    raise ValueError('Conflicting original atom identities')
                wanted[_key(atom)] = identity
    except (KeyError, TypeError) as exc:
        raise ValueError('Choose valid observed residue rows') from exc
    try:
        with _bound(cmd, wanted.values(), state) as bound:
            current = collect_atoms(cmd, '%' + bound, state)
    except Exception as exc:
        raise ValueError('Residue membership or state has changed; refresh the browser') from exc
    if {_key(a): _identity(a) for a in current} != wanted:
        raise ValueError('Residue membership or identity has changed; refresh the browser')
    return _capture(cmd, cmd.get_unused_name('molscene_capture'), current, 'residue_browser', state)


def selection_summary(cmd, selection, *, state=1):
    return _summary(collect_atoms(cmd, selection, state), state)


def save_selection(cmd, name, selection, *, state=1):
    _name(cmd, name)
    atoms = collect_atoms(cmd, selection, state)
    return _capture(cmd, name, atoms, selection, state)


def delete_selection(cmd, name):
    if (not isinstance(name, str) or name not in cmd.get_names('selections')
            or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name)
            or name.lower() in _RESERVED or name.lower().startswith('_molscene')):
        raise ValueError('Choose an existing user selection; native picks and control selections are protected')
    cmd.delete(name)


def focus_selection(cmd, selection, *, state=1, buffer=3.0, orient=True):
    _number(buffer, 'Zoom buffer')
    _boolean(orient, 'orient')
    atoms = collect_atoms(cmd, selection, state)
    with _bound(cmd, atoms, state) as bound:
        if orient:
            cmd.orient(bound, state=state, animate=0)
        cmd.center(bound, state=state, animate=0)
        cmd.zoom(bound, buffer=buffer, state=state, animate=0)
    return dict(_summary(atoms, state), buffer=float(buffer), orient=orient)


def hide_context(cmd, selection, *, state=1, solvent=True, hydrogens=True):
    _boolean(solvent, 'solvent')
    _boolean(hydrogens, 'hydrogens')
    atoms = collect_atoms(cmd, selection, state)
    with _bound(cmd, atoms, state) as bound:
        water = _matching(cmd, bound, 'solvent') if solvent else set()
        hydrogen = {_key(a) for a in atoms if a['element'].upper() in ('H', 'D', 'T')} if hydrogens else set()
        hidden = [a for a in atoms if _key(a) in water | hydrogen]
        if hidden:
            with _bound(cmd, hidden, state) as target:
                cmd.hide('everything', target)
    return dict(_summary(atoms, state), solvent_atom_count=len(water),
                hydrogen_atom_count=len(hydrogen), hidden_atom_count=len(water | hydrogen))


def apply_preset(cmd, selection, preset, *, state=1, ligand=None, radius=5.0, frame=True):
    if preset not in ('overview', 'surface', 'pocket'):
        raise ValueError('Preset must be overview, surface or pocket')
    _boolean(frame, 'frame')
    _number(radius, 'Pocket radius', positive=True)
    atoms = collect_atoms(cmd, selection, state)
    with _bound(cmd, atoms, state) as bound:
        polymer = _matching(cmd, bound, 'polymer')
        guide = _matching(cmd, bound, 'guide')
        ions = _matching(cmd, bound, 'inorganic')
        guide_sites = {_residue_key(a) for a in atoms if _key(a) in guide}
        trace = [a for a in atoms if _key(a) in polymer and _residue_key(a) in guide_sites]
        ligands, pocket, markers = [], [], []
        if preset == 'pocket':
            ligands = collect_atoms(cmd, ligand, state)
            ligand_keys = {_key(a) for a in ligands}
            if not ligand_keys <= {_key(a) for a in atoms}:
                raise ValueError('Ligand must be a nonempty subset of the selected context')
            candidates = [a for a in atoms if _key(a) in polymer and _key(a) not in ligand_keys]
            sites = {_residue_key(a) for a in candidates
                     if any(math.dist(a['coord'], b['coord']) <= radius for b in ligands)}
            pocket = [a for a in candidates if _residue_key(a) in sites]
            if not pocket:
                raise ValueError('No nearby selected polymer residue within the pocket radius')
        # No scene mutation occurs until every parameter and derived subset passed.
        cmd.hide('everything', bound)
        if preset == 'surface':
            cmd.show('surface', bound)
        else:
            if trace:
                with _bound(cmd, trace, state) as target:
                    cmd.show('cartoon', target)
            traced_keys = {_key(t) for t in trace}
            sticks = ([a for a in atoms if _key(a) not in traced_keys | ions]
                      if preset == 'overview' else pocket + ligands)
            if sticks:
                with _bound(cmd, sticks, state) as target:
                    cmd.show('sticks', target)
            # A guide/representation flag does not guarantee drawable cartoon
            # geometry, and sticks alone cannot draw an unbonded atom. Keep
            # full polymers readable while making every selected trace site
            # and stick atom visible without guessing backbone connectivity.
            marker_keys = (traced_keys & guide) | {_key(a) for a in sticks}
            markers = [a for a in atoms if _key(a) in marker_keys]
            if markers:
                with _bound(cmd, markers, state) as target:
                    cmd.set('sphere_scale', 0.20, target)
                    cmd.show('spheres', target)
            ion_atoms = [a for a in atoms if _key(a) in ions]
            if ion_atoms and preset == 'overview':
                with _bound(cmd, ion_atoms, state) as target:
                    cmd.show('spheres', target)
        if frame:
            with _bound(cmd, pocket + ligands if preset == 'pocket' else atoms, state) as target:
                cmd.zoom(target, buffer=3.0, state=state, animate=0)
    result = dict(preset=preset, selection=selection, state=state, frame=frame,
                  selected_atom_count=len(atoms),
                  selected_residue_count=len({_residue_key(a) for a in atoms}),
                  marker_atom_count=len(markers),
                  display_parameters={'marker_sphere_scale': 0.20} if preset != 'surface' else {},
                  definition='Display only frozen selected atoms at the requested state')
    if preset == 'pocket':
        result.update(ligand=ligand, radius_angstrom=float(radius),
                      ligand_atom_count=len(ligands), ligand_residue_count=len({_residue_key(a) for a in ligands}),
                      pocket_atom_count=len(pocket), pocket_residue_count=len({_residue_key(a) for a in pocket}),
                      definition='Selected polymer residues with an atom-center distance <= radius from a ligand atom; '
                                 'expand only within selected requested-state atoms, excluding ligand atoms')
    return result


def prepare_structure(cmd, selection, *, object_name, state=1, remove_solvent=True,
                      remove_hydrogens=False, altloc='primary'):
    _name(cmd, object_name)
    _boolean(remove_solvent, 'remove_solvent')
    _boolean(remove_hydrogens, 'remove_hydrogens')
    if altloc not in ('primary', 'all'):
        raise ValueError('altloc must be primary or all')
    atoms = collect_atoms(cmd, selection, state)
    with _bound(cmd, atoms, state) as bound:
        solvent = _matching(cmd, bound, 'solvent') if remove_solvent else set()
    retained = [a for a in atoms if _key(a) not in solvent]
    removed = dict(solvent=len(atoms) - len(retained))
    stage = [a for a in retained if not remove_hydrogens or a['element'].upper() not in ('H', 'D', 'T')]
    removed['hydrogens'] = len(retained) - len(stage)
    retained = [a for a in stage if altloc == 'all' or a['alt'] in ('', 'A')]
    removed['altloc'] = len(stage) - len(retained)
    if not retained:
        raise ValueError('Preparation would retain no atoms')
    try:
        with _bound(cmd, retained, state) as bound:
            cmd.create(object_name, bound, source_state=state, target_state=1, zoom=0, quiet=1)
        if cmd.count_atoms('%' + object_name) != len(retained):
            raise RuntimeError('Backend did not create the complete cleaned object')
    except Exception:
        cmd.delete(object_name)
        raise
    return dict(object=object_name, state=1, source_state=state, selected_atom_count=len(atoms),
                retained_atom_count=len(retained), removed=removed,
                identities=[_identity(a) for a in retained],
                parameters=dict(selection=selection, object_name=object_name, state=state,
                                remove_solvent=remove_solvent, remove_hydrogens=remove_hydrogens,
                                altloc=altloc))


def _scene(cmd, name):
    if not isinstance(name, str) or name not in cmd.get_scene_list() or name in ('auto', 'new', '*'):
        raise ValueError('Choose an existing scene bookmark')


def update_scene(cmd, name):
    _scene(cmd, name)
    cmd.scene(name, 'update')


def rename_scene(cmd, name, new_name):
    _scene(cmd, name)
    if (not isinstance(new_name, str) or not new_name.strip() or new_name in ('auto', 'new', '*')
            or any(c in new_name for c in '\r\n\x00')):
        raise ValueError('Enter a nonreserved scene bookmark name')
    if new_name in cmd.get_scene_list():
        raise ValueError('Scene bookmark name already exists')
    cmd.scene(name, 'rename', new_key=new_name)
