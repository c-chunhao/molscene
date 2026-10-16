# Testing and reproducibility

This document separates a test definition from a recorded successful run. A dependency skip is not backend or desktop validation. The [v0.3 validation record](validation-v03.json) identifies the frozen source and actual release checks; the [historical v0.2 record](validation.json) remains intact.

## Reproduce locally

Use a writable temporary directory and a fresh output destination. `python` means your selected environment's interpreter:

```sh
python -m pip install ".[test]"
python -m molscene doctor
python -m unittest discover -s tests -v
python -m molscene selftest --output results/selftest
python examples/reproduce_v02.py --output results/gallery
python -m examples.reproduce_v03 --output results/gallery-v03
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

The separate required Linux job installs conda-forge PyMOL, its resolved PyQt binding, Pillow and Xvfb/Mesa support, prints environment/backend/Qt versions, asserts the desktop test dependency flags, and runs the full suite plus offline selftest. The suite runs the native harness normally and with clean preferences, the real workbench fixture/export/active-close workflow, and the navigation/preset/clean-copy/interface workflow. A separate log gate requires all four native tests to report `ok`, rejecting skipped execution. Missing dependencies, invalid GL or failed native lifecycle therefore fail this job. Native artifacts are retained; evidence is uploaded on success or failure. The environment is resolved rather than locked, so logs establish versions for a particular run. This does not imply future combinations are verified. Configuration follows the [official setup-micromamba v3 inputs](https://github.com/mamba-org/setup-micromamba).

Actual conclusions belong to the [Actions run for the release commit](https://github.com/c-chunhao/molscene/actions). Workflow code alone is not evidence of a green run. Historical v0.1 Linux backend [run](https://github.com/c-chunhao/molscene/actions/runs/38674100313/job/116223640261) passed backend tests before the desktop existed and does not validate v0.2 native GUI.

## Environment and scope notes

The v0.2 local desktop runtime is an independent open-source community build: Windows, Python 3.10.18, `pymol-open-source` package 3.2.0a0/native 3.2.0a, NumPy 2.2.6, PySide6-Essentials/shiboken6 6.8.1 and Pillow 12.3.0. The PyMOL package is prerelease. Numerical/API observations from the older installed Incentive PyMOL 3.1.6.1 are historical, not a desktop compatibility claim. Larger exports in that separate installation showed an evaluation overlay; the public gallery is generated normally with the independent open-source build.

Include the MolScene revision, OS, Python/backend/Qt versions, `doctor`, exact command/selections, full test summary and relevant report fields when reporting a bug. Use public or minimal synthetic structures and remove private coordinates, credentials, license material and personal paths from shared logs. [Contribution guidance](../CONTRIBUTING.md).

## v0.3 acceptance contract

The new interface tests use independent equal-sphere area oracles (3% tolerance
for dot-sampled pair loss), partner symmetry, complete zero/nonzero residue
rows, signed differences, exact union context, filtering, requested-state
spatial membership and success/failure cleanup. Structure-helper tests check
exact original membership, unusual residue labels, observed sequence,
selection visibility, scoped display, a Euclidean pocket oracle, untouched
original atom/bond/state data, clean-copy counts and PSE scene persistence.

`test_reproduce_v03.py` tests the separate gallery's closed inventory,
same-name sentinels, overwrite preflight, owned links/ancestor links/hardlinks
where supported, LF hashes and deterministic scientific consistency. Its
backend boundary fixtures exercise ownership; actual gallery generation is
separate evidence and uses real PyMOL calculations/images/replay.

Native acceptance additionally requires
`NativeNavigationTests.test_native_navigation_presets_and_interface_workflow`.
The independently runnable `tests/navigation_native.py` helper exercises real
browser selection/handoff, pocket preset, scene edit, clean copy, interface
worker, complete result export, GL/mouse interaction and normal close. Installed
wheel checks copy that standalone helper outside the checkout and invoke it
with `-I`, without source-path injection or sibling-test imports. The existing
three native tests remain required alongside it.

## Recorded v0.3 source results

Verified 2026-10-16 on the final integrated 0.3.0 source, including version,
generator and the reviewed session-replacement cache correction. All Python,
version, manifest and CI source hashes were unchanged across both final runs.
Later static evidence edits do not alter those tested source bytes.

| Windows environment | Discovered | Passed | Skipped | Failed | Seconds |
| --- | --- | --- | --- | --- | --- |
| Python 3.10.18, open-source PyMOL 3.2.0a0/native 3.2.0a, PySide6/Qt 6.8.1 | 194 | 177 | 17 | 0 | 220.480 |
| Python 3.10.18 without PyMOL or Qt | 194 | 43 | 151 | 0 | 11.886 |

All seventeen backend-run skips were local exFAT link/junction/hardlink or
distinct Windows 8.3 alias capabilities; no backend or GUI dependency test
skipped. All four required native tests executed and reported `ok`: standard
startup, clean-preferences startup, workbench export/cooperative close and
navigation/preset/clean-copy/interface workflow. The plain run's skips include
expected absent scientific/GUI dependencies and the same filesystem limits.
`doctor` passed and offline selftest passed all nine checks. Source module/test/
example/metadata/CI hashes and the actual check names are in the v0.3 record.

The v0.3 gallery generation and actual source native screenshot were observed
on 2026-10-16 with Python 3.10.18, open-source PyMOL package 3.2.0a0/native
3.2.0a and PySide6/Qt 6.8.1. Gallery ownership/numeric tests discovered 9:
6 passed, 3 F-volume link capability skips, 0 failures. The separate real
gallery produced 14 owned artifacts with matching hashes, five inspected PNGs
and relocated standalone PML/PSE atom/bond/scene parity. Native capture had
valid GL, 3,222 sampled framebuffer colors, the actual 198-row interface result
with 4 MET-filtered rows and normal close. These focused observations do not
replace the final integrated full-suite or installed-package checks.

The following sections retain historical v0.2 results and package checks.
They are not substituted for the v0.3 evidence above.

## Recorded v0.3 package checks

The wheel was forcibly installed into separate F: plain and backend environments.
All 21 installed Python modules and four pinned coordinate/provenance files
matched the wheel and frozen source. Both environments passed import, CLI help
and version checks outside the checkout with `-I`; ordinary import/CLI parsing
left PyMOL and Qt unloaded. The plain environment had no PyMOL or Qt installed.

The installed backend passed `doctor`, the new 1HSG interface CLI with all 198
residue rows and half-total area 1806.5388121381402 Å², and all nine offline
selftest checks. The exact standalone `navigation_native.py` was copied outside
the checkout and run with `-I`, without source-path injection or sibling helper
imports. Actual native acceptance passed GL, rotation/zoom, exact 17-atom UBQ
handoff, the 33-site geometric HSG pocket, scene editing, 1559-atom clean copy,
complete 198-row interface/CSV and current-view export. Source atoms/properties/
coordinates/colors/bonds were unchanged and native close completed normally.
That installed run sampled 701 UBQ and 2368 HSG framebuffer colors; these are
separate from the 3222-color source screenshot capture.

The source archive retained and byte-matched all 42 package/test/example Python
files and four pinned data/provenance files. In a fresh extraction, its own
package was imported from the extracted source. Targeted interface science
passed 13/13 tests in 1.069 s; interface engine/standalone replay passed 15/15
in 15.101 s, with zero skips/failures. Extracted-source offline selftest passed
all nine checks. This archive acceptance did not repeat the entire source suite.

Final artifacts are built from a fresh exported source stage on F:, with code,
data, original documentation and gallery bytes checked against the source and
committed Git blobs. The example ZIP preserves both historical v0.2 and new
v0.3 bytes. Full public privacy checks cover 155 files, 19 PNG metadata records
and 13 PSE opcode streams without unpickling, with zero private-machine-prefix
or credential findings. The new gallery's 14 manifest entries match source,
committed blobs and ZIP bytes; pinned coordinate whitespace is preserved.
The separate v0.3 validation record identifies exact source hashes and local
acceptance. Final archive hashes belong to the distributed SHA256SUMS and
commit-specific publication evidence, avoiding self-referential archive hashes.

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
