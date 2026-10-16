"""Example contract: explicit solvers, closed bundles and public provenance."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'examples' / 'reproduce_v04.py'


class ReproduceV04Tests(unittest.TestCase):
    def run_example(self, *args):
        return subprocess.run([sys.executable, '-B', '-S', str(SCRIPT), *map(str, args)],
                              cwd=ROOT, text=True, capture_output=True, check=False)

    def module(self):
        self.assertTrue(SCRIPT.is_file(), 'The v0.4 example entry point is missing')
        spec = importlib.util.spec_from_file_location('reproduce_v04', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_help_does_not_require_backend_or_tools(self):
        # A backend import during parsing would break installation-only use.
        result = self.run_example('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        for option in ('--tools', '--output', '--skip-potential'):
            self.assertIn(option, result.stdout)

    def test_default_requires_explicit_tools_without_creating_output(self):
        # A silent potential skip must never be a successful science example.
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'gallery'
            result = self.run_example('--output', output)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('--tools', result.stderr)
            self.assertFalse(output.exists())

    def test_occupied_output_is_preserved_before_backend_launch(self):
        # Reproduction must not turn the advanced new-directory rule into overwrite.
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'gallery'
            output.mkdir()
            sentinel = output / 'keep.txt'
            sentinel.write_bytes(b'original\r\n')
            result = self.run_example('--skip-potential', '--output', output)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('new example output directory', result.stderr)
            self.assertEqual(sentinel.read_bytes(), b'original\r\n')
            self.assertEqual(list(output.iterdir()), [sentinel])

    def test_direct_script_checks_required_tools_outside_checkout_without_backend(self):
        # A direct extracted-source invocation must not import another installation
        # or reach fixture/native setup before noticing absent solver entries.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = root / 'tools.json'
            tools.write_text(json.dumps({'schema_version': 1, 'tools': {}}), encoding='utf-8')
            output = root / 'public'
            env = dict(os.environ)
            env.pop('PYTHONPATH', None)
            result = subprocess.run([sys.executable, '-B', '-S', str(SCRIPT), '--tools', str(tools),
                '--output', str(output)], cwd=root, env=env, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 1)
            self.assertIn('apbs and pdb2pqr', result.stderr)
            self.assertNotIn('Traceback', result.stderr)
            self.assertEqual(list(root.iterdir()), [tools])

    def test_public_coordinate_falls_back_to_installed_data_and_checks_pinned_bytes(self):
        # A copied example outside the checkout must use wheel-installed public data.
        example = self.module()
        self.assertTrue(hasattr(example, 'public_coordinate_path'), 'Installed example data lookup is missing')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed = root / 'prefix/share/molscene/examples/data/1ubq.pdb'
            installed.parent.mkdir(parents=True)
            payload = (ROOT / 'examples/data/1ubq.pdb').read_bytes()
            installed.write_bytes(payload)
            with patch.object(example, 'ROOT', root / 'copied-example'), patch.object(sys, 'prefix', str(root / 'prefix')):
                self.assertEqual(example.public_coordinate_path(), installed)
                installed.write_bytes(payload + b'changed')
                with self.assertRaisesRegex(ValueError, 'Pinned public ubiquitin'):
                    example.public_coordinate_path()

    def test_bundle_audit_rejects_missing_unexpected_and_changed_payloads(self):
        # Checking only listed hashes would allow an unlisted private file to ship.
        example = self.module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = b'public fixture\n'
            (root / 'payload.txt').write_bytes(payload)
            manifest = {'schema_version': 1, 'files': {'payload.txt': {
                'sha256': hashlib.sha256(payload).hexdigest(), 'bytes': len(payload)}}}
            (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            self.assertEqual(set(example.audit_bundle(root)), {'payload.txt', 'manifest.json'})
            (root / 'private.txt').write_text('unlisted', encoding='utf-8')
            with self.assertRaises(ValueError):
                example.audit_bundle(root)
            (root / 'private.txt').unlink()
            (root / 'payload.txt').write_bytes(b'changed')
            with self.assertRaises(ValueError):
                example.audit_bundle(root)
            (root / 'payload.txt').unlink()
            with self.assertRaises(ValueError):
                example.audit_bundle(root)

    def test_public_projection_preserves_original_and_rebuilds_hashes(self):
        # Redacting a report without rebuilding hashes creates an unverifiable bundle.
        example = self.module()
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / 'original'
            public = Path(directory) / 'public'
            original.mkdir()
            private_prefix = str(Path(directory).resolve())
            (original / 'tools.json').write_text(json.dumps({'executable': private_prefix + '/solver',
                'sha256': 'a' * 64, 'version': '3.4.1'}), encoding='utf-8')
            (original / 'report.json').write_text(json.dumps({'schema_version': 2, 'passed': True,
                'analysis': {'energy': -1.25}, 'artifacts': {'tools': 'tools.json'},
                'artifact_hashes': {'tools.json': hashlib.sha256((original / 'tools.json').read_bytes()).hexdigest()},
                'parameters': {'source': private_prefix + '/source.pqr'}}), encoding='utf-8')
            entries = {p.name: {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                'bytes': p.stat().st_size} for p in original.iterdir()}
            (original / 'manifest.json').write_text(json.dumps({'schema_version': 1, 'files': entries}), encoding='utf-8')
            before = {p.name: p.read_bytes() for p in original.iterdir()}
            example.project_public_bundle(original, public, private_paths=[private_prefix])
            self.assertEqual(before, {p.name: p.read_bytes() for p in original.iterdir()})
            self.assertEqual(set(example.audit_bundle(public)), set(before))
            report = json.loads((public / 'report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['analysis']['energy'], -1.25)
            projection = report['public_projection']
            self.assertEqual(projection['original_manifest_sha256'], hashlib.sha256(before['manifest.json']).hexdigest())
            self.assertTrue(projection['derivative'])
            self.assertTrue(projection['redactions'])
            self.assertEqual(report['artifact_hashes']['tools.json'], hashlib.sha256((public / 'tools.json').read_bytes()).hexdigest())
            for path in public.iterdir():
                self.assertNotIn(private_prefix.encode(), path.read_bytes())

    def test_bundle_audit_rejects_unlisted_empty_directory(self):
        # A closed example may not silently carry an undeclared directory tree.
        example = self.module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'manifest.json').write_text(json.dumps({'schema_version': 1, 'files': {}}), encoding='utf-8')
            (root / 'private-notes').mkdir()
            with self.assertRaises(ValueError):
                example.audit_bundle(root)

    def test_projection_preserves_scientific_input_bytes_and_validates_as_bundle(self):
        # Newline changes would invalidate source hashes and standalone DX replay.
        from molscene.advanced import validate_bundle
        from molscene.desktop.controller import WorkbenchController
        example = self.module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original, public = root / 'original', root / 'public'
            (original / 'inputs').mkdir(parents=True)
            payloads = {'inputs/density.dx': b'object 1 class gridpositions counts 2 2 2\r\n',
                'prepared.pqr': b'ATOM 1 C ALA 1 0 0 0 1 1.5\r\n',
                'inputs/selection.pdb': b'ATOM      1  CA  ALA A   1       0.000   0.000   0.000\r\n'}
            for relative, data in payloads.items():
                (original / relative).write_bytes(data)
            digest = hashlib.sha256(payloads['inputs/density.dx']).hexdigest()
            report = {'schema_version': 2, 'passed': True, 'operation': 'density',
                'inputs': {'density': {'path': 'inputs/density.dx', 'sha256': digest,
                    'bytes': len(payloads['inputs/density.dx'])}},
                'analysis': {'level': 0.35}, 'source_fingerprints': {'1': 'a' * 64},
                'advanced_fingerprints': {'files': {'density': digest}},
                'artifact_hashes': {relative: hashlib.sha256(data).hexdigest() for relative, data in payloads.items()}}
            (original / 'report.json').write_text(json.dumps(report), encoding='utf-8')
            entries = {relative: {'sha256': hashlib.sha256((original / relative).read_bytes()).hexdigest(),
                'bytes': (original / relative).stat().st_size} for relative in [*payloads, 'report.json']}
            (original / 'manifest.json').write_text(json.dumps({'schema_version': 1, 'files': entries}), encoding='utf-8')
            example.project_public_bundle(original, public, private_paths=[str(root)])
            for relative, data in payloads.items():
                self.assertEqual((public / relative).read_bytes(), data)
            projected = validate_bundle(public / 'report.json')
            self.assertEqual(projected['inputs'], report['inputs'])
            self.assertEqual(projected['source_fingerprints'], report['source_fingerprints'])
            self.assertEqual(projected['advanced_fingerprints'], report['advanced_fingerprints'])
            controller = WorkbenchController(None, root)
            self.assertEqual(controller.result_status(projected), 'unverified')
            with self.assertRaisesRegex(ValueError, 'stale or unverified'):
                controller.load_advanced_result(public / 'report.json')

    def test_projection_redacts_discovered_runtime_paths_and_preserves_selections(self):
        # Runtime probe paths may differ from the configured executable prefix.
        example = self.module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original, public = root / 'original', root / 'public'
            original.mkdir()
            tool = {'executable': '/opt/runtime/bin/python', 'runtime': {
                'base_prefix': '/home/private-user/runtime', 'version': '3.11.14'},
                'sha256': 'b' * 64, 'argv': ['/opt/runtime/bin/python', '-B', '-m', 'pdb2pqr']}
            (original / 'tools.json').write_text(json.dumps(tool), encoding='utf-8')
            report = {'schema_version': 2, 'passed': True, 'operation': 'potential',
                'parameters': {'selection': '/ubiquitin///1/CA', 'temperature': 298.15},
                'analysis': {'energy': -1.25}, 'artifact_hashes': {
                    'tools.json': hashlib.sha256((original / 'tools.json').read_bytes()).hexdigest()}}
            (original / 'report.json').write_text(json.dumps(report), encoding='utf-8')
            entries = {p.name: {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                'bytes': p.stat().st_size} for p in original.iterdir()}
            (original / 'manifest.json').write_text(json.dumps({'schema_version': 1, 'files': entries}), encoding='utf-8')
            example.project_public_bundle(original, public, private_paths=[str(root)])
            projected = json.loads((public / 'tools.json').read_text(encoding='utf-8'))
            self.assertNotIn('private-user', (public / 'tools.json').read_text(encoding='utf-8'))
            self.assertNotIn('/opt/runtime', (public / 'tools.json').read_text(encoding='utf-8'))
            self.assertEqual(projected['runtime']['version'], '3.11.14')
            self.assertEqual(projected['sha256'], 'b' * 64)
            projected_report = json.loads((public / 'report.json').read_text(encoding='utf-8'))
            self.assertEqual(projected_report['parameters'], report['parameters'])
            self.assertEqual(projected_report['analysis'], report['analysis'])

    def test_projection_refuses_private_paths_in_immutable_scientific_inputs(self):
        # Scientific input bytes must neither leak a known path nor be rewritten.
        example = self.module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original, public = root / 'original', root / 'public'
            original.mkdir()
            payload = ('REMARK private source ' + str(root) + '/source.pdb\r\n').encode('utf-8')
            (original / 'source.pdb').write_bytes(payload)
            (original / 'report.json').write_text(json.dumps({'schema_version': 2, 'passed': True,
                'operation': 'density', 'artifact_hashes': {'source.pdb': hashlib.sha256(payload).hexdigest()}}), encoding='utf-8')
            entries = {p.name: {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                'bytes': p.stat().st_size} for p in original.iterdir()}
            (original / 'manifest.json').write_text(json.dumps({'schema_version': 1, 'files': entries}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Scientific input contains a local path'):
                example.project_public_bundle(original, public, private_paths=[str(root)])
            self.assertEqual((original / 'source.pdb').read_bytes(), payload)

    @unittest.skipUnless(os.environ.get('MOLSCENE_REQUIRE_ADVANCED') == '1',
                         'requires explicit advanced science acceptance environment')
    def test_real_cli_gallery_inventory_provenance_and_original_preservation(self):
        # This catches a missing real workflow, an implicit skip or broken projection.
        example = self.module()
        tools = os.environ.get('MOLSCENE_TOOLS_CONFIG')
        self.assertTrue(tools and Path(tools).is_absolute() and Path(tools).is_file())
        with tempfile.TemporaryDirectory(prefix='v04-') as directory:
            root = Path(directory)
            output = root / 'public'
            result = subprocess.run([sys.executable, '-B', '-m', 'examples.reproduce_v04', '--tools', tools,
                '--output', str(output)], cwd=ROOT, text=True, capture_output=True, check=False, timeout=1200)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            example.audit_bundle(output)
            validation = json.loads((output / 'validation.json').read_text(encoding='utf-8'))
            self.assertEqual(validation['potential'], {'executed': True, 'skip_requested_by_user': False})
            self.assertEqual(set(validation['bundles']), {'timeline-project', 'animation', 'density', 'potential'})
            self.assertEqual(validation['synthetic_map']['origin_angstrom'], [2, -3, 1])
            original = root / validation['private_original_directory']
            for name in ('timeline-project', 'animation', 'density', 'potential'):
                self.assertEqual(example.audit_bundle(original / name), example.audit_bundle(output / name))
                report = json.loads((output / name / 'report.json').read_text(encoding='utf-8'))
                self.assertEqual(report['public_projection']['original_manifest_sha256'],
                                 hashlib.sha256((original / name / 'manifest.json').read_bytes()).hexdigest())
                self.assertTrue(report['passed'])
                for path in (output / name).rglob('*'):
                    if path.is_file() and path.suffix in {'.json', '.log', '.pml'}:
                        text = path.read_text(encoding='utf-8')
                        self.assertNotIn(str(root), text)
                        self.assertNotIn(str(root).replace('\\', '/'), text)
            self.assertEqual({p.name for p in (output / 'animation/frames').iterdir()},
                {'frame_000001.png', 'frame_000002.png', 'frame_000003.png', 'frame_000004.png'})
            for relative in ('prepared.pqr', 'potential.dx'):
                private_lines = (original / 'potential' / relative).read_text(encoding='utf-8').splitlines()
                public_lines = (output / 'potential' / relative).read_text(encoding='utf-8').splitlines()
                if relative.endswith('.pqr'):
                    private_lines = [line for line in private_lines if line.startswith(('ATOM', 'HETATM'))]
                    public_lines = [line for line in public_lines if line.startswith(('ATOM', 'HETATM'))]
                self.assertEqual(private_lines, public_lines)


if __name__ == '__main__':
    unittest.main()
