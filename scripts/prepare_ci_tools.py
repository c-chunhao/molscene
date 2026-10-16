"""Prepare byte-identical, unique CI tool files without changing installed files.

Executables stay beside their resolved original to retain relative shared-library
and Python virtual-environment discovery. Metadata copies are evidence inputs,
not relocated or rewritten package installations.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys


def require(value, message):
    if not value:
        raise ValueError(message)


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def identity(path):
    info = path.lstat()
    kind = 'symlink' if stat.S_ISLNK(info.st_mode) else 'regular' if stat.S_ISREG(info.st_mode) else 'other'
    return dict(path=str(path), type=kind, mode=stat.S_IMODE(info.st_mode),
                nlink=info.st_nlink, device=info.st_dev, inode=info.st_ino,
                bytes=info.st_size, mtime_ns=info.st_mtime_ns,
                sha256=sha256(path) if kind == 'regular' else None)


def unique_copy(original, destination, records, label):
    """Never unlink/rewrite an installed source; never create a hardlink."""
    lexical = Path(os.path.abspath(original))
    source = lexical.resolve(strict=True)
    destination = Path(destination)
    require(destination.is_absolute(), 'Copy destination must be absolute')
    require(destination.parent.resolve(strict=True) == destination.parent,
            'Copy destination parent must not traverse links')
    entry = dict(label=label, original_lexical=identity(lexical),
                 original_resolved=identity(source), prepared=None, passed=False)
    records.append(entry)  # Retain original identity even if creation/check fails.
    before = entry['original_resolved']
    require(before['type'] == 'regular', 'Tool source must resolve to regular file')
    require(source != destination and not destination.exists() and not destination.is_symlink(),
            'Prepared path must be new and distinct')
    created = False
    try:
        with source.open('rb') as reader, destination.open('xb') as writer:
            created = True
            shutil.copyfileobj(reader, writer, 1024 * 1024)
        destination.chmod(before['mode'])
        prepared = identity(destination)
        entry['prepared'] = prepared
        require(identity(source) == before and identity(lexical) == entry['original_lexical'],
                'Installed source identity changed during copy')
        require(prepared['type'] == 'regular' and prepared['nlink'] == 1,
                'Prepared file must be unique and regular')
        require(prepared['sha256'] == before['sha256'] and prepared['bytes'] == before['bytes']
                and prepared['mode'] == before['mode'], 'Copy bytes or mode differ')
        require((prepared['device'], prepared['inode']) != (before['device'], before['inode']),
                'Prepared file must not alias source')
        entry['passed'] = True
        return destination
    except BaseException:
        # Do not conceal a failed or partial prepared identity in the evidence.
        if created and destination.is_file():
            entry['prepared'] = identity(destination)
        raise


def configure(artifacts, records):
    from molscene.external_tools import load_tools
    artifacts = artifacts.resolve(strict=True)
    runtime = json.loads((artifacts / 'pdb2pqr-runtime.json').read_text())
    require(runtime['bits'] == 64, 'PDB2PQR interpreter must be x64')
    metadata_root = artifacts / 'tool-runtime'
    metadata_root.mkdir()  # A rerun must use a clean artifact destination.

    def executable(value, label):
        require(value is not None, 'Missing executable: ' + label)
        source = Path(value).resolve(strict=True)
        return unique_copy(value, source.parent / ('.molscene-ci-' + label + source.suffix), records, label)

    def metadata(value, label):
        return unique_copy(value, metadata_root / (label + '.metadata'), records, label)

    def entry(path, *, prefix=(), files=(), timeout=1200):
        return dict(argv=[str(path), *prefix], architecture='x64',
                    path_dirs=[str(path.parent)], runtime_files=list(files),
                    timeout_seconds=timeout, memory_limit_mib=4096)

    apbs_meta = list(Path(sys.prefix, 'conda-meta').glob('apbs-3.4.1-py311ha92c01f_1.json'))
    require(len(apbs_meta) == 1, 'Expected exact APBS conda metadata')
    apbs = executable(shutil.which('apbs'), 'apbs')
    apbs_metadata = metadata(apbs_meta[0], 'apbs-package')
    interpreter = executable(runtime['executable'], 'pdb2pqr-python')
    pqr_metadata = metadata(runtime['metadata'], 'pdb2pqr-package')
    probe_code = (
        'import importlib.metadata,json,struct,sys; '
        'print(json.dumps(dict(executable=sys.executable,prefix=sys.prefix,'
        'python=list(sys.version_info[:3]),bits=struct.calcsize("P")*8,'
        'pdb2pqr=importlib.metadata.version("pdb2pqr"))))')
    completed = subprocess.run([str(interpreter), '-B', '-c', probe_code],
                               check=False, text=True, capture_output=True, timeout=30)
    with (artifacts / 'prepared-pdb2pqr-runtime.json').open('x', encoding='utf-8', newline='\n') as stream:
        # Private evidence retains actual prepared interpreter output and exit.
        json.dump(dict(returncode=completed.returncode, stdout=completed.stdout,
                       stderr=completed.stderr), stream, indent=2)
        stream.write('\n')
    require(completed.returncode == 0, 'Prepared PDB2PQR interpreter failed')
    actual = json.loads(completed.stdout)
    require(Path(actual['executable']).resolve(strict=True) == interpreter,
            'Prepared interpreter did not execute the copied file')
    require(Path(actual['prefix']).resolve(strict=True) == Path(runtime['prefix']).resolve(strict=True),
            'Prepared interpreter lost its original virtual environment')
    require(actual['pdb2pqr'] == '3.7.1' and actual['bits'] == 64
            and tuple(actual['python']) >= (3, 11, 0), 'Prepared PDB2PQR runtime mismatch')
    tools = dict(apbs=entry(apbs, files=[dict(path=str(apbs_metadata), role='package_metadata')]),
                 pdb2pqr=entry(interpreter, prefix=['-B', '-m', 'pdb2pqr'], timeout=600,
                     files=[dict(path=str(interpreter), role='interpreter'),
                            dict(path=str(pqr_metadata), role='package_metadata')]),
                 ffmpeg=entry(executable(shutil.which('ffmpeg'), 'ffmpeg')),
                 ffprobe=entry(executable(shutil.which('ffprobe'), 'ffprobe')))
    config = artifacts / 'tools.json'
    with config.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(dict(schema_version=1, tools=tools), stream, indent=2)
        stream.write('\n')
    load_tools(config)  # Existing architecture/path/runtime guards remain active.
    with open(os.environ['GITHUB_ENV'], 'a', encoding='utf-8') as stream:
        stream.write('MOLSCENE_TOOLS_CONFIG=' + str(config) + '\n')


def main():
    artifacts = Path('artifacts').resolve(strict=True)
    proof = artifacts / 'tool-runtime-identity.json'
    require(not proof.exists(), 'Refuse previous identity proof')
    records = []
    interpreter = Path(os.path.abspath(sys.executable))
    record = dict(schema_version=1, passed=False, files=records,
                  main_test_interpreter=dict(lexical=identity(interpreter), resolved=identity(interpreter.resolve(strict=True))),
                  policy='Unique byte copies; original installed files and metadata unchanged; executable sibling placement preserves relative runtime lookup.')
    try:
        configure(artifacts, records)
        record['passed'] = True
    except BaseException as error:
        record['error_type'] = type(error).__name__
        raise
    finally:
        # Persist observed original lstat/type/nlink/hash even on preparation failure.
        with proof.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(record, stream, indent=2)
            stream.write('\n')


if __name__ == '__main__':
    main()
