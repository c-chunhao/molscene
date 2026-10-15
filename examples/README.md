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

Use the Python environment containing PyMOL, from the repository root:

```sh
python -m molscene render examples/data/1ubq.pdb --output examples/outputs/overview
python -m molscene render examples/data/1hsg.pdb --style pocket --ligand "resn MK1" --radius 5 --output examples/outputs/pocket
python -m molscene render examples/data/1ubq.pdb --style surface --output examples/outputs/surface
python -m molscene align examples/data/1ubq.pdb examples/data/1ubq.cif --output examples/outputs/alignment
```

If the gallery output folders already contain files, use a fresh destination or deliberately add `--overwrite`. Each run writes `scene.png`, `scene.pse`, `scene.pml`, `report.json`, and input copies needed for replay. Keep a complete output folder when moving it to another machine. In a fresh PyMOL session, change to that folder and execute `@scene.pml`; the replay does not require the MolScene package. Session portability can depend on the receiving PyMOL version.

The alignment uses the same 1UBQ structure in PDB and mmCIF formats. It is a coordinate and workflow consistency check, not evidence of similarity between distinct proteins.

## What the selections mean

The pinned 1UBQ file loads as 660 atoms and one protein chain with 76 protein residues. Counting every residue identifier, including solvent, gives 134 residues; a report's all-residue count must not be presented as protein length.

The pinned 1HSG file loads as 1,686 atoms. `resn MK1` contains 45 ligand atoms. At 5 Å, 115 protein atoms meet the direct distance criterion. Expanding those hits with PyMOL's whole-residue selection yields 236 protein atoms in 33 residues. Pocket membership therefore depends on both the radius and the explicit residue-expansion rule. It is not an interaction-energy calculation.

## Structure attribution

- **[1UBQ — ubiquitin](https://www.rcsb.org/structure/1UBQ).** Vijay-Kumar S, Bugg CE, Cook WJ (1987), *Journal of Molecular Biology* 194:531–544. [DOI: 10.1016/0022-2836(87)90679-6](https://doi.org/10.1016/0022-2836(87)90679-6).
- **[1HSG — HIV protease complexed with L-735,524](https://www.rcsb.org/structure/1HSG).** Chen Z, Li Y, Chen E, Hall DL, Darke PL, Culberson C, Shafer JA, Kuo LC (1994), *Journal of Biological Chemistry* 269:26344–26348. [PubMed: 7929352](https://pubmed.ncbi.nlm.nih.gov/7929352/).

Coordinate files retain their archive/depositor records. MolScene's MIT license covers its original software and documentation; cite the archive entries and original papers when reusing the structures in scientific work.
