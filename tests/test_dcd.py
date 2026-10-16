"""Original DCD byte records and coordinate oracles, independent of production parser."""
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

try:
    from molscene import dcd
except ImportError:
    dcd=None

# Atom names intentionally oppose lexical order in native tests.
ORACLE=[[[10*f+i+.25, -3*f-i-.5, f*2+i*4+.75] for i in range(4)] for f in range(6)]

def original_dcd(frames=ORACLE, **flags):
    def record(payload): return struct.pack('<i',len(payload))+payload+struct.pack('<i',len(payload))
    header=bytearray(84); header[:4]=b'CORD'
    struct.pack_into('<i',header,4,len(frames)); struct.pack_into('<i',header,12,1)
    struct.pack_into('<i',header,36,flags.get('fixed',0)); struct.pack_into('<f',header,40,1.)
    struct.pack_into('<i',header,44,flags.get('cell',0)); struct.pack_into('<i',header,48,flags.get('four',0)); struct.pack_into('<i',header,80,24)
    result=record(header)+record(struct.pack('<i',1)+b'Original MolScene tiny trajectory oracle'.ljust(80,b' '))+record(struct.pack('<i',len(frames[0])))
    for frame in frames:
        for axis in range(3): result+=record(struct.pack('<'+'f'*len(frame),*[p[axis] for p in frame]))
    return result

class DCDTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(dcd,'DCD scanner is required')
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup); self.path=Path(self.temp.name)/'original.dcd'; self.path.write_bytes(original_dcd())

    def test_full_scan_matches_independent_coordinate_oracle(self):
        scan=dcd.scan_dcd(self.path)
        self.assertEqual((scan['frame_count'],scan['atom_count']), (6,4))
        self.assertEqual(list(dcd.iter_dcd_frames(self.path)), ORACLE)

    def test_refuses_record_lies_variants_nonfinite_and_full_budget(self):
        raw=original_dcd()
        bad=[raw[:-1],raw+b'x',original_dcd(cell=1),original_dcd(four=1),original_dcd(fixed=1)]
        lie=bytearray(raw); struct.pack_into('<i',lie,8,2); bad.append(lie)
        trailer=bytearray(raw); struct.pack_into('<i',trailer,len(raw)-4,2); bad.append(trailer)
        nan=bytearray(raw); struct.pack_into('<f',nan,204,float('nan')); bad.append(nan)
        for data in bad:
            self.path.write_bytes(data)
            with self.subTest(length=len(data)), self.assertRaises(ValueError): dcd.scan_dcd(self.path)
        self.path.write_bytes(raw)
        with patch.object(dcd,'MAX_SAMPLES',23), self.assertRaisesRegex(ValueError,'budget|limit'): dcd.scan_dcd(self.path)

    @unittest.skipUnless(importlib.util.find_spec('pymol2') is not None,'requires user PyMOL')
    def test_native_all_frames_replace_state1_and_preserve_topology_order(self):
        import pymol2
        from molscene.artifacts import sha256
        pm=pymol2.PyMOL(); pm.start(); self.addCleanup(pm.stop); cmd=pm.cmd
        for i,name in enumerate(('Z9','A1','Y8','B2')):
            cmd.pseudoatom('topology',name=name,resi=str(i+1),pos=[-900,-900,-900])
        order=[a.name for a in cmd.get_model('topology').atom]; before=sha256(self.path)
        metadata=dcd.load_dcd(cmd,'topology',self.path)
        self.assertEqual(cmd.count_states('topology'),6)
        self.assertEqual([a.name for a in cmd.get_model('topology').atom],order)
        for state in (1,3,6): self.assertEqual(cmd.get_coordset('topology',state).tolist(),ORACLE[state-1])
        self.assertEqual(sha256(self.path),before)
        self.assertEqual(metadata['native_mapping'],[dict(source_frame=i,state=i) for i in range(1,7)])

    @unittest.skipUnless(importlib.util.find_spec('pymol2') is not None,'requires user PyMOL')
    def test_archived_original_pdb_dcd_pair_order_and_all_coordinates(self):
        import json
        import pymol2
        root=Path(__file__).resolve().parents[1]/'examples/data'
        oracle=json.loads((root/'trajectory-tiny-v06.json').read_text())
        pm=pymol2.PyMOL(); pm.start(); self.addCleanup(pm.stop); cmd=pm.cmd
        cmd.load(str(root/'trajectory-tiny-v06.pdb'),'topology')
        self.assertEqual([a.name for a in cmd.get_model('topology').atom],oracle['atom_names'])
        dcd.load_dcd(cmd,'topology',root/'trajectory-tiny-v06.dcd')
        for state,expected in enumerate(oracle['coordinates_angstrom'],1): self.assertEqual(cmd.get_coordset('topology',state).tolist(),expected)
