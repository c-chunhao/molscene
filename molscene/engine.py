"""Scene construction, geometry, and portable output; PyMOL imports are lazy."""
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import tempfile

from . import __version__
from . import analysis as science, artifacts, styles
from .snapshot import scene_fingerprint, record_snapshot_status

_FILES = artifacts.FILES
_SUFFIXES = {'.pdb', '.cif', '.mmcif'}
_INPUT_NAMES = artifacts.INPUT_NAMES


def _runtime():
    try:
        import pymol2
        return pymol2
    except ImportError as exc:
        raise RuntimeError('PyMOL is unavailable in this Python. Run with your existing '
                           'PyMOL Python interpreter, or install an open-source PyMOL backend.') from exc


def doctor():
    """Start a normal isolated context and report the active interpreter/backend."""
    try:
        with _runtime().PyMOL() as pm:
            version = pm.cmd.get_version()[0]
        return {'tool': 'molscene', 'tool_version': __version__, 'python': sys.version.split()[0],
                'executable': sys.executable, 'pymol_version': version, 'ok': True}
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError('Could not start the PyMOL context: ' + str(exc)) from exc


def _input(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ValueError('Input does not exist or is not a regular file: ' + str(path))
    if path.suffix.lower() not in _SUFFIXES:
        raise ValueError('Input must be PDB or mmCIF (.pdb, .cif, .mmcif): ' + str(path))
    if not path.stat().st_size:
        raise ValueError('Input structure is empty: ' + str(path))
    return path


def _dimensions(width, height, dpi):
    for name, value, maximum in (('width', width, 16384), ('height', height, 16384), ('dpi', dpi, 2400)):
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f'{name} must be an integer between 1 and {maximum}')
    if width * height > 64_000_000:
        raise ValueError('Image exceeds 64 million pixels')


def _selection(value, name):
    if not isinstance(value, str) or not value.strip() or any(c in value for c in '\r\n\x00'):
        raise ValueError(name + ' must be a nonempty single-line PyMOL selection')
    return value


_no_link = artifacts.no_link
_output = artifacts.output


class _Scene:
    """Record literal API calls while executing against an isolated cmd instance."""
    def __init__(self, cmd):
        self.cmd = cmd
        self.lines = []

    def call(self, method, *args, **kwargs):
        literal = ', '.join([*(ascii(x) for x in args), *(k + '=' + ascii(v) for k, v in kwargs.items())])
        self.lines.append('cmd.' + method + '(' + literal + ')')
        return getattr(self.cmd, method)(*args, **kwargs)

    def __getattr__(self, method):
        return lambda *args, **kwargs: self.call(method, *args, **kwargs)

    def load(self, stage, relative, object_name):
        self.lines.append(f'cmd.load(str(_molscene_root / {relative!r}), {object_name!r})')
        self.cmd.load(str(stage / relative), object_name)
        if self.cmd.count_atoms(object_name) == 0:
            raise ValueError('Input contains no atoms: ' + relative)

    def write_replay(self, path):
        header = ['# MolScene standalone replay: use @scene.pml in PyMOL.',
                  '# For directories containing parser punctuation, use this Python loader:',
                  '# import os',
                  '# old = os.getcwd()',
                  '# os.chdir(output_directory)',
                  '# try: cmd.do("@scene.pml")',
                  '# finally: os.chdir(old)',
                  '# The loader uses only os and the active PyMOL cmd (no MolScene install).',
                  'python', 'from pathlib import Path',
                  '_molscene_root = Path(__script__).resolve().parent']
        path.write_text('\n'.join(header + self.lines + ['python end', '']), encoding='utf-8')


def _scope(object_name, selection):
    return f'({object_name}) and ({selection})'


def _stats(cmd, selection, state=1):
    atoms = science.collect_atoms(cmd, selection, state)
    protein = science._collect_atoms(cmd, _scope(selection, 'polymer.protein'), state, allow_empty=True)
    residue = lambda a: (a['segi'], a['chain'], a['resi'], a['resn'])
    return {'atoms': len(atoms), 'residues': len({residue(a) for a in atoms}),
            'protein_residues': len({residue(a) for a in protein}),
            'chains': len({(a['segi'], a['chain']) for a in atoms})}


def _bind(scene, name, selection, state):
    """Persist the exact state-specific membership, including in the replay."""
    _state(state)
    _selection(selection, name)
    try:
        count = scene.call('select', name, selection, state=state, enable=0, quiet=1)
        if not count:
            raise ValueError(f'{name} selection has no atoms with coordinates in state {state}')
        for object_name in scene.cmd.get_object_list(name):
            coords = scene.cmd.get_coordset(object_name, state=state, copy=1)
            if coords is None or not len(coords):
                raise ValueError(f'Object {object_name!r} has no coordinates in state {state}')
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('Invalid selection or coordinate state: ' + str(exc)) from exc
    return name


def _state(state):
    if type(state) is not int or state < 1:
        raise ValueError('state must be an explicit positive integer')
    return state


def _base(scene, background='white'):
    scene.call('bg_color', 'white' if background == 'transparent' else background)
    for name, value in (('ray_opaque_background', int(background != 'transparent')), ('antialias', 2),
                        ('orthoscopic', 1), ('depth_cue', 0), ('ray_shadows', 0),
                        ('specular', 0.2), ('cartoon_trace_atoms', 0)):
        scene.call('set', name, value)
    scene.call('hide', 'everything', 'all')


def _overview(scene, object_name, style='cartoon', state=1):
    scene.call('show', style, _scope(object_name, 'polymer.protein'))
    colors = ('marine', 'teal', 'violetpurple', 'salmon', 'wheat', 'lightblue', 'lime')
    chains = sorted(scene.cmd.get_chains(object_name))
    for index, chain in enumerate(chains):
        scene.call('color', colors[index % len(colors)], _scope(object_name, 'chain ' + json.dumps(chain)))
    scene.call('show', 'sticks', _scope(object_name, 'organic'))
    scene.call('show', 'spheres', _scope(object_name, 'inorganic'))
    scene.call('set', 'sphere_scale', 0.25, object_name)
    scene.call('orient', object_name, state=state)
    scene.call('zoom', object_name, 3, state=state)


def _analysis_context(scene, selection, state):
    """Show molecular context even without a protein/cartoon guide trace."""
    _overview(scene, selection, state=state)
    scene.call('show', 'lines', selection)
    scene.call('show', 'sticks', _scope(selection, 'not polymer.protein'))
    scene.call('show', 'nonbonded', selection)


def _require_atoms(cmd, selection, label, exact=None):
    try:
        count = cmd.count_atoms(selection)
    except Exception as exc:
        raise ValueError('Invalid ' + label + ' selection: ' + str(exc)) from exc
    if not count or (exact is not None and count != exact):
        expectation = f'exactly {exact} atom' if exact is not None else 'at least one atom'
        raise ValueError(f'{label} selection must match {expectation}; matched {count}')
    return count


def _tables(stage, kind, result):
    if kind == 'polar':
        identity = ('object', 'index', 'segi', 'chain', 'resi', 'resn', 'name', 'element', 'alt', 'state')
        fields = [end + '_' + key for end in ('donor', 'acceptor') for key in identity]
        fields += ['distance_angstrom', 'donor_membership', 'acceptor_membership']
        rows = []
        for pair in result['pairs']:
            row = {end + '_' + key: pair[end][key] for end in ('donor', 'acceptor') for key in identity}
            row.update(distance_angstrom=pair['distance_angstrom'],
                       donor_membership=','.join(pair['donor_membership']),
                       acceptor_membership=','.join(pair['acceptor_membership']))
            rows.append(row)
        artifacts.write_csv(stage / 'polar.csv', rows, fields)
        return {'polar_csv': 'polar.csv'}
    if kind == 'contacts':
        identity = ('object', 'index', 'segi', 'chain', 'resi', 'resn', 'name', 'element', 'alt', 'state')
        fields = [end + '_' + key for end in ('atom_a', 'atom_b') for key in identity] + ['distance_angstrom']
        rows = []
        for pair in result['pairs']:
            row = {end + '_' + key: pair[end][key] for end in ('atom_a', 'atom_b') for key in identity}
            row['distance_angstrom'] = pair['distance_angstrom']
            rows.append(row)
        artifacts.write_csv(stage / 'contacts.csv', rows, fields)
        return {'contacts_csv': 'contacts.csv'}
    if kind == 'sasa':
        artifacts.write_csv(stage / 'sasa.csv', result['residues'],
                            ('object', 'segi', 'chain', 'resi', 'resn', 'state', 'area_angstrom2', 'atom_count'))
        return {'sasa_csv': 'sasa.csv'}
    if kind == 'interface':
        artifacts.write_csv(stage / 'interface.csv', result['residues'],
            ('partner', 'object', 'segi', 'chain', 'resi', 'resn', 'state', 'atom_count',
             'isolated_area_angstrom2', 'complex_area_angstrom2', 'buried_area_angstrom2'))
        return {'interface_csv': 'interface.csv'}
    if kind == 'inspect':
        artifacts.write_csv(stage / 'chains.csv', result['chains'],
                            ('id', 'object', 'segi', 'chain', 'atom_count', 'residue_site_count',
                             'protein_residue_site_count', 'nucleic_residue_site_count'))
        lines = []
        for row in result['sequences']:
            lines.append('>' + row['id'])
            lines.extend(row['sequence'][i:i + 80] for i in range(0, len(row['sequence']), 80))
        # A comment documents the valid empty sequence output for nonpolymers.
        (stage / 'sequence.fasta').write_text('\n'.join(lines or ['; No coordinate guide-atom sequence']) + '\n', encoding='utf-8')
        return {'chains_csv': 'chains.csv', 'sequence': 'sequence.fasta'}
    return {}


def _report(cmd, operation, parameters, inputs, structure, result, produced):
    return {'schema_version': 2, 'tool': 'molscene', 'tool_version': __version__,
            'pymol_version': cmd.get_version()[0], 'created_at': datetime.now(timezone.utc).isoformat(),
            'operation': operation, 'inputs': inputs, 'parameters': parameters,
            'structure': structure, 'analysis': result, 'artifacts': produced}


def _geometry_scene(scene, kind, parameters, state):
    letters = 'ab' if kind == 'distance' else 'abc' if kind == 'angle' else 'abcd'
    selections = [_scope('molecule', parameters['selection_' + key]) for key in letters]
    result = science.geometry(scene.cmd, kind, selections, state=state)
    bound = [_bind(scene, 'selection_' + key, selection, state) for key, selection in zip(letters, selections)]
    scene.call('distance' if kind == 'distance' else kind, 'measurement', *bound, state=state)
    scene.call('show', 'spheres', ' or '.join(bound))
    scene.call('set', 'sphere_scale', 0.3, ' or '.join(bound))
    for setting in ('dash_color', 'label_color'):
        scene.call('set', setting, 'black', 'measurement')
    if kind == 'distance':
        result.update(distance_angstrom=result['value'], selection_a=selections[0], selection_b=selections[1])
    return result


def _pocket(scene, parameters, state, active):
    _overview(scene, active, 'cartoon', state)
    lig = _bind(scene, 'ligand', _scope('molecule', parameters['ligand']), state)
    neighbor = _bind(scene, 'pocket_neighbors', _scope('molecule',
        f'polymer.protein within {parameters["radius"]!r} of ligand'), state)
    pocket = _bind(scene, 'pocket', _scope('molecule', 'byres pocket_neighbors'), state)
    result = dict(kind='pocket', state=state, ligand_atoms=scene.cmd.count_atoms(lig),
        pocket_neighbor_atoms=scene.cmd.count_atoms(neighbor), pocket_atoms=scene.cmd.count_atoms(pocket),
        pocket_residues=_stats(scene.cmd, pocket, state)['protein_residues'],
        definition='Protein atoms within radius of ligand, expanded to whole residues; geometric proximity only.')
    scene.call('hide', 'sticks', 'molecule')
    scene.call('hide', 'spheres', 'molecule')
    scene.call('set', 'cartoon_transparency', 0.82, 'molecule')
    scene.call('show', 'sticks', 'pocket or ligand')
    scene.call('set', 'stick_radius', 0.10, 'pocket')
    scene.call('set', 'stick_radius', 0.23, 'ligand')
    scene.call('color', 'orange', 'ligand and elem C')
    for element, color in (('N', 'blue'), ('O', 'red'), ('S', 'yellow')):
        scene.call('color', color, '(pocket or ligand) and elem ' + element)
    scene.call('orient', 'ligand', state=state)
    scene.call('zoom', 'ligand', 6, state=state)
    return result


def _alignment(scene, parameters):
    mobile_state, target_state = parameters['mobile_state'], parameters['target_state']
    a = _bind(scene, '_molscene_mobile', _scope('mobile', parameters['selection']), mobile_state)
    b = _bind(scene, '_molscene_target', _scope('target', parameters['selection']), target_state)
    kwargs = dict(cutoff=parameters['cutoff'], cycles=parameters['cycles'], gap=-10.0,
                  extend=-0.5, max_gap=50, object='alignment', transform=1,
                  mobile_state=mobile_state, target_state=target_state)
    method = parameters['method']
    if method == 'cealign':
        raise ValueError('cealign is not supported; choose align or super')
    values = scene.call(method, a, b, **kwargs)
    for name, color, state in (('mobile', 'marine', mobile_state), ('target', 'orange', target_state)):
        active = _bind(scene, '_molscene_' + name + '_display', name, state)
        scene.call('set', 'state', state, name)
        styles.apply_representation(scene, active, 'cartoon')
        scene.call('color', color, active)
    scene.call('orient', '_molscene_mobile_display or _molscene_target_display')
    scene.call('zoom', '_molscene_mobile_display or _molscene_target_display', 3)
    return dict(kind='alignment', method=method, mobile_state=mobile_state, target_state=target_state,
                mobile_atoms=scene.cmd.count_atoms(a), target_atoms=scene.cmd.count_atoms(b),
                align_result=list(values), rmsd_angstrom=float(values[0]), aligned_atoms=int(values[1]),
                mobile_selection=_scope('mobile', parameters['selection']),
                target_selection=_scope('target', parameters['selection']))


def _bind_atoms(scene, name, atoms, state):
    """Record original indexes, avoiding case-insensitive identifier selectors."""
    by_object = {}
    for atom in atoms:
        by_object.setdefault(atom['object'], []).append(atom['index'])
    parts = []
    for number, (obj, indexes) in enumerate(sorted(by_object.items())):
        part = name + '_part_' + str(number)
        scene.call('select_list', part, obj, indexes, state=state, mode='index')
        parts.append(part)
    scene.call('select', name, ' or '.join(parts) or 'none', state=state, enable=0, quiet=1)
    for part in parts:
        scene.call('delete', part)
    return name


def _interface_scene(scene, parameters, state):
    selections = [_scope('molecule', parameters['selection_' + side]) for side in ('a', 'b')]
    result = science.interface(scene.cmd, *selections, state=state, altloc=parameters['altloc'])
    buried = []
    residue_keys = ('object', 'segi', 'chain', 'resi', 'resn')
    for side, selection, color, highlight in zip(('a', 'b'), selections,
                                                ('marine', 'orange'), ('cyan', 'yelloworange')):
        # Keep the figure's membership identical to SASA: PyMOL alt grammar is
        # case-insensitive and would incorrectly retain lower-case "a".
        atoms = [atom for atom in science.collect_atoms(scene.cmd,
            '(' + selection + ') and not solvent and not elem H+D+T', state)
            if parameters['altloc'] == 'all' or atom['alt'] in ('', 'A')]
        if len(atoms) != result['partner_' + side]['selected_atom_count']:
            raise RuntimeError('Interface display membership differs from scientific result')
        name = _bind_atoms(scene, 'interface_' + side, atoms, state)
        scene.call('show', 'cartoon', _scope(name, 'polymer'))
        # Cartoon visibility depends on trace connectivity, not guide count.
        # Small retained-guide markers keep isolated/disconnected fragments
        # visible without replacing the whole-polymer cartoon presentation.
        guides = _scope(name, 'polymer and guide')
        scene.call('show', 'spheres', guides)
        scene.call('set', 'sphere_scale', 0.3, guides)
        nonpolymer = _scope(name, 'not polymer')
        scene.call('show', 'sticks', nonpolymer)
        # Unbonded nonprotein atoms need spheres; sticks alone produce no image.
        scene.call('show', 'spheres', nonpolymer)
        scene.call('set', 'sphere_scale', 0.3, nonpolymer)
        if scene.cmd.count_atoms(_scope(name, 'polymer')) and not scene.cmd.count_atoms(_scope(name, 'polymer and guide')):
            # A valid protein subset can contain only sidechain atoms. Without
            # cartoon guides or selected bonds it otherwise renders blank.
            scene.call('show', 'sticks', name)
            scene.call('show', 'spheres', name)
            scene.call('set', 'sphere_scale', 0.3, name)
        scene.call('color', color, name)
        sites = {tuple(row[key] for key in residue_keys) for row in result['residues']
                 if row['partner'] == side.upper() and row['buried_area_angstrom2'] > 0}
        site_atoms = [atom for atom in atoms if tuple(atom[key] for key in residue_keys) in sites]
        highlighted = _bind_atoms(scene, 'interface_buried_' + side, site_atoms, state)
        scene.call('show', 'sticks', highlighted)
        scene.call('color', highlight, highlighted)
        buried.extend(site_atoms)
    _bind_atoms(scene, 'interface_buried', buried, state)
    union = 'interface_a or interface_b'
    scene.call('set', 'stick_radius', 0.16, union)
    scene.call('orient', union, state=state)
    scene.call('zoom', union, 3, state=state)
    return result


def _run(operation, sources, output_dir, parameters, overwrite):
    output = _output(output_dir, list(sources.values()), overwrite)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.molscene-', dir=output.parent) as temporary:
        stage = Path(temporary)
        local, input_report = artifacts.copy_inputs(stage, sources)
        try:
            with _runtime().PyMOL() as pm:
                scene = _Scene(pm.cmd)
                state = parameters.get('state', 1)
                if operation == 'align':
                    scene.load(stage, local['mobile'], 'mobile')
                    scene.load(stage, local['target'], 'target')
                    states = {'mobile': parameters['mobile_state'], 'target': parameters['target_state']}
                else:
                    scene.load(stage, local['input'], 'molecule')
                    states = {'molecule': state}
                structure = {name: _stats(pm.cmd, name, value) for name, value in states.items()}
                produced = {'report': 'report.json', 'inputs': list(local.values())}
                if operation == 'inspect':
                    result = science.inspect_structure(pm.cmd, _scope('molecule', parameters['selection']), state)
                    result['kind'] = 'inspect'
                else:
                    _base(scene, parameters.get('background', 'white'))
                    scene.call('frame', state)
                    for name, coordinate_state in states.items():
                        protein = _scope(name, 'polymer.protein')
                        ca_only = pm.cmd.count_atoms(protein) > 0 and pm.cmd.count_atoms(_scope(protein, 'not name CA')) == 0
                        scene.call('set', 'cartoon_trace_atoms', int(ca_only), name)
                    if operation == 'align':
                        result = _alignment(scene, parameters)
                    else:
                        active = _bind(scene, '_molscene_display', _scope('molecule', parameters.get('selection', 'all')), state)
                        if operation == 'render':
                            if parameters['representation'] is not None:
                                styles.apply_representation(scene, active, parameters['representation'])
                                scene.call('orient', active, state=state)
                                scene.call('zoom', active, 3, state=state)
                                result = dict(kind='render', state=state, representation=parameters['representation'])
                            else:
                                _require_atoms(pm.cmd, _scope(active, 'polymer.protein'), 'protein render')
                                if parameters['style'] == 'pocket':
                                    result = _pocket(scene, parameters, state, active)
                                else:
                                    _overview(scene, active, 'surface' if parameters['style'] == 'surface' else 'cartoon', state)
                                    result = dict(kind='render', state=state)
                            result['coloring'] = styles.apply_coloring(scene, active, parameters['coloring'], parameters['color'])
                            # Pocket carbon/element colors are part of the legacy preset.
                            if parameters['style'] == 'pocket' and parameters['representation'] is None:
                                scene.call('color', 'orange', 'ligand and elem C')
                                for element, color in (('N', 'blue'), ('O', 'red'), ('S', 'yellow')):
                                    scene.call('color', color, '(pocket or ligand) and elem ' + element)
                            scene.call('set', 'transparency', parameters['surface_transparency'], active)
                            if parameters['style'] != 'pocket':
                                scene.call('set', 'stick_radius', parameters['stick_radius'], active)
                        elif operation == 'interface':
                            result = _interface_scene(scene, parameters, state)
                        else:
                            _analysis_context(scene, active, state)
                            if operation in ('measure', 'angle', 'dihedral'):
                                result = _geometry_scene(scene, 'distance' if operation == 'measure' else operation, parameters, state)
                            elif operation in ('contacts', 'polar'):
                                options = dict(state=state, cutoff=parameters['cutoff'])
                                if operation == 'polar':
                                    options.update(angle=parameters['angle'], altloc=parameters['altloc'], max_pairs=parameters['max_pairs'])
                                result = (science.contacts if operation == 'contacts' else science.polar_contacts)(
                                    pm.cmd, _scope('molecule', parameters['selection_a']),
                                    _scope('molecule', parameters['selection_b']), **options)
                                result['kind'] = operation
                                result['displayed_pair_count'] = min(200, result['pair_count'])
                                if operation == 'polar':
                                    result['annotation_pair_cap'] = 200
                                keys = ('donor', 'acceptor') if operation == 'polar' else ('atom_a', 'atom_b')
                                for i, pair in enumerate(result['pairs'][:200]):
                                    bound = [_bind(scene, '_molscene_contact_' + side,
                                        f"({pair[side]['object']}) and index {pair[side]['index']}", state)
                                        for side in keys]
                                    scene.call('distance', operation, *bound, state=state)
                                if result['displayed_pair_count']:
                                    scene.call('hide', 'labels', operation)
                            elif operation == 'sasa':
                                context = parameters['context']
                                result = science.sasa(pm.cmd, active,
                                    _scope('molecule', context) if context is not None else None,
                                    state=state, altloc=parameters['altloc'])
                                result['kind'] = 'sasa'
                    scene.call('deselect')
                    width, height, dpi = (parameters[key] for key in ('width', 'height', 'dpi'))
                    scene.call('viewport', width, height)
                    pm.cmd.png(str(stage / 'scene.png'), width=width, height=height, dpi=dpi, ray=1, quiet=1)
                    pm.cmd.save(str(stage / 'scene.pse'))
                    scene.write_replay(stage / 'scene.pml')
                    produced.update(png='scene.png', session='scene.pse', replay='scene.pml')
                produced.update(_tables(stage, operation, result))
                report = _report(pm.cmd, operation, parameters, input_report, structure, result, produced)
                artifacts.write_json(stage / 'report.json', report)
        except (ValueError, RuntimeError):
            raise
        except Exception as exc:
            raise RuntimeError(f'PyMOL {operation} failed: {exc}') from exc
        names = [value for value in produced.values() if isinstance(value, str)]
        artifacts.commit(stage, output, list(sources.values()), overwrite, names, local.values())
        return report


def _options(width, height, dpi, state):
    _dimensions(width, height, dpi)
    return dict(width=width, height=height, dpi=dpi, state=_state(state))


def render(input_path, output_dir, *, style='cartoon', ligand=None, radius=5.0,
           width=1600, height=1200, dpi=300, overwrite=False, representation=None,
           selection='all', coloring='chain', color='marine', state=1, background='white',
           surface_transparency=0.2, stick_radius=0.18):
    source = _input(input_path)
    options = _options(width, height, dpi, state)
    if style not in ('cartoon', 'surface', 'pocket'):
        raise ValueError('style must be cartoon, surface, or pocket')
    if representation is not None and representation not in styles.REPRESENTATIONS:
        raise ValueError('Unsupported representation')
    if coloring not in styles.COLORINGS:
        raise ValueError('Unsupported coloring')
    if representation is not None and style == 'pocket':
        raise ValueError('representation cannot be combined with pocket style')
    for name, value in (('radius', radius), ('stick_radius', stick_radius)):
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
            raise ValueError(name + ' must be a positive finite number')
    if isinstance(surface_transparency, bool) or not isinstance(surface_transparency, (float, int)) or not math.isfinite(surface_transparency) or not 0 <= surface_transparency <= 1:
        raise ValueError('surface_transparency must be between 0 and 1')
    _selection(background, 'background')
    _selection(color, 'color')
    if style == 'pocket' or ligand is not None:
        _selection(ligand, 'ligand')
    return _run('render', {'input': source}, output_dir,
        dict(options, style=style, ligand=ligand, radius=float(radius), representation=representation,
             selection=_selection(selection, 'selection'), coloring=coloring, color=color, background=background,
             surface_transparency=surface_transparency, stick_radius=stick_radius), overwrite)


def _geometry(input_path, output_dir, kind, selections, width, height, dpi, state, overwrite):
    options = _options(width, height, dpi, state)
    options.update({'selection_' + key: _selection(value, 'selection_' + key)
                    for key, value in zip('abcd', selections)})
    return _run(kind, {'input': _input(input_path)}, output_dir, options, overwrite)


def measure(input_path, output_dir, *, selection_a, selection_b,
            width=1600, height=1200, dpi=300, state=1, overwrite=False):
    return _geometry(input_path, output_dir, 'measure', [selection_a, selection_b], width, height, dpi, state, overwrite)


def angle(input_path, output_dir, *, selection_a, selection_b, selection_c,
          width=1600, height=1200, dpi=300, state=1, overwrite=False):
    return _geometry(input_path, output_dir, 'angle', [selection_a, selection_b, selection_c], width, height, dpi, state, overwrite)


def dihedral(input_path, output_dir, *, selection_a, selection_b, selection_c, selection_d,
             width=1600, height=1200, dpi=300, state=1, overwrite=False):
    return _geometry(input_path, output_dir, 'dihedral', [selection_a, selection_b, selection_c, selection_d], width, height, dpi, state, overwrite)


def contacts(input_path, output_dir, *, selection_a, selection_b, cutoff=4.0, state=1,
             width=1600, height=1200, dpi=300, overwrite=False):
    options = _options(width, height, dpi, state)
    return _run('contacts', {'input': _input(input_path)}, output_dir,
        dict(options, selection_a=_selection(selection_a, 'selection_a'),
             selection_b=_selection(selection_b, 'selection_b'), cutoff=cutoff), overwrite)


def sasa(input_path, output_dir, *, selection='all', context=None, state=1, altloc='primary',
         width=1600, height=1200, dpi=300, overwrite=False):
    options = _options(width, height, dpi, state)
    if context is not None:
        _selection(context, 'context')
    return _run('sasa', {'input': _input(input_path)}, output_dir,
        dict(options, selection=_selection(selection, 'selection'), context=context, altloc=altloc), overwrite)


def polar(input_path, output_dir, *, selection_a, selection_b, state=1, cutoff=3.5,
          angle=45.0, altloc='primary', max_pairs=100000, width=1600, height=1200,
          dpi=300, overwrite=False):
    """Render typed polar candidates, preserving complete JSON/CSV and frozen replay."""
    from .polar import _validate
    _validate(state, cutoff, angle, altloc, max_pairs)
    options = _options(width, height, dpi, state)
    return _run('polar', {'input': _input(input_path)}, output_dir,
                dict(options, selection_a=_selection(selection_a, 'selection_a'),
                     selection_b=_selection(selection_b, 'selection_b'), cutoff=cutoff,
                     angle=angle, altloc=altloc, max_pairs=max_pairs), overwrite)


def interface(input_path, output_dir, *, selection_a, selection_b, state=1, altloc='primary',
              width=1600, height=1200, dpi=300, overwrite=False):
    """Render disjoint retained partners and export complete signed buried SASA."""
    options = _options(width, height, dpi, state)
    if altloc not in ('primary', 'all'):
        raise ValueError('altloc must be primary or all')
    return _run('interface', {'input': _input(input_path)}, output_dir,
        dict(options, selection_a=_selection(selection_a, 'selection_a'),
             selection_b=_selection(selection_b, 'selection_b'), altloc=altloc), overwrite)


def inspect(input_path, output_dir, *, selection='all', state=1, overwrite=False):
    return _run('inspect', {'input': _input(input_path)}, output_dir,
                dict(selection=_selection(selection, 'selection'), state=_state(state)), overwrite)


def align(mobile_path, target_path, output_dir, *, selection='polymer.protein and name CA',
          width=1600, height=1200, dpi=300, overwrite=False, method='align',
          mobile_state=1, target_state=1, cutoff=2.0, cycles=5):
    _dimensions(width, height, dpi)
    _state(mobile_state)
    _state(target_state)
    if method not in ('align', 'super'):
        raise ValueError('method must be align or super')
    if isinstance(cutoff, bool) or not isinstance(cutoff, (int, float)) or not math.isfinite(cutoff) or cutoff <= 0:
        raise ValueError('cutoff must be a positive finite number')
    if type(cycles) is not int or cycles < 0:
        raise ValueError('cycles must be a nonnegative integer')
    return _run('align', {'mobile': _input(mobile_path), 'target': _input(target_path)}, output_dir,
        dict(selection=_selection(selection, 'selection'), width=width, height=height, dpi=dpi,
             method=method, mobile_state=mobile_state, target_state=target_state, cutoff=cutoff, cycles=cycles,
             gap=-10.0, extend=-0.5, max_gap=50, transform=1), overwrite)


def _session(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != '.pse' or not path.stat().st_size:
        raise ValueError('input_session must be an existing nonempty .pse file')
    return path


def export_session(session_path, output_dir, *, width=1600, height=1200, dpi=300,
                   background='white', ray=True, overwrite=False, analysis_records=None):
    """Export a restored PSE's camera and styles; replay loads the bundled PSE."""
    source = _session(session_path)
    _dimensions(width, height, dpi)
    _selection(background, 'background')
    if type(ray) is not bool:
        raise ValueError('ray must be a boolean')
    output = _output(output_dir, [source], overwrite)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.molscene-', dir=output.parent) as temporary:
        stage = Path(temporary)
        local, input_report = artifacts.copy_inputs(stage, {'input': source})
        with _runtime().PyMOL() as pm:
            pm.cmd.load(str(stage / local['input']))
            view, state = list(pm.cmd.get_view()), pm.cmd.get_state()
            if not pm.cmd.count_atoms('all'):
                raise ValueError('Session contains no molecular atoms to export')
            pm.cmd.bg_color('white' if background == 'transparent' else background)
            pm.cmd.set('ray_opaque_background', int(background != 'transparent'))
            from . import native_render
            rendering = None
            if native_render.requires_native(pm.cmd, ray):
                if not (16 <= width <= 4096 and 16 <= height <= 4096 and dpi <= 1200):
                    raise ValueError('Native OpenGL export requires width and height 16..4096 and dpi 1..1200; choose supported dimensions')
                from .bundles import Bundle
                from .external_tools import ExternalToolError
                import uuid
                pm.cmd.save(str(stage / 'scene.pse'))
                # Reuse Bundle only for owned scratch and bounded failure logs.
                # Legacy publication/overwrite stays with artifacts.commit below.
                diagnostics = Bundle(output.parent / ('.molscene-native-' + uuid.uuid4().hex),
                    protected=[source, stage, output], inventory=['manifest.json'])
                try:
                    with diagnostics:
                        native = native_render.render_native(stage / 'scene.pse', [stage / 'scene.png'],
                            dict(width=width, height=height, dpi=dpi, ray=ray),
                            work_dir=diagnostics.scratch, bundle=diagnostics)
                        rendering = native_render.report_metadata(native, stage)
                        rendering.update(session='scene.pse', source_sha256=native['result']['source_sha256'])
                except Exception as error:
                    if (diagnostics.failure_path / 'failure.json').is_file():
                        error.failure_path = str(diagnostics.failure_path)
                        suffix = '; native diagnostics retained at ' + error.failure_path
                        # Legacy worker reports str(error), not custom attributes.
                        error.args = (str(error) + suffix,)
                        if isinstance(error, OSError):
                            error.strerror = (error.strerror or 'Native rendering failed') + suffix
                        if isinstance(error, ExternalToolError):
                            for stream in ('stdout', 'stderr'):
                                retained = artifacts.checked_path(diagnostics.failure_path / 'logs' / ('native.' + stream + '.log'))
                                if stream + '_path' in error.result:
                                    error.result[stream + '_path'] = str(retained) if retained.is_file() else None
                    raise
            else:
                pm.cmd.png(str(stage / 'scene.png'), width=width, height=height, dpi=dpi, ray=int(ray), quiet=1)
                pm.cmd.set_view(view)
                pm.cmd.save(str(stage / 'scene.pse'))
            replay = _Scene(pm.cmd)
            replay.lines.append("cmd.load(str(_molscene_root / 'scene.pse'))")
            replay.write_replay(stage / 'scene.pml')
            produced = dict(png='scene.png', session='scene.pse', replay='scene.pml', report='report.json', inputs=list(local.values()))
            report = _report(pm.cmd, 'export', dict(width=width, height=height, dpi=dpi,
                background=background, ray=ray, state=state), input_report, {}, {'kind': 'export'}, produced)
            if rendering is not None:
                report['analysis']['rendering'] = rendering
                report['warnings'] = native['warnings']
            digest = input_report[0]['sha256']
            report.update(snapshot_id=digest, view=view, replay_contract='bundled_session')
            fingerprint = scene_fingerprint(pm.cmd, state)
            report['source_fingerprint'] = fingerprint
            records = json.loads(json.dumps(analysis_records or [], allow_nan=False))
            fingerprints = {state: fingerprint}
            for record in records:
                if not isinstance(record, dict):
                    raise ValueError('analysis_records must contain dictionaries')
                record['snapshot_status'] = record_snapshot_status(pm.cmd, record, state, fingerprints)
            report['analysis_records'] = records
            artifacts.write_json(stage / 'report.json', report)
        artifacts.commit(stage, output, [source], overwrite,
            ['scene.png', 'scene.pse', 'scene.pml', 'report.json'], local.values())
        return report
