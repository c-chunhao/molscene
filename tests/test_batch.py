"""Real renderer tests for batch safety, persistence and cancellation."""
import csv
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

try:
    from molscene import batch
except ImportError:
    batch = None

PDB = '''ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C
ATOM      2  CA  GLY A   2       3.000   4.000   0.000  1.00 20.00           C
ATOM      3  CA  ALA A   3       3.000   4.000   5.000  1.00 20.00           C
END
'''
HAS_PYMOL = importlib.util.find_spec('pymol2') is not None
ROOT = Path(__file__).resolve().parents[1]


class BatchFixture:
    def setUp(self):
        self.assertIsNotNone(batch, 'Batch module must be implemented')
        self.temp = tempfile.TemporaryDirectory(prefix='molscene-batch-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / 'inputs'
        self.inputs.mkdir()
        (self.inputs / 'a.pdb').write_text(PDB)
        self.output = self.root / 'output'


class BatchSafetyTests(BatchFixture, unittest.TestCase):
    def test_progress_publish_survives_a_real_transient_reader(self):
        baseline = batch.run_batch(self.inputs, self.output, cancelled=lambda: True)
        changed = dict(baseline, completed=7)
        target = self.output / 'batch.json'
        reader = target.open('rb')
        errors = []
        releases = []
        replace = os.replace

        def observe(source, destination):
            try:
                return replace(source, destination)
            except OSError as exc:
                errors.append(exc)
                self.assertEqual(json.loads(target.read_text()), baseline)
                if not releases:
                    release = threading.Timer(0.2, reader.close)
                    releases.append(release)
                    release.start()
                raise

        try:
            with patch.object(batch.os, 'replace', side_effect=observe):
                batch._persist(self.output, changed, [self.inputs / 'a.pdb'], None)
        finally:
            for release in releases:
                release.join()
            reader.close()
        if os.name == 'nt':
            self.assertTrue(errors, 'The actual Windows read handle must overlap publication')
            self.assertTrue(all(getattr(error, 'winerror', None) in (5, 32, 33) for error in errors))
        self.assertEqual(json.loads(target.read_text()), changed)
        self.assertFalse(list(self.output.glob('.molscene-batch-*')))

    @unittest.skipUnless(os.name == 'nt', 'Windows sharing restrictions')
    def test_persistent_progress_reader_fails_within_bound_and_preserves_previous_json(self):
        baseline = batch.run_batch(self.inputs, self.output, cancelled=lambda: True)
        target = self.output / 'batch.json'
        attempts = []
        replace = os.replace

        def observe(source, destination):
            attempts.append(destination)
            return replace(source, destination)

        started = time.monotonic()
        with target.open('rb'), patch.object(batch.os, 'replace', side_effect=observe):
            with self.assertRaises(PermissionError):
                batch._persist(self.output, dict(baseline, completed=7), [self.inputs / 'a.pdb'], None)
        self.assertGreater(len(attempts), 1)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(json.loads(target.read_text()), baseline)
        self.assertFalse(list(self.output.glob('.molscene-batch-*')))

    def test_nonsharing_publish_error_propagates_without_retry_or_clobber(self):
        baseline = batch.run_batch(self.inputs, self.output, cancelled=lambda: True)
        error = OSError('Disk full')
        error.winerror = 112
        with patch.object(batch.os, 'replace', side_effect=error) as replace:
            with self.assertRaisesRegex(OSError, 'Disk full'):
                batch._persist(self.output, dict(baseline, completed=7), [self.inputs / 'a.pdb'], None)
        self.assertEqual(replace.call_count, 1)
        self.assertEqual(json.loads((self.output / 'batch.json').read_text()), baseline)
        self.assertFalse(list(self.output.glob('.molscene-batch-*')))

    @unittest.skipUnless(os.name == 'nt', 'Windows sharing retry')
    def test_progress_retry_rechecks_changed_destination_type(self):
        baseline = batch.run_batch(self.inputs, self.output, cancelled=lambda: True)
        target = self.output / 'batch.json'
        error = PermissionError('Reader released while destination changed')
        error.winerror = 5

        def changed_destination(source, destination):
            target.unlink()
            target.mkdir()
            (target / 'sentinel').write_text('keep')
            raise error

        with patch.object(batch.os, 'replace', side_effect=changed_destination):
            with self.assertRaisesRegex(ValueError, 'regular file'):
                batch._persist(self.output, baseline, [self.inputs / 'a.pdb'], None)
        self.assertEqual((target / 'sentinel').read_text(), 'keep')
        self.assertFalse(list(self.output.glob('.molscene-batch-*')))

    @unittest.skipUnless(os.name == 'nt', 'Windows sharing retry')
    def test_progress_retry_rejects_a_new_link_without_touching_external_file(self):
        baseline = batch.run_batch(self.inputs, self.output, cancelled=lambda: True)
        target = self.output / 'batch.json'
        outside = self.root / 'outside.json'
        outside.write_text('keep')
        probe = self.root / 'probe'
        try:
            probe.symlink_to(outside)
        except OSError as exc:
            self.skipTest('Filesystem cannot create symlinks: ' + str(exc))
        error = PermissionError('Destination changed while reader closed')
        error.winerror = 5

        def changed_destination(source, destination):
            target.unlink()
            target.symlink_to(outside)
            raise error

        with patch.object(batch.os, 'replace', side_effect=changed_destination):
            with self.assertRaisesRegex(ValueError, 'link|reparse'):
                batch._persist(self.output, baseline, [self.inputs / 'a.pdb'], None)
        self.assertEqual(outside.read_text(), 'keep')
        self.assertFalse(list(self.output.glob('.molscene-batch-*')))

    def test_rejects_both_ancestry_directions_without_mutation(self):
        for output in (self.inputs, self.inputs / 'child', self.root):
            with self.subTest(output=output), self.assertRaisesRegex(ValueError, 'overlap|ancestor|contain'):
                batch.run_batch(self.inputs, output, overwrite=True)
        self.assertEqual((self.inputs / 'a.pdb').read_text(), PDB)
        self.assertFalse((self.inputs / 'child').exists())

    def test_nonempty_output_and_empty_input_rejected_before_summaries(self):
        self.output.mkdir()
        (self.output / 'sentinel').write_text('keep')
        with self.assertRaisesRegex(ValueError, 'nonempty|overwrite'):
            batch.run_batch(self.inputs, self.output)
        self.assertFalse((self.output / 'batch.json').exists())
        (self.inputs / 'a.pdb').unlink()
        with self.assertRaisesRegex(ValueError, 'No|no|empty'):
            batch.run_batch(self.inputs, self.root / 'empty')
        self.assertFalse((self.root / 'empty').exists())

    def test_symlinked_input_and_output_rejected(self):
        linked = self.root / 'linked'
        try:
            linked.symlink_to(self.inputs, target_is_directory=True)
        except OSError as exc:
            self.skipTest('Filesystem cannot create symlinks: ' + str(exc))
        with self.assertRaisesRegex(ValueError, 'link|reparse'):
            batch.run_batch(linked, self.output)
        with self.assertRaisesRegex(ValueError, 'link|reparse'):
            batch.run_batch(self.inputs, linked / 'out', overwrite=True)

    def test_linked_summary_and_input_file_rejected(self):
        self.output.mkdir()
        target = self.root / 'sentinel'
        target.write_text('untouched')
        try:
            (self.output / 'batch.json').symlink_to(target)
        except OSError as exc:
            self.skipTest('Filesystem cannot create symlinks: ' + str(exc))
        with self.assertRaisesRegex(ValueError, 'link|reparse'):
            batch.run_batch(self.inputs, self.output, overwrite=True)
        self.assertEqual(target.read_text(), 'untouched')
        (self.output / 'batch.json').unlink()
        (self.inputs / 'linked.pdb').symlink_to(self.inputs / 'a.pdb')
        with self.assertRaisesRegex(ValueError, 'link|reparse'):
            batch.run_batch(self.inputs, self.output, overwrite=True)

    def test_hardlinked_input_rejected(self):
        try:
            os.link(self.inputs / 'a.pdb', self.inputs / 'b.pdb')
        except OSError as exc:
            self.skipTest('Filesystem cannot create hardlinks: ' + str(exc))
        with self.assertRaisesRegex(ValueError, 'link'):
            batch.run_batch(self.inputs, self.output)

    def test_cancel_before_first_item_persists_pending_and_not_passed(self):
        report = batch.run_batch(self.inputs, self.output, cancelled=lambda: True)
        self.assertFalse(report['passed'])
        self.assertTrue(report['cancelled'])
        self.assertEqual((report['completed'], report['failed']), (0, 0))
        self.assertEqual(report['items'][0]['status'], 'pending')
        self.assertEqual(json.loads((self.output / 'batch.json').read_text()), report)


class BatchErrorPrivacyTests(BatchFixture, unittest.TestCase):
    def test_unc_and_device_error_paths_keep_basename_without_absolute_location(self):
        paths = (
            r'\\server\private-share\library.pyd',
            r'\\?\UNC\server\private-share\library.pyd',
            r'\\?\C:\private\library.pyd',
            r'\\.\C:\private\library.pyd',
            r'\\.\PIPE\private\library.pyd',
            r'\??\C:\private\library.pyd',
            r'\Device\HarddiskVolume1\private\library.pyd',
            '//server/private-share/library.pyd',
            r'"\\server\private share\library.pyd"',
        )
        for path in paths:
            with self.subTest(path=path):
                message = batch._error_message(RuntimeError(
                    'Backend dependency failed: ' + path + '; input inputs/input.pdb'),
                    self.inputs / 'a.pdb', self.output, self.inputs)
                self.assertNotIn(path, message)
                self.assertNotIn('server', message)
                self.assertNotIn('private', message)
                self.assertNotIn('HarddiskVolume1', message)
                self.assertIn('library.pyd', message)
                self.assertIn('Backend dependency failed:', message)
                self.assertIn('inputs/input.pdb', message)

    def test_backend_path_errors_are_sanitized_in_summaries_and_callbacks_and_stay_failed(self):
        snapshots = []
        diagnostic = (r'Backend dependency failed: \\server\private-share\library.pyd '
                      r'and \\?\UNC\server\private-share\library.pyd; input inputs/input.pdb')
        # Only the external backend fails; orchestration and persistence stay real.
        with patch('molscene.batch.engine.render', side_effect=RuntimeError(diagnostic)):
            report = batch.run_batch(self.inputs, self.output, progress=snapshots.append)
        self.assertFalse(report['passed'])
        self.assertEqual((report['completed'], report['failed']), (0, 1))
        self.assertEqual(report['items'][0]['status'], 'failed')
        self.assertEqual(json.loads((self.output / 'batch.json').read_text()), report)
        with (self.output / 'batch.csv').open(newline='', encoding='utf-8') as handle:
            rows = list(csv.DictReader(handle))
        errors = [report['items'][0]['error'], rows[0]['error']]
        errors += [snapshot['items'][0]['error'] for snapshot in snapshots
                   if snapshot['items'][0]['status'] == 'failed']
        self.assertGreaterEqual(len(errors), 3)
        for error in errors:
            self.assertNotIn('server', error)
            self.assertNotIn('private-share', error)
            self.assertNotIn('\\\\', error)
            self.assertIn('library.pyd', error)
            self.assertIn('inputs/input.pdb', error)


@unittest.skipUnless(HAS_PYMOL, 'PyMOL required for real batch rendering')
class BatchRenderTests(BatchFixture, unittest.TestCase):
    def run_batch(self, **options):
        return batch.run_batch(self.inputs, self.output,
                               render_options={'width': 160, 'height': 120, 'dpi': 72}, **options)

    def test_sorted_success_failure_and_persisted_callback_snapshot(self):
        (self.inputs / 'z-invalid.pdb').write_text('not a structure\n')
        (self.inputs / 'ignored.txt').write_text(PDB)
        (self.inputs / 'nested').mkdir()
        (self.inputs / 'nested' / 'also-ignored.pdb').write_text(PDB)
        snapshots = []
        def progress(report):
            persisted = json.loads((self.output / 'batch.json').read_text())
            self.assertEqual(persisted, report)
            snapshots.append(report)
            report['items'][0]['input'] = 'callback cannot mutate runner'
        report = self.run_batch(progress=progress)
        self.assertFalse(report['passed'])
        self.assertEqual((report['completed'], report['failed']), (1, 1))
        self.assertEqual([item['input'] for item in report['items']], ['a.pdb', 'z-invalid.pdb'])
        self.assertEqual([item['status'] for item in report['items']], ['completed', 'failed'])
        self.assertEqual(report['items'][0]['output'], '0001-a')
        self.assertTrue((self.output / '0001-a' / 'scene.png').is_file())
        self.assertTrue(any(r['completed'] == 1 and r['failed'] == 0 for r in snapshots))
        self.assertEqual(json.loads((self.output / 'batch.json').read_text()), report)
        with (self.output / 'batch.csv').open(newline='', encoding='utf-8') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([row['status'] for row in rows], ['completed', 'failed'])
        for name in ('batch.json', 'batch.csv'):
            self.assertNotIn(str(self.root), (self.output / name).read_text())

    def test_same_stems_get_distinct_output_directories(self):
        (self.inputs / 'a.cif').write_bytes((ROOT / 'examples/data/1ubq.cif').read_bytes())
        report = self.run_batch()
        self.assertTrue(report['passed'])
        self.assertEqual([item['output'] for item in report['items']], ['0001-a', '0002-a'])
        self.assertEqual(report['completed'], 2)

    def test_cancel_after_first_completed_retains_pending_for_reopened_ui(self):
        (self.inputs / 'b.pdb').write_text(PDB)
        stop = []
        def progress(report):
            if report['completed'] == 1:
                stop.append(True)
        report = self.run_batch(cancelled=lambda: bool(stop), progress=progress)
        self.assertTrue(report['cancelled'])
        self.assertFalse(report['passed'])
        self.assertEqual([item['status'] for item in report['items']], ['completed', 'pending'])
        self.assertFalse((self.output / '0002-b').exists())
        reopened = json.loads((self.output / 'batch.json').read_text())
        self.assertEqual(reopened, report)
        self.assertEqual(reopened['completed'], 1)

    def test_failed_overwrite_preserves_previous_item_artifacts(self):
        first = self.run_batch()
        self.assertTrue(first['passed'])
        scene = self.output / '0001-a' / 'scene.png'
        original = scene.read_bytes()
        (self.inputs / 'a.pdb').write_text('not a structure\n')
        failed = self.run_batch(overwrite=True)
        self.assertFalse(failed['passed'])
        self.assertEqual(failed['failed'], 1)
        self.assertEqual(scene.read_bytes(), original)
        self.assertNotIn(str(self.root), failed['items'][0]['error'])


if __name__ == '__main__':
    unittest.main()
