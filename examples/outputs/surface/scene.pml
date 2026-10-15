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
cmd.frame(1)
cmd.set('cartoon_trace_atoms', 0, 'molecule')
cmd.select('_molscene_display', '(molecule) and (all)', state=1, enable=0, quiet=1)
cmd.count_atoms('_molscene_display')
cmd.show('surface', '_molscene_display')
cmd.count_atoms('_molscene_display')
cmd.orient('_molscene_display', state=1)
cmd.zoom('_molscene_display', 3, state=1)
cmd.count_atoms('_molscene_display')
cmd.iterate('_molscene_display', 'values.append(float(b))', space={'values': []})
cmd.spectrum('b', 'blue_white_red', '_molscene_display', minimum=2.0, maximum=46.900001525878906)
cmd.set('transparency', 0.2, '_molscene_display')
cmd.set('stick_radius', 0.18, '_molscene_display')
cmd.deselect()
cmd.viewport(1000, 750)
python end
