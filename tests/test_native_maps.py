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

    def test_standalone_parser_matches_complete_scientific_dx_document(self):
        positions='object 1 class gridpositions counts 2 2 2\n'
        geometry='origin -10 -10 -10\ndelta 20 0 0\ndelta 0 20 0\ndelta 0 0 20\n'
        connections='object 2 class gridconnections counts 2 2 2\n'
        payload='object 3 class array type double rank 0 items 8 data follows\n0 1 2 3 4 5 6 7\n'
        field='attribute "dep" string "positions"\nobject "grid" class field\ncomponent "positions" value 1\ncomponent "connections" value 2\ncomponent "data" value 3\n'
        valid=[positions+geometry+payload+connections+field,
               field+positions+payload+geometry+connections,
               connections+positions+payload+geometry+field]
        invalid=[valid[0]+connections, valid[0]+'object 1 class field\n',
                 valid[0]+'component "data" value 3\n', valid[0]+'8\n',
                 valid[0].replace('"connections" value 2','"connections" value 3'),
                 valid[0].replace('items 8','items 16777217'),
                 valid[0].replace('0 1 2 3 4 5 6 7','0 1 2 3 4 5 6 nan'),
                 valid[0]+'object "'+('x'*4097)+'" class field\n']
        namespace={}
        exec(replay_dx_loader_source(),namespace)
        calls=[]
        namespace['_load_native_parts']=lambda cmd,shape,origin,basis,values,**kw: calls.append((shape,origin,basis,list(values)))
        metadata=grid_metadata(Grid((2,2,2),(-10.,)*3,((20.,0.,0.),(0.,20.,0.),(0.,0.,20.)),array('d',range(8)),'V',{}))
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'ordered.dx'
            for text in valid:
                with self.subTest(valid=text):
                    path.write_text(text,encoding='ascii')
                    grid=read_dx(path)
                    namespace['load_native_dx_file'](None,path,metadata,hashlib.sha256(path.read_bytes()).hexdigest(),name='ordered')
                    self.assertEqual(calls[-1],(list(grid.shape),list(grid.origin),[list(row) for row in grid.basis],list(grid.values)))
            for text in invalid:
                with self.subTest(invalid=text[:100]):
                    path.write_text(text,encoding='ascii')
                    with self.assertRaises(ValueError):
                        read_dx(path)
                    count=len(calls)
                    with self.assertRaises(ValueError):
                        namespace['load_native_dx_file'](None,path,metadata,hashlib.sha256(path.read_bytes()).hexdigest(),name='rejected')
                    self.assertEqual(len(calls),count)

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
