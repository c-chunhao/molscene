"""Typed external-tool configuration, verified identity and managed execution."""
import copy
from contextlib import contextmanager
import json
import os
from pathlib import Path
import queue
import re
import signal
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time

from .artifacts import checked_path, sha256
from .bundles import CancelledError

_NAMES = {'apbs', 'pdb2pqr', 'ffmpeg', 'ffprobe'}
_ENTRY_KEYS = {'argv', 'architecture', 'path_dirs', 'runtime_files', 'timeout_seconds', 'memory_limit_mib'}
_SHELLS = {'cmd', 'powershell', 'pwsh', 'sh', 'bash', 'dash', 'zsh', 'csh', 'wscript', 'cscript'}


class ExternalToolError(RuntimeError):
    """A tool failed; result retains JSON-compatible diagnostics and log paths."""
    def __init__(self, message, result):
        super().__init__(message)
        self.result = result


def _absolute(value, directory=False):
    if not isinstance(value, str) or not value or '\x00' in value or not Path(value).is_absolute():
        raise ValueError('Tool paths must be absolute strings')
    path = checked_path(value)
    if not (path.is_dir() if directory else path.is_file()):
        raise ValueError('Tool path is missing or has the wrong type: ' + value)
    return path


def _architecture(path):
    with Path(path).open('rb') as handle:
        header = handle.read(64)
        if header[:2] == b'MZ' and len(header) == 64:
            handle.seek(struct.unpack_from('<I', header, 60)[0])
            pe = handle.read(6)
            machine = struct.unpack('<H', pe[4:])[0] if pe[:4] == b'PE\0\0' else 0
            value = {0x14c: 'x86', 0x8664: 'x64'}.get(machine)
        elif header[:4] == b'\x7fELF' and len(header) >= 20:
            endian = '<' if header[5] == 1 else '>'
            machine = struct.unpack_from(endian + 'H', header, 18)[0]
            value = {3: 'x86', 62: 'x64'}.get(machine)
        elif header[:4] in (b'\xce\xfa\xed\xfe', b'\xcf\xfa\xed\xfe'):
            value = {7: 'x86', 0x1000007: 'x64'}.get(struct.unpack_from('<I', header, 4)[0])
        else:
            value = None
    if value is None:
        raise ValueError('Unsupported executable architecture: ' + str(path))
    return value


def _config(config):
    if not isinstance(config, dict) or set(config) != {'schema_version', 'tools'}:
        raise ValueError('Tool configuration must contain exactly schema_version and tools')
    if type(config['schema_version']) is not int or config['schema_version'] != 1:
        raise ValueError('Tool configuration schema_version must be 1')
    entries = config['tools']
    if not isinstance(entries, dict) or not set(entries) <= _NAMES:
        raise ValueError('Unknown tool name')
    for name, entry in entries.items():
        if not isinstance(entry, dict) or set(entry) != _ENTRY_KEYS:
            raise ValueError('Invalid tool entry keys: ' + name)
        argv = entry['argv']
        if not isinstance(argv, list) or not argv or len(argv) > 128 or any(not isinstance(s, str) or '\x00' in s for s in argv):
            raise ValueError('Tool argv must be a nonempty list of literal strings')
        executable = _absolute(argv[0])
        if executable.suffix.lower() in {'.cmd', '.bat', '.ps1', '.sh'} or executable.stem.lower() in _SHELLS:
            raise ValueError('Shell and batch tools are not allowed')
        if os.name != 'nt' and not os.access(executable, os.X_OK):
            raise ValueError('Tool is not executable')
        if entry['architecture'] not in ('x86', 'x64') or _architecture(executable) != entry['architecture']:
            raise ValueError('Executable architecture does not match configured x86/x64 architecture')
        for key, maximum in (('timeout_seconds', 3600), ('memory_limit_mib', 4096 if entry['architecture'] == 'x64' else 1536)):
            if type(entry[key]) is not int or not 1 <= entry[key] <= maximum:
                raise ValueError('Invalid ' + key)
        if not isinstance(entry['path_dirs'], list) or len(entry['path_dirs']) > 64:
            raise ValueError('path_dirs must be a bounded list')
        for value in entry['path_dirs']:
            _absolute(value, directory=True)
        if not isinstance(entry['runtime_files'], list) or len(entry['runtime_files']) > 128:
            raise ValueError('runtime_files must be a bounded list')
        for runtime in entry['runtime_files']:
            if not isinstance(runtime, dict) or set(runtime) != {'path', 'role'} or runtime['role'] not in ('dll', 'interpreter', 'package_metadata'):
                raise ValueError('Invalid typed runtime file')
            path = _absolute(runtime['path'])
            if runtime['role'] in ('dll', 'interpreter') and _architecture(path) != entry['architecture']:
                raise ValueError('Runtime architecture mismatch')
    return copy.deepcopy(config)


def load_tools(path):
    path = checked_path(path)
    if not path.is_file() or path.stat().st_size > 65536:
        raise ValueError('Tool configuration must be a regular file of at most 64 KiB')
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError('Duplicate configuration key: ' + key)
            result[key] = value
        return result
    return _config(json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=pairs))


def _check_cancel(cancelled):
    if cancelled and cancelled():
        raise CancelledError('External tool cancelled')


def _identity(entry):
    return {'architecture': _architecture(entry['argv'][0]),
            'hashes': {'executable': sha256(entry['argv'][0]),
                       'runtime_files': [{'path': row['path'], 'role': row['role'], 'sha256': sha256(row['path'])}
                                         for row in entry['runtime_files']]}}


def _environment(entry, scratch):
    env = dict(os.environ)
    for key in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'CONDA_PREFIX'):
        env.pop(key, None)
    system = [str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32'), os.environ.get('SystemRoot', 'C:/Windows')] if os.name == 'nt' else ['/usr/bin', '/bin']
    env['PATH'] = os.pathsep.join([*entry['path_dirs'], *system])
    env.update(PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1',
               TEMP=str(scratch), TMP=str(scratch), TMPDIR=str(scratch))
    return env


def _read_protocol(stream, records):
    total = 0
    try:
        while True:
            line = stream.readline(16385)
            if not line:
                return
            total += len(line)
            if len(line) > 16384 or total > 65536:
                records.put({'type': 'error', 'message': 'Guardian protocol overflow'})
                return
            records.put(json.loads(line))
    except (OSError, ValueError) as exc:
        records.put({'type': 'error', 'message': 'Invalid guardian protocol: ' + str(exc)})
    finally:
        stream.close()


def _log_paths(log_prefix):
    prefix = checked_path(log_prefix)
    if not prefix.parent.is_dir():
        raise ValueError('Log directory must exist')
    paths = {key: str(checked_path(str(prefix) + suffix)) for key, suffix in
             (('stdout_path', '.stdout.log'), ('stderr_path', '.stderr.log'))}
    for path in paths.values():
        if Path(path).exists():
            raise ValueError('Refusing to replace tool log: ' + path)
    return prefix, paths


def _managed(name, entry, args, cwd, log_prefix, cancelled=None, progress=None, timeout=None):
    _check_cancel(cancelled)
    cwd = _absolute(str(cwd), directory=True)
    prefix, paths = _log_paths(log_prefix)
    result = dict(returncode=None, elapsed_seconds=0, argv=[*entry['argv'], *args],
                  version=None, warnings=[], **paths, **_identity(entry))
    with tempfile.TemporaryDirectory(prefix='.molscene-tool-', dir=prefix.parent) as temporary:
        scratch = Path(temporary)
        request = dict(schema_version=1, argv=result['argv'], cwd=str(cwd),
                       environment=_environment(entry, scratch), timeout_seconds=timeout or entry['timeout_seconds'],
                       log_limit_bytes=16 * 1024 * 1024, memory_limit_mib=entry['memory_limit_mib'], **paths)
        request_path = scratch / 'request.json'
        request_path.write_text(json.dumps(request, ensure_ascii=True), encoding='utf-8')
        # Bootstrap uses the installed/source package root, never the caller's
        # PYTHONPATH; tools receive the separate sanitized environment above.
        guardian_env = _environment(entry, scratch)
        guardian_env['PYTHONPATH'] = str(Path(__file__).resolve().parent.parent)
        _check_cancel(cancelled)
        process = subprocess.Popen([sys.executable, '-B', '-u', '-m', 'molscene.process_guardian', str(request_path)],
                                   cwd=Path(__file__).resolve().parent.parent, env=guardian_env,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   shell=False, close_fds=True, start_new_session=os.name != 'nt',
                                   creationflags=0x08000000 if os.name == 'nt' else 0)
        records = queue.Queue(maxsize=8)
        reader = threading.Thread(target=_read_protocol, args=(process.stdout, records), daemon=True)
        reader.start()
        terminal = None
        try:
            deadline = time.monotonic() + (timeout or entry['timeout_seconds']) + 5
            while True:
                _check_cancel(cancelled)
                try:
                    record = records.get(timeout=.02)
                except queue.Empty:
                    if process.poll() is not None and not reader.is_alive():
                        break
                    if time.monotonic() >= deadline:
                        raise ExternalToolError('Guardian lifetime timeout', result)
                    continue
                if record.get('type') == 'ready':
                    # Windows venv launchers may have a distinct wrapper PID.
                    # The ownership PID is reported by the actual guardian.
                    if type(record.get('pid')) is not int or record['pid'] <= 0 or (os.name != 'nt' and record['pid'] != process.pid):
                        raise ExternalToolError('Invalid guardian ownership PID', result)
                    result['guardian_pid'] = record['pid']
                    if progress:
                        progress(dict(stage='tool_ready', completed=0, total=1, message=name, pid=record['pid']))
                    _check_cancel(cancelled)
                    process.stdin.write(b'G')
                    process.stdin.flush()
                elif record.get('type') in ('result', 'error'):
                    terminal = record
                    break
            if terminal is None:
                raise ExternalToolError('Guardian exited without a terminal result', result)
            result.update({key: terminal[key] for key in ('returncode', 'elapsed_seconds') if key in terminal})
            if terminal['type'] == 'error':
                raise ExternalToolError(terminal.get('message') or 'External tool failed', result)
        finally:
            process.stdin.close()  # Cancellation, exception, or normal completion.
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            if os.name != 'nt':
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            reader.join(timeout=1)
        _check_cancel(cancelled)
        if progress:
            progress(dict(stage='tool_finished', completed=1, total=1, message=name))
        return result


def _entry(name, config):
    config = _config(config)
    if name not in config['tools']:
        raise ValueError('Tool is not configured: ' + str(name))
    return config['tools'][name]


def _text(result):
    return '\n'.join(Path(result[key]).read_text(encoding='utf-8', errors='replace') for key in ('stdout_path', 'stderr_path'))


@contextmanager
def _probe_workspace(work_dir):
    folder = Path(tempfile.mkdtemp(prefix='.molscene-probe-', dir=work_dir))
    try:
        yield folder
    except BaseException:
        # Failure diagnostics stay private and reachable from ExternalToolError.
        # Bundle owners retain their registered logs and clean their own scratch.
        raise
    else:
        checked_path(folder)
        if folder.parent != work_dir:
            raise RuntimeError('Probe cleanup escaped its owned parent')
        shutil.rmtree(folder)


def probe_tool(name, config, *, work_dir, cancelled=None, progress=None):
    entry = _entry(name, config)
    _check_cancel(cancelled)
    work_dir = _absolute(str(work_dir), directory=True)
    with _probe_workspace(work_dir) as folder:
        result = _managed(name, entry, ['-version' if name in ('ffmpeg', 'ffprobe') else '--version'],
                          folder, folder / 'version', cancelled, progress, timeout=15)
        text = _text(result)
        patterns = {'apbs': r'\bAPBS\s+(3\.4\.1)\b', 'pdb2pqr': r'(?:\bPDB2PQR\s+(?:version\s+)?|^)(\d+\.\d+\.\d+)\b',
                    'ffmpeg': r'\bffmpeg version\s+(\S+)', 'ffprobe': r'\bffprobe version\s+(\S+)'}
        found = re.search(patterns[name], text, re.IGNORECASE | re.MULTILINE)
        if not found or result['returncode'] not in ({0, 13} if name == 'apbs' else {0}):
            raise ExternalToolError('Unrecognized or unsuccessful ' + name + ' version probe', result)
        result['version'] = found.group(1)
        if name == 'pdb2pqr':
            interpreter = copy.deepcopy(entry)
            interpreter['argv'] = [entry['argv'][0], '-B']
            runtime = _managed(name, interpreter, ['-c', 'import json,sys,struct; print(json.dumps({"version":list(sys.version_info[:3]),"bits":struct.calcsize("P")*8}))'],
                               folder, folder / 'python', cancelled, progress, timeout=15)
            try:
                python = json.loads(Path(runtime['stdout_path']).read_text())
                if runtime['returncode'] != 0 or tuple(python['version']) < (3, 11) or python['bits'] != (64 if entry['architecture'] == 'x64' else 32):
                    raise ValueError('Python >=3.11 matching architecture is required')
            except (ValueError, KeyError, TypeError) as exc:
                raise ExternalToolError('Invalid PDB2PQR interpreter: ' + str(exc), runtime) from exc
            result['python_version'] = '.'.join(map(str, python['version']))
        # Successful private probe logs are discarded; failure logs are retained.
        result['stdout_path'] = result['stderr_path'] = None
        return result


def run_tool(name, config, args, *, cwd, log_prefix, cancelled=None, progress=None):
    if not isinstance(args, list) or any(not isinstance(arg, str) or '\x00' in arg for arg in args):
        raise ValueError('Tool arguments must be a list of literal strings')
    entry = _entry(name, config)
    _check_cancel(cancelled)
    prefix, paths = _log_paths(log_prefix)
    try:
        probe = probe_tool(name, config, work_dir=prefix.parent, cancelled=cancelled, progress=progress)
    except ExternalToolError as exc:
        # Registered primary logs must also contain diagnostics when the run
        # cannot pass its prerequisite version probe.
        for key, destination in paths.items():
            source = exc.result.get(key)
            if source and Path(source).is_file():
                checked_path(destination)
                with Path(source).open('rb') as reader, Path(destination).open('xb') as writer:
                    shutil.copyfileobj(reader, writer, length=65536)
                exc.result[key] = destination
        raise
    try:
        result = _managed(name, entry, args, cwd, log_prefix, cancelled, progress)
    except ExternalToolError as exc:
        exc.result['version'] = probe['version']
        if 'python_version' in probe:
            exc.result['python_version'] = probe['python_version']
        raise
    result.update(version=probe['version'])
    if 'python_version' in probe:
        result['python_version'] = probe['python_version']
    if result['returncode'] != 0:
        raise ExternalToolError(name + ' exited with status ' + str(result['returncode']), result)
    return result


def tool_manifest(config, probes):
    config = _config(config)
    result = {'schema_version': 1, 'tools': {}}
    for name, probe in probes.items():
        if name not in config['tools'] or not isinstance(probe, dict) or not probe.get('version'):
            raise ValueError('Manifest requires actual successful tool probes')
        result['tools'][name] = {**copy.deepcopy(config['tools'][name]), **copy.deepcopy(probe)}
    return result
