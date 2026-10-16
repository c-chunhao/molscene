# Interfaces, scientific definitions and implementation boundaries

MolScene is independent MIT software around public PyMOL APIs. PyMOL supplies the native molecular engine, Qt viewport, menus and console. MolScene supplies the dock, validation, scientific definitions, provenance and file workflows. No commercial binaries, license material or UI assets are redistributed. [Upstream source](https://github.com/schrodinger/pymol-open-source), [license](https://github.com/schrodinger/pymol-open-source/blob/master/LICENSE) and [command reference](https://pymol.org/pymol-command-ref.html) document the separate dependency.

## Runtime boundaries

```text
CLI → argument validation → instance-bound pymol2 context → shared analysis/styles
GUI → official PyMOL Qt window → MolScene dock/controller → same analysis/styles
GUI long job → immutable PSE snapshot + JSON → independent worker → result envelope
```

`import molscene` and CLI parsing do not import Qt/PyMOL. Desktop import is deferred until `gui`; the window uses `pymol.Qt` rather than mixing bindings. The official startup-plugin mechanism defers attachment until the native QApplication exists. Qt widgets and live `cmd` operations remain on the main thread. Contacts, SASA, ray export and batch use independent worker processes, invoked with argument arrays; filenames and selection expressions are API values, never interpolated shell commands.

The worker accepts schema-version-1 jobs with operation, input session where required, output and parameters. `result_path` selects the atomic JSON envelope destination; the envelope has `passed` plus `result` or `error`. Scientific outputs can share that envelope path. Workers do not import the GUI. Unsafe source/result collisions fail without overwriting a source in an attempt to report an error.

## State and atom identity

Every scientific operation selects a positive integer coordinate state. Selection resolution uses that state's actual coordinates, including spatial predicates. PyMOL's singleton fallback is not accepted as proof that an object has state 2. Mixed `all` selections use atoms actually present in the specified state; directly selecting an object absent in that state yields an error. Alignment fixes mobile/target states separately.

Atom identity retains object, index, segment, chain, residue number including insertion code, residue name, atom name, element, altloc and state. Equal indices in different objects are distinct. Residue-site and chain keys retain object/segment identity; blank chains are meaningful. The old all-residue count is not a protein-length field.

## Scientific contracts

Geometry returns kind/value/units/state/atoms. Distance is in angstrom; angles and signed dihedrals are in degrees. A–B–C uses vertex B. Each endpoint must select exactly one atom; repeated/zero-length/undefined geometries fail. The signed torsion follows the public PyMOL convention. The legacy `distance_angstrom` report field remains available.

Contacts use heavy-atom center distance ≤ cutoff (default 4 Å), unique undirected pairs and no self-pairs. Overlapping selections are deduplicated, cross-object identities preserved, and results sorted by distance and full identity. Residue-pair summaries record minimum distance and atom-pair count. At most 100,000 pairs are permitted; excess fails rather than truncating. At most the shortest 200 pairs are drawn, while CSV keeps the complete successful result. This is geometric proximity, not a chemical hydrogen-bond classifier or affinity model.

SASA uses `dot_solvent=1`, `solvent_radius=1.4`, `dot_density=3`. Default context is non-solvent heavy atoms in target-containing objects; default `primary` altloc retains blank+A. Target must be a subset of the filtered context. The entire context is merged into one temporary object, retaining a source mapping, so inter-object occlusion is included. Area is calculated once on that context and summed over target atoms and residues. Original coordinates, B-factors and settings are preserved; source atoms are not used as a scratch area table. `all` altloc is explicit and can describe overlapping alternative conformers. Results include target/context counts, filtering/parameters and Å² areas. Surface rendering is a display and does not itself constitute SASA or electrostatics.

Inspection returns atoms, residue sites, elements, objects, chains and coordinate sequences. Sequence uses observed guide atoms, unknowns normalize to X, and residue-to-position mapping is retained. Missing residues are not inferred and complete SEQRES is not reconstructed. `align`/`super` delegate to PyMOL; recorded fit statistics and algorithm parameters must be interpreted for the chosen structures, not as universal similarity scores.

Scientific CLI figures show protein cartoons with molecular lines, sticks for nonprotein atoms (including nucleic acids), and nonbonded marks for isolated atoms. Partial molecules without a cartoon trace therefore retain visible context in PNG, PSE and standalone PML. The legacy default `render` presets still require protein; explicit representations can render other molecules.

## Partner interface area

`analysis.interface(cmd, selection_a, selection_b, state=1, altloc='primary')`
uses the same non-solvent heavy-atom/blank+A filtering as SASA. Original
object/index identities bind retained membership with explicit-state
`select_list`; overlap after filtering and either empty partner fail. Removed
shared solvent/hydrogen/alternate atoms do not create retained overlap. The
context is precisely A∪B, never the full source objects.

Four SASA calculations produce A alone, B alone, A in A+B and B in A+B.
Each partner's buried area is its isolated minus complex contribution. The
result names their sum `total_buried_area_angstrom2` and half that sum
`interface_area_angstrom2`. Raw differences remain signed. Complete residue
rows include zero loss and retain partner, original object/segment/chain/
residue label, state, atom count and isolated/complex/buried areas. A residue
split between partners has separate rows. Probe radius is 1.4 Å and dot
density is 3. Areas approximate a geometric surface; they are not interaction
energies, binding affinities or chemistry-assigned hydrogen bonds.

The CLI `interface` command writes schema-2 JSON, complete `interface.csv`,
PNG/PSE/PML and copied input coordinates. Replay uses public PyMOL APIs and
relative copied coordinates without importing MolScene. Retained partners use
distinct colors, with buried residue sites highlighted. GUI interface jobs use
the existing immutable PSE worker and source-fingerprint contract.

## Live structure workflows

`desktop/structure.py` contains Qt-free structure helpers; the controller
checks the main thread before delegation. Browser rows group the exact
object/segi/chain/resi/resn at the requested state, in source coordinate order.
Labels remain strings, including blank chains, insertion codes and negative
residue numbers. Observed one-letter codes do not reconstruct missing sequence.
Selection creation revalidates each frozen object/index/identity rather than
interpolating residue strings into selection grammar. Saved names reject
reserved words, backend rewriting and collisions; native picks are protected.

Focus uses explicit-state orientation, centering and zoom with a finite
nonnegative buffer. Hiding solvent/H/D/T changes representations only. Live
presets freeze selected membership; overview uses polymer traces, sticks and
ion spheres where appropriate, while surface supports nonproteins. A pocket
requires a ligand subset of the chosen context and a finite positive radius;
it includes selected polymer residue sites with an atom-center distance ≤ that
radius, expanding only within the selected requested-state context. Presets
preserve atom colors and unrelated display settings; camera framing is explicit.
Overview/pocket place sphere markers on selected polymer guides and stick
atoms using atom-scoped scale 0.20. Surface does not add markers. These markers
keep isolated/disconnected selected atoms visible without guessing cartoon
connectivity; they do not replace the full-protein cartoon with sticks.

`prepare_structure` creates a new object at state 1 from an explicit source
state. Solvent, optional H/D/T and alternate filters run in that order, with
disjoint removal counts and original retained identities. Primary alternates
keep blank+A labels unchanged. Ions/ligands survive unless explicitly filtered.
Invalid/colliding names, absent coordinates and empty retained sets fail before
creation; failed partial creation is cleaned up. Original coordinates, bonds
and atom properties are preserved. This is an atom filter, without hydrogen
addition, atom repair, ligand optimization or protonation assignment.

Scene update/rename uses native scene records, preserving other bookmarks and
PSE persistence. Result search/sort is a Qt proxy over complete original rows;
CSV/FASTA export uses those original rows and keeps source/state/staleness.

## Display and reproducibility

Representation and coloring are shared between CLI and GUI. Original protein presets remain; explicit representation mode scopes the operation to a validated selection. Cartoon/ribbon require a suitable polymer and CA-only policy is applied only to true CA-only objects. B-factor coloring records its observed range without replacing source B-factors.

Reports add integer `schema_version=2`, finite numeric results, parameter/state records, input SHA-256 and relative artifact names while preserving v0.1 distance/RMSD fields. CLI coordinate PML rebuilds from copied inputs and recorded API operations without MolScene. GUI export preserves arbitrary native edits using the bundled PSE; GUI PML loads that relative session and restores state/view. Keep the complete output folder. Backend/driver/font differences can change pixels, and PSE portability depends on PyMOL compatibility.

Snapshot records separate PSE byte SHA-256 (`snapshot_id`) from semantic `source_fingerprint`, state and calculation time. Fingerprint hashes a canonical finite JSON representation of the chosen state's atom collection. Camera/session-cache changes can alter PSE bytes without changing coordinates. Controller and export share one status validator: `current`, `stale`, or `unverified` when provenance is insufficient. Legacy single-state records still require the current global state to match their recorded state.

Live alignment additionally stores `source_fingerprints`, a map from decimal state strings to semantic hashes, captured after the mobile transform for every distinct mobile/target state. These explicit-state records remain current across display-frame and camera changes, but edits or deletion in either contributing state make them stale. The original `source_fingerprint` and `state` remain the mobile-state fields. Older mixed-state alignment records without a complete state map are `unverified`; their unrecorded target state cannot be positively verified. Stale records remain labelled historical results and are not replayed as current annotations.

## Output protection and scope

Output staging uses a fixed owned-artifact list, explicit overwrite and a final recheck before replacement. Lexical link/reparse checks precede canonical input containment/alias checks, including Windows aliases. Computation failure preserves existing valid outputs. Multiple file replacement is not a crash-atomic directory transaction. Reports use source basenames/hashes and relative artifacts; private paths and credentials do not belong in committed evidence.

Batch sorts nonrecursive local PDB/CIF/mmCIF inputs into numbered safe directories. Input/output roots cannot be ancestors of one another. Persisted progress distinguishes pending/running/completed/failed and cancellation; completed outputs survive cancellation. A failed item makes the CLI fail.

On Windows, publishing a batch summary retries transient access/sharing/lock errors for at most two seconds while rechecking output, input and temporary paths each attempt. An open progress reader can otherwise block atomic replacement on exFAT. Other errors and persistent denial still fail; previous summary bytes remain intact until replacement succeeds.

## Advanced contracts

`advanced.run_advanced` is the shared entry for `timeline_project`, `animation`,
`density` and `potential`; CLI and workers use the same validated requests.
The separate read-only `tools` operation needs no PSE. Its per-tool diagnostics
remain available even if one probe fails. Widgets delegate through the existing
controller/JobManager, with progress JSON and cooperative cancellation markers.
They contain no scientific algorithms and do not probe tools on the Qt thread.

Advanced explicit no-ray and volume-bearing image exports use a fixed-bootstrap
official PyMOL Qt/OpenGL child, supervised through the existing guardian. The
Qt main thread draws/exports; a whole timeline sequence stays in one child with
per-frame progress and normal owned-window close. Structured bounded request/
result/progress data, result identity, successful exit, complete image decoding,
dimensions and hashes are checked before publication. CPU ray remains for other
ray-enabled advanced exports. Reports retain requested/effective ray, actual
renderer/GPU and opacity limitations; OpenGL is not labelled CPU ray tracing.
These renderer modules are ordinary wheel package modules, imported lazily;
the four external-tool configuration names do not change. No geometry,
interpolation, units or assigned radii change with the rendering path.

Figure/current-view export uses the same native renderer when ray is disabled
or the final scene contains a volume. It saves the final scene PSE once before
rendering, retains legacy publication/explicit-overwrite behavior and reports
the actual backend in `analysis.rendering`. Native size limits fail explicitly;
other ray-enabled Figure exports retain the existing CPU branch. Renderer
failure diagnostics remain reachable through the worker's error message.

Grid values use canonical `(i*ny+j)*nz+k` order and physical basis/origin in Å.
Parsed float64 hashes are distinct from native float32 value hashes. Map identity
binds source SHA, field values, full supported native intrinsic geometry,
load/current matrices and state. Physical registration uses the delta relative
to the fresh-load matrix. A session save cannot manufacture a source association.
Aligned-copy replay preserves the full world transform, distinct from the fitted
scientific delta; mode 2 align/super is rejected before copies/native fitting.

Timeline identity binds canonical JSON and the frozen PSE display snapshot.
Potential identity binds original explicit atom membership/state, coordinates
and actual radii; subsequent owned result objects are excluded. Session epoch
also prevents results from a replaced session being applied. Atom-only
fingerprints cannot establish map or display equivalence. Missing evidence is
`unverified`, changed relevant evidence is `stale`.

Advanced bundles use an exact inventory and no-replace directory publication,
with repeated lexical link/reparse/hardlink/ancestry checks. This is separate
from the legacy explicit-overwrite multi-file export described above. Payload
SHA-256 excludes report/manifest to avoid recursive hashes; the manifest hashes
the report and every other payload, excluding itself. Failure evidence retains
bounded logs and failure metadata without copied scientific inputs. Standalone
PML uses public `cmd` plus stdlib and relative files, without importing MolScene
or invoking solvers. Timeline replay embeds its interpolation/seek semantics.

External commands are argument arrays under an owned guardian process. POSIX
uses a new session/process group; Windows assigns the guardian to a kill-on-close
Job Object before spawning tools. Cancellation, timeout and worker death stop
owned descendants; no executable-name killing is used. Actual tool version,
architecture, executable/runtime metadata hashes, argv and logs are retained.
This provenance can include local paths: public example derivatives explicitly
record original manifest hash/redactions and recompute report/manifest hashes,
while preserving the private original. Binary PSE review remains required.

The [advanced guide](ADVANCED.md) specifies LPBE preparation/solver defaults,
units, radius conventions, map interpretations, numerical bounds and all CLI
controls. Actual native/numerical/replay/encoder evidence belongs in
[TESTING](TESTING.md), independent of a feature's implementation.

The release validates selected public API workflows. It does not reproduce commercial PyMOL in full or validate every native command, plugin, trajectory, biological assembly or chemical-interaction workflow. Historical v0.1 observations from a separately installed Incentive build were API evidence only; evaluation-marked exports were never repaired or published as the open-source gallery. Historical native desktop evidence is tied to the exact open-source runtime in [TESTING.md](TESTING.md).

Public implementation references consulted 2026-10-15: [pymol2](https://github.com/schrodinger/pymol-open-source/blob/master/modules/pymol2/__init__.py), [Qt binding wrapper](https://github.com/schrodinger/pymol-open-source/blob/master/modules/pymol/Qt/__init__.py), [native Qt GUI](https://github.com/schrodinger/pymol-open-source/blob/master/modules/pmg_qt/pymol_qt_gui.py), [Qt Essentials package details](https://doc.qt.io/qtforpython-6.8/package_details.html). Upstream branch links can change; they do not identify a commercial binary's complete source.
