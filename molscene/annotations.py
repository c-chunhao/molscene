"""Literal guided labels on frozen requested-state atom membership.

Only the explicitly chosen custom template executes a native expression. This
module is backend-lazy and uses an instance-bound public PyMOL cmd throughout.
"""
from collections import defaultdict
from contextlib import contextmanager
import math

from .analysis import collect_atoms


_IDENTITY = ('object', 'index', 'segi', 'chain', 'resi', 'resn', 'name',
             'element', 'alt', 'state')
_RESIDUE = ('object', 'segi', 'chain', 'resi', 'resn')


def _anchor_order(atom):
    kind = (0 if atom['name'] == 'CA' else 1 if atom['name'] == 'P' else
            2 if atom['element'].upper() not in ('H', 'D', 'T') else 3)
    # Anchor class takes precedence; within that class prefer blank, then A,
    # then the lowest native index among the remaining selected alternates.
    alternate = 0 if atom['alt'] == '' else 1 if atom['alt'] == 'A' else 2
    return kind, alternate, atom['index']


def label_plan(cmd, selection, *, template='residue', state=1, expression='name'):
    """Return count and exact identity targets without evaluating label text.

    Recipe targets include ``text``. Custom targets instead accompany the
    top-level unevaluated ``expression``. Neither case changes source labels,
    style, camera or frame; residue anchors never expand the selection.
    """
    if template not in ('atom', 'residue', 'chain_residue', 'custom'):
        raise ValueError('template must be atom, residue, chain_residue or custom')
    if template == 'custom' and not isinstance(expression, str):
        raise ValueError('custom expression must be a native expression string')
    atoms = collect_atoms(cmd, selection, state=state)
    if template in ('residue', 'chain_residue'):
        groups = {}
        for atom in atoms:
            key = tuple(atom[field] for field in _RESIDUE)
            if key not in groups or _anchor_order(atom) < _anchor_order(groups[key]):
                groups[key] = atom
        atoms = sorted(groups.values(), key=lambda atom: (atom['object'], atom['index']))
    targets = []
    for atom in atoms:
        target = {key: atom[key] for key in _IDENTITY}
        if template == 'atom':
            target['text'] = atom['name']
        elif template in ('residue', 'chain_residue'):
            text = atom['resn'] + atom['resi']
            if template == 'chain_residue':
                text = (atom['chain'] or '(blank)') + ':' + text
            target['text'] = text
        targets.append(target)
    result = dict(selection=selection, template=template, state=state,
                  count=len(targets), targets=targets)
    if template == 'custom':
        result['expression'] = expression
    return result


@contextmanager
def _bound(cmd, targets, state):
    """Select frozen indexes through the API, never interpolated source text."""
    name = cmd.get_unused_name('_molscene_labels')
    part = cmd.get_unused_name('_molscene_label_members')
    enabled = cmd.get_names('selections', enabled_only=1)
    groups = defaultdict(list)
    for atom in targets:
        groups[atom['object']].append(atom['index'])
    try:
        cmd.select(name, 'none', enable=0, quiet=1)
        for obj, indexes in groups.items():
            cmd.select_list(part, obj, indexes, state=state, mode='index', quiet=1)
            cmd.disable(part)
            cmd.select(name, '%' + name + ' or %' + part, state=state, enable=0, quiet=1)
        yield name
    finally:
        cmd.delete(part)
        cmd.delete(name)
        existing = set(cmd.get_names('selections'))
        for selection in enabled:
            if selection in existing:
                cmd.enable(selection)


def apply_labels(cmd, selection, *, template='residue', state=1, expression='name',
                 size=14, color='black', max_labels=500):
    """Preflight, then apply target text/color and shared object-state size.

    Native label rendering reads size at object/state scope, so every label
    there shares this size, including labels outside the selected membership.
    The returned size_scope/size_objects explicitly describe that side effect.
    """
    if (isinstance(size, bool) or not isinstance(size, (int, float))
            or not 8 <= size <= 48 or not math.isfinite(size)):
        raise ValueError('label size must be a finite positive number within 8..48 points')
    if type(max_labels) is not int or not 1 <= max_labels <= 500:
        raise ValueError('max_labels must be an integer within 1..500')
    if not isinstance(color, str) or not color.strip():
        raise ValueError('label color must be a native color name')
    try:
        resolved_color = cmd.get_color_index(color)
    except Exception as exc:
        raise ValueError('label color must resolve through the native color API') from exc
    if resolved_color < 0:
        raise ValueError('Unknown native label color')
    plan = label_plan(cmd, selection, template=template, state=state, expression=expression)
    if plan['count'] > max_labels:
        raise ValueError(f'Label count exceeds max_labels={max_labels}; no labels were changed')
    size_objects = sorted({target['object'] for target in plan['targets']})
    if template == 'custom':
        with _bound(cmd, plan['targets'], state) as bound:
            cmd.label(bound, expression)
    else:
        for target in plan['targets']:
            with _bound(cmd, [target], state) as bound:
                # repr is a Python string literal, including quotes, escapes
                # and Unicode. Source atom/residue text cannot become code.
                cmd.label(bound, repr(target['text']))
    with _bound(cmd, plan['targets'], state) as bound:
        cmd.set('label_color', resolved_color, bound)
    for obj in size_objects:
        cmd.set('label_size', size, obj, state=state)
    return dict(plan, size=size, color=resolved_color,
                size_scope='object_state', size_objects=size_objects)


def clear_labels(cmd, selection, *, state=1):
    """Clear labels on explicit selected members, preserving their styles."""
    atoms = collect_atoms(cmd, selection, state=state)
    targets = [{key: atom[key] for key in _IDENTITY} for atom in atoms]
    with _bound(cmd, targets, state) as bound:
        cmd.label(bound, '')
    return dict(selection=selection, state=state, count=len(targets), targets=targets)
