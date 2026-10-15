# Interface analysis and implementation boundaries

This report records the installed backend observed on **2026-10-15** and the public interfaces used by MolScene. It separates local observations, public upstream implementation references, and the behavior of MolScene's own code. It is an API and behavior analysis, not a reproduction of the complete commercial PyMOL product.

## Observed installation

| Item | Local observation |
| --- | --- |
| Distribution | Installed Incentive PyMOL; package metadata marked proprietary |
| PyMOL version | 3.1.6.1 |
| Python | 3.10.18, Windows, CPython 3.10 native-extension ABI |
| Python/API layers | `pymol2`, `pymol`, `pymol.cmd` command wrappers |
| Other observed packages | `pmg_qt`, `chempy`, Qt-related packages |
| Native core file | `_cmd.cp310-win_amd64.pyd` |
| Native core size | 10,886,656 bytes |
| Native core SHA-256 | `bc39dd7e152fefeafa51a07c75dbf9243e1b79ab535e99d2d543ba52b9ebc0ab` |

The filename and hash identify the locally inspected build. They do not establish equivalence with another PyMOL version or distribution. No installed commercial source, extension binary, license material, or UI assets are included in MolScene.

## Normal API path

```text
MolScene CLI
  → validated input paths, numeric parameters, selection strings
  → isolated pymol2.PyMOL context
  → instance-bound cmd API
  → Python command wrappers
  → pymol._cmd native core
  → loaded objects, representations, fitting, ray rendering, export
```

The public upstream [`pymol2` module](https://github.com/schrodinger/pymol-open-source/blob/master/modules/pymol2/__init__.py) exposes the context/instance layer. Public Python command modules, such as [`viewing.py`](https://github.com/schrodinger/pymol-open-source/blob/master/modules/pymol/viewing.py), show calls through `_cmd`. These sources explain the architectural boundary; they do not prove that every commercial module has identical contents. The observed Qt packages are part of the installed application's UI stack; MolScene's workflow uses the API context.

The [official command reference](https://pymol.org/pymol-command-ref.html) is the behavioral reference for `load`, `select`, `show`, `get_distance`, `align`, `ray`, `png`, and `save`. MolScene passes values through normal Python API calls and uses fixed object names. Filenames are not interpreted as PyMOL script commands. The generated PML encodes paths and selections as Python string literals for replay.

## MolScene data flow

1. Validate the command, local coordinate files, dimensions, radius, selections, and destination.
2. Start a PyMOL context and load fixed internal object names.
3. Apply the requested scene or analysis, checking selected-atom counts.
4. Render and save the scene, session, replay, and copied inputs.
5. Write a JSON provenance report and check required artifacts.

The CLI and report generation use the Python standard library. PyMOL performs parsing of structural formats, molecular representations, fitting, rendering, and session serialization. MolScene does not implement a replacement molecular graphics engine.

### Pocket geometry

The conceptual protein pocket selection is:

```text
byres (protein atoms within radius of ligand atoms)
```

Selections are scoped to the loaded object. Direct distance hits and the residue-expanded pocket are different quantities. In the pinned 1HSG baseline, a 5 Å cutoff gives 115 direct protein atom hits; whole-residue expansion gives 236 atoms in 33 protein residues. This rule does not identify bond types, rank contacts, infer binding affinity, or include biological assemblies beyond the loaded file.

### Measurement and alignment

`measure` requires exactly one selected atom at each endpoint and reports geometric distance in Å. It does not silently choose an atom or substitute a centroid.

`align` delegates to `cmd.align`, with a default protein Cα selection. The returned fit statistics describe the matched atoms and refinement settings; they are recorded with the parameters rather than reduced to an unexplained score. The [upstream reference](https://pymol.org/pymol-command-ref.html#align) describes sequence-based matching and outlier refinement and discusses the suitability of alternative commands for more divergent structures. MolScene's first release exposes this one alignment workflow for related proteins.

### Outputs and replay

PNG supplies the static figure. PSE stores editable backend state. PML provides an inspectable reconstruction with copied coordinate inputs; JSON records input hashes and numerical results. A replay reconstructs the principal scene from explicit steps, while a session uses PyMOL's own serialization. Pixel-perfect equality across different PyMOL builds, graphics drivers, fonts, or rendering implementations is not implied.

Reports use input basenames and hashes. They do not intentionally publish local absolute paths, environment variables, or licensing information. The core CLI has no network-fetch path; the separately invoked example downloader has three fixed public HTTPS sources.

## Evidence and limits

The initial installed-backend probe passed 11 checks: PDB and mmCIF loading; atom/residue/chain selection; cartoon, sticks, and surface flags; a known 5 Å distance; alignment of a rigidly transformed copy; ray PNG output; session restoration; and a real ligand-pocket render. See [TESTING.md](TESTING.md) for numerical evidence and the distinction between this probe and the tool's tests.

These checks cover selected API behavior in one installation. They do not audit every native function, commercial-only feature, license mechanism, plugin, UI path, or file format. MolScene does not patch the commercial executable or bypass licensing. Users supply a properly installed backend under its own terms.

## Public references

- [PyMOL open-source repository](https://github.com/schrodinger/pymol-open-source) — public implementation context.
- [Upstream license](https://github.com/schrodinger/pymol-open-source/blob/master/LICENSE) — terms for the open-source distribution; commercial distributions have their own terms.
- [Official command reference](https://pymol.org/pymol-command-ref.html) — commands and semantics.
- [RCSB 1UBQ](https://www.rcsb.org/structure/1UBQ) and [RCSB 1HSG](https://www.rcsb.org/structure/1HSG) — public experimental structure examples.

References were consulted on 2026-10-15. Links to `master` describe the public upstream branch and can evolve; they are not a pinned source snapshot of the observed commercial binary. MolScene is an independent project, without affiliation or endorsement by Schrödinger or the PyMOL project.
