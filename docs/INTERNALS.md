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

## Display and reproducibility

Representation and coloring are shared between CLI and GUI. Original protein presets remain; explicit representation mode scopes the operation to a validated selection. Cartoon/ribbon require a suitable polymer and CA-only policy is applied only to true CA-only objects. B-factor coloring records its observed range without replacing source B-factors.

Reports add integer `schema_version=2`, finite numeric results, parameter/state records, input SHA-256 and relative artifact names while preserving v0.1 distance/RMSD fields. CLI coordinate PML rebuilds from copied inputs and recorded API operations without MolScene. GUI export preserves arbitrary native edits using the bundled PSE; GUI PML loads that relative session and restores state/view. Keep the complete output folder. Backend/driver/font differences can change pixels, and PSE portability depends on PyMOL compatibility.

Snapshot records separate PSE byte SHA-256 (`snapshot_id`) from semantic `source_fingerprint`, state and calculation time. Fingerprint hashes a canonical finite JSON representation of the chosen state's atom collection. Camera/session-cache changes can alter PSE bytes without changing coordinates. Controller and export share one status validator: `current`, `stale`, or `unverified` when provenance is insufficient. Legacy single-state records still require the current global state to match their recorded state.

Live alignment additionally stores `source_fingerprints`, a map from decimal state strings to semantic hashes, captured after the mobile transform for every distinct mobile/target state. These explicit-state records remain current across display-frame and camera changes, but edits or deletion in either contributing state make them stale. The original `source_fingerprint` and `state` remain the mobile-state fields. Older mixed-state alignment records without a complete state map are `unverified`; their unrecorded target state cannot be positively verified. Stale records remain labelled historical results and are not replayed as current annotations.

## Output protection and scope

Output staging uses a fixed owned-artifact list, explicit overwrite and a final recheck before replacement. Lexical link/reparse checks precede canonical input containment/alias checks, including Windows aliases. Computation failure preserves existing valid outputs. Multiple file replacement is not a crash-atomic directory transaction. Reports use source basenames/hashes and relative artifacts; private paths and credentials do not belong in committed evidence.

Batch sorts nonrecursive local PDB/CIF/mmCIF inputs into numbered safe directories. Input/output roots cannot be ancestors of one another. Persisted progress distinguishes pending/running/completed/failed and cancellation; completed outputs survive cancellation. A failed item makes the CLI fail.

On Windows, publishing a batch summary retries transient access/sharing/lock errors for at most two seconds while rechecking output, input and temporary paths each attempt. An open progress reader can otherwise block atomic replacement on exFAT. Other errors and persistent denial still fail; previous summary bytes remain intact until replacement succeeds.

The release validates these selected public API workflows. It does not reproduce commercial PyMOL in full or validate every native command, plugin, trajectory, density-map, electrostatics, biological assembly or chemical-interaction workflow. Historical v0.1 observations from a separately installed Incentive build were API evidence only; evaluation-marked exports were never repaired or published as the open-source gallery. v0.2 native desktop evidence is tied to the exact open-source runtime in [TESTING.md](TESTING.md).

Public implementation references consulted 2026-10-15: [pymol2](https://github.com/schrodinger/pymol-open-source/blob/master/modules/pymol2/__init__.py), [Qt binding wrapper](https://github.com/schrodinger/pymol-open-source/blob/master/modules/pymol/Qt/__init__.py), [native Qt GUI](https://github.com/schrodinger/pymol-open-source/blob/master/modules/pmg_qt/pymol_qt_gui.py), [Qt Essentials package details](https://doc.qt.io/qtforpython-6.8/package_details.html). Upstream branch links can change; they do not identify a commercial binary's complete source.
