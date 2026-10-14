# MolScene Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver and publish a tested CLI for reproducible PyMOL figures, geometric measurement, alignment, and runtime checks.

**Architecture:** An original Python package invokes a user's existing `pymol2` runtime. A small engine creates scenes and records replayable commands; the CLI validates arguments and exposes errors. Offline self-tests and genuine RCSB examples supply evidence.

**Tech Stack:** Python >=3.10, standard library, existing PyMOL, unittest; Pillow for image validation only.

**Spec:** `docs/superpowers/specs/2026-10-15-molscene-design.md` (confirmed by user).

## Global Constraints

- The public repository contains original code; it never contains commercial binaries, modules, license files, or UI assets.
- Every completed scene produces PNG, PSE, PML, JSON, with input hashes and runtime version.
- Invalid input/empty selection/invalid dimensions/nonfinite radius/nonempty output directory fail clearly with nonzero status.
- `--overwrite` affects only this tool's named outputs and never recursively deletes a directory.
- Public example reports omit private absolute paths and environment credentials.
- Compatibility claims identify the backend actually tested; geometry is not binding energy.

## Review Focus

1. Paths containing spaces, Chinese characters, apostrophes and shell-like punctuation must load and replay literally.
2. Both measurement selections must identify exactly one atom, including alternate locations or ambiguous names.
3. Empty ligands, empty protein selections, and empty alignment correspondences must fail without success claims.
4. Output must not overwrite input or unrelated files; overwrite must not allow an output symlink to redirect writes.
5. Missing runtime and command-level PyMOL failures must return a concise actionable message, including self-test failure.

## File Map and Interfaces

- `molscene/engine.py`: `doctor() -> dict`; `render(input_path, output_dir, *, style='cartoon', ligand=None, radius=5.0, width=1600, height=1200, dpi=300, overwrite=False) -> dict`; `measure(input_path, output_dir, *, selection_a, selection_b, width=1600, height=1200, dpi=300, overwrite=False) -> dict`; `align(mobile_path, target_path, output_dir, *, selection='polymer.protein and name CA', width=1600, height=1200, dpi=300, overwrite=False) -> dict`. Functions accept `str|Path`, raise `ValueError` or `RuntimeError` with useful messages, and return the saved JSON report.
- `molscene/__init__.py`: `__version__ = '0.1.0'`.
- `molscene/__main__.py`, `molscene/cli.py`: argparse, engine delegation, exit statuses 0/1/2.
- `molscene/selftest.py`: `run_selftest(output_dir, *, overwrite=False) -> dict` containing `tests` and `passed`; records synthetic fixtures and outputs. Uses engine where applicable plus direct API to test underlying behavior.
- `tests/test_engine.py`, `tests/test_cli.py`, `tests/test_integration.py`: independent checks.
- `examples/data`, `examples/outputs`, `examples/download_examples.py`: fixed fixtures with attribution and showcase artifacts.
- `README.md`, `README.zh-CN.md`, `docs/INTERNALS.md`, `docs/TESTING.md`, `CONTRIBUTING.md`, `LICENSE`, `.github`: release presentation and repeatable CI.

### Task 1: Scene Engine and Artifact Safety

**Files:** create `molscene/__init__.py`, `molscene/engine.py`, `tests/test_engine.py`, `pyproject.toml`, `.gitignore`.

**Interfaces:** consumes `pymol2.PyMOL`; produces the four engine functions above. Report keys: `tool`, `tool_version`, `pymol_version`, `created_at`, `operation`, `inputs` (basename + sha256), `parameters`, `structure`, `analysis`, `artifacts`. Artifacts map fixed names `scene.png`, `scene.pse`, `scene.pml`, `report.json`. Add a source copy subdirectory or make paths portable relative to the output for PML; document the choice.

- [ ] Write failing tests for invalid paths, NaN radius, bad dimensions, occupied outputs and literal path quoting. Example: `with self.assertRaises(ValueError): render(missing, temporary_directory)`.
- [ ] Run `python -m unittest discover -s tests -p test_engine.py -v`; verify failure before code exists.
- [ ] Implement lazy runtime imports, normal PyMOL context, fixed safe object names and explicit selections scoped to the loaded object. Reject occupied/symlink artifact targets before writing.
- [ ] Record commands as literal Python calls, using `repr` or JSON string literals rather than interpolating PML commands. Replay invokes the same scene construction and supports its recorded local input copies without needing MolScene installed.
- [ ] Render chain-colored cartoons, surface scenes, and pocket scenes. Pocket cartoon context is translucent, sticks are thin, ligand carbon is orange and its sticks thicker; verify ligand visibility visually. Measurement creates a distance object; alignment records the full `cmd.align` result and the specific selections.
- [ ] Save outputs; return version/hash/structure/analysis metadata; check artifact files exist and are nonempty.
- [ ] Run tests and commit only Task 1 paths.

### Task 2: CLI and Offline Self-Test

**Files:** create `molscene/cli.py`, `molscene/__main__.py`, `molscene/selftest.py`, `tests/test_cli.py`, `tests/test_integration.py`.

**Interfaces:** CLI consumes Task 1 functions. Self-test returns `{'passed': bool, 'tests': [{'name': str, 'status': 'passed'|'failed', 'evidence': dict}], ...}`; writes `selftest.json`. CLI returns 1 when `passed` is false.

- [ ] Write parser tests for required selections and missing input, help without PyMOL, dispatch arguments, and error exit codes. Write integration checks for atom cardinality and PML replay.
- [ ] Run CLI tests to verify failures before implementation.
- [ ] Implement five subcommands from the spec; the CLI prints concise output location and key scientific numbers, catches expected validation/runtime failures, and never prints success when testing fails.
- [ ] Build offline fixtures with a fixed peptide and single-atom points at (0,0,0)/(3,4,0). Verify PDB/mmCIF coordinates, selected C-alpha count, representation flags, 5 Å measurement, rotated/translated copy alignment, session recovery, ray image dimensions and nonuniform pixels, and PML replay in a new context.
- [ ] Run `D:/soft/pymol/python.exe -m unittest discover -s tests -v` and `D:/soft/pymol/python.exe -m molscene selftest --output .audit/tool-selftest`.
- [ ] Commit Task 2 paths.

### Task 3: Examples, Interface Research and Documentation

**Files:** create `README.md`, `README.zh-CN.md`, `docs/INTERNALS.md`, `docs/TESTING.md`, `examples/download_examples.py`, example metadata, `CONTRIBUTING.md`, `LICENSE`, issue templates and CI workflow.

**Interfaces:** documentation uses the exact CLI from Task 2; test results are read from `.audit/probe.json` and final tool reports. Main README links three actual generated scene PNGs.

- [ ] Write bilingual quick starts using the user's PyMOL Python, then optional editable pip install with no bundled PyMOL. Include doctor/render/pocket/measure/align/selftest examples and expected output files.
- [ ] Download 1UBQ PDB/CIF and 1HSG PDB using urllib HTTPS with exact IDs, record source URLs/SHA-256, and attribute RCSB entries. Do not depend on live network for core tests.
- [ ] Generate final overview, pocket, surface and alignment examples using the actual CLI; inspect PNGs; retain sample JSON/PML and compact inputs, avoid proprietary or private assets.
- [ ] Describe observed package/command/native-extension boundaries using public upstream links and version-specific facts. Explain that behavior analysis and API tooling do not reproduce the entire commercial product.
- [ ] Add unit CI on Linux/Windows using Python 3.10 and 3.13, plus Linux open-source integration CI using conda-forge `pymol-open-source` and Pillow. The integration job runs real tests/selftest and uploads artifacts on failure.
- [ ] Run documented commands and verify all local README links. Commit documentation and example paths.

### Task 4: Verification, Review and Public Release

**Files:** update docs/TESTING.md with actual counts and final evidence; fix issues in relevant files.

- [ ] Run all unittest tests, offline selftest, literal-path replay, and real CLI examples. Validate exported PNGs with Pillow and view the final figures.
- [ ] Obtain independent code review, inspect code and test evidence, fix material issues and repeat affected checks.
- [ ] Audit staged files for private paths, credentials, commercial binaries and module copies; check `git diff --check` and package metadata.
- [ ] Verify `c-chunhao/molscene` name is available; create public repository only after local checks pass. Push committed source, add descriptive topics and v0.1.0 release with real release notes.
- [ ] Verify remote commit and files, and inspect completed GitHub CI jobs. Resolve CI failures that are within project control; report platform/backend limitations honestly.
- [ ] Deliver repository/release links, actual test results, feature descriptions and sample output image to the user. Do not promise popularity or send unsolicited external messages.

## Plan Review

The CLI, all artifact types, privacy rules, basic tests, real examples, bilingual documentation, interface analysis and GitHub release each map to a task. Interfaces use a single keyword naming scheme. Review focus is exercised by engine validation, CLI tests and replay integration tests. No additional GUI or commercial reimplementation is included.
