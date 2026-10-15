# Testing and reproducibility

This document separates a test definition from a recorded successful run. A dependency skip is not backend or desktop validation. The machine-readable [validation record](validation.json) records v0.2 code hashes, exact environments, source tests, native acceptance and gallery evidence.

## Reproduce locally

Use a writable temporary directory and a fresh output destination. `python` means your selected environment's interpreter:

```sh
python -m pip install ".[test]"
python -m molscene doctor
python -m unittest discover -s tests -v
python -m molscene selftest --output results/selftest
python examples/reproduce_v02.py --output results/gallery
```

The real backend tests use local/synthetic fixtures and do not fetch network data. No-PyMOL runs report expected dependency skips and verify lazy import, parser, validation and failure behavior only. No-Qt runs do not establish GUI compatibility. A platform incapable of hardlinks/reparse points/distinct Windows 8.3 aliases may report capability skips; the reason must be retained. These tests remain enabled for capable CI filesystems.

Native desktop validation runs in a subprocess because native PyMOL owns its QApplication/event loop and quit lifecycle. Require the native harness directly when testing the actual desktop:

```sh
python -B tests/test_desktop.py --native results/native
python -B tests/test_desktop.py --native --clean-startup results/native-clean
```

These commands fail on missing dependencies, startup errors or invalid GL. They verify a valid native QOpenGLWidget, nonuniform framebuffer, mouse rotation/zoom, native atom pick, dock singleton/reopen, panel events and native window shutdown. An offscreen Qt widget is insufficient. Linux runners use `QT_QPA_PLATFORM=xcb`, Xvfb with a 24-bit screen/GLX and software Mesa; no offscreen override is used for acceptance.

The native workbench batch-close acceptance synchronizes after the first persisted, genuinely rendered item, then closes through the normal cancellation marker. This ensures pending items still exist when cancellation is requested; it is not a timing benchmark of an uncontrolled five-file batch. The separately published screenshot/click trace records an earlier actual run with two completed and three pending items. No production worker/render/cancellation algorithm is replaced by the test barrier.

## Scientific and output coverage

Geometry is checked against independent 3–4–5/90° fixtures and signed torsions; degenerate endpoints fail. Contacts are compared with brute-force Euclidean-distance oracles, including boundary, overlap, same index in different objects, zero rows and pair-limit failure. SASA is checked against a single-sphere 4π(r+probe)² oracle (1% tolerance), separated/overlapping spheres, cross-object occlusion, residue sums and unchanged source B-factors/settings. Coordinate inspection uses insertion codes, blank chain/segment distinctions, alternate conformers, unknown residues and pinned PDB/mmCIF parity.

State tests use coordinates that differ between states and objects absent in the requested state; singleton fallback is rejected. Engine/worker/GUI checks cover result parity, snapshot identity/staleness, non-GUI workers, camera preservation, tables/CSV, cancellation/recovery, unusual Unicode/punctuation paths, relocated replay and preservation of previous valid outputs on failure.

File safeguards are checked for links/reparse points, Windows aliases, input/output ancestry, explicit overwrite and fixed owned artifacts. Replay compares molecular state rather than promising pixel identity across backends. Image decoding, dimensions and nonblank pixels supplement manual scientific appearance checks: expected backbone cartoon, intended ligand/residue neighborhood, interpretable surface and intended alignment. No evaluation overlay is removed or modified.

## Packaging verification

Build both distributions and check metadata before release:

```sh
python -m pip install ".[build]"
python -m build --installer uv
python -m twine check dist/*
```

Install the wheel into an independent environment on the task drive. Run outside the checkout with Python `-I`: import/version/help, `doctor`, scientific CLI, offline selftest, and a native GUI smoke against the installed package. `-I` must not be defeated by adding checkout paths to sys.path. The wheel includes original packages, GUI Python plugin resources and pinned coordinates under `share/molscene/examples/data`; PyMOL/Qt remain separate dependencies. Selftest generates its own synthetic fixtures.

Extract sdist into a new directory, compare all pinned fixture hashes with `examples/data/sources.json`, compare package/test/example source bytes, and run fixture-backed science tests plus offline selftest there. The complete source suite is recorded separately; this archive check verifies a different packaging contract. This detects hidden reliance on ignored audit files or local worktrees. Record archive SHA-256, exact installed versions, commands, pass/fail/skip counts and capability reasons. Check tracked files and generated reports for private paths, credentials, runtime files and commercial material before publishing.

## CI environments

The [workflow](../.github/workflows/ci.yml) retains four unit combinations: Ubuntu/Windows × Python 3.10/3.13, installing only MolScene and intentionally omitting PyMOL/Qt. Expected integration skips in those jobs are not desktop results.

The separate required Linux job installs conda-forge PyMOL, its resolved PyQt binding, Pillow and Xvfb/Mesa support, prints environment/backend/Qt versions, asserts the desktop test dependency flags, and runs the full suite plus offline selftest. The suite runs the native harness normally and with clean preferences, plus the real workbench fixture/export/active-close workflow. A separate log gate requires all three native tests to report `ok`, rejecting skipped execution. Missing dependencies, invalid GL or failed native lifecycle therefore fail this job. Native workbench artifacts are retained; evidence is uploaded on success or failure. The environment is resolved rather than locked, so logs establish versions for a particular run. This does not imply future combinations are verified. Configuration follows the [official setup-micromamba v3 inputs](https://github.com/mamba-org/setup-micromamba).

Actual conclusions belong to the [Actions run for the release commit](https://github.com/c-chunhao/molscene/actions). Workflow code alone is not evidence of a green run. Historical v0.1 Linux backend [run](https://github.com/c-chunhao/molscene/actions/runs/38674100313/job/116223640261) passed backend tests before the desktop existed and does not validate v0.2 native GUI.

## Environment and scope notes

The v0.2 local desktop runtime is an independent open-source community build: Windows, Python 3.10.18, `pymol-open-source` package 3.2.0a0/native 3.2.0a, NumPy 2.2.6, PySide6-Essentials/shiboken6 6.8.1 and Pillow 12.3.0. The PyMOL package is prerelease. Numerical/API observations from the older installed Incentive PyMOL 3.1.6.1 are historical, not a desktop compatibility claim. Larger exports in that separate installation showed an evaluation overlay; the public gallery is generated normally with the independent open-source build.

Include the MolScene revision, OS, Python/backend/Qt versions, `doctor`, exact command/selections, full test summary and relevant report fields when reporting a bug. Use public or minimal synthetic structures and remove private coordinates, credentials, license material and personal paths from shared logs. [Contribution guidance](../CONTRIBUTING.md).

## Recorded v0.2 source results

Verified 2026-10-15 on the reviewed 0.2.0 implementation, including version/packaging changes. `validation.json` identifies the tested source by exact module SHA-256 values. The published main commit and remote CI run are recorded in the release publication-evidence.json and notes after actual green CI; the prepublication record keeps its CI URL null and links to the stable release page.

| Windows environment | Discovered | Passed | Skipped | Failed | Seconds |
| --- | --- | --- | --- | --- | --- |
| Python 3.10.18, open-source PyMOL package 3.2.0a0/native 3.2.0a, PySide6/Qt 6.8.1 | 138 | 124 | 14 | 0 | 156.412 |
| Python 3.10.18 without PyMOL or Qt | 138 | 36 | 102 | 0 | 9.500 |

The fourteen full-runtime skips are filesystem capabilities on the local exFAT drive: hardlinks, symlinks/junctions and a distinct Windows 8.3 alias. No backend/GUI tests skipped in that run. The plain environment's skips include missing scientific/GUI dependencies and filesystem capabilities. The final native workbench acceptance completed one real batch item and left four pending on cancellation, with normal native shutdown. An earlier full run exposed an unsynchronized batch-test timing race; the final test uses the explicit first-item barrier described above, with production behavior unchanged. Both the failure and corrective evidence were retained during development. A later full run exposed a real Windows summary-replacement failure while the GUI read progress; bounded retries for sharing/access errors and repeated path guards corrected it. A subsequent real worker regression found that GUI batch input paths supplied relative to the caller's directory were reinterpreted after the worker changed directories. The controller now validates and freezes the canonical input directory before dispatch; real completion and pre-cancellation cases cover it. The table above reports the passing final source run.

## Recorded v0.2 package checks

The refreshed wheel and sdist built successfully in isolated environments using the uv installer. Both independent wheel environments were forcibly reinstalled from the final wheel, then checked outside the checkout with Python `-I`. All 19 installed Python modules and four pinned data files matched the wheel; module hashes matched the source record. The environment without PyMOL/Qt passed lazy import, help and version checks.

The installed backend wheel passed `doctor`, a 660-atom mmCIF inspection and all nine offline selftest checks. Its native GUI smoke passed with valid OpenGL, 1,365 sampled framebuffer colors, actual mouse rotation/zoom, one atom picked, dock/scene/object events, unchanged preferences and normal close. This used the same Windows Python/backend/Qt versions listed above. A separate installed-wheel regression used isolated parent and worker interpreters: a relative input directory from the caller's directory was frozen before the child changed directories, then the real worker completed one item with a valid 120×100 PNG and matching coordinate provenance. No checkout paths were injected.

The extracted sdist matched all 33 package, test and example Python files and four pinned data files. Its targeted controller suite discovered three tests: two passed and one directory-symlink capability test skipped on exFAT, with zero failures in 2.863 seconds. This exercised real worker completion and pre-cancellation with relative batch inputs, plus invalid-directory rejection. CLI/science code, fixtures and gallery bytes are unchanged from the preceding validated candidate.

The final source archive was rebuilt after evidence updates, then source, gallery and documentation bytes were compared with committed Git blobs. All 64 gallery hashes also matched the working tree and example ZIP; generated text uses canonical LF before hashing. Privacy checks covered 126 public files, including 11 PSE sessions and 13 PNG metadata records, with zero findings.

Actual final commit-specific CI conclusions and asset hashes are attached to the [v0.2.0 release](https://github.com/c-chunhao/molscene/releases/tag/v0.2.0) as publication-evidence.json and release notes after green CI; they are not presumed by this prepublication document.
