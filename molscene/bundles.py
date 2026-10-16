"""Closed-inventory advanced exports with exclusive directory publication."""
import ctypes
import errno
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile
import uuid

from .artifacts import checked_path, no_link, sha256, write_json

MAX_LOG_BYTES = 16 * 1024 * 1024


class CancelledError(RuntimeError):
    """A cooperative cancellation, shared by cores and tool execution."""


def _identity(path):
    info = path.lstat()
    return info.st_dev, info.st_ino, info.st_ctime_ns


def _same_object(path, identity):
    try:
        info = path.lstat()
        # ctime changes during ordinary directory writes on POSIX.
        return (info.st_dev, info.st_ino) == identity[:2]
    except FileNotFoundError:
        return False


def _regular(path):
    no_link(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('Expected a unique regular file: '+str(path))
    return info


def _relative(value):
    if not isinstance(value, str) or not value or '\\' in value or any(c in value for c in ':*?\0'):
        raise ValueError('Inventory must contain exact relative POSIX file paths')
    parts = value.split('/')
    if any(not p or p in ('.', '..') or p.endswith((' ', '.')) for p in parts):
        raise ValueError('Unsafe relative artifact path')
    if PurePosixPath(value).is_absolute() or any(re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', p) for p in parts):
        raise ValueError('Unsafe artifact name')
    return value


def _inventory(inventory):
    if isinstance(inventory, (str, bytes)):
        raise ValueError('Inventory must be a sequence of file paths')
    names = tuple(_relative(name) for name in inventory)
    lowered = [name.casefold() for name in names]
    if len(set(lowered)) != len(names) or 'manifest.json' not in names:
        raise ValueError('Inventory requires one manifest.json and no path aliases')
    for name in lowered:
        if any(parent.as_posix() in lowered for parent in PurePosixPath(name).parents if str(parent) != '.'):
            raise ValueError('Inventory file is also used as a directory')
    return names


def _scan(stage):
    no_link(stage)
    if not stage.is_dir():
        raise ValueError('Bundle stage must be a directory')
    files, directories, identities = set(), set(), set()
    pending = [stage]
    while pending:
        current = pending.pop()
        no_link(current)
        for entry in current.iterdir():
            no_link(entry)
            info = entry.lstat()
            relative = entry.relative_to(stage).as_posix()
            if stat.S_ISDIR(info.st_mode):
                directories.add(relative)
                pending.append(entry)
            else:
                _regular(entry)
                identity = (info.st_dev, info.st_ino)
                if identity in identities:
                    raise ValueError('Hardlink alias in bundle')
                identities.add(identity)
                files.add(relative)
    return files, directories


def write_manifest(stage, inventory):
    """Validate the complete payload inventory and write its final digest table."""
    stage = checked_path(stage)
    names = _inventory(inventory)
    files, directories = _scan(stage)
    expected = set(names)-{'manifest.json'}
    expected_dirs = {p.as_posix() for name in names for p in PurePosixPath(name).parents if str(p) != '.'}
    if files-{'manifest.json'} != expected or directories != expected_dirs:
        raise ValueError('Bundle inventory has missing, extra or unexpected directory entries')
    records = {}
    for name in sorted(expected):
        path = stage/name
        before = _regular(path)
        if name.startswith('logs/') and before.st_size > MAX_LOG_BYTES:
            raise ValueError('Published log exceeds byte limit')
        digest = sha256(path)
        after = _regular(path)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError('Artifact changed while hashing')
        records[name] = dict(sha256=digest, bytes=after.st_size)
    manifest = dict(schema_version=1, files=records)
    target = stage/'manifest.json'
    if target.exists():
        _regular(target)
        target.unlink()
    with target.open('x', encoding='utf-8') as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write('\n')
    return manifest


def _rename_noreplace(source, destination):
    """Publish atomically, refusing even an empty race-created destination."""
    if os.name == 'nt':
        move = ctypes.WinDLL('kernel32', use_last_error=True).MoveFileW
        move.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p)
        move.restype = ctypes.c_int
        if not move(str(source), str(destination)):
            raise ctypes.WinError(ctypes.get_last_error())
    elif sys.platform.startswith('linux'):
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, 'renameat2', None)
        if rename is None:
            raise OSError(errno.ENOTSUP, 'Exclusive directory rename is unavailable')
        rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1):
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(destination))
    else:
        raise OSError(errno.ENOTSUP, 'Exclusive directory publication is unsupported on this platform')


def _overlap(a, b):
    return a == b or a in b.parents or b in a.parents


def _cleanup_owned(path, identity):
    """Never traverse a replaced root, link, or reparse point during cleanup."""
    if not _same_object(path, identity):
        return
    try:
        no_link(path)
    except ValueError:
        return
    for entry in path.iterdir():
        info = entry.lstat()
        linked = stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
        if linked:
            # Unlink only the directory entry, never traverse its target.
            if os.name == 'nt' and stat.S_ISDIR(info.st_mode):
                entry.rmdir()
            else:
                entry.unlink()
        elif stat.S_ISDIR(info.st_mode):
            _cleanup_owned(entry, _identity(entry))
        else:
            no_link(entry.parent)
            if os.name == 'nt' and stat.S_ISREG(info.st_mode) and not info.st_mode & stat.S_IWRITE:
                # chmod affects all hardlinks, so only touch a verified unique
                # owned regular file; never change permissions through aliases.
                _regular(entry)
                if not _same_object(entry, (info.st_dev, info.st_ino)):
                    raise ValueError('Owned file changed during cleanup')
                entry.chmod(info.st_mode | stat.S_IWRITE)
            entry.unlink()
    if _same_object(path, identity):
        no_link(path)
        path.rmdir()


class Bundle:
    """Own a stage/scratch pair until a complete bundle is published.

    Register log paths before starting tools. Unpublished normal context exits
    are simply abandoned; exceptional exits retain only registered tool logs.
    """

    def __init__(self, destination, *, protected, inventory, cancelled=None, new_directory_only=False):
        self.destination = checked_path(destination)
        self.protected = tuple(checked_path(p) for p in protected)
        self.inventory = _inventory(inventory)
        self.cancelled = cancelled
        self.new_directory_only = new_directory_only
        self.failure_path = self.destination.parent/('.molscene-failure-'+uuid.uuid4().hex)
        self._logs, self._sources = {}, {}
        self._cached_logs = {}
        self._input_copies = set()
        self._owned = {}
        self._published = self._entered = False
        self._validate_destination()

    def _check_cancel(self):
        if self.cancelled is not None and self.cancelled():
            raise CancelledError('Operation cancelled')

    def _validate_destination(self):
        no_link(self.destination)
        for source in self.protected:
            no_link(source)
            if _overlap(self.destination, source):
                raise ValueError('Destination overlaps a protected input, job or envelope')
            if source.exists() and source.is_file():
                _regular(source)
        if self.destination.exists():
            if self.new_directory_only:
                raise ValueError('Destination must be a new directory')
            if not self.destination.is_dir() or any(self.destination.iterdir()):
                raise ValueError('Destination must be a new or empty directory')

    def __enter__(self):
        if self._entered:
            raise RuntimeError('A bundle context cannot be reused')
        self._check_cancel()
        self._validate_destination()
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        no_link(self.destination.parent)
        self._entered = True
        try:
            for attribute in ('stage', 'scratch'):
                path = Path(tempfile.mkdtemp(prefix='.molscene-'+attribute+'-', dir=self.destination.parent))
                setattr(self, attribute, path)
                self._owned[path] = _identity(path)
                if any(_overlap(path, source) for source in self.protected):
                    raise ValueError('Owned directory overlaps a protected path')
        except BaseException:
            for path, identity in self._owned.items():
                _cleanup_owned(path, identity)
            raise
        return self

    def _active(self):
        if not self._entered or self._published:
            raise RuntimeError('Bundle is not an active unpublished transaction')
        for path, identity in self._owned.items():
            no_link(path)
            if not _same_object(path, identity):
                raise ValueError('Owned bundle directory was replaced')

    def copy_input(self, source, relative):
        self._active()
        self._check_cancel()
        relative = _relative(relative)
        if relative not in self.inventory or relative == 'manifest.json':
            raise ValueError('Input copy is not a declared payload')
        source = checked_path(source)
        if any(_overlap(source, p) for p in (self.stage, self.scratch, self.destination)):
            raise ValueError('Source overlaps the bundle')
        before = _regular(source)
        digest = sha256(source)
        target = self.stage/relative
        if target in self._logs.values():
            raise ValueError('A scientific input cannot also be a retained tool log')
        no_link(target)
        if target.exists():
            raise ValueError('Input copy destination already exists')
        target.parent.mkdir(parents=True, exist_ok=True)
        no_link(target)
        # Keep the exclusive destination handle open throughout copying. A
        # leaf replaced after creation is never reopened for writing.
        with source.open('rb') as reader, target.open('xb') as writer:
            opened = os.fstat(reader.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise ValueError('Source changed before copying')
            identity = _identity(target)
            shutil.copyfileobj(reader, writer, length=1024*1024)
        after = _regular(source)
        _regular(target)
        if (not _same_object(target, identity) or sha256(source) != digest or sha256(target) != digest
                or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
            raise ValueError('Source or input copy changed during copying')
        self._sources[source] = digest
        self._input_copies.add(target)
        self._check_cancel()
        return dict(path=relative, sha256=digest, bytes=after.st_size)

    def retain_failure_logs(self, logs):
        self._active()
        if not isinstance(logs, dict):
            raise ValueError('Log registration must map roles to paths')
        registered = dict(self._logs)
        names = {(role+'.log').casefold(): role for role in registered}
        for role, candidate in logs.items():
            if not isinstance(role, str) or not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}', role):
                raise ValueError('Invalid failure-log role')
            filename = _relative(role+'.log')
            alias = names.get(filename.casefold())
            if alias is not None and alias != role:
                raise ValueError('Failure-log roles alias the same filename')
            path = checked_path(candidate)
            if path in self._input_copies:
                raise ValueError('Scientific input copies cannot be retained as logs')
            if not any(root in path.parents for root in (self.stage, self.scratch)):
                raise ValueError('Failure logs must be inside owned stage or scratch')
            if path.exists():
                if _regular(path).st_size > MAX_LOG_BYTES:
                    raise ValueError('Failure log exceeds byte limit')
            if role in registered and registered[role] != path:
                raise ValueError('Failure-log role is already registered')
            registered[role] = path
            names[filename.casefold()] = role
        self._logs = registered

    def _log_bytes(self, path):
        root = next(p for p in self._owned if p in path.parents)
        if not _same_object(root, self._owned[root]) or path in self._input_copies:
            raise ValueError('Failure log is no longer owned')
        info = _regular(path)
        with path.open('rb') as reader:
            opened = os.fstat(reader.fileno())
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError('Failure log changed before reading')
            data = reader.read(MAX_LOG_BYTES)
            truncated = bool(reader.read(1)) or info.st_size > MAX_LOG_BYTES
        return data, truncated

    @staticmethod
    def _secondary(error, stage, secondary):
        diagnostic = dict(stage=stage, type=type(secondary).__name__, message=str(secondary)[:4096])
        if not hasattr(error, 'bundle_diagnostics'):
            error.bundle_diagnostics = []
        error.bundle_diagnostics.append(diagnostic)
        return diagnostic

    def _finalize_scratch(self):
        # Retain only bounded, verified registered logs in memory so a later
        # rename failure/cancellation still has diagnostics after scratch is gone.
        for role, path in self._logs.items():
            if self.scratch in path.parents and path.exists():
                self._cached_logs[role] = self._log_bytes(path)
        identity = self._owned[self.scratch]
        _cleanup_owned(self.scratch, identity)
        if self.scratch.exists():
            raise ValueError('Owned scratch could not be finalized')
        del self._owned[self.scratch]

    def _retain_failure(self, error):
        records, evidence_errors = {}, []
        no_link(self.failure_path)
        self.failure_path.mkdir()
        for role, source in self._logs.items():
            try:
                if role in self._cached_logs:
                    data, truncated = self._cached_logs[role]
                elif source.exists():
                    data, truncated = self._log_bytes(source)
                else:
                    continue
                (self.failure_path/'logs').mkdir(exist_ok=True)
                target = self.failure_path/'logs'/(role+'.log')
                with target.open('xb') as writer:
                    writer.write(data)
                records[role] = dict(path='logs/'+role+'.log', sha256=sha256(target),
                                     bytes=target.stat().st_size, truncated=truncated)
            except (OSError, ValueError, StopIteration) as secondary:
                record = self._secondary(error, 'retain log', secondary)
                evidence_errors.append(dict(record, role=role))
        write_json(self.failure_path/'failure.json', dict(schema_version=1, cancelled=isinstance(error, CancelledError),
                   error=dict(type=type(error).__name__, message=str(error)[:4096]), logs=records,
                   evidence_errors=evidence_errors))

    def publish(self):
        self._active()
        self._check_cancel()
        self._validate_destination()
        for path in self._logs.values():
            if path.exists() and _regular(path).st_size > MAX_LOG_BYTES:
                raise ValueError('Tool log exceeds byte limit')
        for source, digest in self._sources.items():
            _regular(source)
            if sha256(source) != digest:
                raise ValueError('Copied source changed before publication')
        manifest = write_manifest(self.stage, self.inventory)
        self._active()
        files, directories = _scan(self.stage)
        expected_dirs = {p.as_posix() for name in self.inventory for p in PurePosixPath(name).parents if str(p) != '.'}
        if files != set(self.inventory) or directories != expected_dirs:
            raise ValueError('Bundle inventory changed before publication')
        for name, record in manifest['files'].items():
            path = self.stage/name
            if _regular(path).st_size != record['bytes'] or sha256(path) != record['sha256']:
                raise ValueError('Bundle payload changed before publication')
        with (self.stage/'manifest.json').open(encoding='utf-8') as handle:
            if json.load(handle) != manifest:
                raise ValueError('Manifest changed before publication')
        for source, digest in self._sources.items():
            _regular(source)
            if sha256(source) != digest:
                raise ValueError('Copied source changed before publication')
        self._validate_destination()
        self._check_cancel()
        self._finalize_scratch()
        if self.destination.exists():
            # rmdir itself refuses nonempty directories, including races after checks.
            self._validate_destination()
            self.destination.rmdir()
        self._check_cancel()
        no_link(self.destination)
        _rename_noreplace(self.stage, self.destination)
        self._published = True
        self._owned.clear()
        self._cached_logs.clear()
        return self.destination

    def __exit__(self, exc_type, error, traceback):
        original = error
        try:
            if error is not None and not self._published:
                try:
                    self._retain_failure(error)
                except Exception as secondary:
                    self._secondary(error, 'retain failure evidence', secondary)
            for path, identity in self._owned.items():
                try:
                    _cleanup_owned(path, identity)
                except Exception as secondary:
                    if error is None:
                        error = secondary
                        try:
                            self._retain_failure(error)
                        except Exception as retention_error:
                            self._secondary(error, 'retain failure evidence', retention_error)
                    else:
                        self._secondary(error, 'cleanup', secondary)
        finally:
            self._entered = False
            self._cached_logs.clear()
        if original is None and error is not None:
            raise error
        return False
