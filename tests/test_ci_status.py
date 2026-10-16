"""Exercise the actual CI runner and exact gates with controlled test results."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import textwrap
import unittest
from unittest.mock import patch


WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/ci.yml'


def step_python(name):
    section = WORKFLOW.read_text(encoding='utf-8').split('- name: ' + name, 1)[1]
    section = section.split('\n      - name:', 1)[0]
    if "<<'PY'" not in section:
        raise AssertionError('CI needs a dedicated unittest runner status stream')
    body = section.split("<<'PY'", 1)[1].split('\n', 1)[1]
    return textwrap.dedent(body.split('\n          PY', 1)[0]).strip()


@unittest.skipUnless(WORKFLOW.is_file(), 'CI workflow is repository-only, not shipped in sdist')
class CIStatusTests(unittest.TestCase):
    def test_noisy_success_skip_failure_missing_and_spoofed_ok(self):
        import ast
        runner = step_python('Run full test suite with real PyMOL')
        gate_names = ('Require native GL, mouse, pick and lifecycle tests to have passed',
                      'Require actual Linux no-replace links and process lifetime proofs')
        for gate_name in gate_names:
            gate = step_python(gate_name)
            tree = ast.parse(gate)
            required_node = next(n for n in tree.body if isinstance(n, ast.Assign)
                                 and any(isinstance(t, ast.Name) and t.id == 'required' for t in n.targets))
            required = ast.literal_eval(required_node.value)
            self.assertEqual(len(required), 12 if gate_name == gate_names[0] else 8)
            target_name = next(iter(required))
            # Exercise every unchanged mandatory identity in each gate.
            for outcome in ('pass', 'skip', 'fail', 'missing', 'unrelated', 'spoof'):
                with self.subTest(gate=gate_name, outcome=outcome), tempfile.TemporaryDirectory() as directory:
                    suite = unittest.TestSuite()
                    for name, case in required.items():
                        module, cls_name = case.rsplit('.', 1)
                        if outcome == 'unrelated' and name == target_name:
                            cls_name = 'UnrelatedTests'
                        def test(case_self, selected=(name == target_name), name=name, case=case):
                            print('PID_CLEANUP [123, 456]')
                            print('Advanced native evidence: retained diagnostic')
                            if selected and outcome == 'spoof':
                                print(f'{name} ({case}) ... ok')
                                case_self.skipTest('not executed')
                            if selected and outcome == 'skip':
                                case_self.skipTest('dependency missing')
                            if selected and outcome == 'fail':
                                case_self.fail('controlled failure')
                        cls = type(cls_name, (unittest.TestCase,), {'__module__': module, name: test})
                        if outcome != 'missing' or name != target_name:
                            suite.addTest(cls(name))
                    previous = Path.cwd()
                    diagnostics = io.StringIO()
                    try:
                        os.chdir(directory)
                        Path('artifacts').mkdir()
                        with patch.object(unittest.defaultTestLoader, 'discover', return_value=suite), \
                                contextlib.redirect_stdout(diagnostics), contextlib.redirect_stderr(diagnostics):
                            with self.assertRaises(SystemExit) as exit_status:
                                exec(compile(runner, 'ci-runner', 'exec'), {})
                        self.assertEqual(exit_status.exception.code, int(outcome == 'fail'))
                        Path('artifacts/tests.txt').write_text(diagnostics.getvalue(), encoding='utf-8')
                        self.assertIn('PID_CLEANUP', diagnostics.getvalue())
                        self.assertIn('Advanced native evidence:', diagnostics.getvalue())
                        if outcome == 'pass':
                            exec(compile(tree, 'ci-gate', 'exec'), {})
                        else:
                            with self.assertRaises(AssertionError):
                                exec(compile(tree, 'ci-gate', 'exec'), {})
                    finally:
                        os.chdir(previous)
