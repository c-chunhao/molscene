# MolScene 0.2

[![Tests](https://github.com/c-chunhao/molscene/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/c-chunhao/molscene/actions/workflows/ci.yml)

**A native PyMOL workbench with reproducible figures and structural analysis.**

[简体中文](README.zh-CN.md) · [Desktop guide](docs/GUI.md) · [Examples](examples/README.md) · [Validation](docs/TESTING.md) · [Scientific definitions](docs/INTERNALS.md)

MolScene adds a dock to the upstream PyMOL Qt window and provides an offline CLI for local PDB/mmCIF structures. Rotate, zoom and pick atoms in the native viewport, apply representations and colors, calculate geometry, contacts and solvent-accessible surface area (SASA), and export an editable session with provenance. PyMOL supplies the molecular graphics engine and native interface; MolScene supplies the workbench, shared analysis definitions and reproducible output workflows.

![Native PyMOL viewport and MolScene contact results](docs/screenshots/workbench-contacts.png)

Actual native desktop capture: 1HSG MK1/protein geometric contacts, state 1, 97 pairs. [Desktop guide and click trace](docs/GUI.md).

| Ubiquitin overview | HIV protease ligand pocket |
| --- | --- |
| ![Ubiquitin cartoon](examples/outputs/overview/scene.png) | ![MK1 ligand pocket](examples/outputs/pocket/scene.png) |

## Install and launch

The core CLI requires Python 3.10 or newer and a separately installed PyMOL backend for calculations. The pinned GUI extra requires Python **3.10–3.13** because PySide6-Essentials 6.8.1 requires Python below 3.14; the locally tested desktop uses 3.10.18. A Python 3.10 backend environment is the recommended starting point. MolScene does not bundle PyMOL. A source checkout runs directly; installing it makes the commands available outside the checkout:

```sh
git clone https://github.com/c-chunhao/molscene.git
cd molscene
python -m pip install ".[gui,test]"
python -m molscene doctor
python -m molscene gui examples/data/1ubq.pdb --workspace workspace
```

The `gui` extra pins `PySide6-Essentials==6.8.1`. Existing compatible Qt bindings are accessed through `pymol.Qt`; do not mix bindings in one process. `test` adds Pillow for image validation. Import, `--help` and `--version` work without PyMOL or Qt; actual scientific commands require PyMOL, and `gui` additionally requires its native Qt resources and a working OpenGL display.

One independently managed backend option is [conda-forge PyMOL](https://github.com/schrodinger/pymol-open-source):

```sh
conda create -n molscene -c conda-forge python=3.10 pymol-open-source pyqt pillow pip
conda activate molscene
python -m pip install .
python -m molscene gui examples/data/1ubq.pdb --workspace workspace
```

This conda command resolves its own Qt binding. It is an installation recipe, not a claim that all future resolved versions are verified. [Testing](docs/TESTING.md) records exact observed environments and separates native desktop checks from backend-only checks. The locally tested desktop uses Windows, Python 3.10.18, community PyMOL package 3.2.0a0/native 3.2.0a, PySide6/Qt 6.8.1 and NumPy 2.2.6; the PyMOL wheel is a prerelease distribution.

## CLI workflows

Run from the checkout below, or substitute your own local coordinate paths after installation. Quote each PyMOL selection as one shell argument. Use `COMMAND --help` for every option.

```sh
# Default protein cartoon, or explicit representation and coloring
python -m molscene render examples/data/1ubq.pdb --output results/overview
python -m molscene render examples/data/1ubq.pdb --representation surface --coloring bfactor --state 1 --background transparent --output results/surface
python -m molscene render examples/data/1hsg.pdb --style pocket --ligand "resn MK1" --radius 5 --output results/pocket

# Exactly one atom per geometry endpoint; B is the angle vertex
python -m molscene measure examples/data/1ubq.pdb --selection-a "resi 1 and name CA" --selection-b "resi 2 and name CA" --state 1 --output results/distance
python -m molscene angle examples/data/1ubq.pdb --selection-a "resi 1 and name CA" --selection-b "resi 2 and name CA" --selection-c "resi 3 and name CA" --state 1 --output results/angle
python -m molscene dihedral examples/data/1ubq.pdb --selection-a "resi 1 and name CA" --selection-b "resi 2 and name CA" --selection-c "resi 3 and name CA" --selection-d "resi 4 and name CA" --state 1 --output results/dihedral

# Heavy-atom center distances, SASA in context, coordinate-derived sequence
python -m molscene contacts examples/data/1hsg.pdb --selection-a "resn MK1" --selection-b "polymer.protein" --cutoff 4 --state 1 --output results/contacts
python -m molscene sasa examples/data/1ubq.pdb --selection "polymer.protein" --context "not solvent and not hydro" --altloc primary --state 1 --output results/sasa
python -m molscene inspect examples/data/1ubq.cif --state 1 --output results/inspect

# Same-structure format consistency check; sorted nonrecursive batch
python -m molscene align examples/data/1ubq.pdb examples/data/1ubq.cif --method align --mobile-state 1 --target-state 1 --output results/alignment
python -m molscene batch examples/data --state 1 --width 800 --height 600 --output results/batch
python -m molscene selftest --output results/selftest
```

Representation choices are cartoon, ribbon, sticks, lines, spheres, surface, mesh, dots and nonbonded; coloring choices are chain, element, single, rainbow and bfactor. The original cartoon/surface/pocket presets are protein-centric. Explicit representations allow suitable nonprotein selections; cartoon/ribbon require an appropriate polymer. B-factor colors record the actual range and analysis preserves source B-factors.

## Results and interpretation

Every scientific operation fixes a positive coordinate `state` (CLI default 1). Missing coordinates are rejected instead of borrowing another state's coordinates. Alignment fixes mobile and target states separately. A multi-state file is not a trajectory analysis.

| Operation | Outputs in addition to `report.json` |
| --- | --- |
| Render, geometry, alignment | `scene.png`, `scene.pse`, `scene.pml`, copied inputs |
| Contacts | Scene outputs and complete `contacts.csv` |
| SASA | Scene outputs and per-residue `sasa.csv` |
| Inspect | `chains.csv`, `sequence.fasta`; no forced ray rendering |
| Batch | `batch.json`, `batch.csv`, numbered per-input output directories |

Reports use schema version 2, input hashes, explicit parameters, units and relative artifact paths. Existing distance/RMSD fields remain available. Output destinations require explicit `--overwrite` when occupied; unsafe links, aliases and input/output collisions are rejected. Failed computation preserves prior valid outputs. Batch failures return nonzero; cancellation preserves completed items and records pending items honestly.

Contacts are unique undirected heavy-atom pairs within the cutoff (default 4 Å), excluding self-pairs. They describe geometry, not hydrogen-bond chemistry or binding affinity. The maximum is 100,000 pairs; exceeding it fails without truncation. Up to the shortest 200 are drawn; the successful CSV contains every pair.

SASA uses a 1.4 Å solvent probe, dot density 3 and solvent-accessible dots. The default context is non-solvent heavy atoms in objects containing the target and the default `primary` altloc filter keeps blank+A. The target must belong to the filtered context. Target area is calculated in that whole context, preserving inter-residue and inter-object occlusion; it is not the sum of independently exposed isolated residues. `--altloc all` includes all conformers and changes the physical interpretation. Sequence export uses observed guide atoms, preserves residue mapping and normalizes unknowns to X; it does not reconstruct missing residues or complete SEQRES.

Keep each output directory together for replay. CLI coordinate workflows reconstruct from copied coordinates and recorded API operations without MolScene. GUI export preserves arbitrary native edits with a bundled PSE; its PML loads that PSE, so it must travel with the script. Snapshot results carry state, time and semantic coordinate fingerprints; changed scenes are labelled stale. [The desktop guide](docs/GUI.md) explains this workflow.

## Scope and provenance

MolScene's original code is [MIT licensed](LICENSE). PyMOL and Qt are separate dependencies under their respective distribution licenses. The native viewport, menus, command console, molecular engine and upstream Qt resources come from PyMOL; they were not recreated or copied from a commercial installation. This independent project is not affiliated with or endorsed by Schrödinger or the PyMOL project. See the [upstream open-source license](https://github.com/schrodinger/pymol-open-source/blob/master/LICENSE) and [interface analysis](docs/INTERNALS.md).

This release covers common representation/coloring controls, object/scene management, state-specific geometry, geometric contacts, SASA, coordinate sequence, alignment and batch figures. Validated hydrogen-bond chemistry, trajectory statistics, a separate movie timeline, density-map workflows, electrostatics, automatic biological assembly and commercial-only components remain outside the validated MolScene workflows. Native PyMOL may expose additional commands under its own behavior and licensing; MolScene does not claim commercial parity.

The public examples use an independent open-source runtime and retain [coordinate attribution](examples/README.md). No evaluation overlay was removed or modified. The core tool is offline; only the optional fixed-source, checksum-verified example downloader accesses the network. See [testing evidence](docs/TESTING.md) and [contribution guidance](CONTRIBUTING.md).
