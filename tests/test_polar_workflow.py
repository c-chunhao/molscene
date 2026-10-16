"""Real polar CLI/worker/replay and controller snapshot contracts, without Qt."""
import csv
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from molscene import analysis, engine
from tests.test_polar import fragments

HAS_PYMOL = importlib.util.find_spec('pymol2') is not None
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(HAS_PYMOL, 'requires the user-provided PyMOL interpreter')
class PolarWorkflowTests(unittest.TestCase):
    def setUp(self):
        import pymol2
        from molscene.desktop.controller import WorkbenchController
        self.pm = pymol2.PyMOL()
        self.pm.start()
        self.addCleanup(self.pm.stop)
        self.cmd = self.pm.cmd
        fragments(self.cmd)
        self.temporary = tempfile.TemporaryDirectory(prefix='polar-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.controller = WorkbenchController(self.cmd, self.root)

    def record(self, state=1):
        result = analysis.polar_contacts(self.cmd, 'don', 'acc', state=state)
        return self.controller.record_result('polar', result, dict(selection_a='don', selection_b='acc', state=state), state=state)

    def test_same_coordinate_bond_or_charge_change_is_stale(self):
        for edit in ('charge', 'bond'):
            record = self.record()
            self.assertEqual(self.controller.result_status(record), 'current')
            self.cmd.turn('x', 12)
            self.cmd.color('yellow', 'don')
            self.assertEqual(self.controller.result_status(record), 'current')
            if edit == 'charge':
                self.cmd.alter('don and name N', 'formal_charge=1')
            else:
                self.cmd.unbond('don and name N', 'don and name C1')
            self.assertEqual(self.controller.result_status(record), 'stale')
            with self.assertRaisesRegex(ValueError, 'stale'):
                self.controller.annotate_result(record)

    def test_explicit_state_fingerprint_and_frozen_annotation_table(self):
        for name in ('don', 'acc'):
            self.cmd.create(name, name, source_state=1, target_state=2, zoom=0)
        self.cmd.pseudoatom('singleton', pos=[90,0,0])
        self.cmd.frame(1)
        record = self.record(2)
        self.assertEqual(self.controller.result_status(record), 'current')
        self.cmd.frame(2)
        self.assertEqual(self.controller.result_status(record), 'current')
        with patch('molscene.analysis.polar_contacts', side_effect=AssertionError('must not recalculate')):
            self.controller.annotate_result(record)
        self.assertEqual(record['displayed_pair_count'], 1)
        self.assertEqual(record['annotation_pair_cap'], 200)
        self.assertEqual(self.cmd.get_type(record['annotation']), 'object:measurement')
        self.controller.export_table(record, 'pairs', self.root / 'polar.csv')
        with (self.root / 'polar.csv').open(newline='', encoding='utf8') as handle:
            self.assertEqual(len(list(csv.DictReader(handle))), 1)
        unverified = dict(record)
        unverified.pop('chemistry_fingerprint')
        with self.assertRaisesRegex(ValueError, 'unverified'):
            self.controller.annotate_result(unverified)

    def test_isolated_worker_snapshot_and_source_preservation(self):
        self.cmd.set('session_file', 'user-session.pse')
        before = analysis.collect_atoms(self.cmd)
        job = self.controller.prepare_job('polar', dict(selection_a='don', selection_b='acc', state=1), state=1)
        self.assertEqual(self.cmd.get('session_file'), 'user-session.pse')
        run = subprocess.run([sys.executable, '-B', '-m', 'molscene.worker', str(job)], cwd=ROOT,
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        envelope = json.loads(job.with_name('result.json').read_text(encoding='utf8'))
        result = envelope['result']
        self.assertEqual(result['pair_count'], 1)
        self.assertEqual(len(result['chemistry_fingerprint']), 64)
        self.assertEqual(self.controller.result_status(self.controller.record_result('polar', result)), 'current')
        self.assertEqual(analysis.collect_atoms(self.cmd), before)

    def test_snapshot_helper_restores_session_file_success_failure_legacy_and_advanced(self):
        self.cmd.set('session_file', 'original.pse')
        self.cmd.select('user_selection', 'don')
        self.cmd.color('red', 'don')
        self.cmd.turn('y', 31)
        for operation in ('inspect', 'timeline_project'):
            parameters = {'selection':'don', 'state':1} if operation == 'inspect' else {'timeline':self.controller.timeline_new()}
            output = None if operation == 'inspect' else self.root / 'timeline'
            for failure in (False, True):
                with self.subTest(operation=operation, failure=failure):
                    before = self.cmd.get_session()['names']
                    view = self.cmd.get_view()
                    save = self.cmd.save
                    def write(*args, **kwargs):
                        save(*args, **kwargs)
                        if failure:
                            raise RuntimeError('deliberate save failure')
                    with patch.object(self.cmd, 'save', side_effect=write):
                        if failure:
                            with self.assertRaisesRegex(RuntimeError, 'deliberate'):
                                self.controller.prepare_job(operation, parameters, output=output)
                        else:
                            self.controller.prepare_job(operation, parameters, output=output)
                    self.assertEqual(self.cmd.get('session_file'), 'original.pse')
                    self.assertEqual(self.cmd.get_session()['names'], before)
                    self.assertEqual(self.cmd.get_view(), view)

    def test_annotation_cap_keeps_complete_json_csv_and_source_rows(self):
        for i in range(201):
            self.cmd.create('acceptor_' + str(i), 'acc', zoom=0)
        record = self.controller.record_result('polar', analysis.polar_contacts(
            self.cmd, 'don', 'acceptor_*'), state=1)
        self.assertEqual(record['pair_count'], 201)
        self.controller.annotate_result(record)
        self.assertEqual(record['displayed_pair_count'], 200)
        self.assertEqual(record['annotation_pair_cap'], 200)
        self.assertEqual(len(json.loads(json.dumps(record))['pairs']), 201)
        path = self.root / 'all-pairs.csv'
        self.controller.export_table(record, 'pairs', path)
        with path.open(encoding='utf8', newline='') as handle:
            self.assertEqual(len(list(csv.DictReader(handle))), 201)

    def test_engine_cli_complete_csv_and_relocated_standalone_replay(self):
        source = self.root / 'fragments.cif'
        self.cmd.create('source', 'don or acc', zoom=0)
        self.cmd.save(str(source), 'source')
        output = self.root / 'engine'
        report = engine.polar(source, output, selection_a='name N', selection_b='name O', width=120, height=100)
        self.assertEqual(report['analysis']['pair_count'], 1)
        self.assertEqual(report['analysis']['displayed_pair_count'], 1)
        with (output/'polar.csv').open(newline='', encoding='utf8') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['donor_name'], rows[0]['acceptor_name']), ('N', 'O'))
        relocated = self.root / 'moved'
        shutil.move(str(output), relocated)
        self.cmd.reinitialize()
        old = Path.cwd()
        try:
            os.chdir(relocated)
            self.cmd.do('@scene.pml', echo=0)
        finally:
            os.chdir(old)
        self.assertEqual(self.cmd.get_type('polar'), 'object:measurement')
        self.assertAlmostEqual(self.cmd.get_distance('molecule and name N', 'molecule and name O'), 3.)
        cli = subprocess.run([sys.executable, '-B', '-m', 'molscene', 'polar', str(source), '--selection-a','name N',
                              '--selection-b','name O','--output',str(self.root/'cli'),'--width','120','--height','100'],
                             cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(cli.returncode, 0, cli.stdout + cli.stderr)
        self.assertIn('candidates', cli.stdout)
        self.assertEqual(json.loads((self.root/'cli'/'report.json').read_text())['analysis']['pair_count'], 1)


class PolarLazyTests(unittest.TestCase):
    def test_cli_help_imports_no_backend_or_qt(self):
        code = "from molscene.cli import main; import sys; assert main(['polar','--help']) == 0; assert not any(x.startswith(('pymol','PyQt','PySide')) for x in sys.modules)"
        run = subprocess.run([sys.executable, '-B', '-c', code], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
