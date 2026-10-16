# Biological assemblies and trajectory analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax for tracking.

**Goal:** Release MolScene 0.6.0 with deposited biological assemblies and scientifically explicit trajectory workflows in CLI and native GUI.
**Architecture:** Two independent scientific services publish closed guarded bundles through the existing advanced worker pipeline. A focused Qt section widget integrates each into Structure/Analysis, while Results retains complete tables and derived assembly copies load explicitly.
**Tech Stack:** Python >=3.10 stdlib, lazily imported NumPy/PyMOL, existing Qt binding, unittest and GitHub Actions.
**Spec:** docs/superpowers/specs/2026-10-17-assembly-trajectory-design.md

## Global Constraints

- All new files, worktrees, environments, caches, examples and evidence stay under F:/001_data/CaoCh/008_Reverse/01pymol. Existing tools on C:/D: may be read/run. No global configuration changes.
- Python >=3.10, lazy PyMOL/Qt/NumPy imports; backend-free CLI help and request validation remain usable. NumPy is supplied by the compatible PyMOL scientific runtime; no new mandatory package.
- Preserve existing rendering, timelines, density, electrostatics, labels, persistence and public examples. Do not redistribute proprietary software or make commercial parity claims.
- New bundles use Bundle(new_directory_only=True), checked_path, complete manifests and artifact hashes; cancellation publishes no successful partial bundle. Protect original inputs, private copies, job and envelope on validation/error paths.

## Review Focus

- Assembly label/auth chain mismatch, blank PDB chain and noncommuting composite transforms must generate exact deposited membership, not heuristics (Task 1).
- Trajectory reference outside sampled frames, spatial selection changing over time, missing/discrete atoms and collinear fits must be handled explicitly (Task 2).
- DCD topology dummy state, reordered atoms, unsupported cell/fixed/4D variants and inflated header bounds must fail or match independently decoded frame coordinates (Task 2).
- Malformed worker requests and outputs/envelopes aliasing source paths must preserve bytes even on errors/cancellation (Task 3).
- New copies/long paths/narrow pages and completed results without private source identity must not yield clipped controls or false current/stale status (Task 4).

## Task 1: Deposited biological assembly service

**Files:** Create molscene/cif_tables.py, molscene/assembly.py, molscene/assembly_export.py, tests/test_assembly.py, tests/test_assembly_export.py; original fixture files under examples/data/assembly-*; real examples/data/3c70.cif plus source attribution in a separate v06-sources.json. No shared pipeline/GUI edits.
**Interfaces:** assembly_catalog(path)->dict; validate_assembly_request(request)->dict canonical source_path/assembly_id/state; build_assembly(cmd,source_path,*,assembly_id='1',state=1,cancelled=None,progress=None)->dict; export_assembly(input_session,output_dir,request,*,cancelled=None,progress=None,protected=())->dict.

- [ ] Write tests first for original PDB and mmCIF identity/nonidentity/Cartesian compositions, source label/auth mismatch, NULL blank chain, incomplete/missing/duplicate metadata and bounded malicious expressions. Watch intended failures.
- [ ] Implement bounded CIF1 token/table parser and PDB REMARK350 reader, safe operation expansion and validation. No eval or new dependency; exact spec caps.
- [ ] Implement native exact member copies, transform evidence, immutable original sources and closed bundle export/replay. Return report rows as top-level copies list[dict], inputs={'source':...}, parameters=canonical request; expose copy_files metadata for GUI and preserve each original core identity.
- [ ] Verify real 3C70 nonidentity assembly against deposited matrices and original analytic fixture, per-copy CIF independent reload <=0.001 angstrom, manifest and relocated replay, collision/cancel/protection failures. Source hash before/after exact. Report actual tests and exact result metadata keys.
- [ ] Run focused unittest files with backend and full plain suite, self-review and commit only owned files. Independent task review required.

## Task 2: Trajectory numeric, DCD and bundle service

**Files:** Create molscene/trajectory.py, molscene/trajectory_export.py, molscene/trajectory_plots.py, optional molscene/dcd.py; tests/test_trajectory.py, tests/test_trajectory_export.py, tests/test_dcd.py and original tiny trajectory fixtures under examples/data. No shared pipeline/GUI edits.
**Interfaces:** validate_trajectory_request(request)->dict; trajectory_analysis(cmd,request,*,cancelled=None,progress=None)->dict; export_trajectory(input_session,output_dir,request,*,cancelled=None,progress=None,protected=(),source_input=None)->dict. Canonical object_name plus selection/fit_selection/reference_state/start/stop/stride/align/time_step_ps/time_start_ps/trajectory_path. Follow spec defaults/caps and final Builder decisions exactly, including original CLI source_input provenance.

- [ ] Write failing independent analytic tests for rigid rotation/translation, proper rotations excluding reflections, nonzero fluctuation known values, Rg/residue aggregation, sampling/time/reference outside range, nonfinite/missing identity/degenerate fit and cancellation.
- [ ] Implement reference-frozen exact membership across explicit fixed-topology states, stable NumPy Kabsch/population metrics, copied arrays and complete top-level frames/atoms/residues tables. Reject unsupported/discrete states; no source modification.
- [ ] Implement bounded ordinary 3D no-cell/unfixed DCD header validation and isolated native load_traj with state=1/average=1/image=0; reject unsupported variants and header count/resource lies. Original independent struct fixture coordinates verify first/middle/last frame exact mapping and input hash preservation. If native loader cannot support the subset, report blocker rather than silent fallback.
- [ ] Implement guarded bundle source snapshot/DCD inputs, CSV tables, self-contained escaped SVGs with accurate axes and standalone replay; manifests/protection/cancel/relocated replay tests.
- [ ] Run focused numeric/backend tests and full plain suite, self-review and commit only owned files. Independent task review required.

## Task 3: Guarded services, controller and CLI integration

**Files:** Modify molscene/advanced.py, molscene/worker.py, molscene/desktop/controller.py, molscene/desktop/jobs.py, molscene/cli.py; add tests/test_molecular_workflows.py, tests/test_molecular_cli.py.
**Interfaces:** Consume Task 1/2 validators/exporters. advanced operations assembly and trajectory use {'request','cancel_file'}. External roles source_path->source and trajectory_path->trajectory; trajectory input_session snapshot->session. Controller assembly_catalog(path), prepare_job/new operation, load_assembly_result(report_path) returning loaded collision-free copies. Completed records use exact validated report path and prepared context before recording.

- [ ] Write failing pipeline/CLI tests for backend-free help and catalog, invalid requests, raw source roles protected on error, original input/copy changed, out-of-output envelopes, cancellation and correct reports.
- [ ] Register validation/dispatch/source paths/worker raw protection/copy roles/cooperative operations. Assembly never saves unrelated live PSE; trajectory saves a frozen PSE and captures only selected object's sampled+reference native members.
- [ ] Implement private request binding, context currentness and closed validated explicit load_assembly_result with unique names, original camera/session_file preservation and rollback. Copies never invalidate original-source currentness. Complete worker reports bind _report_path before record_result without implicitly loading.
- [ ] Add documented CLI arguments; coordinates load with object 'trajectory_source', PSE requires actual object name. Preflight original inputs/output before temporary capture and protect coordinate origin through dispatch.
- [ ] Run focused worker/controller/CLI tests with real backend plus full plain suite, self-review and commit. Independent task review required.

## Task 4: Native GUI assembly and trajectory workflows

**Files:** Create molscene/desktop/molecular_panel.py, tests/test_molecular_gui.py, tests/molecular_native.py; modify panel.py and minimal needed controller/jobs integration owned by this task after Task 3. Extend CI required identity list/test status checks for new mandatory real cases.
**Interfaces:** AssemblyControls and TrajectoryControls attached to parent workbench; use parent.run/_launch_job, shared source/output browsers, current object inventory, collapse/wordwrap/vertical actions. Keep five page indices and Results cancellation behavior.

- [ ] Write failing controls tests for fields/defaults/catalog ID selection/load copies/currentness and full fivepage job locking. Write an opt-in required native workflow test using existing owned-process supervision contract.
- [ ] Implement readable Structure Biological assembly and Analysis Trajectory sections; metadata summary/complete Results tables and plot paths. Optional time step disabled by default; trajectory state/stride distinct from animation.
- [ ] Test native actual asynchronous Build then explicit Load copies and Analyze, source immutability, result status after adding copies/editing originals, full table counts, closed outputs and cancellation. Record actual unmodified PNG evidence of both workflows.
- [ ] Check 360/420/540/800 widths and long F paths for no horizontal overflow/hidden actions, no Qt lifetime warnings or orphan workers. CI must fail if new required acceptance is skipped.
- [ ] Run focused GUI/native/integration tests and full plain suite, self-review and commit. Independent task review required.

## Task 5: Examples, full validation and publication

**Files:** Update version metadata/__init__, README/README.zh-CN, docs/FEATURE-GAPS.md, docs/GUI.md, docs/INTERNALS.md, docs/TESTING.md; add examples/reproduce_v06.py, examples documentation and real screenshots. Update package data for original/public fixtures and generator. Keep all prior examples immutable.
**Interfaces:** Consume canonical reports and reproducible services, run offline public examples from source and isolated installed wheel. Public release assets include wheel, sdist, examples, test report and SHA256SUMS.

- [ ] Add offline reproducible analytic/native examples and tested guide commands, factual source attribution, explicit equal weighting/no-PBC/DCD subset/assembly membership and scientific limits.
- [ ] Run full backend suite with all required new and existing native/science cases, full backend-free suite, build/twine checks, isolated installed new native/scientific cases and source/archive identity checks. Inspect actual generated PNG/SVG outputs and relocated replays. Retain observed skip reasons, never call skipped cases passes.
- [ ] Independent whole-branch review, fix meaningful findings with focused regression, then commit actual validation documentation before final CI. Use dedicated status logs, exact final commit and all required identities.
- [ ] Push only authorized main update and v0.6.0 tag, verify actual five-job final CI success, publish reviewed notes/assets and download them to verify hashes. Preserve private refs/evidence; no push --all/--tags.
- [ ] Back up F desktop package, install tested wheel no-deps, run actual native smoke/doctor, and fast-forward clean primary. Record release/test/desktop evidence and deliver concise Chinese handoff with F launcher and GitHub links.

