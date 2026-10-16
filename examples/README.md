# Reproducible examples

These examples use public RCSB PDB coordinate files. The source structures are experimental models; the MolScene figures illustrate reproducible visualization and geometric analysis.

## v0.4 advanced workflows

The separate generator uses synthetic maps/spheres and the pinned public 1UBQ
coordinates below. It does not download structures or tools. Select an
environment containing MolScene, PyMOL and Pillow, and an explicit schema-1
[tools configuration](advanced-tools.example.json) pointing to real APBS 3.4.1
and PDB2PQR 3.7.1. Every placeholder path must be replaced; PDB2PQR uses its own
Python 3.11+ interpreter. The desktop may retain Python 3.10.

```sh
python examples/reproduce_v04.py --tools /absolute/path/tools.json --output results/gallery-v04
```

This generates a four-frame, 12 FPS camera/display timeline project and PNG
sequence, an asymmetric synthetic DX map with nonzero origin and a 90° z
rotation plus (8,-2,3) Å registration, and real prepared ubiquitin LPBE potential.
The public input hash is checked before use. Preparation uses explicit protein
state 1, PARSE, pH 7/PROPKA and water dropping; the solve requests 65³, default
2/78.5 dielectrics, 0.15 M salt and a ±5 kBT/e display range. See
[ADVANCED](../docs/ADVANCED.md) for the full assumptions and limitations.
The synthetic field is illustrative, not experimental electron density.

Images are 800 × 600 at 96 DPI so the potential unit/range and clipping legend
remain readable. This generator requests no-ray images, so it needs official Qt resources and a
working OpenGL display even for the CLI synthetic-only run. Advanced no-ray/
volume rendering uses a supervised native child; Linux CI supplies Xvfb/GLX.
Inspect requested/effective ray, actual GPU renderer and decoded image evidence.
OpenGL output may be opaque and does not establish CPU ray rendering.

`--skip-potential` is an explicit user choice for a tool-free synthetic example:

```sh
python examples/reproduce_v04.py --skip-potential --output results/synthetic-v04
```

The example-level `validation.json` records that skip; this run cannot satisfy
the required real-science release gate. Required release acceptance executes
APBS, PDB2PQR and H.264, with no dependency skips. Existing output directories
are refused; there is no `--overwrite`. Original v0.2/v0.3 examples remain
separate and keep their historical behavior.

Each feature directory has a closed inventory and `manifest.json`. Project:
`source.pse,timeline.json,replay.pml,report.json,manifest.json`. Animation adds
exactly four `frames/frame_000001.png`…`frame_000004.png`. Density contains
`inputs/density.dx,density.json,scene.pse,scene.png,replay.pml,report.json,manifest.json`.
Potential preparation contains `source.pse,inputs/selection.pdb,prepared.pqr,
preparation.json,potential.dx,apbs.in,tools.json`, the four APBS/PDB2PQR stdout/
stderr logs, `scene.pse,scene.png,replay.pml,report.json,manifest.json`.
The example-level manifest includes those complete feature inventories plus
`validation.json`; no runtime executable/DLL is copied.

Tool runs retain private original bundles/logs in a sibling
`.molscene-v04-private-*` directory. The selected public output contains
**derivatives**: each report's `public_projection` records its original manifest
SHA-256 and redactions; affected artifact hashes and the manifest are recomputed.
Science values and original private bytes are preserved. PSE bytes are copied
unchanged and require independent binary/session privacy review before public
distribution. The generator's text redaction is not a claim that this review
has passed. Never publish the private sibling or a tools config with local paths.

Scientific DX/PQR/PDB inputs retain exact bytes and line endings, preserving
input hashes and replay references. Public projections have no private GUI job
context: they remain unverified for current-scene result application. Replay
their complete folders, use Open project for a timeline, or explicitly import
maps/PQR/DX through the appropriate controls.

Replay each complete relocated feature directory with `@replay.pml` in fresh
compatible PyMOL. Timeline exposes `molscene_seek(frame)`/`molscene_play()`.
Replay needs neither MolScene nor solvers/encoder. Review camera/display changes,
registered map geometry and numeric potential legend/radius conventions in
addition to hashes and nonblank images. Actual v0.4 source/package/native/Linux
results belong in [TESTING](../docs/TESTING.md); no historical gallery proves them.

## Download and verify

From the repository root, using any Python 3.10+ interpreter:

```sh
python examples/download_examples.py
```

The standard-library downloader requests exactly the three HTTPS URLs below and writes them to `examples/data`, together with `sources.json`. The manifest records `source` and a `files` array containing each `file`, `url`, and `sha256`. Matching local files are checked and reused. Changed local files are refused unless you explicitly pass `--overwrite`; remote content with an unexpected checksum is always refused. Upstream files can change over time: review any mismatch instead of deleting the expected hash. `--output DIR` selects another destination. Neither the CLI nor the offline tests need network access.

| File | Source | SHA-256 of the pinned bytes |
| --- | --- | --- |
| `1ubq.pdb` | [RCSB HTTPS PDB](https://files.rcsb.org/download/1UBQ.pdb) | `d4a6812d8951cf6594e6a0763f089e35f5a80b62acb3c117b2c5565228a7b161` |
| `1ubq.cif` | [RCSB HTTPS mmCIF](https://files.rcsb.org/download/1UBQ.cif) | `056f98710cb2b36f633c45e41902a02eb446e82871da21ff2dd44f74a56ca0f6` |
| `1hsg.pdb` | [RCSB HTTPS PDB](https://files.rcsb.org/download/1HSG.pdb) | `d430675c358060808ef614002cfdcfcfa824714d89f1240adf5749d2ba60fed2` |

## Generate the gallery

Use the Python environment containing PyMOL and Pillow, from the repository root. The checked-in v0.2 figures use this complete reproduction script:

```sh
python examples/reproduce_v02.py --output results/gallery
```

To regenerate checked-in outputs, select `--output examples/outputs --overwrite` deliberately. The script fixes state 1, validates the pinned input hashes and report provenance, and checks report schema, finite results, relative existing artifacts and image dimensions/content. It writes the file sizes and SHA-256 hashes to `validation.json`. Visual workflows save `scene.png`, `scene.pse`, `scene.pml`, `report.json` and input copies; inspection saves CSV/FASTA without ray rendering. Keep complete output folders when moving them. In a fresh PyMOL session, change to a CLI output folder and execute `@scene.pml`; the replay does not require MolScene. GUI exports instead require their bundled PSE. Session portability can depend on the receiving PyMOL version.

The alignment uses the same 1UBQ structure in PDB and mmCIF formats. It is a coordinate and workflow consistency check, not evidence of similarity between distinct proteins.

Regeneration normalizes LF and hashes only a closed list of validated report artifacts, input copies and fixed batch summaries/items. Unrelated descendant files are preserved byte-for-byte and excluded from `validation.json`, including when `--overwrite` is used. Reported artifact paths must use fixed relative names and cannot traverse links or reparse points or refer to hardlinked files.

## v0.3 interface and live workflows

The new gallery has its own directory; the historical v0.2 outputs and
`reproduce_v02.py` remain unchanged. From the checkout root in a backend/Pillow
environment, run:

```sh
python -m examples.reproduce_v03 --output results/gallery-v03
```

Use `--output examples/outputs/v0.3 --overwrite` to replace its fixed owned
artifacts. The generator runs the real `molscene interface` CLI on the pinned
1HSG protease chains A/B, using `polymer.protein`, state 1 and primary altloc.
It checks each signed isolated-minus-complex difference, all partner/residue
sums and the half-total interface convention. The MK1 ligand and waters are
excluded from these two protein partners; context is exactly their union.
These coordinate chains are not an automatic biological-assembly reconstruction.

The live example uses the same public coordinate bytes with the Qt-free
controller on a real PyMOL context. It captures observed catalytic residue
sites, saves a named selection, applies overview/surface/geometric-pocket
presets, updates/renames a scene and creates a solvent/H-D-T/altloc-filtered
copy. It compares original atom/bond data before and after. Its PML reopens the
bundled PSE in a fresh context and verifies semantic atom identity and scene
names. This controller evidence is distinct from native widget/mouse acceptance.

The closed inventory contains six files in `interface/`: `scene.png`,
`scene.pse`, `scene.pml`, `report.json`, `interface.csv`, `inputs/input.pdb`.
It contains eight files in `live/`: `overview.png`, `surface.png`, `pocket.png`,
`clean.png`, `scene.pse`, `scene.pml`, `workflows.json`, `inputs/input.pdb`.
`validation.json` records SHA-256 and byte count for exactly those 14 files;
it does not hash itself. Text artifacts use LF, matching committed Git blobs
and example ZIP bytes; input coordinates keep their pinned original bytes.
Unrelated files are preserved byte-for-byte, including same-name descendants.
Every owned destination and ancestor is checked for links/reparse points
before resolution/generation; hardlinked owned files are rejected. The script
does not recursively clean a destination. Existing owned files require
`--overwrite` even when no manifest is present.

Observed on 2026-10-16 (Asia/Shanghai), with Python 3.10.18 and open-source
PyMOL native 3.2.0a, on the v0.3 implementation:

| v0.3 calculation | Recorded result |
| --- | --- |
| 1HSG partner A / B retained protein atoms | 757 / 757 |
| Partner A isolated / complex SASA | 6732.574285 / 4936.178322 Å² |
| Partner B isolated / complex SASA | 6818.204382 / 5001.522722 Å² |
| A / B buried difference | 1796.395964 / 1816.681661 Å² |
| Total buried area / half-total interface area | 3613.077624 / 1806.538812 Å² |
| Complete interface residue rows | 198, including zero-loss rows |
| Live source / observed sites / observed protein sites | 1686 atoms / 326 sites / 198 protein sites |
| MK1 / geometric 5 Å pocket | 45 ligand atoms / 236 protein atoms in 33 sites |
| Clean copy | 1559 retained; 127 solvent atoms removed; 0 H/D/T and 0 alternate removals |
| Standalone relocated replay | Interface coordinate PML and live bundled PSE/PML passed atom/bond/scene comparison |

The five 1000 × 750 PNGs were visually inspected for readable protein traces,
distinct partner colors and buried highlights, ligand/pocket neighborhood,
surface and cleaned-copy context. Small guide/stick markers support isolated
atoms; they do not add atoms or change scientific membership. The source PDB
has 127 solvent atoms, while all-residue and protein-site counts remain distinct.
No numerical result was changed during public privacy checks. JSON/CSV retain
full precision; values in this table are rounded for reading.

Image dimensions/content, input hashes and finite numeric consistency are
automated checks. Scientific appearance and the real desktop capture are
reviewed separately; nonblank pixels alone do not validate a figure. Pixel
identity across PyMOL builds is not promised. The v0.3 release's actual run
counts, values and package/native evidence belong in [TESTING](../docs/TESTING.md).

## What the selections mean

The pinned 1UBQ file loads as 660 atoms and one protein chain with 76 protein residues. Counting every residue identifier, including solvent, gives 134 residues; a report's all-residue count must not be presented as protein length.

The pinned 1HSG file loads as 1,686 atoms. `resn MK1` contains 45 ligand atoms. At 5 Å, 115 protein atoms meet the direct distance criterion. Expanding those hits with PyMOL's whole-residue selection yields 236 protein atoms in 33 residues. Pocket membership therefore depends on both the radius and the explicit residue-expansion rule. It is not an interaction-energy calculation.

## Structure attribution

- **[1UBQ — ubiquitin](https://www.rcsb.org/structure/1UBQ).** Vijay-Kumar S, Bugg CE, Cook WJ (1987), *Journal of Molecular Biology* 194:531–544. [DOI: 10.1016/0022-2836(87)90679-6](https://doi.org/10.1016/0022-2836(87)90679-6).
- **[1HSG — HIV protease complexed with L-735,524](https://www.rcsb.org/structure/1HSG).** Chen Z, Li Y, Chen E, Hall DL, Darke PL, Culberson C, Shafer JA, Kuo LC (1994), *Journal of Biological Chemistry* 269:26344–26348. [PubMed: 7929352](https://pubmed.ncbi.nlm.nih.gov/7929352/).

Coordinate files retain their archive/depositor records. MolScene's MIT license covers its original software and documentation; cite the archive entries and original papers when reusing the structures in scientific work.

## v0.2 scientific gallery

The source checkout includes a real CLI reproduction script:

```sh
python examples/reproduce_v02.py --output results/gallery
```

It runs overview, ligand pocket, B-factor surface, same-structure alignment, angle, dihedral, 4 Å ligand/protein contacts, protein SASA, mmCIF inspection and a three-input batch. Every command fixes state 1 (alignment fixes both states); default images are 1000 × 750 at 300 DPI. `--width`/`--height` change dimensions. Actual scientific review and desktop screenshots are separate evidence, not synthesized by this script.

Contacts use `resn MK1` against `polymer.protein`; they count unique heavy-atom center distances ≤4 Å, not hydrogen bonds. SASA targets `polymer.protein` in `not solvent and not hydro` context with primary blank+A altloc. The complete context supplies occlusion; residue areas are contributions in that context. Inspection exports coordinate-derived sequence, not full SEQRES.

Observed with open-source PyMOL native 3.2.0a on 2026-10-15:

| Example | Recorded result |
| --- | --- |
| 1UBQ Cα residues 1–2–3 angle | 119.829889° |
| 1UBQ Cα residues 1–2–3–4 signed dihedral | 177.046° |
| 1HSG MK1/protein contacts at 4 Å | 97 unique heavy-atom pairs |
| 1UBQ protein SASA in non-solvent heavy-atom context | 4869.986287 Å² |
| 1UBQ mmCIF inspection | 660 atoms; observed protein sequence 76 residues |
| Same-structure PDB/mmCIF alignment | RMSD 0 Å; 76 aligned atoms |
| Batch of the three pinned inputs | 3 completed; 0 failed; not cancelled |

The B-factor surface uses the actual 2.0–46.9 range; it is not an electrostatic map. The SASA image shows the structure context rather than an area heatmap. Eight main figures were visually inspected for expected backbone/ligand/surface/annotation/alignment content and absence of an evaluation overlay. The manifest records exact file bytes; image hashes need not match across different PyMOL builds.

The wheel stores pinned coordinate inputs in `share/molscene/examples/data` under the installation prefix; the sdist retains this guide, downloader, source tests and original `examples/data` paths. Offline selftest creates synthetic fixtures itself. The public gallery and screenshots must be regenerated and reviewed on the exact release implementation; historical v0.1 images do not prove new scientific/GUI workflows.
