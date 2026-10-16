"""Closed v0.3 gallery ownership and numeric validation at the backend boundary."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('reproduce_v03', ROOT / 'examples/reproduce_v03.py')
reproduce = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reproduce)


def numeric_report():
    return {'schema_version': 2, 'operation': 'interface', 'parameters': {'state': 1},
            'analysis': {'kind': 'interface', 'state': 1,
                'partner_a': {'selected_atom_count': 1, 'isolated_area_angstrom2': 100.0,
                              'complex_area_angstrom2': 80.0, 'buried_area_angstrom2': 20.0},
                'partner_b': {'selected_atom_count': 1, 'isolated_area_angstrom2': 110.0,
                              'complex_area_angstrom2': 85.0, 'buried_area_angstrom2': 25.0},
                'total_buried_area_angstrom2': 45.0, 'interface_area_angstrom2': 22.5,
                'residues': [dict(partner=p, object='molecule', segi='', chain=p,
                    resi='1', resn='ALA', state=1, atom_count=1,
                    isolated_area_angstrom2=i, complex_area_angstrom2=c,
                    buried_area_angstrom2=i-c) for p, i, c in [('A', 100., 80.), ('B', 110., 85.)]]}}


class GalleryV03Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='gallery-v03-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.output = self.root / 'gallery'

    def fake_interface(self, data, output, width, height, overwrite):
        self.write_owned('interface', width, height)
        report = numeric_report()
        report['artifacts'] = {'report': 'report.json', 'png': 'scene.png',
            'session': 'scene.pse', 'replay': 'scene.pml', 'interface_csv': 'interface.csv',
            'inputs': ['inputs/input.pdb']}
        source = (data / '1hsg.pdb').read_bytes()
        report['inputs'] = [{'basename': '1hsg.pdb', 'sha256': hashlib.sha256(source).hexdigest()}]
        (output / 'report.json').write_text(json.dumps(report) + '\r\n', encoding='utf-8')

    def fake_live(self, data, output, width, height, overwrite):
        self.write_owned('live', width, height)
        report = {'schema_version': 1, 'state': 1, 'source_atoms': 1686,
            'clean': {'selected_atom_count': 1686, 'retained_atom_count': 1514,
                      'removed': {'solvent': 172, 'hydrogens': 0, 'altloc': 0}},
            'replay': {'passed': True}, 'presets': ['overview', 'surface', 'pocket']}
        (output / 'workflows.json').write_text(json.dumps(report) + '\r\n', encoding='utf-8')

    def write_owned(self, workflow, width, height):
        for relative in reproduce.OWNED_FILES:
            if not relative.startswith(workflow + '/'):
                continue
            path = self.output / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix == '.png':
                from PIL import Image
                image = Image.new('RGB', (width, height))
                image.putdata([(i % 255, (i // 255) % 255, 30) for i in range(width * height)])
                image.save(path)
            elif path.suffix == '.pdb':
                shutil.copyfile(ROOT / 'examples/data/1hsg.pdb', path)
            else:
                path.write_bytes(b'owned\r\n')

    def generate(self, overwrite=True):
        with patch.object(reproduce, 'run_interface', side_effect=self.fake_interface), \
                patch.object(reproduce, 'run_live', side_effect=self.fake_live):
            return reproduce.generate(self.output, width=40, height=30, overwrite=overwrite)

    @unittest.skipUnless(importlib.util.find_spec('PIL'), 'requires Pillow test extra')
    def test_complete_manifest_lf_hashes_and_unrelated_same_name_sentinels(self):
        extra = self.output / 'live/notes/workflows.json'
        extra.parent.mkdir(parents=True)
        original = b'private\r\n'
        extra.write_bytes(original)
        manifest = self.generate()
        self.assertEqual(extra.read_bytes(), original)
        self.assertEqual({row['file'] for row in manifest['files']}, set(reproduce.OWNED_FILES))
        for row in manifest['files']:
            data = (self.output / row['file']).read_bytes()
            self.assertEqual(row['sha256'], hashlib.sha256(data).hexdigest())
            self.assertEqual(row['bytes'], len(data))
            if row['file'] in reproduce.TEXT_FILES:
                self.assertNotIn(b'\r', data)

    def test_existing_owned_file_requires_overwrite_before_backend_runs(self):
        owned = self.output / 'live/pocket.png'
        owned.parent.mkdir(parents=True)
        owned.write_bytes(b'prior valid output')
        with patch.object(reproduce, 'run_interface') as backend:
            with self.assertRaises((ValueError, RuntimeError)):
                reproduce.generate(self.output, overwrite=False)
        backend.assert_not_called()
        self.assertEqual(owned.read_bytes(), b'prior valid output')

    def test_directory_in_owned_file_slot_is_rejected_before_backend_runs(self):
        (self.output / 'live/scene.pse').mkdir(parents=True)
        with patch.object(reproduce, 'run_interface') as backend:
            with self.assertRaises((ValueError, RuntimeError)):
                reproduce.generate(self.output, overwrite=True)
        backend.assert_not_called()

    def check_owned_link(self, kind, directory=False):
        outside = self.root / ('outside-dir' if directory else 'outside.dat')
        if directory:
            outside.mkdir()
            (outside / 'sentinel').write_bytes(b'private\r\n')
        else:
            outside.write_bytes(b'private\r\n')
        target = self.output / ('live' if directory else 'live/scene.pse')
        target.parent.mkdir(parents=True)
        try:
            if kind == 'symlink':
                target.symlink_to(outside, target_is_directory=directory)
            else:
                os.link(outside, target)
        except OSError as exc:
            self.skipTest(f'Filesystem cannot create {kind}: {exc}')
        # A failing resolve proves rejection happens during lexical lstat checks.
        with patch.object(reproduce, 'run_interface') as backend, \
                patch.object(Path, 'resolve', side_effect=AssertionError('resolved before rejecting link')):
            with self.assertRaises((ValueError, RuntimeError)):
                reproduce.generate(self.output, overwrite=True)
        backend.assert_not_called()
        self.assertEqual((outside / 'sentinel' if directory else outside).read_bytes(), b'private\r\n')

    def test_owned_symlink_rejected_before_resolve(self):
        self.check_owned_link('symlink')

    def test_owned_directory_link_rejected_before_resolve(self):
        self.check_owned_link('symlink', directory=True)

    def test_owned_hardlink_rejected_before_resolve(self):
        self.check_owned_link('hardlink')

    def test_report_cannot_extend_inventory_or_redirect_inputs(self):
        report = numeric_report()
        report['artifacts'] = {'report': 'report.json', 'inputs': ['../private.pdb']}
        self.output.mkdir()
        (self.output / 'report.json').write_text(json.dumps(report), encoding='utf-8')
        with self.assertRaises((ValueError, RuntimeError)):
            reproduce.read_interface(self.output)

    def test_deterministic_numeric_contract_rejects_inconsistent_or_nonfinite_data(self):
        report = numeric_report()
        reproduce.validate_interface(report)
        mutations = [lambda a: a.update(interface_area_angstrom2=45.0),
            lambda a: a.update(total_buried_area_angstrom2=44.0),
            lambda a: a['partner_a'].update(buried_area_angstrom2=21.0),
            lambda a: a['residues'].pop(),
            lambda a: a['residues'][0].update(buried_area_angstrom2=float('nan')),
            lambda a: a.update(state=2)]
        for mutate in mutations:
            bad = copy.deepcopy(report)
            mutate(bad['analysis'])
            with self.subTest(analysis=bad['analysis']):
                with self.assertRaises((ValueError, RuntimeError)):
                    reproduce.validate_interface(bad)

    def test_signed_surface_difference_is_preserved(self):
        report = numeric_report()
        a = report['analysis']
        a['partner_a'].update(complex_area_angstrom2=101.0, buried_area_angstrom2=-1.0)
        a['residues'][0].update(complex_area_angstrom2=101.0, buried_area_angstrom2=-1.0)
        a.update(total_buried_area_angstrom2=24.0, interface_area_angstrom2=12.0)
        reproduce.validate_interface(report)


if __name__ == '__main__':
    unittest.main()
