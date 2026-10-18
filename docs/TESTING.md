# Testing and reproducibility

## v0.6 assembly and trajectory verification

Focused source example evidence, observed 2026-10-17 on the independent Windows
Python 3.10/PyMOL runtime: `test_reproduce_v06` executed six tests, all passed,
no skips, in 11.352 seconds. It actually exported deposited 3C70 assembly 1
(two copies/10,410 atoms), a three-state fitted original tetrahedron and a
six-frame original raw DCD. It checked hand-derived finite RMSD/population
RMSF/equal-weight Rg, exact source/history bytes, complete nested manifests,
backend-free help, installed-layout data lookup and relocated standalone replay
in a process that explicitly blocks importing MolScene. This is analysis/export
evidence, without PNG rendering or a visible GUI, and is not a final package or
exact-commit CI claim.

```sh
python -m unittest discover -s tests -p "test_reproduce_v06.py" -v
python examples/reproduce_v06.py --output results/gallery-v06
python -m molscene assemblies examples/data/3c70.cif
python -m molscene assembly examples/data/3c70.cif --assembly-id 1 --state 1 --output results/assembly-v06
python -m molscene trajectory examples/data/trajectory-tiny-v06.pdb --object trajectory_source --selection all --no-align --trajectory examples/data/trajectory-tiny-v06.dcd --output results/trajectory-v06
```

The new focused suites are `test_assembly`, `test_assembly_export`,
`test_trajectory`, `test_dcd`, `test_trajectory_export`, `test_molecular_cli`,
`test_molecular_workflows`, `test_molecular_gui` and `test_reproduce_v06`.
Pure cases cover CIF/PDB syntax, right-to-left composition, exact label/auth
membership, resource caps, proper Kabsch rotation/reflection/degeneracy,
analytic metrics, time/frame selection and strict DCD record scanning. Native
cases must actually execute deposited expansion, source/live immutability,
independent identity/coordinate reload, DCD state/order mapping, closed bundles,
relocated replay, cancellation/protected-path failures and controller bindings.
The original tiny trajectories are controlled analytic fixtures, not evidence
for arbitrary chemistry, DCD atom correspondence or experimental dynamics.

Full release acceptance additionally requires full backend discovery, full
backend-free discovery with observed skip reasons, build/twine checks, wheel
and sdist fixture/source byte identity, and source-free isolated installed-wheel
native/scientific examples. Native GUI acceptance must exercise actual
Build/Analyze/Load copies, complete Results tables, last-checked currentness,
narrow layouts, cancellation/close and meaningful unmodified framebuffer
captures. Inspect generated SVGs and relocated replay separately. A dependency
skip does not satisfy any required new native feature case.

Retain all existing required scientific, GUI, solver, encoder and filesystem/
process protection gates. Actual five-job Linux/Windows CI must identify the
final exact commit; a workflow definition, historical success or local focused
run does not establish that result. Build/package validation, whole-branch
review, final CI, downloaded-release hash verification and backed-up desktop
upgrade are separate release evidence. Historical v0.5 records below remain
unchanged and do not supply v0.6 acceptance counts.

## Recorded v0.6.0 local validation

Actual local tests only. Source-freeze maps identify each gate input; they do not establish equality with a final public commit. Final source and package provenance require separate sidecars. Hosted CI and publication are not established. No unobserved external-tool version is inferred.

| Run | Discovered | Passed | Skipped | Failures/errors | Seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| Source backend | 596 | 574 | 22 | 0/0 | 1057.610 |
| Source plain | 596 | 211 | 385 | 0/0 | 103.422 |
| Installed required | 17 | 17 | 0 | 0/0 | 676.078 |

All 17 required cases passed in source backend and source-free installed runs. Plain skipped every required native case; its recorded dependency/platform reasons are preserved. All 54 current source and installed runtime modules match the audited wheel. Every gate runtime input matches the audited wheel exactly. The JSON retains every actual passed ID, sanitized skip reason, source-freeze/runtime hash, helper/config identity and gate proof hash.

Observed installed environment: python=3.10.18 | packaged by conda-forge | (main, Jun  4 2025, 14:42:04) [MSC v.1943 64 bit (AMD64)]; pymol_distribution=3.2.0a0; qt_binding=PySide6; qt_version=6.8.1; pymol_native_version=3.2.0a.

[Machine-readable v0.6 local record](validation-v06.json).

An earlier full run timed out during the PDB2PQR version prerequisite at its 15-second limit. The unchanged isolated case and final full-source/installed gates passed; failure evidence is retained. No production timeout, test identity or skip was changed. The exact startup delay was not reproduced.

Separately, installed-wheel and extracted-sdist offline examples passed independent analytic CSV checks, original input-byte checks, closed manifests and native session-string inspection. Standalone replay with MolScene imports blocked restored two 5,205-atom assembly copies, three fitted trajectory states, and all six DCD states under a Unicode/apostrophe path. The example ZIP preserves all 166 prior payload members, including 153 historical outputs, and adds 32 accepted v0.6 gallery files. These are separate package/example observations; exact final-commit CI and published asset hashes belong to the release sidecars.

Windows CI subsequently exposed a lexical fixture assertion comparing a resolved long temporary path with its equivalent 8.3 alias. The test now verifies physical file identity with `samefile`; the pinned-data corruption check is unchanged. All six focused example tests passed, and fresh backend-free discovery again passed 211 of 596 cases with 385 expected skips (54.067 seconds). Runtime modules, example/data bytes and the tested wheel are unchanged. The historical full/native gate inputs above predate this test-only correction; source provenance records its exact old/new test hashes, and final-commit CI verifies the correction on both Windows Python versions.

## Recorded v0.5 local validation

Local actual-test evidence only. Source freeze maps describe each gate input; they do not assert all frozen files equal the final public commit. Final commit and deltas belong to source-provenance/publication sidecars. Hosted CI and publication are not established here; actual CI job results belong to release publication-evidence.json. No unobserved APBS, PDB2PQR or FFmpeg version is inferred.

| Run | Discovered | Passed | Skipped | Failures/errors | Seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| Source backend | 512 | 490 | 22 | 0/0 | 1275.344 |
| Source plain | 512 | 175 | 337 | 0/0 | 83.312 |
| Installed required | 13 | 13 | 0 | 0/0 | 717.860 |

All 13 canonical required cases actually passed in source backend and source-free installed runs. Plain dependency/platform skips establish their stated limitations. The 45 current runtime modules match the audited installed wheel and each gate input. Exact passed IDs, sanitized skip reasons, per-input freeze hashes and observed environment versions are retained in validation-v05.json.

[Machine-readable v0.5 local record](validation-v05.json).

## v0.5 release acceptance

The v0.5 daily workflow has focused tests in `test_polar`, `test_polar_workflow`,
`test_annotations`, `test_persistence`, `test_persistence_worker`, `test_daily_gui`
and `test_reproduce_v05`. The gallery uses CPU ray with pinned public coordinates
and an original bonded synthetic fixture; it verifies complete inventories,
source mappings, literal label rows, candidate rows and relocated replay.
Headless success establishes those operations only. No v0.5 final source,
installed-package, native GUI, solver or hosted-CI success is inferred from these
test definitions or the historical counts below.

```sh
python -m unittest tests.test_reproduce_v05 tests.test_ci_status -v
python examples/reproduce_v05.py --output results/gallery-v05
python -m molscene polar examples/data/1ubq.pdb --selection-a "name N" --selection-b "name O" --output results/polar-v05
python -m molscene coordinates examples/data/1ubq.pdb --selection "chain A and name CA" --state 1 --format cif --output results/coordinates-v05
python -m molscene session-save results/polar-v05/scene.pse --output results/session-v05
```

Keep all twelve v0.4 required native/science/replay/encoder identities below and
add this exact thirteenth identity:
`test_daily_gui.NativeDailyTests.test_native_daily_polar_labels_exports_and_narrow_navigation`.
It must use actual native widgets, narrow navigation, meaningful framebuffer
content, polar rows/annotation, literal labels, coordinate/session save+reload,
job locking/cancellation, chooser cancellation and normal owned-process cleanup.
An offscreen widget, object names, or dependency skip does not meet this gate.

The dedicated Linux status channel must contain all **21** exact successful
identities: the thirteen above plus these unchanged eight protection cases:

| Case | Required methods |
| --- | --- |
| `test_bundles.BundleTests` | `test_publish_exact_inventory_hashes_and_empty_log`, `test_nested_symlink_rejected_and_cleanup_preserves_target`, `test_hardlink_alias_rejected_without_source_damage`, `test_destination_created_between_check_and_rename_is_not_replaced` |
| `test_external_tools.ExternalToolsTests` | `test_timeout_reaps_child_and_grandchild`, `test_cancellation_during_run_reaps_descendants`, `test_forced_worker_death_reaps_descendants`, `test_forced_guardian_death_reaps_descendants` |

Diagnostics remain separate from runner status. `test_ci_status` executes the
actual workflow runner/gates and rejects skips, failures, missing identities,
wrong classes, same-prefix classes, noise and spoofed stdout successes. Four
plain Ubuntu/Windows × Python 3.10/3.13 jobs remain; Windows must execute the
actual NTFS junction/hardlink guards. Actual solver and encoder probes remain
mandatory in the fifth Linux job. Test counts and skip reasons must come from
the exact frozen source; a workflow file is not proof of CI execution.

Before publication, independently review the whole branch, freeze runtime/tests/
examples/metadata/CI, then run complete backend and plain suites and all thirteen
required native/science/replay/encoder cases. Serialize native/GL phases. Build
wheel/sdist in fresh staging, install outside source and verify origins under
`-I`; run installed daily workflows and all preserved gates. Safely extract the
sdist and execute its own example/CLI, then replay with MolScene unavailable.
Compare historical example/pinned bytes; inspect decoded images, PNG metadata,
PSE contents and all text for privacy and scientific appearance. Preserve original
PSE/PNG/scientific bytes in public projections and retain original hashes.
Finally require five successful jobs and all 21 actual Linux successes on the
exact public head, downloaded evidence, actual resolved tool versions, archive
inventories/hashes and complete downloaded release-asset verification. Record
these results only after those runs; no dependency skip is native acceptance.

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
python examples/reproduce_v04.py --tools /absolute/path/tools.json --output results/gallery-v04
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

Direct `reproduce_v04.py` source/extracted-sdist execution selects its adjacent
MolScene package and launches the CLI from that source root. For isolated wheel
checks, copy the script outside every checkout so it uses the installed package;
do not add a checkout to `sys.path` or `PYTHONPATH`. Its public coordinate lookup
falls back to `share/molscene/examples/data` under the installation prefix and
still checks the pinned byte hash before use.

## CI environments

The [workflow](../.github/workflows/ci.yml) retains four unit combinations: Ubuntu/Windows × Python 3.10/3.13, installing only MolScene and intentionally omitting PyMOL/Qt. Expected integration skips in those jobs are not desktop results.

The separate required Linux job uses Python 3.11, conda-forge PyMOL 3.1/3.2,
Qt5, Pillow, FFmpeg/ffprobe and pinned APBS `3.4.1=py311ha92c01f_1`. PDB2PQR
3.7.1 uses a separate copied-interpreter Python 3.11 environment. Conda records
retain resolved builds, URLs and hashes; pip resolution/download hashes and
actual executable versions/architecture are retained too. Import, Qt binding,
tools and encoder assertions run before the suite. `MOLSCENE_REQUIRE_ADVANCED=1`
and an absolute `MOLSCENE_TOOLS_CONFIG` make missing science dependencies fail.
Xvfb/Mesa uses xcb and a 24-bit GLX display.

The twelve preserved v0.4 native/science/replay/encoder cases below, plus the
v0.5 daily identity above, must each produce
their full `test_name (module.Case) ... ok` line. A skip cannot satisfy a gate.
Windows NTFS unit jobs also require actual junction/hardlink safeguards; Linux
requires actual symlink/hardlink/no-replace and process-tree lifetime tests.
Evidence uploads even on failure. A resolved environment or workflow file alone
does not prove execution. Configuration follows the
[official setup-micromamba v3 inputs](https://github.com/mamba-org/setup-micromamba).

Actual conclusions belong to the [Actions run for the release commit](https://github.com/c-chunhao/molscene/actions). Workflow code alone is not evidence of a green run. Historical v0.1 Linux backend [run](https://github.com/c-chunhao/molscene/actions/runs/38674100313/job/116223640261) passed backend tests before the desktop existed and does not validate v0.2 native GUI.

## v0.4 required acceptance

| Exact unittest case | Required test |
|---|---|
| `test_desktop.NativeDesktopTests` | `test_native_startup_events_framebuffer_and_shutdown` |
| `test_desktop.NativeDesktopTests` | `test_native_clean_preferences_startup_and_shutdown` |
| `test_workbench.QtWorkbenchTests` | `test_native_fixtures_exports_and_cooperative_window_close` |
| `test_navigation_gui.NativeNavigationTests` | `test_native_navigation_presets_and_interface_workflow` |
| `test_advanced_gui.AdvancedNativeTests` | `test_native_advanced_timeline_density_potential_and_close` |
| `test_native_render.NativeRenderTests` | `test_native_volume_frames_and_cancel_cleanup` |
| `test_density_engine.DensityEngineTests` | `test_registered_density_relocated_replay` |
| `test_animation.AnimationEncoderTests` | `test_real_h264_frame_count_and_duration` |
| `test_animation.AnimationReplayTests` | `test_relocated_timeline_replay_without_molscene` |
| `test_electrostatics_real.RealElectrostaticsTests` | `test_real_apbs_uniform_sign_dielectric_translation_convergence` |
| `test_electrostatics_real.RealElectrostaticsTests` | `test_real_apbs_born_dielectric_boundary` |
| `test_electrostatics_real.RealElectrostaticsTests` | `test_real_pdb2pqr_ubiquitin_surface_and_replay` |

Map checks use asymmetric/nonzero-origin geometry, explicit file-axis mapping,
independent coordinates and full relocated replay. The electrostatics gates use
an analytical homogeneous-dielectric charge limit (sign, dielectric, translation
and convergence), a published Born dielectric-boundary numerical fixture plus
independent continuum comparison, and actual public ubiquitin preparation/
potential/display/replay. Published finite-grid Born regression and continuum
analytic values are distinct oracles. Retain warnings and actual solver counts.
Preparation checks original heavy-atom identity, additions, charge/radius
assignment, zero-radius hydrogens and source preservation. Color/legend/radius
sampling checks complement field values; grid extrema are not surface extrema.

The advanced native harness exercises widgets, timeline display changes,
registered density mesh/surface/volume, actual potential/ramp, responsive worker
cancellation/descendant cleanup and cooperative close. Framebuffer comparisons
must establish visible geometry, including visible versus hidden volume; object
names alone are insufficient. Existing four native assertions remain required.

The supervised-renderer gate additionally proves a visible Gaussian volume
differs from its hidden control, at least two timeline images differ within one
native child, source/camera preservation, normal window close and cancellation
with all owned renderer PIDs absent. Advanced explicit no-ray and volume-bearing
exports use an official Qt/OpenGL child through the process guardian. Reports
must identify requested/effective ray, the actual GPU renderer and decoded
image diagnostics. OpenGL output may be opaque and is not CPU ray tracing.
Header checks alone cannot establish render success; deliberately hidden or
transparent flat scenes remain valid when their images decode correctly.

Before publication freeze source and run the full source suite, the plain
environment, isolated installed-wheel GUI/science/replay, extracted-sdist
examples, original/v0.4 examples and relocated standalone replay. The sdist must
contain `reproduce_v04.py`, the tools JSON and Born input/PQR/license/source
files; execute from the extraction rather than importing the checkout. Record
archive inventories/hashes and scan text, logs, PNG metadata and PSE data for
local paths/credentials. Public projections must identify the original manifest
SHA-256/redactions, recompute their own artifact/report/manifest hashes and
preserve original evidence/pinned coordinate bytes. Never package runtime binaries.

The exact public source commit must pass Linux64/Qt5 CI before v0.4 publication.
No v0.4 full-suite, package, native or remote CI success is inferred from the
historical v0.3 tables below. Record new counts/versions/commit/CI URL after the
corresponding frozen-source runs; until then these are acceptance requirements.

## Recorded v0.4 local acceptance

Recorded complete local source/test freeze: `99e09702`; retained wheel runtime build: `7ea9a28`. Runtime modules and examples remain byte-identical through the later CI portability changes. The tests later canonicalize temporary/interpreter paths, specify UTF-8 for the Unicode worker script and resolve scientific evidence cwd; original assertions and production protections are preserved. CI prepares unique byte-identical tool copies beside their original executables, retaining library/venv lookup and actual original/copy identity evidence. Those post-suite changes passed independent review and new sequential Python 3.10/3.13 plain runs; they do not retroactively change the local results below. The release sdist is rebuilt with the corrected fixtures. Exact final commit, full five-job CI, archive inventories and downloaded asset hashes are recorded in `source-provenance.json` and `publication-evidence.json`; no hosted success is inferred here.

| Run | Discovered | Passed | Skipped | Failed/errors | Seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| Windows source backend | 440 | 420 | 20 | 0/0 | 1051.703 |
| Windows source plain | 440 | 155 | 285 | 0/0 | 52.591 |
| Independent wheel required cases | 12 | 12 | 0 | 0/0 | 709.065 |

All twelve required cases executed in the complete backend and isolated installed-wheel runs. Plain dependency/platform skips do not validate backend/desktop behavior; exact passed IDs and sanitized per-case skip reasons are retained in `validation-v04.json`.

The recorded local wheel/sdist audit checked 41 matching modules; that pre-portability sdist contains 201 members with 199 source files byte-matched. Its hashes remain in the JSON record. The final release sdist has a separate fresh inventory/hash in release provenance and includes the later test/CI preparation fixes. Installed and plain package origins were checked independently.

Actual advanced gallery potential, public projection identity/privacy, isolated relocated replay and nine offline selftest checks passed their recorded gates. The extracted-sdist example explicitly skipped potential and establishes secondary packaging behavior only. Legacy gallery manifest bytes and image header dimensions were checked; this aggregator does not itself decode images or inspect scientific appearance. Refer to their generator/audit evidence.

LPBE, raw scalar thresholds, surface-radius/unit assumptions and GPU-dependent OpenGL opacity remain explicit scientific limits. Camera interpolation is not molecular dynamics; grid extrema are not surface extrema.

The twenty backend-run skips are filesystem link/junction/8.3-alias capabilities. No backend, Qt, solver, encoder or required acceptance case skipped. The corrected CI identity parser also passed the unchanged affected test under actual Python 3.10, 3.11 and 3.13. [Machine-readable v0.4 record](validation-v04.json).


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
