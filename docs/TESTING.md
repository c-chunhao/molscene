# Testing and reproducibility

MolScene distinguishes **backend baseline observations**, **tests of its own implementation**, and **CI environments**. A backend import or a passing unit-only job does not establish that rendering and replay work.

## Final local tool results

Verified on **2026-10-15**, against implementation revision **`9b8df7c`**, using `python -m unittest discover -s tests -v` and the actual `python -m molscene selftest --output DIR` command:

| Local Windows environment | Discovered | Passed | Skipped | Suite elapsed time | CLI selftest |
| --- | --- | --- | --- | --- | --- |
| Python 3.10.18, installed Incentive PyMOL native 3.1.6.1 | 35 | 35 | 0 | 13.060 s | 9/9 passed |
| Python 3.10.18, independent community wheel `pymol-open-source` 3.2.0a0, native 3.2.0a | 35 | 35 | 0 | 12.773 s | 9/9 passed |
| Plain Python 3.14.3, without PyMOL | 35 | 17 | 18 | 1.789 s | Requires PyMOL |

All three suite runs completed without failures. The plain-Python run verifies the available unit/failure-path behavior; its 18 skips do not establish backend integration. Each backend's CLI selftest JSON recorded `passed: true` and nine individually passing checks: runtime, PDB/mmCIF coordinates, Cα selection, representations, known 5 Å distance, rigid alignment, session restoration, ray image, and portable replay. Portable replay covers directories containing spaces, Chinese characters, an apostrophe, a semicolon, and literal dollar/parenthesis characters after the original fixtures have been removed.

These are local observations, not universal compatibility claims. The community wheel is a prerelease build. The separate Linux conda-forge environment remains unverified until its actual CI job succeeds. Suite timings describe these individual runs and are not performance benchmarks.

[Machine-readable validation record](validation.json) preserves the curated test results, selftest evidence, and gallery hashes without publishing local absolute paths.

## Recorded installed-backend baseline

An initial probe ran on **2026-10-15**, with installed Incentive PyMOL **3.1.6.1**, Python **3.10.18**, on **Windows**. All **11 backend checks passed**. This probe preceded the final MolScene CLI and is not a count of MolScene tests.

| Check | Recorded evidence |
| --- | --- |
| Real 1UBQ PDB load | 660 atoms, 76 Cα atoms, chain A, one state |
| 1UBQ mmCIF load | 660 atoms; maximum coordinate difference from PDB: 0.0 Å |
| Chain/residue/atom selections | 76 Cα atoms; residue 1: 8 atoms |
| Cartoon representation | Representation flags observed on 660 selected atoms |
| Sticks representation | Representation flags observed on 660 selected atoms |
| Surface representation | Representation flags observed on 660 selected atoms |
| Known-geometry distance | 5.0 Å |
| Rotated/translated-copy alignment | RMSD 0.0 Å; 76 aligned atoms |
| Overview ray image | 1200 × 900 PNG, 145,886 bytes, 15,085 distinct colors |
| Session restoration | 6 objects; 660 atoms; 76 selected atoms; view maximum difference 0.0 |
| Real 1HSG ligand pocket | 45 ligand atoms; 236 whole-residue pocket atoms; 1200 × 900 PNG, 513,764 bytes, 14,171 colors |

For the pinned inputs and hashes, see [examples/README.md](../examples/README.md). 1UBQ has 76 protein residues and 134 total residues when solvent is included. The 1HSG pocket figure above uses the residue-expanded selection, not just the 115 protein atoms directly within 5 Å of MK1.

The baseline records come from the local audit captured during development. The public test suite and commands below are the reproducible evidence for MolScene's implementation. Generated images can differ in bytes across backend versions; fixed dimensions, image content, selection counts, geometry, session state, and replay provide useful checks alongside scientific inspection of the figures.

## Tested environments and output limits

Local numerical/API tests have passed in two separate Windows Python 3.10 environments:

| Backend | Version identification | Interpretation |
| --- | --- | --- |
| Existing installed Incentive PyMOL bundle | Native version 3.1.6.1 | Numerical/API behavior was exercised; larger exports displayed an evaluation overlay |
| Independently installed community open-source wheel | Package `pymol-open-source` 3.2.0a0; native version 3.2.0a | A separate prerelease backend used to generate the public example gallery |

During manual inspection, 1600 × 1200 exports from the installed bundle displayed the literal message `No License File - For Evaluation Only (0 days remaining)`. Those observations describe that local environment; passing numerical/API tests does not validate its commercial license status or establish that its exports are suitable for production figures. The public gallery is generated normally with the independent community wheel, rather than altering an evaluation-marked image. That wheel is a prerelease, and its local results are separate from the Linux conda-forge CI environment.

Image dimensions, nonuniform pixels, and representation flags do not by themselves establish scientific correctness. Manual acceptance also checks that cartoons follow the protein backbone, the pocket shows the intended residues and ligand, the surface is interpretable, and the alignment displays the intended structures. In particular, a trace through every atom can produce a nonblank but scientifically misleading ribbon. Regenerated figures must be inspected again after a representation change.

The four shipped gallery PNGs were regenerated through the open-source CLI at the default **1600 × 1200 pixels, 300 DPI**, then manually inspected on 2026-10-15. The overview shows the ubiquitin fold with the expected backbone cartoon; the pocket's transparent protein context leaves the orange MK1 ligand visible; the surface is a solid molecular surface; and the alignment shows coincident blue/orange copies of 1UBQ. All four reports identify native PyMOL **3.2.0a**, and the inspected images show no evaluation overlay. This visual review complements the automated checks; it does not validate the experimental models for every research use.

## Run the tool tests

From the repository root:

```sh
python -m unittest discover -s tests -v
```

Unit tests exercise argument validation and failure paths without requiring PyMOL. Integration tests use a real `pymol2` backend and Pillow. In an environment without PyMOL, those tests are reported as skipped; inspect the summary instead of treating skips as verified integration behavior.

Use the interpreter from an existing PyMOL installation for the full suite. Install Pillow in that environment if it is not already present:

```sh
python -m pip install pillow
python -m molscene doctor
python -m unittest discover -s tests -v
python -m molscene selftest --output results/selftest
```

Each selftest run should use a new output directory, or an explicit `--overwrite`. It generates fixed geometry and peptide inputs locally, records per-check outcomes, and returns nonzero if any check fails. Keep its report alongside the test log when reporting a backend result.

For example, on Windows PowerShell:

```powershell
& "D:/soft/pymol/python.exe" -m unittest discover -s tests -v
& "D:/soft/pymol/python.exe" -m molscene selftest --output results/selftest
```

That path is an example; substitute the interpreter in your own installation.

## Integration coverage

The integration suite is intended to check PDB/mmCIF loading, selection counts, representation flags, known-distance geometry, rigid-transform alignment, PNG dimensions and nonuniform content, session restoration, and PML replay in a fresh context. Numerical tolerances account for floating-point behavior. Replay comparisons focus on the principal molecular state rather than assuming bit-identical rendering on different systems.

Core tests use generated/local fixtures. They do not fetch data during a run. The optional three-file downloader is documented separately, so upstream availability does not decide whether the core tests pass.

`render` requires protein atoms and currently rejects water-only or DNA/RNA-only input. This keeps its protein-oriented cartoon, surface, and pocket workflows explicit. `measure` accepts general atom selections scoped to the loaded object, with exactly one atom per endpoint. The suite checks clear rejection of unsupported protein-rendering input rather than treating an empty or misleading figure as success.

For a manual acceptance check, generate the [gallery](../examples/README.md#generate-the-gallery), inspect each PNG against the criteria above, open each PSE, and replay each PML from its complete output directory in a fresh context. Check the corresponding JSON inputs, units, selection counts, and alignment statistics. Image decoding alone does not verify the scientific meaning of a scene.

## CI environments

The [workflow](../.github/workflows/ci.yml) defines:

| Job | Environment | What it establishes when green |
| --- | --- | --- |
| Unit matrix | Ubuntu and Windows; Python 3.10 and 3.13, without installing PyMOL | CLI/unit behavior on four combinations; backend tests may skip |
| Open-source integration | Ubuntu; conda-forge Python 3.10, `pymol-open-source`, Pillow | Backend import/version, the full test suite, and offline selftest on that resolved environment |

The integration job prints package/backend versions, asserts that `pymol2` is importable, and uses Xvfb and software OpenGL for a headless runner. On failure, diagnostic output folders are uploaded if available. The environment currently resolves conda-forge packages instead of using a lockfile; inspect the job log for the exact versions installed in a particular run.

The workflow definition is a test plan, not a recorded successful run. Refer to the [repository's actual Actions runs](https://github.com/c-chunhao/molscene/actions) for the result of a particular commit. A Windows installed-backend result must not be generalized to Linux or to the open-source build.

The action setup follows the [official `setup-micromamba` usage](https://github.com/mamba-org/setup-micromamba). CI does not install or redistribute Incentive PyMOL.

## Useful bug-report evidence

Include the MolScene revision/version, OS, Python version, `doctor` output, full command with selections, test failure, and relevant report fields. Use a public or minimal synthetic structure when possible. Review logs before sharing: omit private coordinates, credentials, license material, personal paths, and unrelated environment details. See [CONTRIBUTING.md](../CONTRIBUTING.md).
