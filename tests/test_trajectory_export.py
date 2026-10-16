"""Transactional bundle, relocation/replay and escaped SVG trajectory tests."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
try:
    from molscene import trajectory_export, trajectory_plots
except ImportError:
    trajectory_export=trajectory_plots=None
from molscene.artifacts import sha256
from molscene.bundles import CancelledError

class PlotTests(unittest.TestCase):
    def test_plot_escaped_and_has_explicit_units(self):
        self.assertIsNotNone(trajectory_plots, 'trajectory plots required')
        svg=trajectory_plots.line_svg([1,2],[0,3],title='<x & y>',xlabel='Coordinate state',ylabel='RMSD (angstrom)')
        ET.fromstring(svg)
        self.assertIn('&lt;x &amp; y&gt;',svg); self.assertIn('angstrom',svg)
        self.assertNotIn('http',svg.replace('http://www.w3.org/2000/svg',''))

@unittest.skipUnless(importlib.util.find_spec('pymol2') is not None,'requires user PyMOL')
class ExportTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(trajectory_export, 'trajectory exporter required')
        import pymol2
        self.pm=pymol2.PyMOL(); self.pm.start(); self.addCleanup(self.pm.stop); cmd=self.pm.cmd
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup); self.root=Path(self.temp.name)
        for i,p in enumerate([[0,0,0],[2,0,0],[0,3,0],[0,0,4]],1): cmd.pseudoatom('m',name='C'+str(i),pos=p,resi=str(i),b=12+i)
        cmd.load_coordset([[1,0,0],[3,0,0],[1,3,0],[1,0,4]],'m',state=2)
        self.source=self.root/'input.pse'; cmd.save(str(self.source)); self.digest=sha256(self.source)
        self.request=dict(object_name='m',selection='all',fit_selection='all')

    def test_bundle_closure_immutable_snapshot_and_relocated_native_replay(self):
        report=trajectory_export.export_trajectory(self.source,self.root/'bundle',self.request)
        self.assertEqual(report['operation'],'trajectory'); self.assertEqual(len(report['frames']),2)
        self.assertEqual(sha256(self.source),self.digest)
        self.assertEqual(sha256(self.root/'bundle/source.pse'),self.digest)
        manifest=json.loads((self.root/'bundle/manifest.json').read_text())
        self.assertEqual(set(manifest['files']),{p.relative_to(self.root/'bundle').as_posix() for p in (self.root/'bundle').rglob('*') if p.is_file()}-{'manifest.json'})
        for name,record in manifest['files'].items(): self.assertEqual(sha256(self.root/'bundle'/name),record['sha256'])
        shutil.move(self.root/'bundle',self.root/'relocated')
        self.pm.cmd.reinitialize(); self.pm.cmd.do('@'+str(self.root/'relocated/replay.pml'))
        self.assertEqual(self.pm.cmd.count_states('m'),2)
        self.assertEqual(self.pm.cmd.get_coordset('m',2).tolist()[0],[1,0,0])
        for name in ('rmsd','rmsf','rg'): ET.parse(self.root/'relocated'/ (name+'.svg'))

    def test_refuses_existing_output_protected_and_cancelled(self):
        output=self.root/'existing'; output.mkdir()
        with self.assertRaises(ValueError): trajectory_export.export_trajectory(self.source,output,self.request)
        with self.assertRaises(ValueError): trajectory_export.export_trajectory(self.source,self.root/'protected',self.request,protected=(self.root/'protected/job.json',))
        with self.assertRaises(CancelledError): trajectory_export.export_trajectory(self.source,self.root/'cancelled',self.request,cancelled=lambda:True)
        self.assertFalse((self.root/'cancelled').exists()); self.assertEqual(sha256(self.source),self.digest)

    def test_original_cli_source_hash_bound_and_archived(self):
        original=self.root/'source.pdb'; self.pm.cmd.save(str(original),'m',state=0)
        provenance=dict(path=str(original),sha256=sha256(original))
        report=trajectory_export.export_trajectory(self.source,self.root/'provenance',self.request,source_input=provenance)
        self.assertEqual(report['inputs']['original']['sha256'],provenance['sha256'])
        self.assertEqual(sha256(self.root/'provenance/inputs/original.pdb'),provenance['sha256'])
        provenance['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'hash|changed'): trajectory_export.export_trajectory(self.source,self.root/'bad',self.request,source_input=provenance)
        self.assertFalse((self.root/'bad').exists())

    def test_dcd_bundle_replay_all_native_states_then_stride(self):
        from tests.test_dcd import original_dcd, ORACLE
        self.pm.cmd.delete_states('m','2'); topology=self.root/'topology.pse'; self.pm.cmd.save(str(topology))
        dcd=self.root/'input.dcd'; dcd.write_bytes(original_dcd()); digest=sha256(dcd)
        req=dict(self.request,trajectory_path=str(dcd),align=False,start=2,stop=6,stride=2)
        report=trajectory_export.export_trajectory(topology,self.root/'dcd',req)
        self.assertEqual([r['source_frame'] for r in report['frames']],[2,4,6])
        self.assertEqual(sha256(dcd),digest)
        self.pm.cmd.reinitialize(); self.pm.cmd.do('@'+str(self.root/'dcd/replay.pml'))
        self.assertEqual(self.pm.cmd.count_states('m'),6)
        self.assertEqual(self.pm.cmd.get_coordset('m',1).tolist(),ORACLE[0])


    def test_dcd_count_mismatch_and_multistate_topology_rejected(self):
        from tests.test_dcd import original_dcd
        dcd=self.root/'mismatch.dcd'; dcd.write_bytes(original_dcd())
        with self.assertRaisesRegex(ValueError,'single-state'):
            trajectory_export.export_trajectory(self.source,self.root/'multi',dict(self.request,trajectory_path=str(dcd)))
        self.assertFalse((self.root/'multi').exists())
        self.pm.cmd.delete_states('m','2'); self.pm.cmd.remove('m and index 4')
        top=self.root/'three.pse'; self.pm.cmd.save(str(top))
        with self.assertRaisesRegex(ValueError,'atom count'):
            trajectory_export.export_trajectory(top,self.root/'count',dict(self.request,trajectory_path=str(dcd)))
        self.assertFalse((self.root/'count').exists())

    def test_midway_and_prepublish_cancel_never_publish(self):
        for trigger in ('analyze','publish'):
            stopped=[]
            def progress(message):
                if message['stage']==trigger: stopped.append(True)
            out=self.root/trigger
            with self.assertRaises(CancelledError):
                trajectory_export.export_trajectory(self.source,out,self.request,cancelled=lambda:bool(stopped),progress=progress)
            self.assertFalse(out.exists()); self.assertEqual(sha256(self.source),self.digest)

class PlotExtremeTests(unittest.TestCase):
    def test_finite_extreme_time_range_keeps_finite_svg_positions(self):
        svg=trajectory_plots.line_svg([-1e308,1e308],[1,2],title='T',xlabel='Time (ps)',ylabel='Rg (angstrom)')
        self.assertNotIn('nan',svg.lower()); self.assertNotIn('inf',svg.lower())
