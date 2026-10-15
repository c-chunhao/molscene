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
cmd.load(str(_molscene_root / 'inputs/input.pdb'), 'molecule')
cmd.bg_color('white')
cmd.set('ray_opaque_background', 1)
cmd.set('antialias', 2)
cmd.set('orthoscopic', 1)
cmd.set('depth_cue', 0)
cmd.set('ray_shadows', 0)
cmd.set('specular', 0.2)
cmd.set('cartoon_trace_atoms', 0)
cmd.hide('everything', 'all')
cmd.set('cartoon_trace_atoms', 0, 'molecule')
cmd.show('cartoon', '(molecule) and (polymer.protein)')
cmd.color('marine', '(molecule) and (chain "A")')
cmd.show('sticks', '(molecule) and (organic)')
cmd.show('spheres', '(molecule) and (inorganic)')
cmd.set('sphere_scale', 0.25, 'molecule')
cmd.orient('molecule')
cmd.zoom('molecule', 3)
cmd.deselect()
cmd.viewport(1600, 1200)
python end
