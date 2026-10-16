"""Bounded PyMOL-typed polar-contact candidates (not confirmed H bonds)."""
from collections import defaultdict
from contextlib import ExitStack
import hashlib
import json
import math

from . import analysis


def _key(atom):
    return atom['object'], atom['index'], atom['state']


def _present_objects(cmd, state):
    if type(state) is not int or state < 1:
        raise ValueError('state must be an explicit positive integer')
    for name in sorted(cmd.get_object_list('all')):
        coords = cmd.get_coordset(name, state=state, copy=1)
        if coords is not None and len(coords):
            yield name


def _chemistry(cmd, state):
    """Include complete source topology: neighbors can influence native orientation."""
    rows, bonds = [], set()
    for name in _present_objects(cmd, state):
        expression = '%' + name
        atoms = analysis.collect_atoms(cmd, expression, state)
        model = cmd.get_model(expression, state=state)
        donors = set(cmd.index('(' + expression + ') and donors'))
        acceptors = set(cmd.index('(' + expression + ') and acceptors'))
        properties = {a.index: a for a in model.atom}
        for atom in atoms:
            source = properties[atom['index']]
            rows.append(dict(atom, formal_charge=int(source.formal_charge),
                             donor=(name, atom['index']) in donors,
                             acceptor=(name, atom['index']) in acceptors))
        for bond in model.bond:
            left, right = sorted(model.atom[i].index for i in bond.index)
            bonds.add((name, left, right, int(bond.order)))
    return dict(state=state, atoms=rows, bonds=sorted(bonds))


def chemistry_fingerprint(cmd, state=1):
    """Explicit-state coordinates, native roles, formal charges and bond topology.

    Molecules absent from this state are excluded; camera, colors and the
    displayed frame are irrelevant. All neighbors in present molecules count.
    """
    payload = json.dumps(_chemistry(cmd, state), sort_keys=True, separators=(',', ':'),
                         ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(payload.encode('utf8')).hexdigest()


def _validate(state, cutoff, angle, altloc, max_pairs):
    if type(state) is not int or state < 1:
        raise ValueError('state must be an explicit positive integer')
    for name, value, lower, upper in (('cutoff', cutoff, .1, 10), ('angle', angle, 0, 90)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lower <= value <= upper:
            raise ValueError(f'{name} must be finite and within {lower}..{upper}')
    if altloc not in ('primary', 'all'):
        raise ValueError('altloc must be primary or all')
    if type(max_pairs) is not int or not 1 <= max_pairs <= 100000:
        raise ValueError('max_pairs must be an integer within 1..100000')


def polar_contacts(cmd, selection_a, selection_b, *, state=1, cutoff=3.5,
                   angle=45.0, altloc='primary', max_pairs=100000):
    """Query native typed donor->acceptor candidates with frozen state membership."""
    _validate(state, cutoff, angle, altloc, max_pairs)
    with ExitStack() as stack:
        groups, endpoints, selections, counts = [], [], [], {}
        def bind(rows):
            by_object = defaultdict(list)
            for atom in rows:
                by_object[atom['object']].append(atom['index'])
            names = []
            for obj, indexes in sorted(by_object.items()):
                name = cmd.get_unused_name('_molscene_polar')
                stack.callback(cmd.delete, name)
                cmd.select_list(name, obj, indexes, state=state, mode='index', quiet=1)
                names.append(name)
            return ' or '.join(names) if names else 'none'
        for side, expression in zip(('A', 'B'), (selection_a, selection_b)):
            frozen = stack.enter_context(analysis._resolved_selection(cmd, expression, state))
            original = analysis.collect_atoms(cmd, frozen, state)
            retained = [a for a in original if a['element'].upper() not in ('H', 'D', 'T')
                        and (altloc == 'all' or a['alt'] in ('', 'A'))]
            roles = {}
            for role in ('donors', 'acceptors'):
                native = set(cmd.index('(' + frozen + ') and ' + role))
                roles[role] = [a for a in retained if (a['object'], a['index']) in native]
            typed = {_key(a): a for rows in roles.values() for a in rows}
            groups.append({_key(a) for a in original})
            endpoints.append(typed)
            selections.append({role: bind(rows) for role, rows in roles.items()})
            counts[side] = dict(input_atoms=len(original), retained_heavy_atoms=len(retained),
                                filtered_atoms=len(original)-len(retained),
                                typed_heavy_atoms=len(typed), nonpolar_heavy_atoms=len(retained)-len(typed),
                                donors=len(roles['donors']), acceptors=len(roles['acceptors']))
        # Reuse the spatial-grid conservative inclusive cap, before native queries.
        candidates = 0
        if endpoints[0] and endpoints[1]:
            candidates = analysis.contacts(cmd, bind(endpoints[0].values()), bind(endpoints[1].values()),
                                           state=state, cutoff=cutoff, max_pairs=max_pairs)['pair_count']
        chemistry = _chemistry(cmd, state)
        direct = {(name, left, right) for name, left, right, order in chemistry['bonds']}
        atoms = {key: atom for side in endpoints for key, atom in side.items()}
        pairs = {}
        for first, second in ((0, 1), (1, 0)):
            if not counts['AB'[first]]['donors'] or not counts['AB'[second]]['acceptors']:
                continue
            for donor_id, acceptor_id in cmd.find_pairs(selections[first]['donors'], selections[second]['acceptors'],
                    state1=state, state2=state, cutoff=cutoff, mode=1, angle=angle):
                donor_key, acceptor_key = (*donor_id, state), (*acceptor_id, state)
                if donor_key == acceptor_key:
                    continue
                donor, acceptor = atoms[donor_key], atoms[acceptor_key]
                if donor['object'] == acceptor['object']:
                    left, right = sorted((donor['index'], acceptor['index']))
                    if (donor['object'], left, right) in direct:
                        continue
                    if donor['alt'] and acceptor['alt'] and donor['alt'] != acceptor['alt']:
                        continue
                key = donor_key, acceptor_key
                if key in pairs:
                    continue
                if len(pairs) >= max_pairs:
                    raise ValueError(f'Polar directed count exceeds max_pairs={max_pairs}; no truncated result is returned')
                pairs[key] = dict(donor=donor, acceptor=acceptor,
                                  distance_angstrom=math.dist(donor['coord'], acceptor['coord']),
                                  donor_membership=[label for label, group in zip('AB', groups) if donor_key in group],
                                  acceptor_membership=[label for label, group in zip('AB', groups) if acceptor_key in group])
        rows = sorted(pairs.values(), key=lambda p: (p['distance_angstrom'], analysis._identity(p['donor']), analysis._identity(p['acceptor'])))
        fingerprint = chemistry_fingerprint(cmd, state)
        return dict(kind='polar', pair_count=len(rows), pairs=rows, candidate_count=candidates,
                    state=state, cutoff_angstrom=float(cutoff), angle_degrees=float(angle), altloc=altloc,
                    max_pairs=max_pairs, candidate_pair_cap=max_pairs, directed_pair_cap=max_pairs,
                    candidate_definition='unique undirected non-self typed-heavy A/B pairs at distance <= cutoff, before orientation, alt-pair and direct-bond exclusions',
                    output_definition='unique directed donor->acceptor pairs after native orientation, self, alt-pair and direct-bond exclusions',
                    native_role_counts=counts, pymol_version=cmd.get_version()[0],
                    chemistry_fingerprint=fingerprint, selection_state_policy='requested_state_only',
                    method='PyMOL native donors/acceptors; find_pairs(mode=1) in both A/B directions',
                    cutoff_convention='strict distance < cutoff; preflight distance <= cutoff',
                    definition='unique directed PyMOL-typed polar-contact candidates',
                    limitations=['Native coarse endpoint orientation tolerance, not a measured D-H-A angle.',
                                 'Candidates depend on existing topology/typing; not confirmed hydrogen bonds, protonation, binding energy or affinity.',
                                 'Different nonblank alt labels within one object are excluded; cross-object labels do not establish correlated conformers.'])
