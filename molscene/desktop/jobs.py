"""One independent worker, managed entirely by the Qt main event loop."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys

from pymol.Qt import QtCore

_COOPERATIVE = frozenset(('timeline_project', 'animation', 'density', 'potential', 'tools'))


def worker_command(job_path):
    return [sys.executable, '-B', '-u', '-m', 'molscene.worker', str(job_path)]


class JobManager(QtCore.QObject):
    started = QtCore.Signal(object)
    progress = QtCore.Signal(object)
    finished = QtCore.Signal(object)
    failed = QtCore.Signal(str)
    cancelled = QtCore.Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = None
        self.job = None
        self._cancelled = False
        self._stdout = ''
        self._stderr = ''
        self._last_progress = None
        self.last_result = None
        self._poll = QtCore.QTimer(self)
        self._poll.setInterval(250)
        self._poll.timeout.connect(self._read_progress)

    @property
    def active(self):
        return self.process is not None

    def start(self, job_path):
        if self.active:
            raise ValueError('A job is already running; cancel it or wait for completion')
        path = Path(job_path).resolve()
        job = json.loads(path.read_text(encoding='utf-8-sig'))
        if job.get('schema_version') != 1 or not job.get('result_path'):
            raise ValueError('A version 1 worker job with explicit result_path is required')
        command = worker_command(path)
        self.job = job
        self._cancelled = False
        self._stdout = self._stderr = ''
        self._last_progress = None
        self.last_result = None
        process = QtCore.QProcess(self)
        self.process = process
        environment = QtCore.QProcessEnvironment.systemEnvironment()
        source = str(Path(__file__).resolve().parents[2])
        environment.insert('PYTHONPATH', source + os.pathsep + environment.value('PYTHONPATH', ''))
        environment.insert('PYTHONDONTWRITEBYTECODE', '1')
        environment.insert('PYTHONIOENCODING', 'utf-8')
        process.setProcessEnvironment(environment)
        process.setWorkingDirectory(str(path.parent))
        process.readyReadStandardOutput.connect(self._read_stdout)
        process.readyReadStandardError.connect(self._read_stderr)
        process.errorOccurred.connect(self._error)
        process.finished.connect(self._done)
        process.setProgram(command[0])
        process.setArguments(command[1:])
        process.start()
        self._poll.start()
        self.started.emit(deepcopy(job))

    def _read_stdout(self):
        if self.process is None:
            return
        self._stdout += bytes(self.process.readAllStandardOutput()).decode('utf-8', errors='replace')
        while '\n' in self._stdout:
            line, self._stdout = self._stdout.split('\n', 1)
            try:
                record = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(record, dict) and 'progress' in record:
                self.progress.emit(record['progress'])
        self._stdout = self._stdout[-65536:]

    def _read_stderr(self):
        if self.process is None:
            return
        self._stderr += bytes(self.process.readAllStandardError()).decode('utf-8', errors='replace')
        self._stderr = self._stderr[-16000:]

    def _batch_report(self):
        if self.job and self.job['operation'] == 'batch':
            try:
                return json.loads((Path(self.job['output']) / 'batch.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                pass
        return None

    def _read_progress(self):
        report = self._batch_report()
        if report is not None and report != self._last_progress:
            self._last_progress = deepcopy(report)
            self.last_result = deepcopy(report)
            self.progress.emit(report)

    def cancel(self):
        if self.process is None or self._cancelled:
            return
        if self.job['operation'] == 'batch' or self.job['operation'] in _COOPERATIVE:
            marker = self.job.get('parameters', {}).get('cancel_file')
            if not marker:
                raise ValueError('Job has no cooperative cancel marker')
            # Marker is an exclusively owned path in prepare_job's private dir.
            with Path(marker).open('x', encoding='utf-8') as handle:
                handle.write('cancel requested\n')
            self._cancelled = True
            if self.job['operation'] in _COOPERATIVE:
                process = self.process
                QtCore.QTimer.singleShot(5000, lambda: process.kill() if self.process is process else None)
            return
        self._cancelled = True
        self.process.terminate()
        process = self.process
        # kill only this process, never a replacement launched after it finishes.
        QtCore.QTimer.singleShot(1500, lambda: process.kill() if self.process is process else None)

    def _release(self):
        self._poll.stop()
        process = self.process
        self.process = None
        if process is not None:
            process.deleteLater()

    def _error(self, error):
        if error == QtCore.QProcess.FailedToStart and self.process is not None:
            message = self.process.errorString()
            self._release()
            self.failed.emit('Worker could not start: ' + message)

    def _done(self, exit_code, exit_status):
        if self.process is None:
            return
        self._read_stdout()
        self._read_stderr()
        self._read_progress()
        if self._cancelled and self.job['operation'] not in _COOPERATIVE | {'batch'}:
            result = self._batch_report() or dict(items=[], completed=0, failed=0)
            result = deepcopy(result)
            result.update(cancelled=True, passed=False)
            for item in result.get('items', []):
                if item['status'] == 'running':
                    item.update(status='pending', error='Cancelled while running; output was not confirmed complete')
            self._release()
            self.cancelled.emit(result)
            return
        try:
            envelope = json.loads(Path(self.job['result_path']).read_text(encoding='utf-8'))
            if not isinstance(envelope, dict):
                raise ValueError('Worker envelope must be a JSON object')
            self.last_result = envelope.get('result', self.last_result)
            if isinstance(self.last_result, dict) and self.last_result.get('cancelled'):
                self._release()
                self.cancelled.emit(self.last_result)
                return
            if not isinstance(envelope.get('passed'), bool):
                raise ValueError('Worker envelope lacks a boolean passed field')
            if not envelope['passed']:
                error = envelope.get('error', 'Worker reported failure')
                if isinstance(error, dict):
                    error = error.get('message', str(error))
                raise ValueError(str(error))
            if exit_code != 0 or exit_status != QtCore.QProcess.NormalExit:
                raise ValueError('Worker exited unsuccessfully: ' + str(exit_code))
            result = envelope['result']
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if self._cancelled and self.job['operation'] in _COOPERATIVE:
                result = dict(operation=self.job['operation'], passed=False, cancelled=True)
                self._release()
                self.cancelled.emit(result)
                return
            message = str(exc)
            if self._stderr.strip():
                message += '\n' + self._stderr.strip()
            self._release()
            self.failed.emit(message)
        else:
            self._release()
            self.finished.emit(result)
