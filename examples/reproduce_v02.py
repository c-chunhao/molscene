"""Regenerate the public v0.2 gallery using a separately installed PyMOL.

Run from a source checkout. This script performs real CLI calls, checks their
artifacts, then writes relative file hashes; it does not fabricate images.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
TEXT_ARTIFACTS = {'report.json', 'scene.pml', 'contacts.csv', 'sasa.csv',
                  'chains.csv', 'sequence.fasta', 'batch.json', 'batch.csv'}
REPORT_ARTIFACTS = (TEXT_ARTIFACTS - {'batch.json', 'batch.csv'}) | {'scene.png', 'scene.pse'} | {
    f'inputs/{role}{suffix}' for role in ('input', 'mobile', 'target')
    for suffix in ('.pdb', '.cif', '.mmcif')}


def checked_artifact(directory, relative, allowed):
    """Accept only fixed owned names, before following or reading any path."""
    if not isinstance(relative, str) or relative not in allowed:
        raise RuntimeError('Unexpected or nonrelative artifact path')
    path = directory / relative
    for candidate in (path, *path.parents):
        metadata = candidate.lstat()
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & 0x400:
            raise RuntimeError('Artifact path contains a link or reparse point')
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink > 1:
        raise RuntimeError('Artifact must be an unlinked regular file')
    if directory.resolve() not in path.resolve().parents:
        raise RuntimeError('Artifact escapes its workflow directory')
    return path


def read_report(directory, inventory):
    path = checked_artifact(directory, 'report.json', REPORT_ARTIFACTS)
    report = json.loads(path.read_text(encoding='utf-8'))
    if report.get('schema_version') != 2 or not isinstance(report.get('artifacts'), dict):
        raise RuntimeError('Expected report schema_version 2 and fixed artifacts')
    json.dumps(report, allow_nan=False)
    inventory.add(path)
    for value in report['artifacts'].values():
        for relative in value if isinstance(value, list) else [value]:
            inventory.add(checked_artifact(directory, relative, REPORT_ARTIFACTS))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'examples/outputs')
    parser.add_argument('--overwrite', action='store_true')
    parser.add_argument('--width', type=int, default=1000)
    parser.add_argument('--height', type=int, default=750)
    args = parser.parse_args()
    output = args.output.absolute()
    manifest = output / 'validation.json'
    for path in (manifest, output, *output.parents):
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or (getattr(metadata, 'st_file_attributes', 0)
                                              & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)):
            parser.error('Output must not traverse a link or reparse point')
    if manifest.exists() and not manifest.is_file():
        parser.error('validation.json must be a regular file')
    if manifest.exists() and not args.overwrite:
        parser.error('Existing validation.json requires --overwrite')
    from PIL import Image
    version = subprocess.run([sys.executable, '-B', '-m', 'molscene', '--version'],
                             cwd=ROOT, check=True, text=True, capture_output=True).stdout.strip()
    data = ROOT / 'examples/data'
    source_manifest = json.loads((data / 'sources.json').read_text(encoding='utf-8'))
    source_hashes = {item['file']: item['sha256'] for item in source_manifest['files']}
    for filename, expected in source_hashes.items():
        if hashlib.sha256((data / filename).read_bytes()).hexdigest() != expected:
            raise RuntimeError(f'Pinned source checksum mismatch: {filename}')

    def check_sources(report):
        for source in report['inputs']:
            if source_hashes.get(source['basename']) != source['sha256']:
                raise RuntimeError('Report input provenance differs from pinned coordinates')

    ubq, hsg = str(data / '1ubq.pdb'), str(data / '1hsg.pdb')
    workflows = [
        ('overview', ['render', ubq]),
        ('pocket', ['render', hsg, '--style', 'pocket', '--ligand', 'resn MK1', '--radius', '5']),
        ('surface', ['render', ubq, '--representation', 'surface', '--coloring', 'bfactor']),
        ('alignment', ['align', ubq, str(data / '1ubq.cif'), '--mobile-state', '1', '--target-state', '1']),
        ('angle', ['angle', ubq, '--selection-a', 'resi 1 and name CA', '--selection-b', 'resi 2 and name CA', '--selection-c', 'resi 3 and name CA']),
        ('dihedral', ['dihedral', ubq, '--selection-a', 'resi 1 and name CA', '--selection-b', 'resi 2 and name CA', '--selection-c', 'resi 3 and name CA', '--selection-d', 'resi 4 and name CA']),
        ('contacts', ['contacts', hsg, '--selection-a', 'resn MK1', '--selection-b', 'polymer.protein', '--cutoff', '4']),
        ('sasa', ['sasa', ubq, '--selection', 'polymer.protein', '--context', 'not solvent and not hydro', '--altloc', 'primary']),
        ('inspect', ['inspect', str(data / '1ubq.cif')]),
    ]
    evidence = {'schema_version': 1, 'molscene_version': version.split()[-1],
                'generated_at': datetime.now(timezone.utc).isoformat(),
                'source_sha256': source_hashes,
                'python': sys.version.split()[0], 'width': args.width,
                'height': args.height, 'workflows': [], 'files': []}
    inventory = set()
    for name, command in workflows:
        directory = output / name
        command += ['--output', str(directory)]
        if name != 'alignment':
            command += ['--state', '1']
        if name != 'inspect':
            command += ['--width', str(args.width), '--height', str(args.height)]
        if args.overwrite:
            command.append('--overwrite')
        subprocess.run([sys.executable, '-B', '-m', 'molscene', *command], cwd=ROOT, check=True)
        report = read_report(directory, inventory)
        check_sources(report)
        parameters = report.get('parameters', {})
        expected_states = ('mobile_state', 'target_state') if name == 'alignment' else ('state',)
        if any(parameters.get(key) != 1 for key in expected_states):
            raise RuntimeError(f'{name}: report state disagrees with requested state 1')
        if name != 'inspect':
            image_path = checked_artifact(directory, 'scene.png', REPORT_ARTIFACTS)
            inventory.add(image_path)
            with Image.open(image_path) as img:
                if img.size != (args.width, args.height):
                    raise RuntimeError(f'{name}: image dimensions disagree')
                if len(img.convert('RGB').getcolors(img.width * img.height) or []) < 20:
                    raise RuntimeError(f'{name}: image lacks molecular content')
        evidence['workflows'].append({'name': name, 'report': f'{name}/report.json',
                                      'pymol_version': report.get('pymol_version'),
                                      'analysis': report.get('analysis'),
                                      'parameters': report.get('parameters')})
    batch_command = ['batch', str(data), '--state', '1', '--width', str(args.width),
                     '--height', str(args.height), '--output', str(output / 'batch')]
    if args.overwrite:
        batch_command.append('--overwrite')
    subprocess.run([sys.executable, '-B', '-m', 'molscene', *batch_command], cwd=ROOT, check=True)
    batch_dir = output / 'batch'
    for name in ('batch.json', 'batch.csv'):
        inventory.add(checked_artifact(batch_dir, name, {'batch.json', 'batch.csv'}))
    batch = json.loads((batch_dir / 'batch.json').read_text(encoding='utf-8'))
    if not batch['passed'] or batch['completed'] != 3 or batch['failed'] or batch['cancelled']:
        raise RuntimeError('Expected three completed pinned batch inputs')
    pinned_inputs = sorted(source_hashes, key=lambda name: (name.casefold(), name))
    if len(batch['items']) != len(pinned_inputs):
        raise RuntimeError('Batch items differ from pinned inputs')
    for index, (item, source) in enumerate(zip(batch['items'], pinned_inputs), 1):
        # The pinned input names have simple ASCII stems; these are the exact
        # deterministic batch destinations, never arbitrary reported directories.
        expected_output = f'{index:04d}-{Path(source).stem}'
        expected_report = expected_output + '/report.json'
        if (item.get('input') != source or item.get('output') != expected_output
                or item.get('report') != expected_report or item.get('status') != 'completed'):
            raise RuntimeError('Batch item does not match its fixed pinned destination')
        report_path = checked_artifact(batch_dir, item['report'], {expected_report})
        report = read_report(report_path.parent, inventory)
        check_sources(report)
        if report['parameters']['state'] != 1:
            raise RuntimeError('Batch report state disagrees')
        image_path = checked_artifact(report_path.parent, 'scene.png', REPORT_ARTIFACTS)
        inventory.add(image_path)
        with Image.open(image_path) as img:
            if img.size != (args.width, args.height):
                raise RuntimeError('Batch image dimensions disagree')
    evidence['workflows'].append({'name': 'batch', 'report': 'batch/batch.json',
                                  'completed': batch['completed'], 'failed': batch['failed']})
    # Git publishes these text artifacts with LF. Canonicalize before hashing
    # so Windows generation, Git checkouts and ZIP contents share the bytes.
    # Coordinate input copies retain their separately pinned original bytes.
    owned_names = {entry.relative_to(output).as_posix() for entry in inventory}
    for path in sorted(inventory):
        checked_artifact(output, path.relative_to(output).as_posix(), owned_names)
        content = path.read_bytes()
        if path.name in TEXT_ARTIFACTS:
            content = content.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
            path.write_bytes(content)
        evidence['files'].append({'file': path.relative_to(output).as_posix(),
                                 'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content)})
    # Replace our manifest atomically instead of following an existing filename
    # while writing. Engine commands independently protect their own artifacts.
    descriptor, temporary = tempfile.mkstemp(prefix='.molscene-validation-', dir=output)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(json.dumps(evidence, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
        os.replace(temporary, manifest)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f'Validated {len(workflows) + 1} workflows; manifest: {manifest}')


if __name__ == '__main__':
    main()
