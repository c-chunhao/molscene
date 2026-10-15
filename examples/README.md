# Reproducible examples

These examples use public RCSB PDB coordinate files. The source structures are experimental models; the MolScene figures illustrate reproducible visualization and geometric analysis.

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
