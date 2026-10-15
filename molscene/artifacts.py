"""Fixed owned artifacts, lexical path protection and staged replacement."""
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

FILES = ('scene.png', 'scene.pse', 'scene.pml', 'report.json', 'contacts.csv',
         'sasa.csv', 'chains.csv', 'sequence.fasta')
INPUT_NAMES = tuple(role + ext for role in ('input', 'mobile', 'target')
                    for ext in ('.pdb', '.cif', '.mmcif', '.pse'))


def no_link(path):
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except (FileNotFoundError, NotADirectoryError):
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Output paths must not contain links or reparse points: ' + str(candidate))


def checked_path(path):
    path = Path(os.path.abspath(os.path.expanduser(os.fspath(path))))
    no_link(path)
    for ancestor in path.parents:
        if ancestor.exists() and not ancestor.is_dir():
            raise ValueError('Output parent must be a directory: ' + str(ancestor))
    return path.resolve()


def output(path, sources, overwrite):
    path = checked_path(path)
    if path.exists() and not path.is_dir():
        raise ValueError('Output must be a directory: ' + str(path))
    sources = [Path(source).resolve() for source in sources]
    if any(source == path or path in source.parents for source in sources):
        raise ValueError('Output directory must not contain an input structure or session')
    if path.exists() and any(path.iterdir()) and not overwrite:
        raise ValueError('Output directory is nonempty; choose a new directory or use overwrite=True')
    for name in (*FILES, 'inputs'):
        target = path / name
        no_link(target)
        if target.exists() and ((name == 'inputs') != target.is_dir()):
            raise ValueError('Owned artifact has the wrong file type: ' + str(target))
    for name in INPUT_NAMES:
        target = path / 'inputs' / name
        no_link(target)
        if target.exists() and not target.is_file():
            raise ValueError('Owned input copy must be a regular file: ' + str(target))
    return path


def copy_inputs(stage, sources):
    (stage / 'inputs').mkdir()
    local, rows = {}, []
    for role, source in sources.items():
        relative = 'inputs/' + role + source.suffix.lower()
        if Path(relative).name not in INPUT_NAMES:
            raise ValueError('Unrecognized owned input name')
        shutil.copyfile(source, stage / relative)
        local[role] = relative
        rows.append(dict(role=role, basename=source.name, sha256=sha256(stage / relative)))
    return local, rows


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def write_csv(path, rows, fields):
    with Path(path).open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def commit(stage, destination, sources, overwrite, names, inputs):
    for name in names:
        if name not in FILES or not (stage / name).is_file() or not (stage / name).stat().st_size:
            raise RuntimeError('Did not produce a nonempty owned artifact: ' + name)
    output(destination, sources, overwrite)
    destination.mkdir(exist_ok=True)
    (destination / 'inputs').mkdir(exist_ok=True)
    for relative in (*inputs, *names):
        os.replace(stage / relative, destination / relative)


def result_path(path, protected=()):
    """Validate an exclusive JSON destination before success OR error reporting."""
    target = checked_path(path)
    for source in protected:
        source = Path(source).resolve()
        if target == source or target in source.parents or source in target.parents:
            raise ValueError('Result path collides with a job, session or source: ' + str(target))
        if target.exists() and source.exists() and target.samefile(source):
            raise ValueError('Result path aliases a protected source')
    if target.exists():
        raise ValueError('Result already exists; refusing to overwrite: ' + str(target))
    return target


def atomic_result(path, value, protected=()):
    path = result_path(path, protected)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.molscene-result-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write('\n')
        result_path(path, protected)
        # Windows rename fails if destination exists and works on exFAT.
        # POSIX link publishes the complete inode exclusively (rename replaces).
        if os.name == 'nt':
            os.rename(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
