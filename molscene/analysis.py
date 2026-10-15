"""State-specific scientific operations on an instance-bound public PyMOL cmd.

No backend or GUI imports occur at module import time. Atom identities always
refer to the original object and atom index, including across temporary copies.
"""
import math
from collections import defaultdict
from contextlib import contextmanager
from itertools import product
from urllib.parse import quote


def _selection(selection):
    if not isinstance(selection, str) or not selection.strip() or any(c in selection for c in '\r\n\x00'):
        raise ValueError('selection must be a nonempty single-line PyMOL selection')
    return selection


@contextmanager
def _resolved_selection(cmd, selection, state, allow_empty=False):
    """Bind membership once in the requested state, independently of the view."""
    if type(state) is not int or state < 1:
        raise ValueError('state must be an explicit positive integer')
    _selection(selection)
    temporary = cmd.get_unused_name('_molscene_selection')
    try:
        try:
            # get_model(state=N) alone fixes only returned coordinates. Spatial
            # predicates must also resolve their membership in state N.
            count = cmd.select(temporary, '(' + selection + ')', state=state, enable=0, quiet=1)
            if not count and not allow_empty:
                raise ValueError(f'Selection has no atoms with coordinates in state {state}')
            for name in cmd.get_object_list(temporary):
                coords = cmd.get_coordset(name, state=state, copy=1)
                if coords is None or not len(coords):
                    raise ValueError(f'Object {name!r} has no coordinates in state {state}')
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError('Invalid selection or coordinate state: ' + str(exc)) from exc
        yield temporary
    finally:
        cmd.delete(temporary)


def validate_state(cmd, selection, state=1):
    """Require nonempty requested-state membership, without singleton fallback."""
    with _resolved_selection(cmd, selection, state):
        pass
    return state


def _identity(atom):
    return (atom['object'], atom['index'], atom['segi'], atom['chain'],
            atom['resi'], atom['resn'], atom['name'], atom['element'], atom['alt'], atom['state'])


def collect_atoms(cmd, selection='all', state=1):
    """Collect requested-state members; empty/absent-state selections fail."""
    return _collect_atoms(cmd, selection, state)


def _collect_atoms(cmd, selection, state, allow_empty=False):
    with _resolved_selection(cmd, selection, state, allow_empty) as resolved:
        try:
            atoms = cmd.get_model(resolved, state=state).atom
        except Exception as exc:
            raise ValueError('Invalid atom selection: ' + str(exc)) from exc
    rows = []
    for atom in atoms:
        coord = [float(x) for x in atom.coord]
        b, vdw = float(atom.b), float(atom.vdw)
        if len(coord) != 3 or not all(math.isfinite(x) for x in (*coord, b, vdw)):
            raise ValueError('Atom coordinates, B-factor and radius must be finite')
        rows.append(dict(object=atom.model, index=int(atom.index), segi=atom.segi,
                         chain=atom.chain, resi=atom.resi, resn=atom.resn, name=atom.name,
                         element=atom.symbol, alt=atom.alt, state=state,
                         coord=coord, b=b, vdw=vdw))
    return sorted(rows, key=_identity)


def _subtract(a, b):
    return [x - y for x, y in zip(a, b)]


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def geometry(cmd, kind, selections, state=1):
    """Distance, vertex angle, or signed PyMOL-convention dihedral."""
    counts = {'distance': 2, 'angle': 3, 'dihedral': 4}
    if kind not in counts:
        raise ValueError('kind must be distance, angle, or dihedral')
    if isinstance(selections, str) or len(selections) != counts[kind]:
        raise ValueError(f'{kind} requires {counts[kind]} single-atom selections')
    atoms = []
    for selection in selections:
        rows = collect_atoms(cmd, selection, state)
        if len(rows) != 1:
            raise ValueError(f'Each selection must match exactly one atom; matched {len(rows)}')
        atoms.append(rows[0])
    if len({_identity(atom) for atom in atoms}) != len(atoms):
        raise ValueError('Geometry requires distinct atoms')
    coords = [atom['coord'] for atom in atoms]
    if any(math.dist(a, b) <= 1e-12 for i, a in enumerate(coords) for b in coords[i + 1:]):
        raise ValueError('Geometry is undefined for repeated points')
    if kind == 'distance':
        value = math.dist(*coords)
    elif kind == 'angle':
        a, b = _subtract(coords[0], coords[1]), _subtract(coords[2], coords[1])
        cosine = _dot(a, b) / math.sqrt(_dot(a, a) * _dot(b, b))
        value = math.degrees(math.acos(max(-1.0, min(1.0, cosine))))
    else:
        a, b, c = (_subtract(coords[i + 1], coords[i]) for i in range(3))
        n1, n2 = _cross(a, b), _cross(b, c)
        if _dot(n1, n1) <= 1e-24 or _dot(n2, n2) <= 1e-24:
            raise ValueError('Dihedral is undefined for collinear points')
        value = math.degrees(math.atan2(_dot(_cross(n1, n2), b) / math.sqrt(_dot(b, b)), _dot(n1, n2)))
    if not math.isfinite(value):
        raise ValueError('Geometry must be finite')
    return dict(kind=kind, value=value, units='angstrom' if kind == 'distance' else 'degree',
                state=state, atoms=atoms, selection_state_policy='requested_state_only')


def _residue(atom):
    return {key: atom[key] for key in ('object', 'segi', 'chain', 'resi', 'resn')}


def _residue_key(atom):
    return tuple(atom[key] for key in ('object', 'segi', 'chain', 'resi', 'resn'))


def contacts(cmd, selection_a, selection_b, state=1, cutoff=4.0,
             heavy_only=True, max_pairs=100000):
    """Unique undirected atom-center contacts, bounded by a spatial grid and cap."""
    if isinstance(cutoff, bool) or not isinstance(cutoff, (int, float)) or not math.isfinite(cutoff) or cutoff <= 0:
        raise ValueError('cutoff must be a finite positive number')
    if type(max_pairs) is not int or not 1 <= max_pairs <= 100000:
        raise ValueError('max_pairs must be a positive integer no greater than 100000')
    if type(heavy_only) is not bool:
        raise ValueError('heavy_only must be a boolean')
    a, b = collect_atoms(cmd, selection_a, state), collect_atoms(cmd, selection_b, state)
    if heavy_only:
        a, b = ([row for row in rows if row['element'].upper() not in ('H', 'D', 'T')] for rows in (a, b))
    # Integer ratios also support extreme finite coordinate/cutoff combinations
    # without overflowing a float quotient when choosing grid cells.
    cn, cd = float(cutoff).as_integer_ratio()
    def cell(coord):
        result = []
        for value in coord:
            n, d = value.as_integer_ratio()
            result.append((n * cd) // (d * cn))
        return tuple(result)
    grid = defaultdict(list)
    for atom in b:
        grid[cell(atom['coord'])].append(atom)
    pairs, seen = [], set()
    for first in a:
        center = cell(first['coord'])
        for delta in product((-1, 0, 1), repeat=3):
            for second in grid.get(tuple(x + d for x, d in zip(center, delta)), ()):
                left, right = sorted((first, second), key=_identity)
                key = (_identity(left), _identity(right))
                if key[0] == key[1] or key in seen:
                    continue
                distance = math.dist(left['coord'], right['coord'])
                if distance <= cutoff:
                    if len(pairs) >= max_pairs:
                        raise ValueError(f'Contact count exceeds max_pairs={max_pairs}; no truncated result is returned')
                    seen.add(key)
                    pairs.append(dict(atom_a=left, atom_b=right, distance_angstrom=distance))
    pairs.sort(key=lambda p: (p['distance_angstrom'], _identity(p['atom_a']), _identity(p['atom_b'])))
    residues = {}
    for pair in pairs:
        left, right = sorted((pair['atom_a'], pair['atom_b']), key=_residue_key)
        key = (_residue_key(left), _residue_key(right))
        if key not in residues:
            residues[key] = dict(residue_a=_residue(left), residue_b=_residue(right),
                                 min_distance_angstrom=pair['distance_angstrom'], atom_pair_count=0)
        residues[key]['atom_pair_count'] += 1
    return dict(pair_count=len(pairs), pairs=pairs, residue_pairs=[residues[k] for k in sorted(residues)],
                definition='unique undirected atom-center distance <= cutoff; geometric proximity',
                state=state, cutoff_angstrom=float(cutoff), heavy_only=heavy_only,
                selection_state_policy='requested_state_only')


def sasa(cmd, selection='all', context=None, state=1, altloc='primary'):
    """SASA of target atoms in a merged context; source properties are untouched.

    Both target and context use non-solvent heavy atoms. ``primary`` retains
    blank/A alternate locations; ``all`` explicitly retains all conformers.
    The returned target contribution is always evaluated in the full context.
    """
    if altloc not in ('primary', 'all'):
        raise ValueError('altloc must be primary or all')
    raw_target = collect_atoms(cmd, selection, state)
    context_selection = context if context is not None else 'byobject (' + selection + ')'
    raw_context = collect_atoms(cmd, context_selection, state)
    def filtered(expression):
        rows = collect_atoms(cmd, '(' + expression + ') and not solvent and not elem H+D+T', state)
        return [a for a in rows if altloc == 'all' or a['alt'] in ('', 'A')]
    target, surrounding = filtered(selection), filtered(context_selection)
    if not target or not surrounding:
        raise ValueError('SASA requires nonempty target and context after filtering')
    wanted = {_identity(a) for a in target}
    available = {_identity(a) for a in surrounding}
    if not wanted <= available:
        raise ValueError('SASA target must be a subset of the filtered context')
    # Build a single fresh public ChemPy model with a unique residue token for
    # each source atom. Public load_model renumbers IDs, and may reorder atoms;
    # neither IDs nor list position can identify the source after importing.
    from chempy import Atom
    from chempy.models import Indexed
    model = Indexed()
    source_by_id = {}
    for identifier, source in enumerate(surrounding, 1):
        atom = Atom()
        atom.coord = list(source['coord'])
        atom.symbol, atom.vdw = source['element'], source['vdw']
        atom.name, atom.resn, atom.resi = 'C', 'TMP', str(identifier)
        model.atom.append(atom)
        source_by_id[identifier] = source
    temporary = cmd.get_unused_name('_molscene_sasa')
    try:
        cmd.load_model(model, temporary, state=1, zoom=0, quiet=1)
        # Imported nonpolymer atoms can acquire the backend's ignore flag.
        # The numerical context includes every retained atom, regardless of any
        # source display/ignore flags, so clear it only on the temporary copy.
        cmd.flag('ignore', temporary, 'clear')
        # All computation settings are local to the disposable object. Global
        # and original per-object settings need neither mutation nor restoration.
        for key, value in (('dot_solvent', 1), ('solvent_radius', 1.4), ('dot_density', 3)):
            cmd.set(key, value, temporary)
        cmd.get_area(temporary, state=1, load_b=1, quiet=1)
        areas = {}
        for atom in cmd.get_model(temporary, state=1).atom:
            value = float(atom.b)
            if not math.isfinite(value) or value < 0:
                raise ValueError('Backend returned a nonfinite or negative SASA')
            areas[int(atom.resi)] = value
        if set(areas) != set(source_by_id):
            raise RuntimeError('SASA temporary atom mapping is incomplete')
        residues = {}
        selected_areas = []
        for identifier, source in source_by_id.items():
            if _identity(source) not in wanted:
                continue
            value = areas[identifier]
            selected_areas.append(value)
            key = _residue_key(source)
            if key not in residues:
                residues[key] = dict(_residue(source), state=state, area_angstrom2=0.0, atom_count=0)
            residues[key]['area_angstrom2'] += value
            residues[key]['atom_count'] += 1
        return dict(total_area_angstrom2=math.fsum(selected_areas),
                    context_area_angstrom2=math.fsum(areas.values()),
                    residues=[residues[k] for k in sorted(residues)],
                    selected_atom_count=len(target), context_atom_count=len(surrounding),
                    selection_state_policy='requested_state_only',
                    parameters=dict(dot_solvent=1, solvent_radius=1.4, dot_density=3,
                                    altloc=altloc, state=state, heavy_only=True, exclude_solvent=True,
                                    selected_input_atom_count=len(raw_target),
                                    context_input_atom_count=len(raw_context),
                                    selected_filtered_atom_count=len(raw_target) - len(target),
                                    context_filtered_atom_count=len(raw_context) - len(surrounding)))
    finally:
        cmd.delete(temporary)


_RESIDUE_CODES = dict(zip(
    ('ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL').split(),
    'ARNDCQEGHILKMFPSTWYV'))
_RESIDUE_CODES.update({'MSE': 'M', 'SEC': 'U', 'PYL': 'O',
                       'A': 'A', 'C': 'C', 'G': 'G', 'U': 'U', 'T': 'T',
                       'DA': 'A', 'DC': 'C', 'DG': 'G', 'DT': 'T', 'DU': 'U'})


def _site_key(atom):
    return tuple(atom[key] for key in ('object', 'segi', 'chain', 'resi'))


def _chain_key(atom):
    return tuple(atom[key] for key in ('object', 'segi', 'chain'))


def _chain_id(key):
    # Empty segi/chain remain explicit and delimiters inside identifiers cannot
    # create collisions. This identifier is also safe as a FASTA header token.
    return '|'.join(quote(component, safe='') for component in key)


def inspect_structure(cmd, selection='all', state=1):
    """Coordinate-only overview and guide-atom sequences, with source mapping."""
    atoms = collect_atoms(cmd, selection, state)
    guide = _collect_atoms(cmd, '(' + selection + ') and guide', state, allow_empty=True)
    protein = _collect_atoms(cmd, '(' + selection + ') and polymer.protein', state, allow_empty=True)
    nucleic = _collect_atoms(cmd, '(' + selection + ') and polymer.nucleic', state, allow_empty=True)
    elements, objects, chains = defaultdict(int), defaultdict(list), defaultdict(list)
    for atom in atoms:
        elements[atom['element']] += 1
        objects[atom['object']].append(atom)
        chains[_chain_key(atom)].append(atom)
    protein_sites = {_site_key(a) for a in protein}
    nucleic_sites = {_site_key(a) for a in nucleic}
    chain_rows = []
    for key, members in sorted(chains.items()):
        sites = {_site_key(a) for a in members}
        chain_rows.append(dict(object=key[0], segi=key[1], chain=key[2], id=_chain_id(key),
                               atom_count=len(members), residue_site_count=len(sites),
                               protein_residue_site_count=len(sites & protein_sites),
                               nucleic_residue_site_count=len(sites & nucleic_sites)))
    # One coordinate residue per site, preferring blank alt then A. Distinct
    # residue names/alternate guide atoms do not duplicate sequence positions.
    guides = {}
    for atom in guide:
        if atom['alt'] not in ('', 'A'):
            continue
        site = _site_key(atom)
        if site not in guides or (atom['alt'], atom['index']) < (guides[site]['alt'], guides[site]['index']):
            guides[site] = atom
    chain_guides = defaultdict(list)
    for atom in sorted(guides.values(), key=_identity):
        chain_guides[_chain_key(atom)].append(atom)
    sequences = []
    for key, members in sorted(chain_guides.items()):
        residue_map = []
        letters = []
        for position, atom in enumerate(members, 1):
            code = _RESIDUE_CODES.get(atom['resn'].upper(), 'X')
            letters.append(code)
            residue_map.append(dict(_residue(atom), position=position, code=code,
                                    index=atom['index'], name=atom['name'], alt=atom['alt'], state=state))
        sequences.append(dict(id=_chain_id(key), object=key[0], segi=key[1], chain=key[2],
                              sequence=''.join(letters), residue_map=residue_map))
    object_rows = [dict(object=name, state=state, atom_count=len(members),
                        residue_site_count=len({_site_key(a) for a in members}),
                        chain_count=len({_chain_key(a) for a in members}))
                   for name, members in sorted(objects.items())]
    return dict(atom_count=len(atoms), residue_site_count=len({_site_key(a) for a in atoms}),
                residue_name_count=len({_residue_key(a) for a in atoms}),
                protein_residue_site_count=len(protein_sites),
                nucleic_residue_site_count=len(nucleic_sites),
                elements=dict(sorted(elements.items())), objects=object_rows, chains=chain_rows,
                sequences=sequences, state=state, selection_state_policy='requested_state_only')
