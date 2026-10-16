import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

from molscene import cli


class AdvancedCliTests(unittest.TestCase):
    def test_tools_empty_config_is_readonly_success(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'tools.json'
            path.write_text(json.dumps({'schema_version': 1, 'tools': {}}))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = cli.main(['tools', '--config', str(path)])
            self.assertEqual(status, 0)
            self.assertTrue(json.loads(output.getvalue())['passed'])
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_advanced_commands_reject_overwrite_and_expose_exact_flags(self):
        parser = cli._parser()
        cases = [(['timeline-project', 'a.pse', '--timeline', 't.json', '--output', 'out'], 'timeline-project'),
                 (['animate', 'a.pse', '--timeline', 't.json', '--output', 'out', '--mp4'], 'animate'),
                 (['density', 'a.dx', '--format', 'dx', '--output', 'out'], 'density'),
                 (['potential', '--mode', 'import', '--dx', 'a.dx', '--units', 'V', '--pqr', 'a.pqr', '--output', 'out'], 'potential')]
        for argv, command in cases:
            self.assertEqual(parser.parse_args(argv).command, command)
            with self.assertRaises(SystemExit):
                parser.parse_args(argv + ['--overwrite'])

    def test_pure_request_validation_rejects_unknowns_and_bool_sizes(self):
        from molscene.advanced import validate_request
        for parameters in ({'request': {'map_path': 'a.dx', 'format': 'dx'}, 'oops': 1},
                           {'request': {'map_path': 'a.dx', 'format': 'dx', 'render': {'width': True}}}):
            with self.assertRaises(ValueError):
                validate_request('density', parameters)
        result = validate_request('density', {'request': {'map_path': 'a.dx', 'format': 'dx'}})
        self.assertEqual(result['request']['render'], {'width': 800, 'height': 600, 'dpi': 150, 'ray': True})

    def test_potential_requires_explicit_mode_inputs_and_config(self):
        from molscene.advanced import validate_request
        for request in (dict(mode='prepared',pqr_path='ion.pqr'), dict(mode='prepare',selection='all'),
                        dict(mode='import',dx_path='map.dx',pqr_path='ion.pqr')):
            with self.assertRaises(ValueError):
                validate_request('potential', {'request':request})

    def test_timeline_validation_and_help_without_optional_backend(self):
        import subprocess
        import sys
        document = dict(schema_version=1, baseline_scene='baseline', fps=24, frame_count=1,
            keyframes=[dict(id='one', label='', frame=1, scene=None, state=1,
                view=[1,0,0,0,1,0,0,0,1,0,0,-20,0,0,0,1,50,0], interpolation='smooth')])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'timeline.json'
            path.write_text(json.dumps(document))
            for argv in (['--help'], ['timeline-validate', str(path)]):
                result = subprocess.run([sys.executable, '-S', '-B', '-m', 'molscene', *argv],
                    cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_density_matrix_and_volume_ramp_are_protected_json_files(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            matrix, ramp, source = root/'matrix.json', root/'ramp.json', root/'map.dx'
            matrix.write_text(json.dumps([1,0,0,2,0,1,0,3,0,0,1,4,0,0,0,1]))
            ramp.write_text(json.dumps([0,0,0,1,0,5,1,0,0,1]))
            source.write_text('source')
            # Native export is covered separately; inspect the CLI boundary
            # here to catch cwd-dependent JSON parsing or omitted protections.
            with patch('molscene.advanced.run_advanced', return_value={'passed':True}) as run:
                with contextlib.redirect_stdout(io.StringIO()):
                    code = cli.main(['density',str(source),'--format','dx','--output',str(root/'out'),
                        '--kind','volume','--volume-ramp',str(ramp),'--matrix',str(matrix)])
            self.assertEqual(code,0)
            request = run.call_args.args[3]['request']
            self.assertEqual(request['registration']['matrix'],[1,0,0,2,0,1,0,3,0,0,1,4,0,0,0,1])
            self.assertEqual(request['display']['volume_ramp'],[0,0,0,1,0,5,1,0,0,1])
            self.assertEqual(set(run.call_args.kwargs['protected']), {matrix,ramp})

    @unittest.skipUnless(importlib.util.find_spec('pymol2'), 'requires PyMOL')
    def test_actual_timeline_and_animation_cli_worker_share_parameters(self):
        import pymol2
        from molscene.timeline import new_timeline
        from molscene.worker import run_job
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source.pse'
            timeline = root / 'timeline.json'
            with pymol2.PyMOL() as pm:
                pm.cmd.pseudoatom('source', pos=[0,0,0])
                document = new_timeline(pm.cmd)
                pm.cmd.save(str(source))
            timeline.write_text(json.dumps(document))
            for command, operation in [('timeline-project','timeline_project'),('animate','animation')]:
                direct, worker = root / (command+'-direct'), root / (command+'-worker')
                argv = [command, str(source), '--timeline', str(timeline), '--output', str(direct)]
                parameters = dict(timeline=document)
                if operation == 'animation':
                    argv += ['--width','32','--height','32']
                    parameters['render'] = dict(width=32,height=32)
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(argv), 0)
                job = root / (command+'-job.json')
                job.write_text(json.dumps(dict(schema_version=1, operation=operation, input_session=str(source),
                    output=str(worker), result_path=str(root/(command+'-result.json')), parameters=parameters)))
                self.assertEqual(run_job(job), 0)
                a, b = [json.loads((path/'report.json').read_text()) for path in (direct, worker)]
                for key in ('parameters','source_fingerprints','advanced_fingerprints','analysis'):
                    self.assertEqual(a[key], b[key])

    @unittest.skipUnless(importlib.util.find_spec('pymol2'), 'requires PyMOL')
    def test_real_prepared_pqr_cli_worker_scientific_parity(self):
        import os
        import pymol2
        import subprocess
        import sys
        from molscene.desktop.controller import WorkbenchController
        from molscene.external_tools import load_tools
        from molscene.worker import run_job
        config_path = os.environ.get('MOLSCENE_TOOLS_CONFIG')
        if not config_path:
            self.skipTest('actual APBS configuration unavailable')
        config = load_tools(config_path)
        evidence = os.environ.get('MOLSCENE_TASK6_EVIDENCE')
        if evidence:
            Path(evidence).mkdir(parents=True, exist_ok=True)
            root = Path(tempfile.mkdtemp(prefix='parity-', dir=evidence))
        else:
            temporary = tempfile.TemporaryDirectory(prefix='a6-')
            self.addCleanup(temporary.cleanup)
            root = Path(temporary.name)
        pqr = root/'ion.pqr'
        pqr.write_text('ATOM 1 I ION 1 0 0 0 1 3.0\n')
        original = pqr.read_bytes()
        direct, worker = root/'cli',root/'worker'
        code_root = Path(__file__).resolve().parents[1]
        command = [sys.executable,'-B','-m','molscene','potential','--mode','prepared','--pqr',str(pqr),
            '--tools',str(config_path),'--output',str(direct),'--grid','33','33','33','--width','64','--height','48']
        completed = subprocess.run(command,cwd=code_root,capture_output=True,text=True,timeout=120)
        (root/'cli.stdout.log').write_text(completed.stdout,encoding='utf-8')
        (root/'cli.stderr.log').write_text(completed.stderr,encoding='utf-8')
        self.assertEqual(completed.returncode,0,completed.stderr)
        with pymol2.PyMOL() as pm:
            pm.cmd.pseudoatom('unrelated',pos=[99,99,99])
            controller = WorkbenchController(pm.cmd, root)
            job = controller.prepare_job('potential', {'request':dict(mode='prepared',pqr_path=str(pqr),tools=config,
                solver=dict(grid=[33]*3),render=dict(width=64,height=48))},output=worker)
            self.assertNotIn('input_session',json.loads(job.read_text()))
            environment=dict(os.environ,PYTHONPATH=str(code_root))
            completed=subprocess.run([sys.executable,'-B','-m','molscene.worker',str(job)],cwd=job.parent,
                env=environment,capture_output=True,text=True,timeout=120)
            (root/'worker.stdout.log').write_text(completed.stdout,encoding='utf-8')
            (root/'worker.stderr.log').write_text(completed.stderr,encoding='utf-8')
            self.assertEqual(completed.returncode,0,completed.stderr)
            loaded = controller.load_advanced_result(worker/'report.json')
            self.assertEqual(controller.result_status(loaded),'current')
            self.assertEqual(pm.cmd.get_model('unrelated').atom[0].coord,[99.,99.,99.])
        a,b = [json.loads((path/'report.json').read_text()) for path in (direct,worker)]
        for key in ('parameters','source_fingerprints','structure'):
            self.assertEqual(a[key],b[key])
        for key in ('value_sha256','shape','origin_angstrom','basis_angstrom','statistics'):
            self.assertEqual(a['analysis']['raw_grid'][key],b['analysis']['raw_grid'][key])
        self.assertEqual(a['analysis']['solver']['actual_counts'],[33,33,33])
        self.assertEqual(a['analysis']['solver'],b['analysis']['solver'])
        self.assertEqual(a['advanced_fingerprints']['selection'],b['advanced_fingerprints']['selection'])
        self.assertEqual(pqr.read_bytes(),original)
        (root/'parity.json').write_text(json.dumps(dict(passed=True,value_sha256=a['analysis']['raw_grid']['value_sha256'],
            actual_counts=a['analysis']['solver']['actual_counts'],cli=str(direct),worker=str(worker)),indent=2))
