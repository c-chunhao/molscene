"""Strict public density requests, independent of PyMOL installation."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest

try:
    from molscene import density, density_export
except ImportError:
    density = density_export = None


class DensityTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(density, 'density core is required')
        self.assertIsNotNone(density_export, 'density export is required')

    def test_request_defaults_and_exact_types(self):
        request = density_export.validate_request({'map_path': 'source.dx', 'format': 'dx'})
        self.assertEqual(request['source_state'], 1)
        self.assertEqual(request['display'], dict(kind='mesh', level=1., color='cyan', opacity=1., visible=True, volume_ramp=[]))
        self.assertEqual(request['render'], dict(width=800, height=600, dpi=150, ray=True))
        for changes in ({'unexpected': 1}, {'source_state': True}, {'source_state': 0},
                        {'source_state': 2}, {'map_path': 1}, {'format': 'map'},
                        {'render': {'width': True}}, {'render': {'ray': 1}},
                        {'display': {'visible': 1}}, {'display': {'opacity': -1}},
                        {'display': {'level': float('nan')}}, {'display': {'kind': 'volume'}},
                        {'registration': {'rmsd': 0}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                request=dict(map_path='source.dx',format='dx'); request.update(changes)
                density_export.validate_request(request)

    def test_volume_ramp_and_registration_are_separate_typed_contracts(self):
        valid = [0, 0, 0, 1, 0, 2, 1, 0, 0, .8]
        self.assertEqual(density.validate_display({'kind': 'volume', 'volume_ramp': valid})['volume_ramp'], valid)
        for ramp in ([], [0]*5, [0]*10, [0,0,0,2,0,2,1,0,0,1], [False,0,0,1,0,2,1,0,0,1]):
            with self.subTest(ramp=ramp), self.assertRaises(ValueError):
                density.validate_display({'kind': 'volume', 'volume_ramp': ramp})
        matrix = [0,-1,0,11,1,0,0,-7,0,0,1,3,0,0,0,1]
        self.assertEqual(density.validate_registration(dict(kind='matrix', matrix=matrix, copy_name='registered'))['matrix'], matrix)
        for extra in ({'matrix': [1]*16}, {'copy_name': 'all'}, {'copy_name': 'not'}, {'copy_name': 'byres'}, {'copy_name': 'x; delete all'}, {'junk': 1}):
            value = dict(kind='matrix', matrix=matrix, copy_name='registered'); value.update(extra)
            with self.assertRaises(ValueError):
                density.validate_registration(value)

    def test_json_numeric_contract_rejects_tuple_matrix_and_huge_integers(self):
        with self.assertRaises(ValueError):
            density.validate_registration(dict(kind='matrix',matrix=(1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1),copy_name='copy'))
        with self.assertRaises(ValueError): density.validate_display({'level':10**1000})
        source={'map_path':'source.dx','format':'dx','display':{'volume_ramp':[]}}
        normalized=density_export.validate_density_request(source)
        normalized['display']['volume_ramp'].append(1)
        self.assertEqual(source['display']['volume_ramp'],[])

    def test_import_does_not_load_optional_backend(self):
        result = subprocess.run([sys.executable, '-B', '-S', '-c',
            'import sys; import molscene.density, molscene.density_export; '
            'assert not any(x.startswith(("pymol", "PyQt", "PySide", "numpy")) for x in sys.modules)'],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
