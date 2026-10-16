"""Test-only ownership ledger: terminal exits belong to incarnations, not PIDs.

An ownership key identifies one launch/report, never a numeric PID globally.
Unconfirmed records are polled conservatively; acquisition errors propagate.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


def _windows_process(pid):
    import ctypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    return kernel, handle


def process_alive(pid):
    """Strict liveness: only a confirmed absent/signalled process counts as dead."""
    if os.name == 'nt':
        import ctypes
        try:
            kernel, handle = _windows_process(pid)
        except OSError as exc:
            if exc.winerror == 87:  # The PID no longer exists.
                return False
            raise
        try:
            status = kernel.WaitForSingleObject(handle, 0)
            if status not in (0, 258):
                raise ctypes.WinError(ctypes.get_last_error())
            return status == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class OwnedProcesses:
    def __init__(self, probe=process_alive):
        self.records = []
        self.probe = probe

    def register(self, ownership, pid):
        if not isinstance(ownership, str) or not ownership or type(pid) is not int or pid <= 0:
            raise AssertionError('Invalid process ownership')
        if not any(row['ownership'] == ownership and row['pid'] == pid for row in self.records):
            self.records.append(dict(ownership=ownership, pid=pid, registered_ns=time.time_ns()))

    def exited(self, checkpoint):
        complete = True
        for row in self.records:
            if 'exit' in row:
                continue
            alive = self.probe(row['pid'])
            if type(alive) is not bool:
                raise AssertionError('Unknown process liveness: ' + str(row))
            if alive:
                complete = False
            else:
                row['exit'] = dict(checkpoint=checkpoint, verified_ns=time.time_ns(),
                                   method='strict-process-absence-or-signalled')
        return complete


def track_qt_jobs(jobs, report, owned):
    """Register each actual QProcess launch and its tools under a fresh owner."""
    sequence = [0]
    tools = [0]

    def register(pid, role):
        if type(pid) is int and pid > 0:
            report['owned_pids'].append(pid)
            owned.register('job-' + str(sequence[0]) + '-' + role, pid)

    def started(job):
        sequence[0] += 1
        process = jobs.process
        process.started.connect(lambda: register(int(process.processId()), 'worker'))
        register(int(process.processId()), 'worker')

    def progress(event):
        if event.get('stage') == 'tool_ready':
            tools[0] += 1
            register(event.get('pid'), 'tool-' + str(tools[0]))

    jobs.started.connect(started)
    jobs.progress.connect(progress)


def publish_native_owner(directory):
    """Publish the actual runtime PID, which can differ from a venv launcher."""
    root = Path(directory)
    temporary = root / 'native-owner.tmp'
    temporary.write_text(json.dumps(dict(pid=os.getpid(), parent_pid=os.getppid())), encoding='utf-8')
    temporary.rename(root / 'native-owner.json')


def run_native_child(command, directory, timeout, **kwargs):
    """Retain the actual runtime incarnation while communicating with its launcher.

    Windows venv python.exe waits for a separate Python runtime process. Capture
    that runtime's HANDLE during startup, then test the same handle after wait.
    POSIX uses the directly owned Popen PID and its wait instead.
    """
    root = Path(directory)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kwargs)
    deadline = time.monotonic() + timeout
    kernel = handle = None
    evidence = dict(launcher_pid=process.pid, exited=False, handle_closed=False)
    try:
        owner_path = root / 'native-owner.json'
        while not owner_path.is_file() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert owner_path.is_file(), 'Missing native runtime ownership handshake'
        owner_bytes = owner_path.read_bytes()
        owner = json.loads(owner_bytes)
        pid, parent = owner['pid'], owner['parent_pid']
        assert type(pid) is int and pid > 0
        assert pid == process.pid or (os.name == 'nt' and parent == process.pid), owner
        if os.name == 'nt':
            kernel, handle = _windows_process(pid)  # Acquisition failure is fatal.
        evidence.update(owner, captured_ns=time.time_ns(),
                        handshake_sha256=hashlib.sha256(owner_bytes).hexdigest(),
                        method='retained-windows-process-handle' if handle else 'retained-popen-wait')
        (root / 'native-parent.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
        stdout, stderr = process.communicate(timeout=max(.001, deadline - time.monotonic()))
        evidence['launcher_wait'] = dict(method='Popen.communicate', returncode=process.returncode,
                                        verified_ns=time.time_ns())
        if process.returncode == 0:
            status = kernel.WaitForSingleObject(handle, 0) if handle else process.returncode
            evidence['native_wait_status'] = status
            assert status == 0, 'Original native runtime survived launcher wait'
            evidence.update(exited=True, verified_ns=time.time_ns())
        return process, stdout, stderr, pid
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.communicate()
        raise
    finally:
        if handle:
            evidence['handle_closed'] = bool(kernel.CloseHandle(handle))
        else:
            evidence['handle_closed'] = True
        evidence['returncode'] = process.returncode
        (root / 'native-parent.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
        assert evidence['handle_closed'], 'Could not close retained native runtime handle'


def assert_owned_exited(report, child_pid, child_returncode):
    """Validate child exit evidence after the parent's retained Popen has waited.

    Reopening a historical number here can inspect an unrelated process. The
    actual native child is instead covered by the parent's retained wait handle.
    """
    assert type(child_pid) is int and child_pid > 0 and type(child_returncode) is int and child_returncode == 0
    assert child_pid in report['owned_pids'], 'Native child identity mismatch'
    records = report['owned_lifetimes']
    covered = {child_pid}
    for row in records:
        assert type(row['pid']) is int and row['pid'] > 0
        assert isinstance(row['ownership'], str) and row['ownership']
        evidence = row.get('exit', {})
        assert evidence.get('method') == 'strict-process-absence-or-signalled', row
        assert evidence.get('checkpoint') and evidence.get('verified_ns', 0) >= row['registered_ns'], row
        covered.add(row['pid'])
    assert set(report['owned_pids']) == covered, 'Missing owned process lifecycle evidence'


def verify_native_report(directory, filename, child_pid, child_returncode):
    """Persist parent acceptance bound to the exact handshake/report/exit ledger."""
    root = Path(directory)
    raw = (root / filename).read_bytes()
    report = json.loads(raw)
    assert_owned_exited(report, child_pid, child_returncode)
    proof_path = root / 'native-parent.json'
    proof = json.loads(proof_path.read_bytes())
    assert proof['pid'] == child_pid and proof['returncode'] == child_returncode
    assert proof['exited'] and proof['handle_closed'] and proof['native_wait_status'] == 0
    assert proof['launcher_wait']['returncode'] == 0
    assert proof['handshake_sha256'] == hashlib.sha256((root / 'native-owner.json').read_bytes()).hexdigest()
    ledger = json.dumps(report['owned_lifetimes'], sort_keys=True, separators=(',', ':')).encode('utf-8')
    proof['acceptance'] = dict(report_file=filename, report_sha256=hashlib.sha256(raw).hexdigest(),
        ledger_sha256=hashlib.sha256(ledger).hexdigest(), coverage_verified_ns=time.time_ns(),
        owned_pid_count=len(set(report['owned_pids'])), lifetime_count=len(report['owned_lifetimes']))
    proof_path.write_text(json.dumps(proof, indent=2), encoding='utf-8')
