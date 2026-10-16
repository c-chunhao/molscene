"""Real subprocess tests: argv, provenance, limits and descendant ownership."""
import copy
import ctypes
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

try:
    from molscene import external_tools as tools
except ImportError:
    tools = None


def alive(pid):
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
        kernel.CloseHandle(ctypes.c_void_p(handle))
        return code.value == 259
    try:
        state = Path('/proc', str(pid), 'stat')
        if state.exists() and state.read_text().split(') ')[1].startswith('Z'):
            return False
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


class ExternalToolsTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(tools, 'managed external tool implementation is missing')
        self.temporary = tempfile.TemporaryDirectory(prefix='tools unicode 空格 & ', dir=os.environ.get('TEMP'))
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fake = self.root / 'fake tool 空 &.py'
        shutil.copyfile(Path(__file__).parent / 'fixtures/advanced/fake_tool.py', self.fake)
        self.config = {'schema_version': 1, 'tools': {'apbs': {
            'argv': [str(Path(sys.executable).resolve()), '-B', str(self.fake)],
            'architecture': 'x64' if struct.calcsize('P') == 8 else 'x86',
            'path_dirs': [str(Path(sys.executable).parent)], 'runtime_files': [],
            'timeout_seconds': 5, 'memory_limit_mib': 512}}}

    def run_tool(self, args, **kwargs):
        return tools.run_tool('apbs', self.config, args, cwd=self.root,
                              log_prefix=self.root / 'run', **kwargs)

    def test_argv_paths_and_child_environment(self):
        output = self.root / 'captured.json'
        # A relocated venv launcher exercises the executable path itself, in
        # addition to the Unicode script, cwd, logs and argument paths.
        venv_config = Path(sys.prefix) / 'pyvenv.cfg'
        if venv_config.is_file():
            executable = self.root / Path(sys.executable).name
            shutil.copyfile(sys.executable, executable)
            shutil.copymode(sys.executable, executable)
            shutil.copyfile(venv_config, self.root / 'pyvenv.cfg')
            self.config['tools']['apbs']['argv'][0] = str(executable)
        before = dict(os.environ)
        values = ['a b', '中文', '$(touch nope)', 'x&y', '"quote"', '; rm -rf /']
        with patch.dict(os.environ, {'PYTHONPATH': 'untrusted', 'PYTHONHOME': 'untrusted',
                                     'VIRTUAL_ENV': 'untrusted', 'CONDA_PREFIX': 'untrusted'}):
            result = self.run_tool(['capture', str(output), *values])
        self.assertEqual(dict(os.environ), before)
        captured = json.loads(output.read_text())
        self.assertEqual(captured['argv'], values)
        for key in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'CONDA_PREFIX'):
            self.assertNotIn(key, captured['environment'])
        self.assertEqual(captured['environment']['PYTHONNOUSERSITE'], '1')
        self.assertTrue(Path(captured['environment']['TEMP']).is_relative_to(self.root))
        if os.name == 'nt':
            self.assertEqual(captured['console_window'], 0)
        self.assertEqual(result['returncode'], 0)
        self.assertEqual(result['version'], '3.4.1')
        self.assertIn('guardian_pid', result, 'Execution provenance must retain the guardian ownership PID')
        self.assertGreater(result['guardian_pid'], 0)
        self.assertFalse(alive(result['guardian_pid']))
        self.assertEqual(result['architecture'], self.config['tools']['apbs']['architecture'])
        self.assertEqual(len(result['hashes']['executable']), 64)
        self.assertEqual(result['argv'][-len(values):], values)

    def test_invalid_config_rejected_before_spawn(self):
        entry = self.config['tools']['apbs']
        cases = [('argv', 'python --version'), ('architecture', 'arm64'),
                 ('timeout_seconds', True), ('memory_limit_mib', 0),
                 ('runtime_files', [{'path': sys.executable, 'role': 'arbitrary'}]),
                 ('runtime_files', [{'path': 'relative.dll', 'role': 'dll'}]),
                 ('argv', [str(self.root / 'absent.exe')])]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                bad = copy.deepcopy(self.config)
                bad['tools']['apbs'][key] = value
                with self.assertRaises(ValueError):
                    tools.run_tool('apbs', bad, [], cwd=self.root, log_prefix=self.root / 'bad')
        batch = self.root / 'bad.cmd'
        batch.write_text('@echo unsafe')
        entry['argv'] = [str(batch)]
        with self.assertRaises(ValueError):
            self.run_tool([])
        self.assertFalse(list(self.root.glob('*.log')))

    def test_reject_string_args_and_architecture_mismatch(self):
        with self.assertRaises(ValueError):
            self.run_tool('--version')
        self.config['tools']['apbs']['architecture'] = 'x86' if struct.calcsize('P') == 8 else 'x64'
        with self.assertRaises(ValueError):
            self.run_tool([])

    def test_truncated_executable_headers_raise_value_error(self):
        header = bytearray(64)
        header[:2] = b'MZ'
        struct.pack_into('<I', header, 60, 64)
        fixtures = (header + b'PE\0\0', header + b'PE\0\0\x64',
                    b'\xce\xfa\xed\xfe', b'\xcf\xfa\xed\xfe\x07\0\0')
        for index, content in enumerate(fixtures):
            with self.subTest(header=index):
                executable = self.root / ('truncated-' + str(index) + '.exe')
                executable.write_bytes(content)
                executable.chmod(0o700)
                self.config['tools']['apbs']['argv'] = [str(executable)]
                path = self.root / 'tools.json'
                path.write_text(json.dumps(self.config))
                try:
                    tools.load_tools(path)
                except Exception as exc:
                    self.assertIsInstance(exc, ValueError)
                else:
                    self.fail('Truncated executable header was accepted')
        self.assertFalse(list(self.root.glob('*.log')))

    def test_load_tools_rejects_unknown_keys_duplicates_and_boolean_version(self):
        path = self.root / 'tools.json'
        for document in ('{"schema_version":1,"schema_version":1,"tools":{}}',
                         '{"schema_version":true,"tools":{}}',
                         '{"schema_version":1,"tools":{},"environment":{}}'):
            path.write_text(document)
            with self.assertRaises(ValueError):
                tools.load_tools(path)
        path.write_text(json.dumps(self.config))
        self.assertEqual(tools.load_tools(path), self.config)

    def test_nonzero_exit_retains_diagnostics(self):
        with self.assertRaises(tools.ExternalToolError) as caught:
            self.run_tool(['exit', '7'])
        self.assertEqual(caught.exception.result['returncode'], 7)
        self.assertIn('diagnostic', Path(caught.exception.result['stderr_path']).read_text())

    def test_version_missing_or_false_rejected(self):
        for version in ('', 'unrelated 3.4.1', 'APBS 0.0.0'):
            with self.subTest(version=version), patch.dict(os.environ, {'MOLSCENE_FAKE_VERSION': version}):
                with self.assertRaises(tools.ExternalToolError) as caught:
                    tools.probe_tool('apbs', self.config, work_dir=self.root)
                self.assertTrue(Path(caught.exception.result['stdout_path']).is_file())

    def test_failed_run_probe_retains_registered_logs(self):
        with patch.dict(os.environ, {'MOLSCENE_FAKE_VERSION': 'unrecognized'}):
            with self.assertRaises(tools.ExternalToolError) as caught:
                self.run_tool(['missing'])
        self.assertEqual(Path(caught.exception.result['stdout_path']), self.root / 'run.stdout.log')
        self.assertIn('unrecognized', (self.root / 'run.stdout.log').read_text())

    def test_pdb2pqr_rejects_real_interpreter_older_than_311(self):
        if sys.version_info >= (3, 11):
            self.skipTest('Test requires actual older interpreter; >=3.11 covered by real runtime probe')
        self.config['tools']['pdb2pqr'] = self.config['tools'].pop('apbs')
        with patch.dict(os.environ, {'MOLSCENE_FAKE_VERSION': '3.7.1'}):
            with self.assertRaises(tools.ExternalToolError) as caught:
                tools.probe_tool('pdb2pqr', self.config, work_dir=self.root)
        self.assertIn('3.11', str(caught.exception))

    def test_apbs_version_exit_13_is_probe_only(self):
        with patch.dict(os.environ, {'MOLSCENE_FAKE_VERSION_EXIT': '13'}):
            probe = tools.probe_tool('apbs', self.config, work_dir=self.root)
            self.assertEqual(probe['version'], '3.4.1')
            self.assertEqual(probe['returncode'], 13)
            with self.assertRaises(tools.ExternalToolError):
                self.run_tool(['exit', '13'])

    def test_cancellation_before_launch_creates_no_logs(self):
        from molscene.bundles import CancelledError
        with self.assertRaises(CancelledError):
            self.run_tool(['sleep'], cancelled=lambda: True)
        self.assertFalse(list(self.root.glob('*.log')))

    def test_probe_forwards_cancellation_at_launch_boundary(self):
        from molscene.bundles import CancelledError
        flag = [False]
        def progress(record):
            if record['stage'] == 'tool_ready':
                flag[0] = True
        with self.assertRaises(CancelledError):
            tools.probe_tool('apbs', self.config, work_dir=self.root,
                             cancelled=lambda: flag[0], progress=progress)

    def test_ready_guardian_and_launcher_death_preserves_error_and_cleanup(self):
        from molscene.bundles import CancelledError
        for cancel_after_death in (False, True):
            with self.subTest(cancel_after_death=cancel_after_death):
                launched, ownership = [], []
                real_popen = subprocess.Popen
                def launch(*args, **kwargs):
                    # Observe the real launcher so the callback can wait until
                    # all its pipe handles close; no process behavior is faked.
                    process = real_popen(*args, **kwargs)
                    launched.append(process)
                    return process
                def progress(record):
                    if record['stage'] != 'tool_ready':
                        return
                    ownership.append(record['pid'])
                    if os.name == 'nt':
                        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                        kernel.OpenProcess.restype = ctypes.c_void_p
                        handle = kernel.OpenProcess(1 | 0x100000, False, record['pid'])
                        self.assertTrue(handle)
                        try:
                            self.assertTrue(kernel.TerminateProcess(ctypes.c_void_p(handle), 9))
                            self.assertEqual(kernel.WaitForSingleObject(ctypes.c_void_p(handle), 3000), 0)
                        finally:
                            kernel.CloseHandle(ctypes.c_void_p(handle))
                    else:
                        import signal
                        os.kill(record['pid'], signal.SIGKILL)
                    launched[-1].wait(timeout=3)
                error = None
                with patch.object(tools.subprocess, 'Popen', side_effect=launch):
                    try:
                        tools.probe_tool('apbs', self.config, work_dir=self.root,
                                         progress=progress,
                                         cancelled=lambda: cancel_after_death and bool(ownership))
                    except Exception as exc:
                        error = exc
                self.assertTrue(ownership)
                self.assertFalse(alive(ownership[0]))
                self.assertTrue(all(process.poll() is not None for process in launched))
                self.assertTrue(all(process.stdin.closed and process.stdout.closed for process in launched))
                self.assertFalse(list(self.root.rglob('.molscene-tool-*')))
                if cancel_after_death:
                    self.assertIsInstance(error, CancelledError)
                else:
                    self.assertIsInstance(error, tools.ExternalToolError)
                    self.assertEqual(error.result['guardian_pid'], ownership[0])
                    self.assertEqual(error.result['argv'][-1], '--version')
                    json.dumps(error.result, allow_nan=False)
                print('READY_BOUNDARY_CLEANUP ' + json.dumps(dict(guardian_pid=ownership[0],
                      launcher_pid=launched[-1].pid, cancelled=cancel_after_death,
                      exception=type(error).__name__)), flush=True)

    def assert_pids_gone(self, path):
        records = [json.loads(line) for line in path.read_text().splitlines()]
        deadline = time.monotonic() + 5
        while any(alive(row['pid']) for row in records) and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertTrue(records)
        self.assertFalse([row for row in records if alive(row['pid'])], records)
        print('PID_CLEANUP ' + json.dumps(records), flush=True)

    def test_timeout_reaps_child_and_grandchild(self):
        self.config['tools']['apbs']['timeout_seconds'] = 1
        pids = self.root / 'pids.jsonl'
        with self.assertRaises(tools.ExternalToolError) as caught:
            self.run_tool(['tree', str(pids)])
        self.assertIn('timeout', str(caught.exception).lower())
        self.assert_pids_gone(pids)
        self.assertEqual(len(pids.read_text().splitlines()), 3)

    def test_cancellation_during_run_reaps_descendants(self):
        from molscene.bundles import CancelledError
        pids = self.root / 'pids.jsonl'
        def cancelled():
            return pids.exists() and len(pids.read_text().splitlines()) == 3
        with self.assertRaises(CancelledError):
            self.run_tool(['tree', str(pids)], cancelled=cancelled)
        self.assert_pids_gone(pids)

    def test_successful_parent_exit_still_reaps_descendants(self):
        pids = self.root / 'pids.jsonl'
        result = self.run_tool(['orphan', str(pids)])
        self.assertEqual(result['returncode'], 0)
        self.assertEqual(len(pids.read_text().splitlines()), 3)
        self.assert_pids_gone(pids)

    def test_stdout_stderr_flood_is_bounded_and_fails(self):
        with self.assertRaises(tools.ExternalToolError) as caught:
            self.run_tool(['flood'])
        self.assertIn('log', str(caught.exception).lower())
        sizes = [Path(caught.exception.result[key]).stat().st_size for key in ('stdout_path', 'stderr_path')]
        self.assertLessEqual(max(sizes), 16 * 1024 * 1024)
        self.assertEqual(max(sizes), 16 * 1024 * 1024)

    def test_missing_output_is_visible_for_caller_validation(self):
        result = self.run_tool(['missing'])
        self.assertEqual(result['returncode'], 0)
        self.assertFalse((self.root / 'expected.dx').exists())

    def test_forced_worker_death_reaps_descendants(self):
        for boundary in (False, True):
            with self.subTest(boundary=boundary):
                folder = self.root / str(boundary)
                folder.mkdir()
                config_path = folder / 'config.json'
                config_path.write_text(json.dumps(self.config))
                pids = folder / 'pids.jsonl'
                ready = folder / 'ready.json'
                script = folder / 'worker.py'
                script.write_text('import json,os,sys,time\nfrom pathlib import Path\n'
                                  f'sys.path.insert(0,{str(Path(__file__).resolve().parents[1])!r})\n'
                                  'from molscene.external_tools import run_tool,load_tools\n'
                                  'def progress(record):\n'
                                  f' if record["stage"] == "tool_ready" and {boundary!r}:\n'
                                  f'  Path({str(ready)!r}).write_text(json.dumps(record))\n'
                                  '  time.sleep(60)\n'
                                  f'run_tool("apbs",load_tools(Path({str(config_path)!r})),["tree",{str(pids)!r}],'
                                  f'cwd=Path({str(folder)!r}),log_prefix=Path({str(folder / "run")!r}),progress=progress)\n')
                worker = subprocess.Popen([sys.executable, '-B', str(script)], stdout=subprocess.DEVNULL,
                                          stderr=subprocess.PIPE, creationflags=0x08000000 if os.name == 'nt' else 0)
                try:
                    deadline = time.monotonic() + 8
                    while time.monotonic() < deadline:
                        if ready.exists() if boundary else pids.exists() and len(pids.read_text().splitlines()) == 3:
                            break
                        if worker.poll() is not None:
                            self.fail(worker.stderr.read().decode())
                        time.sleep(.02)
                    else:
                        self.fail('worker never reached expected spawn boundary')
                    worker.kill()
                    worker.wait(3)
                    if boundary:
                        guardian_pid = json.loads(ready.read_text())['pid']
                        pids.write_text(json.dumps({'role': 'guardian_before_spawn', 'pid': guardian_pid}) + '\n')
                    self.assert_pids_gone(pids)
                finally:
                    if worker.poll() is None:
                        worker.kill()
                        worker.wait(3)
                    worker.stderr.close()

    def test_forced_guardian_death_reaps_descendants(self):
        pids = self.root / 'pids.jsonl'
        guardian = [None]
        killed = [False]
        def progress(record):
            if record['stage'] == 'tool_ready':
                guardian[0] = record['pid']
        def cancelled():
            if not killed[0] and pids.exists() and len(pids.read_text().splitlines()) == 3:
                if os.name == 'nt':
                    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                    kernel.OpenProcess.restype = ctypes.c_void_p
                    handle = kernel.OpenProcess(1, False, guardian[0])
                    self.assertTrue(handle)
                    kernel.TerminateProcess(ctypes.c_void_p(handle), 9)
                    kernel.CloseHandle(ctypes.c_void_p(handle))
                else:
                    import signal
                    os.kill(guardian[0], signal.SIGKILL)
                killed[0] = True
            return False
        with self.assertRaises(tools.ExternalToolError):
            self.run_tool(['tree', str(pids)], cancelled=cancelled, progress=progress)
        self.assertTrue(killed[0])
        self.assert_pids_gone(pids)

    def test_manifest_uses_actual_probe_identity(self):
        probe = tools.probe_tool('apbs', self.config, work_dir=self.root)
        manifest = tools.tool_manifest(self.config, {'apbs': probe})
        self.assertEqual(manifest['tools']['apbs']['version'], '3.4.1')
        self.assertEqual(manifest['tools']['apbs']['hashes'], probe['hashes'])


class RealToolProbeTests(unittest.TestCase):
    def test_configured_real_tool_identities(self):
        path = os.environ.get('MOLSCENE_TOOLS_CONFIG')
        if not path:
            if os.environ.get('MOLSCENE_REQUIRE_ADVANCED') == '1':
                self.fail('MOLSCENE_TOOLS_CONFIG is required for advanced acceptance')
            self.skipTest('Set MOLSCENE_TOOLS_CONFIG to probe real configured tools')
        self.assertIsNotNone(tools)
        config = tools.load_tools(Path(path))
        with tempfile.TemporaryDirectory(prefix='real-tool-probes-', dir=os.environ.get('TEMP')) as temporary:
            probes = {}
            names = ('apbs', 'pdb2pqr', 'ffmpeg', 'ffprobe') if os.environ.get('MOLSCENE_REQUIRE_ADVANCED') == '1' else tuple(config['tools'])
            for name in names:
                with self.subTest(tool=name):
                    self.assertIn(name, config['tools'])
                    probes[name] = tools.probe_tool(name, config, work_dir=Path(temporary))
                    self.assertTrue(probes[name]['version'])
                    self.assertEqual(len(probes[name]['hashes']['executable']), 64)
            if 'pdb2pqr' in probes:
                self.assertGreaterEqual(tuple(map(int, probes['pdb2pqr']['python_version'].split('.'))), (3, 11))
            print('ACTUAL_TOOL_IDENTITIES ' + json.dumps(tools.tool_manifest(config, probes)), flush=True)


if __name__ == '__main__':
    unittest.main()
