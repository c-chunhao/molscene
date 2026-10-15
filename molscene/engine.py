"""Scene construction, geometry, and portable output; PyMOL imports are lazy."""
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile

from . import __version__

_FILES = ('scene.png', 'scene.pse', 'scene.pml', 'report.json')
_SUFFIXES = {'.pdb', '.cif', '.mmcif'}
_INPUT_NAMES = tuple(role + ext for role in ('input', 'mobile', 'target') for ext in _SUFFIXES)


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


def _no_link(path):
    # Path.resolve would conceal symlinks/junctions, so inspect lexical ancestors first.
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except (FileNotFoundError, NotADirectoryError):
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Output paths must not contain links or reparse points: ' + str(candidate))


def _output(path, sources, overwrite):
    path = Path(os.path.abspath(os.path.expanduser(os.fspath(path))))
    _no_link(path)
    for ancestor in path.parents:
        if ancestor.exists() and not ancestor.is_dir():
            raise ValueError('Output parent must be a directory: ' + str(ancestor))
    if path.exists() and not path.is_dir():
        raise ValueError('Output must be a directory: ' + str(path))
    if any(source == path or path in source.parents for source in sources):
        raise ValueError('Output directory must not contain an input structure')
    if path.exists() and any(path.iterdir()) and not overwrite:
        raise ValueError('Output directory is nonempty; choose a new directory or use overwrite=True')
    for name in (*_FILES, 'inputs'):
        target = path / name
        _no_link(target)
        if target.exists() and ((name == 'inputs') != target.is_dir()):
            raise ValueError('Owned artifact has the wrong file type: ' + str(target))
    for name in _INPUT_NAMES:
        target = path / 'inputs' / name
        _no_link(target)
        if target.exists() and not target.is_file():
            raise ValueError('Owned input copy must be a regular file: ' + str(target))
    return path


class _Scene:
    """Record literal API calls while executing against an isolated cmd instance."""
    def __init__(self, cmd):
        self.cmd = cmd
        self.lines = []

    def call(self, method, *args, **kwargs):
        literal = ', '.join([*(ascii(x) for x in args), *(k + '=' + ascii(v) for k, v in kwargs.items())])
        self.lines.append('cmd.' + method + '(' + literal + ')')
        return getattr(self.cmd, method)(*args, **kwargs)

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


def _stats(cmd, selection):
    atoms = cmd.get_model(selection).atom
    protein = cmd.get_model(_scope(selection, 'polymer.protein')).atom
    residue = lambda a: (a.segi, a.chain, a.resi, a.resn)
    return {'atoms': len(atoms), 'residues': len({residue(a) for a in atoms}),
            'protein_residues': len({residue(a) for a in protein}),
            'chains': len({(a.segi, a.chain) for a in atoms})}


def _base(scene):
    scene.call('bg_color', 'white')
    for name, value in (('ray_opaque_background', 1), ('antialias', 2),
                        ('orthoscopic', 1), ('depth_cue', 0), ('ray_shadows', 0),
                        ('specular', 0.2), ('cartoon_trace_atoms', 0)):
        scene.call('set', name, value)
    scene.call('hide', 'everything', 'all')


def _overview(scene, object_name, style='cartoon'):
    scene.call('show', style, _scope(object_name, 'polymer.protein'))
    colors = ('marine', 'teal', 'violetpurple', 'salmon', 'wheat', 'lightblue', 'lime')
    chains = sorted(scene.cmd.get_chains(object_name))
    for index, chain in enumerate(chains):
        scene.call('color', colors[index % len(colors)], _scope(object_name, 'chain ' + json.dumps(chain)))
    scene.call('show', 'sticks', _scope(object_name, 'organic'))
    scene.call('show', 'spheres', _scope(object_name, 'inorganic'))
    scene.call('set', 'sphere_scale', 0.25, object_name)
    scene.call('orient', object_name)
    scene.call('zoom', object_name, 3)


def _require_atoms(cmd, selection, label, exact=None):
    try:
        count = cmd.count_atoms(selection)
    except Exception as exc:
        raise ValueError('Invalid ' + label + ' selection: ' + str(exc)) from exc
    if not count or (exact is not None and count != exact):
        expectation = f'exactly {exact} atom' if exact is not None else 'at least one atom'
        raise ValueError(f'{label} selection must match {expectation}; matched {count}')
    return count


def _run(operation, sources, output_dir, parameters, overwrite):
    output = _output(output_dir, list(sources.values()), overwrite)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Only a complete successful analysis/render is committed to the destination.
    with tempfile.TemporaryDirectory(prefix='.molscene-', dir=output.parent) as temporary:
        stage = Path(temporary)
        (stage / 'inputs').mkdir()
        input_report = []
        local = {}
        for role, source in sources.items():
            relative = 'inputs/' + role + source.suffix.lower()
            shutil.copyfile(source, stage / relative)
            local[role] = relative
            input_report.append({'role': role, 'basename': source.name,
                                 'sha256': hashlib.sha256((stage / relative).read_bytes()).hexdigest()})
        try:
            with _runtime().PyMOL() as pm:
                scene = _Scene(pm.cmd)
                if operation == 'align':
                    scene.load(stage, local['mobile'], 'mobile')
                    scene.load(stage, local['target'], 'target')
                    names = ('mobile', 'target')
                else:
                    scene.load(stage, local['input'], 'molecule')
                    names = ('molecule',)
                structure = {name: _stats(pm.cmd, name) for name in names}
                _base(scene)
                for name in names:
                    protein = _scope(name, 'polymer.protein')
                    # Trace arbitrary atoms only for genuinely CA-only protein models.
                    # On full-atom proteins this setting would trace sidechains too.
                    ca_only = (pm.cmd.count_atoms(protein) > 0 and
                               pm.cmd.count_atoms(_scope(protein, 'not name CA')) == 0)
                    scene.call('set', 'cartoon_trace_atoms', int(ca_only), name)
                analysis = {}
                if operation == 'render':
                    _require_atoms(pm.cmd, _scope('molecule', 'polymer.protein'), 'protein render')
                    style = parameters['style']
                    _overview(scene, 'molecule', 'surface' if style == 'surface' else 'cartoon')
                    if style == 'pocket':
                        lig = _scope('molecule', parameters['ligand'])
                        analysis['ligand_atoms'] = _require_atoms(pm.cmd, lig, 'ligand')
                        scene.call('select', 'ligand', lig)
                        neighbor = _scope('molecule', f'polymer.protein within {parameters["radius"]!r} of ligand')
                        scene.call('select', 'pocket_neighbors', neighbor)
                        analysis['pocket_neighbor_atoms'] = pm.cmd.count_atoms('pocket_neighbors')
                        _require_atoms(pm.cmd, 'pocket_neighbors', 'pocket protein neighborhood')
                        scene.call('select', 'pocket', _scope('molecule', 'byres pocket_neighbors'))
                        analysis['pocket_atoms'] = pm.cmd.count_atoms('pocket')
                        analysis['pocket_residues'] = _stats(pm.cmd, 'pocket')['protein_residues']
                        analysis['definition'] = 'Protein atoms within radius of ligand, expanded to whole residues; geometric proximity only.'
                        scene.call('hide', 'sticks', 'molecule')
                        scene.call('hide', 'spheres', 'molecule')
                        scene.call('set', 'cartoon_transparency', 0.82, 'molecule')
                        scene.call('show', 'sticks', 'pocket or ligand')
                        scene.call('set', 'stick_radius', 0.10, 'pocket')
                        scene.call('set', 'stick_radius', 0.23, 'ligand')
                        scene.call('color', 'orange', 'ligand and elem C')
                        for element, color in (('N', 'blue'), ('O', 'red'), ('S', 'yellow')):
                            scene.call('color', color, '(pocket or ligand) and elem ' + element)
                        scene.call('orient', 'ligand')
                        scene.call('zoom', 'ligand', 6)
                elif operation == 'measure':
                    a = _scope('molecule', parameters['selection_a'])
                    b = _scope('molecule', parameters['selection_b'])
                    _require_atoms(pm.cmd, a, 'selection A', 1)
                    _require_atoms(pm.cmd, b, 'selection B', 1)
                    _overview(scene, 'molecule')
                    scene.call('select', 'selection_a', a)
                    scene.call('select', 'selection_b', b)
                    analysis['distance_angstrom'] = float(pm.cmd.get_distance(a, b))
                    analysis['selection_a'] = a
                    analysis['selection_b'] = b
                    scene.call('distance', 'measurement', a, b)
                    scene.call('show', 'spheres', 'selection_a or selection_b')
                    scene.call('set', 'sphere_scale', 0.3, 'selection_a or selection_b')
                    scene.call('set', 'dash_color', 'black', 'measurement')
                    scene.call('set', 'label_color', 'black', 'measurement')
                else:
                    a = _scope('mobile', parameters['selection'])
                    b = _scope('target', parameters['selection'])
                    analysis['mobile_atoms'] = _require_atoms(pm.cmd, a, 'mobile alignment')
                    analysis['target_atoms'] = _require_atoms(pm.cmd, b, 'target alignment')
                    result = scene.call('align', a, b, cutoff=2.0, cycles=5, gap=-10.0,
                                        extend=-0.5, max_gap=50, object='alignment', transform=1)
                    analysis.update(align_result=list(result), rmsd_angstrom=float(result[0]),
                                    aligned_atoms=int(result[1]), mobile_selection=a, target_selection=b)
                    for name, color in (('mobile', 'marine'), ('target', 'orange')):
                        scene.call('show', 'cartoon', name)
                        scene.call('color', color, name)
                    scene.call('orient', 'mobile or target')
                    scene.call('zoom', 'mobile or target', 3)
                scene.call('deselect')
                width, height, dpi = (parameters[key] for key in ('width', 'height', 'dpi'))
                scene.call('viewport', width, height)
                # ray=1 is essential: surface/cartoon settings must actually reach the ray renderer.
                pm.cmd.png(str(stage / 'scene.png'), width=width, height=height, dpi=dpi, ray=1, quiet=1)
                pm.cmd.save(str(stage / 'scene.pse'))
                scene.write_replay(stage / 'scene.pml')
                report = {'tool': 'molscene', 'tool_version': __version__,
                          'pymol_version': pm.cmd.get_version()[0],
                          'created_at': datetime.now(timezone.utc).isoformat(), 'operation': operation,
                          'inputs': input_report, 'parameters': parameters, 'structure': structure,
                          'analysis': analysis, 'artifacts': {'png': 'scene.png', 'session': 'scene.pse',
                          'replay': 'scene.pml', 'report': 'report.json', 'inputs': list(local.values())}}
                (stage / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        except (ValueError, RuntimeError):
            raise
        except Exception as exc:
            raise RuntimeError(f'PyMOL {operation} failed: {exc}') from exc
        for name in _FILES:
            if not (stage / name).is_file() or not (stage / name).stat().st_size:
                raise RuntimeError('PyMOL did not produce a nonempty artifact: ' + name)
        # Recheck after rendering so a linked destination cannot slip through the long operation.
        _output(output, list(sources.values()), overwrite)
        output.mkdir(exist_ok=True)
        (output / 'inputs').mkdir(exist_ok=True)
        for relative in local.values():
            os.replace(stage / relative, output / relative)
        for name in _FILES:
            os.replace(stage / name, output / name)
        return report


def render(input_path, output_dir, *, style='cartoon', ligand=None, radius=5.0,
           width=1600, height=1200, dpi=300, overwrite=False):
    """Render cartoon, surface, or a ligand-centered geometric pocket scene."""
    source = _input(input_path)
    _dimensions(width, height, dpi)
    if style not in ('cartoon', 'surface', 'pocket'):
        raise ValueError('style must be cartoon, surface, or pocket')
    if isinstance(radius, bool) or not isinstance(radius, (float, int)) or not math.isfinite(radius) or radius <= 0:
        raise ValueError('radius must be a positive finite number')
    if style == 'pocket':
        _selection(ligand, 'ligand')
    elif ligand is not None:
        _selection(ligand, 'ligand')
    return _run('render', {'input': source}, output_dir,
                dict(style=style, ligand=ligand, radius=float(radius), width=width, height=height, dpi=dpi), overwrite)


def measure(input_path, output_dir, *, selection_a, selection_b,
            width=1600, height=1200, dpi=300, overwrite=False):
    """Measure two single-atom selections in Angstroms and save the annotated scene."""
    source = _input(input_path)
    _dimensions(width, height, dpi)
    return _run('measure', {'input': source}, output_dir,
                dict(selection_a=_selection(selection_a, 'selection_a'), selection_b=_selection(selection_b, 'selection_b'),
                     width=width, height=height, dpi=dpi), overwrite)


def align(mobile_path, target_path, output_dir, *, selection='polymer.protein and name CA',
          width=1600, height=1200, dpi=300, overwrite=False):
    """Superpose similar protein structures with PyMOL align; record its full result."""
    mobile, target = _input(mobile_path), _input(target_path)
    _dimensions(width, height, dpi)
    return _run('align', {'mobile': mobile, 'target': target}, output_dir,
                dict(selection=_selection(selection, 'selection'), width=width, height=height, dpi=dpi,
                     cutoff=2.0, cycles=5, gap=-10.0, extend=-0.5, max_gap=50, transform=1), overwrite)
