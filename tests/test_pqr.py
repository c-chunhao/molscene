import copy
import tempfile
import unittest
from pathlib import Path

from molscene.pqr import read_pqr, validate_preparation


class PQRTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'test.pqr'

    def parse(self, text):
        self.path.write_text(text, encoding='ascii')
        return read_pqr(self.path)

    def test_optional_chain_zero_net_and_charged_zero_radius_h(self):
        result = self.parse('ATOM 1 CA ALA A 1 0 1 2 -0.1 1.8\nATOM 2 H ALA A 1 0 1 3 0.1 0\n')
        self.assertEqual(result['atom_count'], 2)
        self.assertEqual(result['net_charge'], 0)
        self.assertEqual(result['zero_radius_sites'], 1)
        self.assertEqual(result['atoms'][0]['element'], 'C')
        self.assertEqual(result['atoms'][1]['charge'], .1)
        self.assertEqual(self.parse('ATOM 1 C ALA 1 0 1 2 0 1.8\n')['atoms'][0]['chain'], '')

    def test_invalid_numeric_duplicate_empty_and_heavy_radius(self):
        for row in ['ATOM 1 C ALA 1 nan 0 0 0 1.8', 'ATOM 1 C ALA 1 0 0 0 inf 1.8',
                    'ATOM 1 C ALA 1 0 0 0 0 -1', 'ATOM 1 C ALA 1 0 0 0 0 0', '',
                    'ATOM 1 C ALA 1 0 0 0 0 1\nATOM 2 C ALA 1 1 0 0 0 1']:
            with self.subTest(row=row), self.assertRaises(ValueError):
                self.parse(row+'\n')

    def test_ambiguous_hetero_element_is_not_guessed(self):
        parsed = self.parse('HETATM 1 CA UNK 1 0 0 0 2 1.8\n')
        self.assertIsNone(parsed['atoms'][0]['element'])
        with self.assertRaises(ValueError):
            validate_preparation(parsed['atoms'], parsed, '', {})

    def test_preparation_preserves_heavy_identity_coordinates_and_reports_changes(self):
        original = self.parse('ATOM 1 CA ALA A 1 0 0 0 0 1.7\nHETATM 2 O HOH A 2 4 0 0 0 1.5\n')['atoms']
        prepared = self.parse('ATOM 1 CA ALA A 1 0 0 0 -.1 1.9\nATOM 2 H ALA A 1 0 1 0 .1 0\n')
        snapshot = copy.deepcopy(original)
        result = validate_preparation(original, prepared, 'warning: header ignored', {'drop_water': True, 'protonation': 'preserve'})
        self.assertEqual(result['retained_heavy_atoms'], 1)
        self.assertEqual(len(result['excluded_atoms']), 1)
        self.assertEqual(len(result['added_atoms']), 1)
        self.assertEqual(result['protonation_mode'], 'assignment-only')
        self.assertEqual(original, snapshot)
        with self.assertRaises(ValueError):
            validate_preparation(original, prepared, '', {'drop_water': False})
        for diagnostics in ['WARNING: Unable to assign parameters to atom CA', 'unassigned atoms: CA']:
            with self.assertRaises(ValueError):
                validate_preparation(original, prepared, diagnostics, {'drop_water': True})
        moved = copy.deepcopy(prepared)
        moved['atoms'][0]['x'] = 1
        with self.assertRaises(ValueError):
            validate_preparation(original, moved, '', {'drop_water': True})

    def test_dropped_ligand_or_protein_heavy_atom_fails(self):
        original = self.parse('ATOM 1 CA ALA A 1 0 0 0 0 1.7\n')['atoms']
        prepared = self.parse('ATOM 1 C ALA A 1 0 0 0 0 1.7\n')
        with self.assertRaises(ValueError):
            validate_preparation(original, prepared, '', {})

    def test_assignment_only_cannot_drop_original_hydrogens(self):
        original=self.parse('ATOM 1 C ALA 1 0 0 0 -.1 1.7\nATOM 2 H ALA 1 0 1 0 .1 0\n')['atoms']
        prepared=self.parse('ATOM 1 C ALA 1 0 0 0 0 1.7\n')
        with self.assertRaises(ValueError):
            validate_preparation(original,prepared,'',{'protonation':'preserve'})
        report=validate_preparation(original,prepared,'',{'protonation':'propka'})
        self.assertEqual(report['excluded_atoms'],[['','1','ALA','H']])


if __name__ == '__main__':
    unittest.main()
