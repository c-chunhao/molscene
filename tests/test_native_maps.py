from array import array
import hashlib
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

from molscene.grids import Grid,grid_metadata,read_dx,write_dx
from molscene.native_maps import native_dx_text,load_native_dx,replay_dx_loader_source


class NativeMapTests(unittest.TestCase):
    def grid(self):
        return Grid((3,3,3),(-10.,)*3,((10.,0.,0.),(0.,10.,0.),(0.,0.,10.)),
                    array('d',[4.9326540340094691e-05,-2.6192531018203062e-05,3.1253969105056972e-05]*9),'V',{})

    def test_canonical_text_round_trips_native_precision_without_changing_source(self):
        grid=self.grid()
        before=grid.values.tobytes()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'native.dx'
            path.write_text(native_dx_text(grid),encoding='ascii')
            actual=read_dx(path)
            for source,value in zip(grid.values,actual.values):
                self.assertEqual(struct.pack('<f',source),struct.pack('<f',value))
            self.assertEqual(actual.origin,grid.origin)
        self.assertEqual(grid.values.tobytes(),before)

    def test_overflow_rejected_before_backend(self):
        grid=self.grid()
        grid.values[0]=1e100
        with self.assertRaises(ValueError):
            native_dx_text(grid)

    @unittest.skipUnless(importlib.util.find_spec('pymol2'),'PyMOL unavailable')
    def test_public_load_and_standalone_replay_verify_full_field_geometry_and_source_hash(self):
        import pymol2
        grid=self.grid()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'original.dx'
            write_dx(path,grid)
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            with pymol2.PyMOL() as pm:
                actual=load_native_dx(pm.cmd,grid,name='native')
                self.assertEqual(actual['native_extent'],[[-10.,-10.,-10.],[10.,10.,10.]])
                namespace={}
                exec(replay_dx_loader_source(),namespace)
                replay=namespace['load_native_dx_file'](pm.cmd,path,grid_metadata(grid),digest,name='replay')
                self.assertEqual(replay,actual)
                metadata=grid_metadata(grid)
                metadata['origin_angstrom'][0]=0.
                with self.assertRaises(ValueError):
                    namespace['load_native_dx_file'](pm.cmd,path,metadata,digest,name='wrong_geometry')
                self.assertNotIn('wrong_geometry',pm.cmd.get_names('objects'))
                path.write_text(path.read_text()+'# changed\n')
                with self.assertRaises(ValueError):
                    namespace['load_native_dx_file'](pm.cmd,path,grid_metadata(grid),digest,name='rejected')
                self.assertNotIn('rejected',pm.cmd.get_names('objects'))


if __name__ == '__main__':
    unittest.main()
