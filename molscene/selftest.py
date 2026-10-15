"""Offline acceptance checks against a user-provided, normally started PyMOL.

The fixtures are synthetic and deterministic. Pillow is required only for this
self-test's image verification, not for ordinary rendering or analysis.
"""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile

from . import engine

_SCENES = ('cartoon', 'surface', 'measure', 'align')


def _check(condition, message):
    if not condition:
        raise RuntimeError(message)


def _peptide_pdb():
    # A fixed six-residue helix-like trace with N/CA/C/O and a small organic ligand.
    centers = [(2.3 * math.cos(math.radians(100 * i)),
                2.3 * math.sin(math.radians(100 * i)), 1.5 * i) for i in range(6)]
    lines = []
    serial = 0
    for index, (residue, ca) in enumerate(zip(('ALA', 'GLY', 'SER', 'LEU', 'THR', 'VAL'), centers), 1):
        for name, offset, element in (('N', (-0.7, -0.5, -0.5), 'N'),
                ('CA', (0, 0, 0), 'C'), ('C', (0.6, 0.5, 0.5), 'C'), ('O', (0.8, 1.4, 0.5), 'O')):
            serial += 1
            x, y, z = (a + b for a, b in zip(ca, offset))
            lines.append(f'ATOM  {serial:5d} {name:^4s} {residue:3s} A{index:4d}    '
                         f'{x:8.3f}{y:8.3f}{z:8.3f}  1.00 20.00          {element:>2s}')
    lines += ['TER', 'HETATM   25  C1  LIG B   1       5.000   0.000   0.000  1.00 20.00           C',
              'HETATM   26  C2  LIG B   1       6.400   0.000   0.000  1.00 20.00           C', 'END']
    return '\n'.join(lines) + '\n'


_POINTS = ('HETATM    1  C1  PNT A   1       0.000   0.000   0.000  1.00 20.00           C\n'
           'HETATM    2  C2  PNT B   2       3.000   4.000   0.000  1.00 20.00           C\nEND\n')


def _coords(cmd, selection):
    return [[float(value) for value in atom.coord] for atom in cmd.get_model(selection).atom]


def _near_coordinates(actual, expected, tolerance=1e-5):
    _check(len(actual) == len(expected), 'Coordinate atom cardinality changed')
    _check(all(abs(a - b) <= tolerance for aa, bb in zip(actual, expected) for a, b in zip(aa, bb)),
           f'Coordinates differ beyond {tolerance} angstrom')


def _snapshot(cmd):
    objects = sorted(cmd.get_names('objects'))
    selections = sorted(cmd.get_names('selections'))
    colors = []
    cmd.iterate('all', 'colors.append((model, index, color))', space={'colors': colors})
    return dict(objects=[(name, cmd.get_type(name)) for name in objects],
                coordinates={name: _coords(cmd, name) for name in objects if cmd.get_type(name) == 'object:molecule'},
                selections={name: cmd.index(name) for name in selections}, colors=colors,
                representations={rep: cmd.index('rep ' + rep) for rep in ('cartoon', 'sticks', 'surface', 'spheres')},
                view=list(cmd.get_view()), background=cmd.get('bg_rgb'),
                enabled=sorted(cmd.get_names('objects', enabled_only=1)))


def _replay(cmd, directory):
    """Standalone loader also usable without importing MolScene in PyMOL.

    PyMOL command parsing splits semicolons even inside quoted absolute script
    paths. Changing directory and using a fixed basename avoids that parser.
    """
    previous = os.getcwd()
    try:
        os.chdir(directory)
        cmd.do('@scene.pml', echo=0)
    finally:
        os.chdir(previous)


def _prepare_output(output_dir, overwrite):
    output = Path(os.path.abspath(os.path.expanduser(os.fspath(output_dir))))
    engine._no_link(output)
    if output.exists() and not output.is_dir():
        raise ValueError('Output must be a directory: ' + str(output))
    if output.exists() and any(output.iterdir()) and not overwrite:
        raise ValueError('Output directory is nonempty; choose a new directory or use --overwrite')
    engine._no_link(output / 'selftest.json')
    if (output / 'selftest.json').exists() and not (output / 'selftest.json').is_file():
        raise ValueError('Self-test report must be a regular file')
    for name in _SCENES:
        engine._output(output / name, [], overwrite)
    output.mkdir(parents=True, exist_ok=True)
    return output


def run_selftest(output_dir, *, overwrite=False):
    """Run deterministic offline acceptance checks, record every failure, write JSON."""
    output = _prepare_output(output_dir, overwrite)
    result = dict(tool='molscene', tool_version=engine.__version__,
                  created_at=datetime.now(timezone.utc).isoformat(), passed=False, tests=[])

    def record(name, operation):
        transcript = io.StringIO()
        item = dict(name=name, status='passed', evidence={})
        try:
            with redirect_stdout(transcript), redirect_stderr(transcript):
                evidence = operation()
            item['evidence'] = evidence or {}
            _check(not re.search(r'(?im)(?:\bTraceback\b|\bException\b|\bError\s*:)', transcript.getvalue()),
                   'PyMOL printed an error without raising an exception')
        except Exception as exc:
            item['status'] = 'failed'
            item['error'] = f'{type(exc).__name__}: {exc}'
        if transcript.getvalue():
            item['evidence']['transcript'] = transcript.getvalue()
        result['tests'].append(item)
        return item['status'] == 'passed'

    def finish():
        result['passed'] = bool(result['tests']) and all(t['status'] == 'passed' for t in result['tests'])
        engine._no_link(output / 'selftest.json')
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', prefix='.selftest-',
                dir=output, delete=False, encoding='utf-8') as stream:
            temporary = Path(stream.name)
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
        try:
            os.replace(temporary, output / 'selftest.json')
        finally:
            temporary.unlink(missing_ok=True)
        return result

    backend = {}
    def runtime():
        backend['module'] = engine._runtime()
        with backend['module'].PyMOL() as pm:
            result['pymol_version'] = pm.cmd.get_version()[0]
        return {'pymol_version': result['pymol_version']}
    if not record('runtime', runtime):
        return finish()
    pymol2 = backend['module']
    sizes = dict(width=240, height=180, dpi=72, overwrite=overwrite)
    reports = {}

    with tempfile.TemporaryDirectory(prefix='molscene-fixtures-') as temporary:
        fixtures = Path(temporary)
        peptide, cif, mobile, points = (fixtures / name for name in ('peptide.pdb', 'peptide.cif', 'mobile.pdb', 'points.pdb'))
        peptide.write_text(_peptide_pdb(), encoding='ascii')
        points.write_text(_POINTS, encoding='ascii')

        def formats():
            with pymol2.PyMOL() as pm:
                cmd = pm.cmd
                cmd.load(str(peptide), 'peptide')
                original = _coords(cmd, 'peptide')
                _check(len(original) == 26, f'PDB should contain 26 atoms, got {len(original)}')
                _near_coordinates([original[1]], [[2.3, 0.0, 0.0]])
                cmd.save(str(cif), 'peptide')
                cmd.load(str(cif), 'cif_copy')
                _near_coordinates(_coords(cmd, 'cif_copy'), original, tolerance=0.001)
            return dict(pdb_atoms=26, mmcif_atoms=26, coordinate_tolerance_angstrom=0.001)
        record('pdb_mmcif_coordinates', formats)

        def ca_selection():
            with pymol2.PyMOL() as pm:
                pm.cmd.load(str(peptide), 'peptide')
                count = pm.cmd.count_atoms('peptide and polymer.protein and name CA')
                _check(count == 6, f'Six C-alpha atoms expected, got {count}')
            return {'ca_atoms': count}
        record('ca_selection', ca_selection)

        def representations():
            reports['cartoon'] = engine.render(peptide, output / 'cartoon', **sizes)
            reports['surface'] = engine.render(cif, output / 'surface', style='surface', **sizes)
            counts = {}
            for style in ('cartoon', 'surface'):
                with pymol2.PyMOL() as pm:
                    pm.cmd.load(str(output / style / 'scene.pse'))
                    protein = pm.cmd.count_atoms('molecule and polymer.protein and rep ' + style)
                    ligand = pm.cmd.count_atoms('molecule and organic and rep sticks')
                    _check(protein == 24, f'{style}: expected 24 represented protein atoms, got {protein}')
                    _check(ligand == 2, f'{style}: expected 2 ligand stick atoms, got {ligand}')
                    counts[style] = protein
                    counts[style + '_ligand_sticks'] = ligand
            return counts
        record('representations', representations)

        def distance():
            reports['measure'] = engine.measure(points, output / 'measure',
                    selection_a='index 1', selection_b='index 2', **sizes)
            value = reports['measure']['analysis']['distance_angstrom']
            _check(abs(value - 5.0) < 1e-6, f'Expected 5 angstrom distance, got {value}')
            with pymol2.PyMOL() as pm:
                pm.cmd.load(str(output / 'measure' / 'scene.pse'))
                _check(pm.cmd.count_atoms('selection_a') == 1 and pm.cmd.count_atoms('selection_b') == 1,
                       'Measurement endpoints were not restored as single atoms')
                _near_coordinates(_coords(pm.cmd, 'selection_a'), [[0, 0, 0]])
                _near_coordinates(_coords(pm.cmd, 'selection_b'), [[3, 4, 0]])
                _check(pm.cmd.get_type('measurement') == 'object:measurement', 'Distance object missing')
            return {'distance_angstrom': value, 'endpoint_atoms': [1, 1]}
        record('distance_5_angstrom', distance)

        def rigid_alignment():
            with pymol2.PyMOL() as pm:
                cmd = pm.cmd
                cmd.load(str(peptide), 'peptide')
                # Exact 90-degree z rotation plus a fixed translation; coordinate precision is unchanged.
                cmd.alter_state(1, 'peptide', '(x,y,z)=(-y+11,x-7,z+3)')
                cmd.save(str(mobile), 'peptide')
            reports['align'] = engine.align(mobile, peptide, output / 'align', **sizes)
            analysis = reports['align']['analysis']
            _check(analysis['mobile_atoms'] == analysis['target_atoms'] == analysis['aligned_atoms'] == 6,
                   'Alignment must pair all six C-alpha atoms')
            _check(analysis['rmsd_angstrom'] < 1e-4, f"Rigid alignment RMSD is {analysis['rmsd_angstrom']}")
            with pymol2.PyMOL() as pm:
                pm.cmd.load(str(output / 'align' / 'scene.pse'))
                _near_coordinates(_coords(pm.cmd, 'mobile and name CA'), _coords(pm.cmd, 'target and name CA'), tolerance=1e-4)
            return dict(rmsd_angstrom=analysis['rmsd_angstrom'], aligned_atoms=analysis['aligned_atoms'])
        record('rigid_alignment', rigid_alignment)

        def session_restore():
            checked = []
            for style in _SCENES:
                session = output / style / 'scene.pse'
                with pymol2.PyMOL() as pm:
                    cmd = pm.cmd
                    cmd.load(str(session))
                    expected = _snapshot(cmd)
                    cmd.hide('everything')
                    cmd.color('red', 'all')
                    cmd.translate([40, 20, -10], 'all')
                    cmd.bg_color('black')
                    cmd.turn('x', 75)
                    cmd.delete('all')
                    cmd.pseudoatom('temporary_atom')
                    cmd.load(str(session))
                    actual = _snapshot(cmd)
                    _check(actual == expected, f'{style}: session did not restore coordinates, objects, selections, representations, colors, view and background')
                    checked.append(style)
            return dict(scenes=checked, state=['coordinates', 'objects', 'selections', 'representations', 'colors', 'view', 'background', 'enabled'])
        record('session_restore', session_restore)

        def ray_image():
            try:
                from PIL import Image
            except ImportError as exc:
                raise RuntimeError('Image self-test requires Pillow; install Pillow into this PyMOL Python environment') from exc
            checked = []
            for style in _SCENES:
                with Image.open(output / style / 'scene.png') as image:
                    image.load()
                    _check(image.size == (240, 180), f'{style}: incorrect image dimensions {image.size}')
                    colors = image.convert('RGB').getcolors(240 * 180)
                    _check(colors is not None and len(colors) > 4, f'{style}: rendered image is blank or has too few colors')
                    checked.append(dict(scene=style, colors=len(colors)))
            return dict(size=[240, 180], ray=1, images=checked)
        record('ray_image', ray_image)

    # Original fixture files are gone. Replays may depend only on their copied inputs.
    def portable_replay():
        directories = ['space dir', '中文', "apostrophe'", 'bare;semicolon', 'dollar$()']
        evidence = []
        with tempfile.TemporaryDirectory(prefix='molscene-replay-') as temporary:
            replay_root = Path(temporary)
            elsewhere = replay_root / 'unrelated-cwd'
            elsewhere.mkdir()
            for name in directories:
                destination = replay_root / name
                destination.mkdir()
                for style in _SCENES:
                    relocated = destination / style
                    shutil.copytree(output / style, relocated)
                    previous = os.getcwd()
                    transcript = io.StringIO()
                    try:
                        os.chdir(elsewhere)
                        with pymol2.PyMOL() as pm:
                            with redirect_stdout(transcript), redirect_stderr(transcript):
                                _replay(pm.cmd, relocated)
                            _check(Path.cwd() == elsewhere, 'Standalone loader changed the caller working directory')
                            report = reports[style]
                            for object_name, stats in report['structure'].items():
                                _check(pm.cmd.count_atoms(object_name) == stats['atoms'], f'{name}/{style}: replay atom cardinality differs')
                            with pymol2.PyMOL() as saved:
                                saved.cmd.load(str(output / style / 'scene.pse'))
                                actual, expected = _snapshot(pm.cmd), _snapshot(saved.cmd)
                                _check(actual == expected, f'{name}/{style}: replay scene state differs from saved session')
                            _check(not re.search(r'(?im)(?:\bTraceback\b|\bException\b|\bError\s*:)', transcript.getvalue()),
                                   f'{name}/{style}: replay printed an error: ' + transcript.getvalue())
                    except Exception as exc:
                        raise RuntimeError(f'{name}/{style}: {exc}\nReplay transcript:\n{transcript.getvalue()}') from exc
                    finally:
                        os.chdir(previous)
                    evidence.append(dict(directory=name, scene=style, transcript=transcript.getvalue()))
        return dict(directories=directories, scenes=list(_SCENES), replays=evidence,
                    loader='save cwd; chdir(output); cmd.do("@scene.pml"); restore cwd', original_fixtures_removed=True)
    record('portable_replay', portable_replay)
    return finish()
