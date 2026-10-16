"""Private subprocess owner. Its stdin is a non-inherited worker keepalive."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

LOG_LIMIT = 16 * 1024 * 1024


class _WindowsJob:
    def __init__(self):
        import ctypes
        from ctypes import wintypes as w
        self.ctypes = ctypes
        self.kernel = kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                        ('flags', w.DWORD), ('min_working', ctypes.c_size_t),
                        ('max_working', ctypes.c_size_t), ('active', w.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', w.DWORD), ('scheduling', w.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ('r', 'w', 'o', 'rb', 'wb', 'ob')]
        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', IO), ('process_memory', ctypes.c_size_t),
                        ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t),
                        ('peak_job', ctypes.c_size_t)]
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        kernel.CreateJobObjectW.restype = w.HANDLE
        kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        kernel.GetCurrentProcess.restype = w.HANDLE
        kernel.QueryInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p]
        kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        kernel.OpenProcess.restype = w.HANDLE
        kernel.IsProcessInJob.argtypes = [w.HANDLE, w.HANDLE, ctypes.POINTER(w.BOOL)]
        kernel.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
        kernel.CloseHandle.argtypes = [w.HANDLE]
        kernel.SetErrorMode(0x0001 | 0x0002 | 0x8000)
        self.handle = kernel.CreateJobObjectW(None, None)  # Non-inheritable.
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel.AssignProcessToJobObject(self.handle, kernel.GetCurrentProcess()):
            raise ctypes.WinError(ctypes.get_last_error())

    def terminate_descendants(self):
        from ctypes import wintypes as w
        c = self.ctypes
        # Query the owned job, never a machine-wide PID/name search. Open handles
        # and membership checks prevent recycled PID identities being targeted.
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            buffer = c.create_string_buffer(8 + c.sizeof(c.c_size_t) * 65536)
            if not self.kernel.QueryInformationJobObject(self.handle, 3, buffer, len(buffer), None):
                raise c.WinError(c.get_last_error())
            count = c.c_ulong.from_buffer(buffer, 4).value
            pids = (c.c_size_t * count).from_buffer(buffer, 8)
            others = [pid for pid in pids if pid != os.getpid()]
            if not others:
                return
            for pid in others:
                process = self.kernel.OpenProcess(0x0001 | 0x1000, False, pid)
                if process:
                    try:
                        member = w.BOOL()
                        if self.kernel.IsProcessInJob(process, self.handle, c.byref(member)) and member.value:
                            self.kernel.TerminateProcess(process, 1)
                    finally:
                        self.kernel.CloseHandle(process)
            time.sleep(.01)

    def finish(self):
        # Includes this guardian. The worker waits for our exit before returning.
        self.kernel.CloseHandle(self.handle)


def _emit(record):
    sys.stdout.write(json.dumps(record, ensure_ascii=True, allow_nan=False) + '\n')
    sys.stdout.flush()


def _drain(pipe, path, overflow, failures):
    try:
        with Path(path).open('xb', buffering=0) as output:
            size = 0
            while True:
                data = pipe.read(65536)
                if not data:
                    break
                remaining = max(0, LOG_LIMIT - size)
                output.write(data[:remaining])
                size += len(data)
                if size > LOG_LIMIT:
                    overflow.set()
    except BaseException as exc:
        failures.append(str(exc))
        overflow.set()
    finally:
        pipe.close()


def main():
    job = None
    owned = False
    process = None
    readers = []
    start = time.monotonic()
    result = {'returncode': None, 'elapsed_seconds': 0}
    try:
        request_path = Path(sys.argv[1])
        if request_path.stat().st_size > 256 * 1024:
            raise ValueError('Oversized guardian request')
        request = json.loads(request_path.read_text(encoding='utf-8'))
        if request['schema_version'] != 1:
            raise ValueError('Invalid guardian protocol version')
        if os.name == 'nt':
            job = _WindowsJob()
        else:
            # Parent uses start_new_session; refuse accidental unowned invocation.
            if os.getsid(0) != os.getpid() or os.getpgrp() != os.getpid():
                raise RuntimeError('Guardian must own its session and process group')
            signal.signal(signal.SIGTERM, lambda *_: None)
        owned = True
        stopped, proceed = threading.Event(), threading.Event()
        def control():
            if sys.stdin.buffer.read(1) == b'G':
                proceed.set()
                sys.stdin.buffer.read(1)
            stopped.set()  # EOF from worker death or cancellation.
        threading.Thread(target=control, daemon=True).start()
        _emit({'type': 'ready', 'pid': os.getpid()})
        while not proceed.wait(.01):
            if stopped.is_set():
                raise RuntimeError('Worker disconnected before tool spawn')
        if stopped.is_set():
            raise RuntimeError('Worker disconnected before tool spawn')
        process = subprocess.Popen(request['argv'], cwd=request['cwd'], env=request['environment'],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   shell=False, close_fds=True,
                                   creationflags=0x08000000 if os.name == 'nt' else 0)
        overflow, failures = threading.Event(), []
        for pipe, key in ((process.stdout, 'stdout_path'), (process.stderr, 'stderr_path')):
            thread = threading.Thread(target=_drain, args=(pipe, request[key], overflow, failures), daemon=True)
            thread.start()
            readers.append(thread)
        reason = None
        while process.poll() is None:
            if stopped.is_set():
                reason = 'Worker disconnected or cancelled'
                break
            if overflow.is_set():
                reason = 'Tool log limit exceeded or log write failed'
                break
            if time.monotonic() - start >= request['timeout_seconds']:
                reason = 'Tool timeout'
                break
            time.sleep(.01)
        returncode = process.poll()
        if job:
            job.terminate_descendants()
        else:
            os.killpg(os.getpid(), signal.SIGTERM)
        try:
            process.wait(timeout=.5)
        except subprocess.TimeoutExpired:
            reason = reason or 'Descendant lifetime cleanup failed'
        for thread in readers:
            thread.join(.5)
        if any(thread.is_alive() for thread in readers):
            reason = reason or 'Descendant lifetime/log drain failed'
        if overflow.is_set():
            reason = reason or 'Tool log limit exceeded or log write failed'
        result.update(returncode=returncode if returncode is not None else process.poll(),
                      elapsed_seconds=time.monotonic() - start)
        if failures:
            reason = reason or 'Log write failed: ' + '; '.join(failures)
        _emit({'type': 'error' if reason else 'result', 'message': reason, **result})
    except BaseException as exc:
        if process is not None and job:
            job.terminate_descendants()
        result['elapsed_seconds'] = time.monotonic() - start
        try:
            _emit({'type': 'error', 'message': str(exc), **result})
        except (BrokenPipeError, OSError):
            pass
    finally:
        if job:
            job.finish()
        elif owned:
            # Escalation also covers descendants ignoring TERM, even after the
            # immediate child exits. The terminal record is flushed beforehand.
            os.killpg(os.getpid(), signal.SIGKILL)


if __name__ == '__main__':
    main()
