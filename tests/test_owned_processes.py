"""Deterministic ownership/exit evidence, independent of Qt and PID scheduling."""
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest

from tests.owned_processes import OwnedProcesses, assert_owned_exited, process_alive, track_qt_jobs


class OwnedProcessTests(unittest.TestCase):
    def setUp(self):
        self.live = {42: True}
        self.owned = OwnedProcesses(probe=lambda pid: self.live[pid])

    def test_confirmed_exit_survives_unrelated_live_pid_reuse(self):
        self.owned.register('cancel-renderer', 42)
        self.live[42] = False
        self.assertTrue(self.owned.exited('cancel-cleanup'))
        evidence = copy.deepcopy(self.owned.records)
        self.live[42] = True  # The OS now assigns the number to unrelated Edge.
        self.assertTrue(self.owned.exited('density-cleanup'))
        self.assertEqual(self.owned.records, evidence)

    def test_live_original_blocks_cleanup(self):
        self.owned.register('cancel-renderer', 42)
        self.assertFalse(self.owned.exited('cancel-cleanup'))
        self.assertNotIn('exit', self.owned.records[0])

    def test_new_owned_incarnation_reusing_pid_requires_fresh_exit(self):
        self.owned.register('cancel-renderer', 42)
        self.live[42] = False
        self.assertTrue(self.owned.exited('cancel-cleanup'))
        self.live[42] = True
        self.owned.register('density-renderer', 42)
        self.assertFalse(self.owned.exited('density-cleanup'))
        self.assertEqual(len(self.owned.records), 2)
        self.live[42] = False
        self.assertTrue(self.owned.exited('density-cleanup'))
        self.assertEqual([r['exit']['checkpoint'] for r in self.owned.records],
                         ['cancel-cleanup', 'density-cleanup'])

    def test_repeated_observation_of_same_ownership_is_idempotent(self):
        self.owned.register('worker-1', 42)
        self.owned.register('worker-1', 42)
        self.assertEqual(len(self.owned.records), 1)

    def test_unknown_probe_result_cannot_confirm_exit(self):
        owned = OwnedProcesses(probe=lambda pid: None)
        owned.register('worker', 42)
        with self.assertRaisesRegex(AssertionError, 'Unknown'):
            owned.exited('cleanup')
        self.assertNotIn('exit', owned.records[0])

    def test_acquisition_error_cannot_confirm_exit(self):
        def denied(pid):
            raise PermissionError('identity acquisition denied')
        owned = OwnedProcesses(probe=denied)
        owned.register('worker', 42)
        with self.assertRaises(PermissionError):
            owned.exited('cleanup')
        self.assertNotIn('exit', owned.records[0])

    def test_each_tool_launch_gets_fresh_ownership_even_within_same_job(self):
        class Signal:
            def __init__(self): self.callbacks = []
            def connect(self, callback): self.callbacks.append(callback)
            def emit(self, value):
                for callback in self.callbacks: callback(value)
        jobs = SimpleNamespace(started=Signal(), progress=Signal())
        track_qt_jobs(jobs, dict(owned_pids=[]), self.owned)
        event = dict(stage='tool_ready', pid=42)
        jobs.progress.emit(event)
        self.live[42] = False
        self.assertTrue(self.owned.exited('first-tool'))
        jobs.progress.emit(event)  # A separate owned launch reuses the number.
        self.live[42] = True
        self.assertFalse(self.owned.exited('second-tool'))

    def test_native_child_wait_binds_actual_runtime_and_launcher(self):
        from tests.owned_processes import run_native_child, verify_native_report
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            script = ('import sys,time,os,json; from pathlib import Path; '
                      'from tests.owned_processes import publish_native_owner; '
                      'root=Path(sys.argv[1]); publish_native_owner(root); '
                      '(root/"test-native.json").write_text(json.dumps(dict(owned_pids=[os.getpid()],owned_lifetimes=[]))); '
                      '\nwhile not (root/"native-parent.json").exists(): time.sleep(.01)')
            process, stdout, stderr, native_pid = run_native_child(
                [sys.executable, '-B', '-c', script, str(root)], root, timeout=10,
                cwd=Path(__file__).resolve().parents[1], env=dict(os.environ))
            self.assertEqual(process.returncode, 0, stdout + stderr)
            evidence = json.loads((root / 'native-parent.json').read_text())
            self.assertEqual(evidence['pid'], native_pid)
            self.assertEqual(evidence['launcher_pid'], process.pid)
            self.assertTrue(evidence['exited'])
            self.assertTrue(evidence['handle_closed'])
            self.assertFalse(process_alive(native_pid))
            verify_native_report(root, 'test-native.json', native_pid, process.returncode)
            proof = json.loads((root / 'native-parent.json').read_text())
            self.assertEqual(proof['acceptance']['report_sha256'],
                             hashlib.sha256((root / 'test-native.json').read_bytes()).hexdigest())
            self.assertEqual(proof['acceptance']['owned_pid_count'], 1)

    def test_parent_validates_terminal_evidence_without_reopening_pid(self):
        self.owned.register('worker', 42)
        self.live[42] = False
        self.assertTrue(self.owned.exited('child-close'))
        report = dict(owned_pids=[7, 42], owned_lifetimes=self.owned.records)
        report = json.loads(json.dumps(report))
        self.live[42] = True
        assert_owned_exited(report, child_pid=7, child_returncode=0)

    def test_parent_rejects_missing_unverified_and_unwaited_ownership(self):
        self.owned.register('worker', 42)
        report = dict(owned_pids=[7, 42], owned_lifetimes=self.owned.records)
        with self.assertRaises(AssertionError):
            assert_owned_exited(report, child_pid=7, child_returncode=0)
        self.live[42] = False
        self.assertTrue(self.owned.exited('child-close'))
        for changed in (dict(report, owned_pids=[7, 42, 43]),
                        dict(report, owned_lifetimes=[])):
            with self.assertRaises(AssertionError):
                assert_owned_exited(changed, child_pid=7, child_returncode=0)
        with self.assertRaises(AssertionError):
            assert_owned_exited(report, child_pid=7, child_returncode=None)
        with self.assertRaises(AssertionError):
            assert_owned_exited(report, child_pid=8, child_returncode=0)

    def test_real_owned_subprocess_live_then_waited_exit(self):
        child = subprocess.Popen([sys.executable, '-B', '-c', 'import sys; sys.stdin.read()'],
                                 stdin=subprocess.PIPE)
        try:
            owned = OwnedProcesses()
            owned.register('real-child', child.pid)
            self.assertTrue(process_alive(child.pid))
            self.assertFalse(owned.exited('running'))
            child.communicate(timeout=10)
            self.assertTrue(owned.exited('waited'))
            self.assertEqual(child.returncode, 0)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)


if __name__ == '__main__':
    unittest.main()
