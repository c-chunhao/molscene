"""Offline v0.5 daily gallery: public ubiquitin plus original synthetic polar fragments.

Uses only distributed data and the installed MolScene/PyMOL public APIs. A fresh
output directory is required. Private originals remain beside the public gallery;
projection preserves PSE, PNG and coordinate bytes for independent release review.
"""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / 'molscene/__init__.py').is_file() and str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
UBIQUITIN_SHA256 = 'd4a6812d8951cf6594e6a0763f089e35f5a80b62acb3c117b2c5565228a7b161'
POLAR_FIXTURE_SHA256 = 'acfc6847b355e47185ef6224493a1718423ac18a7c18c46ad9b8612df6d8715e'
POLAR_FILES = {'scene.pse','scene.png','scene.pml','polar.csv','report.json','manifest.json','inputs/input.pdb'}
EXPECTED_FILES = {
    'polar-public': POLAR_FILES,
    'polar-synthetic': POLAR_FILES,
    'labels': {'scene.pse','scene.png','replay.pml','labels.json','labels.csv','report.json','manifest.json','inputs/input.pdb'},
    'coordinates': {'source.pse','objects/0001.cif','membership.csv','replay.pml','report.json','manifest.json'},
    'session-save': {'session.pse','replay.pml','report.json','manifest.json'},
}
_PATH_FIELDS = {'argv','path','source_path','executable','interpreter','stdout_path','stderr_path',
                'base_prefix','prefix','python_executable','metadata_path','package_path','output_path'}


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
        'redactions': redactions, 'numeric_values_preserved': True, 'original_artifact_sha256': before,
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


def seal_bundle(root):
    """Add a closed manifest to an original legacy-style figure bundle."""
    root = _safe(root)
    files = _files(root)
    if 'manifest.json' in files:
        raise ValueError('Refusing to replace an existing manifest')
    _json(root/'manifest.json', {'schema_version':1, 'files':{
        name:{'sha256':_sha(root/name),'bytes':(root/name).stat().st_size} for name in sorted(files)}})
    audit_bundle(root)


def polar_fixture_path():
    for candidate in (ROOT/'examples/data/polar-fragments.json',
                      Path(sys.prefix)/'share/molscene/examples/data/polar-fragments.json'):
        candidate = _safe(candidate, missing=True)
        if candidate.is_file():
            if _sha(candidate) != POLAR_FIXTURE_SHA256:
                raise ValueError('Pinned synthetic polar fixture bytes do not match')
            return candidate
    raise ValueError('Synthetic polar fixture is missing from source and installation')


def _synthetic_input(path):
    import pymol2
    from chempy import Atom, Bond
    from chempy.models import Indexed
    fixture = json.loads(polar_fixture_path().read_text(encoding='utf-8'))
    with pymol2.PyMOL() as pm:
        for row in fixture['objects']:
            model = Indexed()
            for record in row['atoms']:
                atom = Atom()
                atom.name, atom.symbol, atom.coord = record['name'], record['element'], record['coord']
                atom.resn, atom.resi, atom.chain = 'TST', '12A', 'A'
                model.atom.append(atom)
            for left,right,order in row['bonds']:
                bond = Bond()
                bond.index, bond.order = [left,right], order
                model.bond.append(bond)
            pm.cmd.load_model(model, row['name'], zoom=0)
        pm.cmd.save(str(path), 'all', state=1, quiet=1)


def _synthetic_display(original, destination):
    """Native element-colored derivative; retain the raw engine figure privately."""
    import pymol2
    inventory = audit_bundle(original)
    destination.mkdir()
    for relative in sorted(inventory - {'scene.png','scene.pse','report.json','manifest.json'}):
        target = destination/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((original/relative).read_bytes())
    palette = [('N','blue'),('O','red'),('C','gray70'),('H','gray85')]
    replay = destination/'scene.pml'
    with replay.open('a',encoding='utf-8',newline='\n') as handle:
        handle.write('\n# Native element colors for the illustrative bonded fixture.\npython\n')
        for element,color in palette:
            handle.write('cmd.color('+repr(color)+', '+repr('molecule and elem '+element)+')\n')
        handle.write('python end\n')
    with pymol2.PyMOL() as pm:
        # Rebuild from distributed relative coordinates and frozen annotation.
        pm.cmd.do('@'+str(replay),echo=0)
        pm.cmd.png(str(destination/'scene.png'),width=800,height=600,dpi=96,ray=1,quiet=1)
        pm.cmd.save(str(destination/'scene.pse'),quiet=1)
    report = json.loads((original/'report.json').read_text(encoding='utf-8'))
    report['gallery_display'] = dict(derivative=True,method='Native public color commands plus CPU ray; no pixel editing',
        element_colors=dict(palette),original_artifact_sha256={name:_sha(original/name) for name in sorted(inventory)},
        science_preserved='Original input, complete CSV, numerical analysis and source identities retained unchanged')
    _json(destination/'report.json',report)
    seal_bundle(destination)


def _labels(source, output, session):
    import pymol2
    from molscene.annotations import apply_labels
    from molscene import __version__
    output.mkdir()
    (output/'inputs').mkdir()
    (output/'inputs/input.pdb').write_bytes(source.read_bytes())
    with pymol2.PyMOL() as pm:
        cmd = pm.cmd
        # read_pdbstr avoids embedding a private load path in the native scene.
        cmd.read_pdbstr(source.read_text(encoding='utf-8'), 'ubiquitin')
        cmd.hide('everything','all')
        cmd.show('cartoon','ubiquitin')
        cmd.color('marine','ubiquitin')
        cmd.bg_color('white')
        cmd.orient('ubiquitin')
        cmd.zoom('ubiquitin', 3)
        selection = 'ubiquitin and resi 1-5 and name CA'
        labels = apply_labels(cmd, selection, template='chain_residue', state=1, size=18, color='black')
        cmd.set('ray_shadows',0)
        offsets = [(-4,-4,0),(-5,1,0),(-3,5,0),(10,10,0),(13,2,0)]
        for target, offset in zip(labels['targets'], offsets):
            cmd.select_list('_target','ubiquitin',[target['index']],state=1,mode='index')
            cmd.set('label_position',list(offset),'_target')
        cmd.delete('_target')
        cmd.set('label_connector',1)
        cmd.set('float_labels',1)
        labels['display_offsets'] = [list(offset) for offset in offsets]
        _json(output/'labels.json',labels)
        fields = ['object','index','segi','chain','resi','resn','name','element','alt','state','text']
        with (output/'labels.csv').open('w',newline='',encoding='utf-8') as handle:
            writer = csv.DictWriter(handle,fieldnames=fields)
            writer.writeheader()
            writer.writerows(labels['targets'])
        lines = ['# Literal recipe targets; public PyMOL replay without MolScene.', 'python',
            'from pathlib import Path', '_root = Path(__script__).resolve().parent',
            "cmd.read_pdbstr((_root/'inputs/input.pdb').read_text(encoding='utf-8'), 'ubiquitin')",
            "cmd.hide('everything','all')", "cmd.show('cartoon','ubiquitin')",
            "cmd.color('marine','ubiquitin')", "cmd.bg_color('white')"]
        for target, offset in zip(labels['targets'], offsets):
            lines.append("cmd.select_list('_target', 'ubiquitin', "+repr([target['index']])+", state=1, mode='index')")
            lines.append("cmd.label('_target', "+repr(repr(target['text']))+")")
            lines.append("cmd.set('label_color', 'black', '_target')")
            lines.append("cmd.set('label_position',"+repr(list(offset))+",'_target')")
        lines += ["cmd.delete('_target')", "cmd.set('label_size',18,'ubiquitin',state=1)",
            "cmd.set('label_connector',1)", "cmd.set('float_labels',1)", "cmd.set('ray_shadows',0)",
            'cmd.set_view('+repr(list(cmd.get_view()))+')', 'python end','']
        (output/'replay.pml').write_text('\n'.join(lines),encoding='utf-8')
        cmd.save(str(output/'scene.pse'),quiet=1)
        cmd.save(str(session),quiet=1)
        cmd.png(str(output/'scene.png'),width=800,height=600,dpi=96,ray=1,quiet=1)
        _json(output/'report.json',dict(schema_version=2,tool='molscene',tool_version=__version__,
            operation='guided_labels',passed=True,pymol_version=cmd.get_version()[0],labels=labels,
            source_sha256=_sha(source),renderer='CPU ray',
            limitations=['Native positive label_size is shared by labels on each target object/state.',
                'Label text/color belong to atom identities shared across ordinary coordinate states.'],
            artifact_hashes={p.relative_to(output).as_posix():_sha(p) for p in output.rglob('*') if p.is_file()}))
    seal_bundle(output)


def generate(output):
    """Generate fresh originals, then a closed public derivative; never overwrite."""
    output = _safe(output, missing=True)
    historical = ROOT/'examples/outputs'
    if output == historical or historical in output.parents:
        raise ValueError('Choose a separate v0.5 directory outside historical examples/outputs')
    if output.exists():
        raise ValueError('Choose a new example output directory')
    source, fixture = public_coordinate_path(), polar_fixture_path()
    from molscene.bundles import Bundle
    from molscene import engine
    from molscene.persistence import export_persistence
    output.parent.mkdir(parents=True,exist_ok=True)
    private = output.parent/('.molscene-v05-private-'+uuid.uuid4().hex[:12])
    _safe(private,missing=True)
    private.mkdir()
    synthetic, session = private/'polar.pdb', private/'labelled.pse'
    _synthetic_input(synthetic)
    # The pinned public structure and synthetic chemistry fixture serve distinct
    # purposes; numerical expected values belong only to the controlled fixture.
    engine.polar(source,private/'polar-public',selection_a='name N',selection_b='name O',
                 width=800,height=600,dpi=96)
    engine.polar(synthetic,private/'polar-synthetic-raw',selection_a='elem N',selection_b='elem O',
                 width=800,height=600,dpi=96)
    for name in ('polar-public','polar-synthetic-raw'):
        seal_bundle(private/name)
    _synthetic_display(private/'polar-synthetic-raw',private/'polar-synthetic')
    _labels(source,private/'labels',session)
    export_persistence(session,private/'coordinates','coordinates',
                       dict(selection='ubiquitin and resi 1-5 and name CA',state=1,format='cif'))
    export_persistence(session,private/'session-save','session_save',{})
    for name,expected in EXPECTED_FILES.items():
        if audit_bundle(private/name) != expected:
            raise ValueError('Unexpected daily example inventory: '+name)
    inventory = ['validation.json','manifest.json'] + [name+'/'+relative
        for name,files in EXPECTED_FILES.items() for relative in sorted(files)]
    with Bundle(output,protected=[private,source,fixture],inventory=inventory,new_directory_only=True) as bundle:
        projections = {name:project_public_bundle(private/name,bundle.stage/name,
            private_paths=[str(private),str(ROOT),str(output)]) for name in EXPECTED_FILES}
        _json(bundle.stage/'validation.json',dict(schema_version=1,gallery_version='0.5.0',
            public_coordinate_sha256=UBIQUITIN_SHA256,synthetic_fixture_sha256=POLAR_FIXTURE_SHA256,
            synthetic_fixture='Original bonded amine/carbonyl fragments, 3 angstrom N-O separation; illustrative, not experimental.',
            bundles=projections,private_original_directory=private.name,
            limitations=['Polar rows are PyMOL-typed candidates, not confirmed hydrogen bonds or binding energies.',
                'Angle is native coarse orientation tolerance; cutoff uses strict native distance < cutoff.',
                'No full native GUI or optional solver acceptance is established by this gallery.',
                'Binary session privacy and scientific/image appearance need independent review before publication.']))
        bundle.publish()
    audit_bundle(output)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True,help='New v0.5 output directory outside historical outputs')
    args = parser.parse_args(argv)
    try:
        generate(args.output)
    except (ValueError,RuntimeError,OSError,ImportError) as exc:
        print(str(exc),file=sys.stderr)
        return 1
    print('Daily gallery complete; private originals retained beside output. Review PSE/PNG before publication.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
