"""Exercise the gallery inventory with recorded CLI outputs at the process boundary."""
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('reproduce_v02', ROOT / 'examples/reproduce_v02.py')
reproduce = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reproduce)


@unittest.skipUnless(importlib.util.find_spec('PIL'), 'requires the Pillow test extra')
class GalleryInventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='gallery-inventory-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.output = self.root / 'outputs'
        self.expected = set()
        self.mutate = lambda directory: None

    def copy_report(self, source, destination):
        report = json.loads((source / 'report.json').read_text(encoding='utf-8'))
        for value in report['artifacts'].values():
            for relative in value if isinstance(value, list) else [value]:
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / relative, target)
                self.expected.add(target.relative_to(self.output).as_posix())

    def cli_output(self, command, **kwargs):
        # PyMOL itself is exercised by engine tests; replay its committed CLI files here.
        if '--version' in command:
            return subprocess.CompletedProcess(command, 0, stdout='molscene 0.2.0\n')
        destination = Path(command[command.index('--output') + 1])
        source = ROOT / 'examples/outputs' / destination.name
        if destination.name == 'batch':
            destination.mkdir(parents=True, exist_ok=True)
            for name in ('batch.json', 'batch.csv'):
                shutil.copyfile(source / name, destination / name)
                self.expected.add('batch/' + name)
            summary = json.loads((source / 'batch.json').read_text(encoding='utf-8'))
            for item in summary['items']:
                self.copy_report(source / item['output'], destination / item['output'])
        else:
            self.copy_report(source, destination)
        self.mutate(destination)
        return subprocess.CompletedProcess(command, 0)

    def run_generator(self):
        with patch.object(reproduce.subprocess, 'run', side_effect=self.cli_output), \
                patch.object(sys, 'argv', ['reproduce_v02.py', '--output', str(self.output), '--overwrite']), \
                redirect_stdout(io.StringIO()):
            reproduce.main()
        return json.loads((self.output / 'validation.json').read_text(encoding='utf-8'))

    def test_unrelated_nested_file_is_unchanged_and_not_in_manifest(self):
        extra = self.output / 'overview/notes/report.json'
        extra.parent.mkdir(parents=True)
        original = b'{"private": "preserve"}\r\n'
        extra.write_bytes(original)
        evidence = self.run_generator()
        self.assertEqual(extra.read_bytes(), original)
        self.assertEqual({row['file'] for row in evidence['files']}, self.expected)
        for row in evidence['files']:
            data = (self.output / row['file']).read_bytes()
            self.assertEqual(row['sha256'], hashlib.sha256(data).hexdigest())
            self.assertEqual(row['bytes'], len(data))
            if Path(row['file']).name in reproduce.TEXT_ARTIFACTS:
                self.assertNotIn(b'\r', data)

    def check_unrelated_link(self, kind):
        outside = self.root / 'private.json'
        original = b'{"private": "external"}\r\n'
        outside.write_bytes(original)
        extra = self.output / 'overview/notes/report.json'
        extra.parent.mkdir(parents=True)
        try:
            if kind == 'symlink':
                extra.symlink_to(outside)
            else:
                os.link(outside, extra)
        except OSError as exc:
            self.skipTest('Filesystem cannot create ' + kind + ': ' + str(exc))
        evidence = self.run_generator()
        self.assertEqual(outside.read_bytes(), original)
        self.assertEqual({row['file'] for row in evidence['files']}, self.expected)

    def test_unrelated_symlink_is_not_read_or_written(self):
        self.check_unrelated_link('symlink')

    def test_unrelated_hardlink_is_not_read_or_written(self):
        self.check_unrelated_link('hardlink')

    def test_reported_unowned_or_escaping_paths_are_rejected(self):
        for malicious in ('notes/report.json', '../private.json', '/private.json',
                          'C:/private.json', 'C:private.json', 'inputs/../report.json',
                          'inputs\\input.pdb', 'scene.png:private', 'scene.png.'):
            with self.subTest(path=malicious):
                def mutate(directory):
                    if directory.name == 'overview':
                        nested = directory / 'notes/report.json'
                        nested.parent.mkdir(exist_ok=True)
                        nested.write_text('{}')
                        report_path = directory / 'report.json'
                        report = json.loads(report_path.read_text(encoding='utf-8'))
                        report['artifacts']['unexpected'] = malicious
                        report_path.write_text(json.dumps(report), encoding='utf-8')
                self.mutate = mutate
                with self.assertRaises((ValueError, RuntimeError)):
                    self.run_generator()

    def test_batch_cannot_redirect_item_report_into_unowned_descendant(self):
        def mutate(directory):
            if directory.name == 'batch':
                report_path = directory / 'batch.json'
                summary = json.loads(report_path.read_text(encoding='utf-8'))
                original = directory / summary['items'][0]['report']
                nested = original.parent / 'notes/report.json'
                nested.parent.mkdir()
                shutil.copyfile(original, nested)
                shutil.copyfile(original.parent / 'scene.png', nested.parent / 'scene.png')
                summary['items'][0]['report'] = nested.relative_to(directory).as_posix()
                report_path.write_text(json.dumps(summary), encoding='utf-8')
        self.mutate = mutate
        with self.assertRaises((ValueError, RuntimeError)):
            self.run_generator()

    def check_reported_link(self, kind):
        outside = self.root / 'external.pml'
        original = b'# external\r\n'
        outside.write_bytes(original)
        probe = self.root / 'link-probe'
        try:
            if kind == 'symlink':
                probe.symlink_to(outside)
            else:
                os.link(outside, probe)
        except OSError as exc:
            self.skipTest('Filesystem cannot create ' + kind + ': ' + str(exc))
        def mutate(directory):
            if directory.name == 'overview':
                target = directory / 'scene.pml'
                target.unlink()
                if kind == 'symlink':
                    target.symlink_to(outside)
                else:
                    os.link(outside, target)
        self.mutate = mutate
        with self.assertRaises((ValueError, RuntimeError)):
            self.run_generator()
        self.assertEqual(outside.read_bytes(), original)

    def test_reported_symlink_is_rejected_before_reading(self):
        self.check_reported_link('symlink')

    def test_reported_hardlink_is_rejected_before_reading(self):
        self.check_reported_link('hardlink')
