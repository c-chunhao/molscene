# Standalone replay. Keep scene.pse beside this file.
python
from pathlib import Path
cmd.load(str(Path(__script__).resolve().parent / 'scene.pse'), format='pse')
python end
