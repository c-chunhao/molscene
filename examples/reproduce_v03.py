"""Generate and validate the separate v0.3 interface/live-workflow gallery.

Uses a separately installed PyMOL and Pillow. Only the fixed OWNED_FILES and
validation.json are read or replaced under --output; unrelated files survive.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
INTERFACE_FILES = frozenset(('scene.png', 'scene.pse', 'scene.pml', 'report.json',
                             'interface.csv', 'inputs/input.pdb'))
LIVE_FILES = frozenset(('overview.png', 'surface.png', 'pocket.png', 'clean.png',
                       'scene.pse', 'scene.pml', 'workflows.json', 'inputs/input.pdb'))
OWNED_FILES = frozenset({f'interface/{name}' for name in INTERFACE_FILES}
                       | {f'live/{name}' for name in LIVE_FILES})
TEXT_FILES = frozenset(name for name in OWNED_FILES
                       if Path(name).suffix in ('.json', '.csv', '.pml'))


def _safe_path(path, *, file=False, missing=False):
    """lstat lexical ancestors before any resolve, read or write."""
    path = Path(path)
    if '..' in path.parts:
        raise ValueError('Parent traversal is not an output destination')
    for candidate in reversed((path, *path.parents)):
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            if missing:
                continue
            raise RuntimeError('Missing owned artifact') from None
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & 0x400:
            raise RuntimeError('Output contains a link or reparse point')
        if candidate == path and file:
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink > 1:
                raise RuntimeError('Owned artifact must be an unlinked regular file')
        elif not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeError('Output ancestor must be a directory')
    return path


def checked_artifact(directory, relative, allowed):
    if not isinstance(relative, str) or relative not in allowed:
        raise RuntimeError('Artifact is outside the fixed gallery inventory')
    return _safe_path(Path(directory) / relative, file=True)


def _json(path, value):
    _safe_path(path, file=True, missing=True)
    # Explicit LF matches committed Git blobs and the example ZIP on Windows.
    with path.open('w', encoding='utf-8', newline='\n') as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def validate_interface(report):
    """Check half-area convention, signed differences and complete residue sums."""
    json.dumps(report, allow_nan=False)
    if report.get('schema_version') != 2 or report.get('operation') != 'interface':
        raise RuntimeError('Expected schema-2 interface report')
    analysis = report['analysis']
    if report['parameters'].get('state') != 1 or analysis.get('state') != 1:
        raise RuntimeError('Gallery requires explicit state 1')
    if analysis.get('kind') != 'interface':
        raise RuntimeError('Expected interface result')
    close = lambda a, b: math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)
    total = 0.0
    seen = set()
    for partner in ('A', 'B'):
        values = analysis['partner_' + partner.lower()]
        if type(values['selected_atom_count']) is not int or values['selected_atom_count'] < 1:
            raise RuntimeError('Empty interface partner')
        rows = [row for row in analysis['residues'] if row['partner'] == partner]
        if not rows or sum(row['atom_count'] for row in rows) != values['selected_atom_count']:
            raise RuntimeError('Incomplete interface residue membership')
        for row in rows:
            identity = tuple(row[key] for key in ('partner', 'object', 'segi', 'chain', 'resi', 'resn'))
            if identity in seen or row['state'] != 1 or type(row['atom_count']) is not int or row['atom_count'] < 1:
                raise RuntimeError('Invalid interface residue identity')
            seen.add(identity)
        for row in [values, *rows]:
            if not close(row['isolated_area_angstrom2'] - row['complex_area_angstrom2'], row['buried_area_angstrom2']):
                raise RuntimeError('Inconsistent signed SASA difference')
        for field in ('isolated_area_angstrom2', 'complex_area_angstrom2', 'buried_area_angstrom2'):
            if not close(sum(row[field] for row in rows), values[field]):
                raise RuntimeError('Incomplete interface residue area sum')
        total += values['buried_area_angstrom2']
    if len(seen) != len(analysis['residues']):
        raise RuntimeError('Unknown interface residue partner')
    if not close(total, analysis['total_buried_area_angstrom2']) or not close(total / 2, analysis['interface_area_angstrom2']):
        raise RuntimeError('Total buried area and half-area convention disagree')


def read_interface(directory):
    report = json.loads(checked_artifact(directory, 'report.json', INTERFACE_FILES).read_text(encoding='utf-8'))
    reported = set()
    for value in report.get('artifacts', {}).values():
        for relative in value if isinstance(value, list) else [value]:
            checked_artifact(directory, relative, INTERFACE_FILES)
            reported.add(relative)
    if reported != INTERFACE_FILES:
        raise RuntimeError('Interface report must enumerate the complete fixed inventory')
    validate_interface(report)
    return report


def run_interface(data, output, width, height, overwrite):
    command = [sys.executable, '-B', '-m', 'molscene', 'interface', str(data / '1hsg.pdb'),
               '--selection-a', 'chain A and polymer.protein',
               '--selection-b', 'chain B and polymer.protein', '--state', '1',
               '--altloc', 'primary', '--width', str(width), '--height', str(height),
               '--output', str(output)]
    if overwrite:
        command.append('--overwrite')
    subprocess.run(command, cwd=ROOT, check=True)


def run_live(data, output, width, height, overwrite):
    import pymol2
    from molscene import analysis
    from molscene.desktop.controller import WorkbenchController
    from molscene.snapshot import scene_fingerprint

    output.mkdir(parents=True, exist_ok=True)
    (output / 'inputs').mkdir(exist_ok=True)
    checked_source = (data / '1hsg.pdb').read_bytes()
    (output / 'inputs/input.pdb').write_bytes(checked_source)
    with pymol2.PyMOL() as pm:
        cmd = pm.cmd
        # Read coordinate bytes instead of storing a developer's absolute load path.
        cmd.read_pdbstr(checked_source.decode('utf-8'), 'molecule')
        ctl = WorkbenchController(cmd, output)
        cmd.bg_color('white')
        for setting, value in (('ray_opaque_background', 1), ('antialias', 2),
                               ('orthoscopic', 1), ('depth_cue', 0), ('ray_shadows', 0)):
            cmd.set(setting, value)
        cmd.color('cyan', 'chain A')
        cmd.color('salmon', 'chain B')
        cmd.color('yellow', 'resn MK1')
        source_atoms = analysis.collect_atoms(cmd, '%molecule', state=1)
        source_bonds = [(b.index, b.order) for b in cmd.get_model('%molecule', state=1).bond]
        rows = ctl.residues('%molecule', state=1)
        chosen = [row for row in rows if row['polymer'] == 'protein' and row['resi'] == '25']
        capture = ctl.select_residues(chosen, state=1)
        saved = ctl.save_selection('catalytic_sites', capture['selection'], state=1)
        presets = []
        for preset in ('overview', 'surface', 'pocket'):
            result = ctl.apply_preset('%molecule', preset, state=1,
                                      ligand='resn MK1' if preset == 'pocket' else None,
                                      radius=5.0, frame=True)
            presets.append(result)
            cmd.deselect()
            cmd.png(str(output / (preset + '.png')), width=width, height=height, dpi=300, ray=1, quiet=1)
            if preset == 'overview':
                ctl.store_scene('overview_bookmark')
            elif preset == 'surface':
                ctl.update_scene('overview_bookmark')
                ctl.rename_scene('overview_bookmark', 'surface_bookmark')
        clean = ctl.prepare_structure('%molecule', object_name='clean_copy', state=1,
                                      remove_solvent=True, remove_hydrogens=True, altloc='primary')
        after = analysis.collect_atoms(cmd, '%molecule', state=1)
        bonds_after = [(b.index, b.order) for b in cmd.get_model('%molecule', state=1).bond]
        if source_atoms != after or source_bonds != bonds_after:
            raise RuntimeError('Live workflow changed original atoms or bonds')
        cmd.disable('molecule')
        ctl.apply_preset('%clean_copy', 'overview', state=1)
        cmd.deselect()
        cmd.png(str(output / 'clean.png'), width=width, height=height, dpi=300, ray=1, quiet=1)
        expected_fingerprint = scene_fingerprint(cmd, state=1)
        expected_scenes = ctl.scenes()
        cmd.save(str(output / 'scene.pse'))
        replay = ('# Standalone replay. Keep scene.pse beside this file.\npython\n'
                  'from pathlib import Path\n'
                  "cmd.load(str(Path(__script__).resolve().parent / 'scene.pse'), format='pse')\n"
                  'python end\n')
        with (output / 'scene.pml').open('w', encoding='utf-8', newline='\n') as handle:
            handle.write(replay)
        evidence = {'schema_version': 1, 'state': 1, 'pymol_version': cmd.get_version()[0],
                    'source_atoms': len(source_atoms), 'observed_residue_sites': len(rows),
                    'observed_protein_sites': sum(row['polymer'] == 'protein' for row in rows),
                    'capture': capture, 'named_selection': saved, 'presets': presets,
                    'clean': clean, 'source_unchanged': True, 'scenes': expected_scenes,
                    'definition': 'Observed coordinate sites; geometric pocket; clean copy filters atoms without protonation.'}
    with pymol2.PyMOL() as pm:
        pm.cmd.do('@' + str(output / 'scene.pml'))
        if scene_fingerprint(pm.cmd, state=1) != expected_fingerprint or list(pm.cmd.get_scene_list()) != expected_scenes:
            raise RuntimeError('Standalone live PML/PSE replay differs from saved scene')
        evidence['replay'] = {'passed': True, 'contract': 'bundled_session',
                              'source_fingerprint': expected_fingerprint}
    _json(output / 'workflows.json', evidence)


def generate(output, *, width=1000, height=750, overwrite=False):
    if type(width) is not int or type(height) is not int or not (1 <= width <= 16384 and 1 <= height <= 16384) or width * height > 64_000_000:
        raise ValueError('Invalid image dimensions')
    output = Path(output).expanduser().absolute()
    _safe_path(output, missing=True)
    # Check EVERY destination before the first backend can write anything.
    for relative in sorted(OWNED_FILES | {'validation.json'}):
        path = _safe_path(output / relative, file=True, missing=True)
        if path.exists() and not overwrite:
            raise ValueError('Existing owned artifacts require --overwrite')
    data = ROOT / 'examples/data'
    sources = json.loads((data / 'sources.json').read_text(encoding='utf-8'))
    hashes = {row['file']: row['sha256'] for row in sources['files']}
    for filename, expected in hashes.items():
        if hashlib.sha256((data / filename).read_bytes()).hexdigest() != expected:
            raise RuntimeError('Pinned coordinate checksum mismatch: ' + filename)
    output.mkdir(parents=True, exist_ok=True)
    run_interface(data, output / 'interface', width, height, overwrite)
    interface = read_interface(output / 'interface')
    if interface.get('inputs') != [{'role': 'input', 'basename': '1hsg.pdb', 'sha256': hashes['1hsg.pdb']}]:
        # Additional input metadata is harmless; provenance values must still match exactly.
        if len(interface.get('inputs', [])) != 1 or any(
                source.get('basename') != '1hsg.pdb' or source.get('sha256') != hashes['1hsg.pdb']
                for source in interface['inputs']):
            raise RuntimeError('Interface input provenance differs from pinned coordinates')
    run_live(data, output / 'live', width, height, overwrite)
    live = json.loads(checked_artifact(output / 'live', 'workflows.json', LIVE_FILES).read_text(encoding='utf-8'))
    json.dumps(live, allow_nan=False)
    clean = live['clean']
    if live.get('state') != 1 or live.get('replay', {}).get('passed') is not True or clean['selected_atom_count'] != live['source_atoms'] or clean['retained_atom_count'] + sum(clean['removed'].values()) != live['source_atoms']:
        raise RuntimeError('Live membership, clean-copy counts or replay validation failed')
    from PIL import Image
    files = []
    for relative in sorted(OWNED_FILES):
        path = checked_artifact(output, relative, OWNED_FILES)
        content = path.read_bytes()
        if relative in TEXT_FILES:
            content = content.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
            path.write_bytes(content)
        if path.suffix == '.png':
            with Image.open(path) as image:
                if image.size != (width, height) or len(image.convert('RGB').getcolors(width * height) or []) < 20:
                    raise RuntimeError('Gallery image dimensions or molecular content failed')
        if relative.endswith('/inputs/input.pdb') and hashlib.sha256(content).hexdigest() != hashes['1hsg.pdb']:
            raise RuntimeError('Copied coordinate bytes differ from pinned input')
        files.append({'file': relative, 'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content)})
    from molscene import __version__
    manifest = {'schema_version': 1, 'molscene_version': __version__,
                'generated_at': datetime.now(timezone.utc).isoformat(),
                'python': sys.version.split()[0], 'width': width, 'height': height,
                'source_sha256': hashes, 'interface': interface['analysis'],
                'live_report': 'live/workflows.json', 'files': files}
    _json(output / 'validation.json', manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'examples/outputs/v0.3')
    parser.add_argument('--overwrite', action='store_true')
    parser.add_argument('--width', type=int, default=1000)
    parser.add_argument('--height', type=int, default=750)
    args = parser.parse_args()
    manifest = generate(args.output, width=args.width, height=args.height, overwrite=args.overwrite)
    print(f"Validated v0.3 gallery: {len(manifest['files'])} owned files")


if __name__ == '__main__':
    main()
