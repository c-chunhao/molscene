"""Independent physical oracles for bounded grid parsing."""
from array import array
import hashlib
import itertools
import json
import math
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

try:
    from molscene import grids
except ImportError:
    grids = None


def dx_text():
    values = ' '.join(str(100*i+10*j+k) for i in range(4)
                      for j in range(3) for k in range(5))
    return ('object 1 class gridpositions counts 4 3 5\norigin 11 -7 3\n'
            'delta 2 0 0\ndelta 0 3 0\ndelta 0 0 4\n'
            'object 2 class gridconnections counts 4 3 5\n'
            'object 3 class array type double rank 0 items 60 data follows\n'
            + values + '\nattribute "dep" string "positions"\n'
            'object "regular positions regular connections" class field\n'
            'component "positions" value 1\ncomponent "connections" value 2\n'
            'component "data" value 3\n')


def mrc_bytes(axes=(1, 2, 3), endian='<', origin=(0, 0, 0), extended=80):
    """Write the specification directly; never call the production writer."""
    shape, starts = (4, 3, 5), (1, 2, 3)
    dims = tuple(shape[a-1] for a in axes)
    header = bytearray(1024)
    struct.pack_into(endian+'10i', header, 0, *dims, 2,
                     *(starts[a-1] for a in axes), 8, 9, 10)
    struct.pack_into(endian+'6f', header, 40, 16, 27, 40, 90, 90, 70)
    struct.pack_into(endian+'3i', header, 64, *axes)
    struct.pack_into(endian+'2i', header, 88, 1, extended)
    struct.pack_into(endian+'3f', header, 196, *origin)
    header[208:212] = b'MAP '
    header[212:216] = b'DA\0\0' if endian == '<' else b'\x11\x11\0\0'
    data = bytearray()
    for section in range(dims[2]):
        for row in range(dims[1]):
            for column in range(dims[0]):
                xyz = [0, 0, 0]
                for a, v in zip(axes, (column, row, section)):
                    xyz[a-1] = v
                data.extend(struct.pack(endian+'f', 100*xyz[0]+10*xyz[1]+xyz[2]))
    return bytes(header) + b'\0'*extended + data


class GridTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(grids, 'grid implementation is required')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'map'

    def test_dx_asymmetric_world_and_order(self):
        self.path.write_text(dx_text(), encoding='ascii')
        grid = grids.read_dx(self.path, value_unit='unknown')
        self.assertEqual(grid.shape, (4, 3, 5))
        for i, j, k in itertools.product(range(4), range(3), range(5)):
            self.assertEqual(grid.values[(i*3+j)*5+k], 100*i+10*j+k)
            self.assertEqual(grids.grid_point(grid, i, j, k), (11+2*i, -7+3*j, 3+4*k))
        metadata = grids.grid_metadata(grid)
        self.assertEqual(metadata['statistics']['mean'], 162)
        self.assertAlmostEqual(metadata['statistics']['population_sd'], math.sqrt(12500+200/3+2))
        expected = b''.join(struct.pack('<d', 100*i+10*j+k)
                            for i, j, k in itertools.product(range(4), range(3), range(5)))
        self.assertEqual(metadata['value_sha256'], hashlib.sha256(expected).hexdigest())
        json.dumps(metadata, allow_nan=False)
        output = self.path.with_suffix('.dx')
        grids.write_dx(output, grid)
        again = grids.read_dx(output)
        self.assertEqual(again.values, grid.values)
        self.assertEqual(again.basis, grid.basis)

    def test_mrc_all_permutations_partial_oblique(self):
        for axes, endian in itertools.product(itertools.permutations((1, 2, 3)), ('<', '>')):
            with self.subTest(axes=axes, endian=endian):
                self.path.write_bytes(mrc_bytes(axes, endian))
                grid = grids.read_mrc(self.path, format='ccp4')
                self.assertEqual(grid.shape, (4, 3, 5))
                for i, j, k in itertools.product(range(4), range(3), range(5)):
                    self.assertEqual(grid.values[(i*3+j)*5+k], 100*i+10*j+k)
                    want = (2*(i+1)+3*math.cos(math.radians(70))*(j+2),
                            3*math.sin(math.radians(70))*(j+2), 4*(k+3))
                    for actual, expected in zip(grids.grid_point(grid, i, j, k), want):
                        self.assertAlmostEqual(actual, expected, places=6)
                json.dumps(grids.grid_metadata(grid), allow_nan=False)

    def test_mrc_origin_requires_explicit_resolution_when_starts_present(self):
        self.path.write_bytes(mrc_bytes(origin=(20, 30, 40)))
        with self.assertRaises(ValueError):
            grids.read_mrc(self.path, format='mrc')
        grid = grids.read_mrc(self.path, format='mrc', interpretation='origin')
        self.assertEqual(grid.origin, (20, 30, 40))
        starts = grids.read_mrc(self.path, format='mrc', interpretation='starts')
        self.assertAlmostEqual(starts.origin[2], 12)
        with self.assertRaises(ValueError):
            grids.read_mrc(self.path, format='ccp4', interpretation='origin')

    def test_mrc_origin_only_auto_and_source_provenance(self):
        raw = bytearray(mrc_bytes(origin=(20, 30, 40)))
        struct.pack_into('<3i', raw, 16, 0, 0, 0)
        self.path.write_bytes(raw)
        grid = grids.read_mrc(self.path, format='mrc')
        self.assertEqual(grid.origin, (20, 30, 40))
        self.assertEqual(grid.metadata['source_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(grid.metadata['interpretation'], 'origin')
        with self.assertRaises(ValueError):
            grids.read_mrc(self.path, format='ccp4')
        ignored = grids.read_mrc(self.path, format='mrc', interpretation='starts')
        self.assertEqual(ignored.origin, (0, 0, 0))
        self.assertEqual(ignored.metadata['source_format'], 'ccp4')
        self.assertEqual(ignored.metadata['requested_format'], 'mrc')

    def test_byte_bound_precedes_parser_and_statistics_remain_finite(self):
        self.path.write_bytes(mrc_bytes())
        with patch.object(grids, 'MAX_BYTES', 1024), self.assertRaises(ValueError):
            grids.read_mrc(self.path, format='ccp4')
        self.path.write_text(dx_text(), encoding='ascii')
        with patch.object(grids, 'MAX_BYTES', 32), self.assertRaises(ValueError):
            grids.read_dx(self.path)
        grid = grids.Grid((2,2,2), (0,0,0), ((1,0,0),(0,1,0),(0,0,1)),
                          array('d', [-1e308,1e308]*4), 'unknown', {})
        summary = grids.grid_metadata(grid)
        self.assertEqual(summary['statistics']['mean'], 0)
        self.assertEqual(summary['statistics']['population_sd'], 1e308)
        json.dumps(summary, allow_nan=False)

    def test_dx_rejects_unsupported_and_malformed_before_use(self):
        text = dx_text()
        bad = [text.replace('rank 0', 'rank 1 shape 3'),
               text.replace('data follows', 'data file external'),
               text.replace('data follows', 'binary data follows'),
               text.replace('gridconnections counts 4 3 5', 'gridconnections counts 4 3 6'),
               text.replace('items 60', 'items 59'), text.replace('0 1 2 3 4', '0 1 2 3'),
               text.replace('0 1 2 3 4', '0 1 2 3 4 5'), text.replace('0 1 2 3 4', 'nan 1 2 3 4'),
               text.replace('origin 11', 'origin inf'),
               text.replace('delta 2 0 0', 'delta -2 0 0'),
               text.replace('delta 2 0 0', 'delta 0 0 0'),
               text.replace('delta 2 0 0', 'delta 2 1 0'),
               text + 'object 9 class array type double rank 0 items 1 data follows\n0\n',
               text.replace('counts 4 3 5', 'counts 4096 4096 4096')]
        for content in bad:
            with self.subTest(content=content[:120]):
                self.path.write_text(content, encoding='ascii')
                with self.assertRaises(ValueError):
                    grids.read_dx(self.path)

    def test_mrc_rejects_headers_and_payload(self):
        base = mrc_bytes()
        variants = [base[:-1]]
        for offset, fmt, values in [(12, 'i', (1,)), (96, 'i', (1,)),
                (64, '3i', (1, 1, 3)), (92, 'i', (-1,)),
                (52, '3f', (90, 90, 0)), (28, 'i', (0,)),
                (0, '3i', (4096, 4096, 4096)), (1104, 'f', (float('nan'),))]:
            data = bytearray(base)
            struct.pack_into('<'+fmt, data, offset, *values)
            variants.append(data)
        for content in variants:
            self.path.write_bytes(content)
            with self.assertRaises(ValueError):
                grids.read_mrc(self.path, format='ccp4')

    def test_rigid_and_general_affine_composition(self):
        rotation = (0, -1, 0, 11, 1, 0, 0, -7, 0, 0, 1, 3, 0, 0, 0, 1)
        self.assertEqual(grids.validate_rigid(rotation), rotation)
        intrinsic = (2, .5, 0, 20, 0, 3, 0, 30, 0, 0, 4, 40, 0, 0, 0, 1)
        self.assertEqual(grids.compose(rotation, intrinsic),
                         (0, -3, 0, -19, 2, .5, 0, 13, 0, 0, 4, 43, 0, 0, 0, 1))
        inverse = grids.inverse_affine(intrinsic)
        for got, want in zip(grids.compose(intrinsic, inverse), (1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1)):
            self.assertAlmostEqual(got, want)
        for index, value in [(0, 1), (1, 1), (2, float('nan')), (15, 0), (10, 0)]:
            bad = list(rotation)
            bad[index] = value
            with self.assertRaises(ValueError):
                grids.validate_rigid(bad)
        with self.assertRaises(ValueError):
            grids.inverse_affine((0,)*16)


if __name__ == '__main__':
    unittest.main()
