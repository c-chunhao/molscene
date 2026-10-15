# MolScene standalone replay: use @scene.pml in PyMOL.
# For directories containing parser punctuation, use this Python loader:
# import os
# old = os.getcwd()
# os.chdir(output_directory)
# try: cmd.do("@scene.pml")
# finally: os.chdir(old)
# The loader uses only os and the active PyMOL cmd (no MolScene install).
python
from pathlib import Path
_molscene_root = Path(__script__).resolve().parent
cmd.load(str(_molscene_root / 'inputs/mobile.pdb'), 'mobile')
cmd.load(str(_molscene_root / 'inputs/target.cif'), 'target')
cmd.bg_color('white')
cmd.set('ray_opaque_background', 1)
cmd.set('antialias', 2)
cmd.set('orthoscopic', 1)
cmd.set('depth_cue', 0)
cmd.set('ray_shadows', 0)
cmd.set('specular', 0.2)
cmd.set('cartoon_trace_atoms', 0)
cmd.hide('everything', 'all')
cmd.set('cartoon_trace_atoms', 0, 'mobile')
cmd.set('cartoon_trace_atoms', 0, 'target')
cmd.align('(mobile) and (polymer.protein and name CA)', '(target) and (polymer.protein and name CA)', cutoff=2.0, cycles=5, gap=-10.0, extend=-0.5, max_gap=50, object='alignment', transform=1)
cmd.show('cartoon', 'mobile')
cmd.color('marine', 'mobile')
cmd.show('cartoon', 'target')
cmd.color('orange', 'target')
cmd.orient('mobile or target')
cmd.zoom('mobile or target', 3)
cmd.deselect()
cmd.viewport(1600, 1200)
python end
