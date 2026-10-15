"""Shared display operations on cmd or the engine's literal-call recorder.

Callers bind spatial selections to a state before applying these operations.
The live desktop passes its current selection; no coordinate state is changed.
"""
import math

REPRESENTATIONS = ('cartoon', 'ribbon', 'sticks', 'lines', 'spheres', 'surface', 'mesh', 'dots', 'nonbonded')
COLORINGS = ('chain', 'element', 'single', 'rainbow', 'bfactor')
COLORS = ('marine', 'teal', 'violetpurple', 'salmon', 'wheat', 'lightblue', 'lime')


def apply_representation(cmd, selection, representation):
    if representation not in REPRESENTATIONS:
        raise ValueError('Unsupported representation: ' + str(representation))
    if not cmd.count_atoms(selection):
        raise ValueError('Representation selection must match at least one atom')
    if representation in ('cartoon', 'ribbon'):
        polymer = '(' + selection + ') and polymer'
        if not cmd.count_atoms('(' + polymer + ') and guide'):
            raise ValueError('Cartoon/ribbon requires renderable polymer guide atoms')
        selection = polymer
        for name in cmd.get_object_list(selection):
            protein = '(' + name + ') and polymer.protein'
            ca_only = cmd.count_atoms(protein) > 0 and cmd.count_atoms('(' + protein + ') and not name CA') == 0
            cmd.set('cartoon_trace_atoms', int(ca_only), name)
            cmd.set('ribbon_trace_atoms', int(ca_only), name)
    cmd.show(representation, selection)
    return dict(representation=representation, atom_count=cmd.count_atoms(selection))


def apply_coloring(cmd, selection, coloring='chain', color='marine'):
    if coloring not in COLORINGS:
        raise ValueError('Unsupported coloring: ' + str(coloring))
    if not cmd.count_atoms(selection):
        raise ValueError('Coloring selection must match at least one atom')
    metadata = dict(mode=coloring)
    if coloring == 'single':
        cmd.color(color, selection)
        metadata['color'] = color
    elif coloring == 'chain':
        rows = []
        cmd.iterate(selection, 'rows.append((model, index, segi, chain))', space={'rows': rows})
        keys = {(model, segi, chain) for model, _, segi, chain in rows}
        # Do not interpolate user chain/segi values into a selection grammar.
        # Public select_list uses atom indexes obtained from the already bound selection.
        for i, key in enumerate(sorted(keys)):
            indices = [index for model, index, segi, chain in rows if (model, segi, chain) == key]
            temporary = cmd.get_unused_name('_molscene_chain')
            try:
                cmd.select_list(temporary, key[0], indices, mode='index')
                cmd.color(COLORS[i % len(COLORS)], temporary)
            finally:
                cmd.delete(temporary)
        metadata['chain_count'] = len(keys)
    elif coloring == 'element':
        for element, value in (('C', 'gray70'), ('N', 'blue'), ('O', 'red'), ('S', 'yellow'),
                               ('P', 'orange'), ('H', 'white')):
            cmd.color(value, '(' + selection + ') and elem ' + element)
    else:
        if coloring == 'bfactor':
            values = []
            cmd.iterate(selection, 'values.append(float(b))', space={'values': values})
            if not values or not all(math.isfinite(x) for x in values):
                raise ValueError('B-factors must be finite')
            metadata['range'] = [min(values), max(values)]
            cmd.spectrum('b', 'blue_white_red', selection, minimum=min(values), maximum=max(values))
        else:
            cmd.spectrum('count', 'rainbow', selection)
    return metadata
