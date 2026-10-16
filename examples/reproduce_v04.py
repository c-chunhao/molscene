"""Offline advanced gallery; external solvers are explicit, separately installed tools.

Private original bundles are retained in a sibling directory. Public derivatives
redact local text paths, identify that derivation and rebuild all affected hashes.
Session privacy and scientific appearance still require release review.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import uuid


ROOT = Path(__file__).resolve().parents[1]
# Direct source/sdist execution uses its adjacent package. A copy outside a
# checkout uses only the installed package, including under isolated Python.
if (ROOT / 'molscene' / '__init__.py').is_file() and str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
UBIQUITIN_SHA256 = 'd4a6812d8951cf6594e6a0763f089e35f5a80b62acb3c117b2c5565228a7b161'
PROJECT_FILES = {'source.pse', 'timeline.json', 'replay.pml', 'report.json', 'manifest.json'}
DENSITY_FILES = {'inputs/density.dx', 'density.json', 'scene.pse', 'scene.png',
                 'replay.pml', 'report.json', 'manifest.json'}
POTENTIAL_FILES = {'source.pse', 'inputs/selection.pdb', 'prepared.pqr', 'preparation.json',
                   'potential.dx', 'apbs.in', 'tools.json', 'logs/apbs.stdout.log',
                   'logs/apbs.stderr.log', 'logs/pdb2pqr.stdout.log', 'logs/pdb2pqr.stderr.log',
                   'scene.pse', 'scene.png', 'replay.pml', 'report.json', 'manifest.json'}
FRAME_COUNT = 4
_PATH_FIELDS = {'argv', 'path', 'source_path', 'executable', 'interpreter',
                'stdout_path', 'stderr_path', 'base_prefix', 'prefix',
                'python_executable', 'metadata_path', 'package_path'}


def _local_paths(value, field=None):
    """Discover provenance paths without treating PyMOL slash selections as paths."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _local_paths(key)
            yield from _local_paths(item, key)
    elif isinstance(value, list):
        for item in value:
            yield from _local_paths(item, field)
    elif isinstance(value, str) and (
            re.match(r'^[A-Za-z]:[\\/]', value)
            or re.match(r'^/(?:home|Users|tmp|private|workspace|__w|opt|usr|var|build|srv)(?:/|$)', value)
            or field in _PATH_FIELDS and value.startswith('/')):
        yield value


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def public_coordinate_path():
    """Find pinned data beside source or under the installed wheel prefix."""
    candidates = [ROOT / 'examples/data/1ubq.pdb',
                  Path(sys.prefix) / 'share/molscene/examples/data/1ubq.pdb']
    for candidate in candidates:
        candidate = _safe(candidate, missing=True)
        if candidate.is_file():
            if _sha(candidate) != UBIQUITIN_SHA256:
                raise ValueError('Pinned public ubiquitin bytes do not match')
            return candidate
    raise ValueError('Pinned public ubiquitin data is missing from source and installation')


def _json(path, value):
    with path.open('w', encoding='utf-8', newline='\n') as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def _safe(path, *, missing=False):
    if '..' in Path(path).parts:
        raise ValueError('Parent traversal is not an example path')
    path = Path(os.path.abspath(path))
    for candidate in reversed((path, *path.parents)):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            if missing:
                continue
            raise ValueError('Missing example path') from None
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Example paths cannot traverse links or reparse points')
        if candidate != path and not stat.S_ISDIR(info.st_mode):
            raise ValueError('Example ancestor is not a directory')
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise ValueError('Example paths cannot use hardlinked files')
    return path


def _files(root):
    root = _safe(root)
    files = set()
    for directory, directories, names in os.walk(root, followlinks=False):
        for name in directories:
            _safe(Path(directory) / name)
        for name in names:
            path = _safe(Path(directory) / name)
            if not stat.S_ISREG(path.lstat().st_mode):
                raise ValueError('Bundle payload must contain regular files')
            files.add(path.relative_to(root).as_posix())
    return files


def audit_bundle(root):
    """Validate the exact manifest inventory, regular files, byte counts and hashes."""
    root = _safe(root)
    actual = _files(root)
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    if (set(manifest) != {'schema_version', 'files'}
            or type(manifest['schema_version']) is not int or manifest['schema_version'] != 1
            or not isinstance(manifest['files'], dict)):
        raise ValueError('Invalid bundle manifest')
    expected = set(manifest['files']) | {'manifest.json'}
    if actual != expected:
        raise ValueError('Bundle contains missing or unexpected files')
    allowed_directories = {parent.as_posix() for relative in expected
        for parent in PurePosixPath(relative).parents if parent.as_posix() != '.'}
    for directory, directories, _ in os.walk(root, followlinks=False):
        for name in directories:
            if (Path(directory) / name).relative_to(root).as_posix() not in allowed_directories:
                raise ValueError('Bundle contains an unexpected directory')
    for relative, entry in manifest['files'].items():
        parts = PurePosixPath(relative)
        if (not isinstance(relative, str) or '\\' in relative or parts.is_absolute()
                or '..' in parts.parts or parts.as_posix() != relative or relative == 'manifest.json'
                or not isinstance(entry, dict) or set(entry) != {'sha256', 'bytes'}
                or type(entry['bytes']) is not int or entry['bytes'] < 0
                or not isinstance(entry['sha256'], str) or re.fullmatch('[0-9a-f]{64}', entry['sha256']) is None):
            raise ValueError('Invalid manifest entry')
        path = _safe(root / relative)
        if entry['bytes'] != path.stat().st_size or entry['sha256'] != _sha(path):
            raise ValueError('Bundle payload hash or size mismatch')
    return expected


def project_public_bundle(original, destination, *, private_paths):
    """Copy one closed bundle; preserve science and original bytes, redact local text."""
    original, destination = _safe(original), _safe(destination, missing=True)
    inventory = audit_bundle(original)
    if destination.exists():
        raise ValueError('Public derivative requires a new directory')
    if original == destination or original in destination.parents or destination in original.parents:
        raise ValueError('Original and derivative cannot contain each other')
    discovered = []
    for relative in inventory:
        if Path(relative).suffix.lower() == '.json':
            discovered.extend(_local_paths(json.loads((original / relative).read_text(encoding='utf-8'))))
    prefixes = set()
    for value in [*private_paths, *discovered]:
        if not str(value):
            continue
        prefixes.update((str(value), str(value).replace('\\', '/'), str(value).replace('/', '\\')))
    prefixes = sorted(prefixes, key=len, reverse=True)
    before = {relative: _sha(original / relative) for relative in inventory}
    redactions = []
    destination.mkdir()
    for relative in sorted(inventory - {'report.json', 'manifest.json'}):
        source = original / relative
        data = source.read_bytes()
        if source.suffix.lower() in {'.json', '.log', '.pml', '.in'}:
            text = data.decode('utf-8')
            count = 0
            for prefix in prefixes:
                for variant in (prefix, json.dumps(prefix)[1:-1]):
                    occurrences = text.count(variant)
                    if occurrences:
                        text = text.replace(variant, '<local-path>')
                        count += occurrences
            if count:
                redactions.append({'artifact': relative, 'kind': 'local absolute path', 'occurrences': count})
            data = text.encode('utf-8')
        elif source.suffix.lower() in {'.dx', '.pdb', '.pqr', '.cif', '.mrc', '.ccp4', '.map'}:
            if any(prefix.encode('utf-8') in data for prefix in prefixes):
                raise ValueError('Scientific input contains a local path; retain it privately instead of rewriting scientific bytes')
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    report_text = (original / 'report.json').read_text(encoding='utf-8')
    count = 0
    for prefix in prefixes:
        for variant in (prefix, json.dumps(prefix)[1:-1]):
            occurrences = report_text.count(variant)
            if occurrences:
                report_text = report_text.replace(variant, '<local-path>')
                count += occurrences
    if count:
        redactions.append({'artifact': 'report.json', 'kind': 'local absolute path', 'occurrences': count})
    report = json.loads(report_text)
    report['public_projection'] = {
        'derivative': True, 'original_manifest_sha256': before['manifest.json'],
        'redactions': redactions, 'numeric_values_preserved': True,
        'session_privacy': 'PSE bytes are preserved; independent binary/session review is required before publication'}
    report['artifact_hashes'] = {relative: _sha(destination / relative)
        for relative in sorted(inventory - {'report.json', 'manifest.json'})}
    _json(destination / 'report.json', report)
    _json(destination / 'manifest.json', {'schema_version': 1, 'files': {
        relative: {'sha256': _sha(destination / relative), 'bytes': (destination / relative).stat().st_size}
        for relative in sorted(inventory - {'manifest.json'})}})
    audit_bundle(destination)
    if before != {relative: _sha(original / relative) for relative in inventory}:
        raise ValueError('Original changed during public projection')
    return report['public_projection']


def _fixtures(directory):
    """Create an asymmetric scalar field, synthetic sphere scenes and public UBQ PSE."""
    import pymol2
    from molscene.timeline import capture_keyframe, edit_keyframe, new_timeline
    directory.mkdir()
    shape, origin = (17, 15, 13), (2.0, -3.0, 1.0)
    values = [math.exp(-((i-5)**2 / 12 + (j-8)**2 / 8 + (k-4)**2 / 5))
              + 0.4 * math.exp(-((i-12)**2 / 5 + (j-3)**2 / 9 + (k-9)**2 / 8))
              for i in range(shape[0]) for j in range(shape[1]) for k in range(shape[2])]
    header = [f'object 1 class gridpositions counts {shape[0]} {shape[1]} {shape[2]}',
              f'origin {origin[0]} {origin[1]} {origin[2]}',
              'delta 0.5 0 0', 'delta 0 0.6 0', 'delta 0 0 0.7',
              f'object 2 class gridconnections counts {shape[0]} {shape[1]} {shape[2]}',
              f'object 3 class array type double rank 0 items {len(values)} data follows']
    (directory / 'synthetic.dx').write_text('\n'.join(header) + '\n' +
        '\n'.join(' '.join(f'{x:.12g}' for x in values[i:i+3]) for i in range(0, len(values), 3)) +
        '\nattribute "dep" string "positions"\nobject "synthetic" class field\n'
        'component "positions" value 1\ncomponent "connections" value 2\ncomponent "data" value 3\n', encoding='utf-8')
    with pymol2.PyMOL() as backend:
        cmd = backend.cmd
        cmd.pseudoatom('sphere', pos=[5, 1, 4], vdw=1.5, elem='C')
        cmd.show('spheres', 'sphere')
        cmd.color('cyan', 'sphere')
        cmd.bg_color('white')
        cmd.zoom('sphere', buffer=5)
        doc = new_timeline(cmd, fps=12)
        cmd.turn('y', 40)
        cmd.color('orange', 'sphere')
        cmd.scene('orange_display', 'store')
        doc = edit_keyframe(doc, 'add', values=capture_keyframe(cmd, frame=FRAME_COUNT,
            state=1, scene='orange_display', label='Display cut', interpolation='hold'))
        cmd.save(str(directory / 'synthetic.pse'))
        _json(directory / 'timeline.json', doc)
        cmd.reinitialize()
        source = public_coordinate_path()
        cmd.read_pdbstr(source.read_text(encoding='utf-8'), 'ubiquitin')
        cmd.hide('everything', 'all')
        cmd.show('cartoon', 'polymer.protein')
        cmd.orient('polymer.protein')
        cmd.save(str(directory / 'ubiquitin.pse'))
    _json(directory / 'registration.json', [0, -1, 0, 8, 1, 0, 0, -2,
                                          0, 0, 1, 3, 0, 0, 0, 1])


def _run_cli(arguments, directory, label):
    result = subprocess.run([sys.executable, '-B', '-m', 'molscene', *map(str, arguments)],
        cwd=ROOT, text=True, capture_output=True, check=False)
    (directory / (label + '.stdout.log')).write_text(result.stdout, encoding='utf-8')
    (directory / (label + '.stderr.log')).write_text(result.stderr, encoding='utf-8')
    if result.returncode:
        raise RuntimeError(f'{label} failed; inspect retained private CLI logs')


def generate(output, *, tools=None, skip_potential=False):
    """Generate through the shared CLI, then independently audit each closed bundle."""
    output = _safe(output, missing=True)
    if output.exists():
        raise ValueError('Choose a new example output directory')
    if not skip_potential and tools is None:
        raise ValueError('--tools is required unless --skip-potential is explicit')
    tools = None if tools is None else _safe(tools)
    if tools is None:
        config = None
    else:
        from molscene.external_tools import load_tools
        config = load_tools(tools)
    if not skip_potential and not {'apbs', 'pdb2pqr'}.issubset(config['tools']):
        raise ValueError('--tools must configure apbs and pdb2pqr for the real potential example')
    output.parent.mkdir(parents=True, exist_ok=True)
    private = output.parent / ('.molscene-v04-private-' + uuid.uuid4().hex[:12])
    _safe(private, missing=True)
    private.mkdir()
    _fixtures(private / 'inputs')
    inputs = private / 'inputs'
    render = ['--width', '800', '--height', '600', '--dpi', '96', '--no-ray']
    jobs = [
        ('timeline-project', ['timeline-project', inputs / 'synthetic.pse', '--timeline', inputs / 'timeline.json']),
        ('animation', ['animate', inputs / 'synthetic.pse', '--timeline', inputs / 'timeline.json', *render]),
        ('density', ['density', inputs / 'synthetic.dx', '--format', 'dx', '--kind', 'mesh',
                     '--level', '0.35', '--matrix', inputs / 'registration.json', *render]),
    ]
    expected = {'timeline-project': PROJECT_FILES,
                'animation': PROJECT_FILES | {f'frames/frame_{frame:06d}.png' for frame in range(1, FRAME_COUNT+1)},
                'density': DENSITY_FILES}
    if not skip_potential:
        jobs.append(('potential', ['potential', '--mode', 'prepare', '--session', inputs / 'ubiquitin.pse',
            '--selection', 'ubiquitin and polymer.protein', '--state', '1', '--force-field', 'PARSE',
            '--ph', '7', '--protonation', 'propka', '--drop-water', '--grid', '65', '65', '65',
            '--units', 'kBT/e', '--display-units', 'kBT/e', '--ramp-limit', '5', '--tools', tools, *render]))
        expected['potential'] = POTENTIAL_FILES
    for label, command in jobs:
        _run_cli([*command, '--output', private / label], private, label)
        if audit_bundle(private / label) != expected[label]:
            raise ValueError('Example produced an unexpected feature inventory: ' + label)
    from molscene.bundles import Bundle
    inventory = ['validation.json', 'manifest.json'] + [f'{label}/{relative}'
        for label, files in expected.items() for relative in sorted(files)]
    private_paths = [str(private), str(ROOT), str(output), str(tools) if tools else '']
    if config:
        for entry in config['tools'].values():
            private_paths.extend([entry['argv'][0], *entry['path_dirs']])
            private_paths.extend(item['path'] for item in entry['runtime_files'])
    with Bundle(output, protected=[private, *([tools] if tools else [])], inventory=inventory) as bundle:
        projections = {label: project_public_bundle(private / label, bundle.stage / label,
            private_paths=private_paths) for label in expected}
        _json(bundle.stage / 'validation.json', {
            'schema_version': 1, 'potential': {'executed': not skip_potential,
                'skip_requested_by_user': skip_potential},
            'public_coordinate_sha256': UBIQUITIN_SHA256,
            'synthetic_map': {'shape': [17, 15, 13], 'origin_angstrom': [2, -3, 1],
                'registration': '90 degree z rotation plus (8,-2,3) angstrom translation'},
            'timeline': {'frames': FRAME_COUNT, 'fps': 12}, 'bundles': projections,
            'private_original_directory': private.name,
            'limitations': ['Synthetic density is an illustrative scalar field, not experimental electron density.',
                'Potential is LPBE with explicit preparation assumptions, not affinity or free energy.',
                'Binary/session privacy and scientific appearance require independent release review.']})
        bundle.publish()
    audit_bundle(output)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools', type=Path, help='Explicit schema-1 external tool config (APBS and PDB2PQR)')
    parser.add_argument('--output', type=Path, required=True, help='New output directory')
    parser.add_argument('--skip-potential', action='store_true',
                        help='Explicitly omit real potential; recorded as a user-requested skip')
    args = parser.parse_args(argv)
    if not args.skip_potential and args.tools is None:
        parser.error('--tools is required unless --skip-potential is explicit')
    try:
        generate(args.output, tools=args.tools, skip_potential=args.skip_potential)
    except (ValueError, RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print('Advanced example complete; private originals retained beside output. Review sessions before publication.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
