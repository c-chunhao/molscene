# MolScene v0.3 daily workflow design

The user requested an evidence-based comparison with commercial PyMOL, common
feature completion, a better graphical interface, real tests and a GitHub
update. All task files must remain on F:. The user explicitly delegated design,
implementation, testing and publication without further item-by-item approval.
Continue the selected subagent implementation and independent review method.

## Approach and scope

Three approaches were considered: wrap additional raw PyMOL commands; replace
the native application with a separate viewer; or improve the daily workflow
on the validated native window and snapshot worker architecture. Choose the
third: it retains genuine molecular interaction and focuses original code on
discoverability, exact selection and reproducible analysis. The first leaves
the current usability gap; the second introduces unnecessary rendering work.

The comparison and priorities are in `docs/FEATURE-GAPS.md`. This release adds
observed-residue navigation and selection handoff, live scoped presets, clean
copies, scene update/rename, interface SASA analysis, and searchable results.
It does not implement a movie timeline, density registration, protonation,
chemical interaction classification or proprietary components.

## Global constraints

- Python >=3.10; backend and Qt remain optional and lazily imported.
- Original MIT workflow code only; no proprietary code, binaries, assets or license material.
- Existing v0.1/v0.2 command defaults, report schema_version 2 and replay contracts remain compatible.
- Every scientific selection uses an explicit positive state; absent states fail without singleton fallback.
- All new local files, runtimes, caches, temporary data, worktrees and evidence stay on F:.
- GUI native Qt5/PyQt5 and Qt6/PySide6 remain supported; no native assertion or dependency gate is weakened.
- Original coordinates, atom identities, B-factors and unrelated object styles remain unchanged by navigation, presets and analyses.
- Scientific results retain source fingerprints; stale data cannot silently become current; filtered previews never truncate exported tables.

## Scientific interface analysis

Add `analysis.interface(cmd, selection_a, selection_b, state=1, altloc='primary')`.
Filter each partner exactly as existing SASA: non-solvent heavy atoms, blank+A
for primary or all alternates on explicit request. Reject empty partners and
overlap of retained original atom identities. Context is exactly A union B,
including across objects, never implicit byobject outsiders.

Calculate SASA(A in A), SASA(B in B), SASA(A in A+B), SASA(B in A+B) using the
existing disposable merged-model method. Report partner isolated and complex
areas, partner buried differences, `total_buried_area_angstrom2` as their sum,
and `interface_area_angstrom2` as half that sum. Preserve signed raw numerical
differences rather than silently clipping them. Residue rows have partner A/B,
object/segi/chain/resi/resn/state, atom count, isolated/complex/buried areas.
Record probe=1.4 A, dot_density=3, filtering/counts, state and definition.
Do not equate the area with affinity, energy or chemically assigned contacts.

Use independent equal-sphere oracles: carbon vdW 1.7 plus probe 1.4 gives
R=3.1; isolated area 4*pi*R^2, at separation d=3 the buried area per partner is
2*pi*R*(R-d/2), tolerance 3%; d=20 gives zero loss. Test partner symmetry,
per-residue sums, overlap, filtering, requested states, unchanged source and
temporary cleanup. Existing SASA semantics must not change.

Expose `engine.interface` and `molscene interface` with A/B selections, state,
altloc and standard image options. Output scene PNG/PSE/PML, report JSON,
complete `interface.csv` and copied input. Replay uses public API and copied
coordinates without importing MolScene. Color retained partners distinctly,
show polymer/cartoon or nonpolymer sticks, and highlight buried residue sites.
Add an independent worker operation with the existing PSE snapshot/provenance
and a GUI action using Analysis target as A and contact partner as B.

## Exact navigation and live workflows

Use a new Qt-independent `desktop/structure.py` for focused structure helpers;
controller methods delegate to it and continue enforcing the main thread.
Observed residue rows group exact object/segment/chain/resi/resn at the chosen
state, retain original atom identities, polymer classification and observed
one-letter code where supported. Keep residue labels as strings, including
insertion codes, negative values, blank and multi-character chains. Display
coordinate order; do not infer a complete biological sequence.

Creating a browser selection must revalidate exact original membership and
identity at the requested state. Use original object/index selection binding
or select_list, never interpolate arbitrary chain/segment/residue strings into
the selection language. Preview shows atom/residue/state counts. Users can use
selected objects, selected browser residues, current native `sele` or an
explicit named selection; send the active selection to analysis target/A or B.
Named save rejects collisions and invalid/reserved names; delete only explicitly
chosen existing selections. No replacement of native object names or picks.

Provide focus/center with finite nonnegative zoom buffer and explicit state,
plus hiding solvent/hydrogen representations within active selection. Do not
delete atoms to simplify a view. Live presets: `overview`, `surface`, `pocket`.
Validate all parameters before changing a live scene. Scope all display changes
to frozen selected membership. Overview shows polymer traces where meaningful,
nonpolymers as sticks and ions appropriately; surface works for nonproteins too.
Pocket requires an explicit nonempty ligand subset of the selected context,
finite positive radius and at least one nearby selected polymer residue; all
nearby expansion is restricted to selected context and requested-state atoms.
Return counts/definition for the actual applied result. Camera framing is an
explicit option. Do not alter unrelated object representations/colors/settings.

`prepare_structure` creates a uniquely requested new molecular object from the
selected state, with target_state=1. Options remove solvent, optionally remove
H/D/T and keep blank+A or all alternates. Preserve ions/ligands unless filtered
explicitly. Reject empty results and colliding/invalid names before mutation;
clean partial destination on backend failure. Report before/retained/removed
counts and original retained atom identities. Preserve original all-state
coordinates, atoms, bonds and metadata. No automatic hydrogen addition or pH.

Add explicit scene update and rename, preserving other scenes; save/reopen PSE
must retain the updated names and the expected camera/display/state contents.

## Interface layout

Keep the four existing tabs and stable tested widget identities. Add compact
header/active-selection summary, meaningful groups and tooltips, and responsive
forms. A separate `desktop/navigation_panel.py` owns browser/selection widgets
and calls controller methods through the panel's error handling. Use a residue
table with object/chain filters, text search and polymer-only default; show all
residue kinds on request. Load rows on explicit navigation refresh and state or
scope change; do not rescan all atoms every 1.5-second object refresh.

Group common presets and focus controls near the active selection; advanced
appearance stays available. Integrate preparation options and scene editing.
Result table filtering/sorting uses a Qt proxy; show visible/total counts and
export complete original rows. Do not hide scientific source/state status.
Preserve job serialization, cooperative cancellation, disabled mutation controls
while jobs run, errors/recovery and orderly native close.

## Verification and release

Start from a verified v0.2 baseline. Focused real-PyMOL tests accompany each
task; independently review each task and the whole authored branch. Run one
final full suite after code integration, plus plain-Python lazy import/CLI,
offline selftest, final installed-wheel isolated native acceptance and sdist
parity. Native coverage must exercise actual browser selection/handoff, preset,
scene edit, cleaning, worker interface result, export, rotation/zoom and close.
Use actual Qt5 and Qt6 evidence (Windows local plus required Linux GitHub CI).

Publish v0.3.0 only after exact public commit CI succeeds. Provide wheel, sdist,
example ZIP, SHA256SUMS and publication evidence; verify server hashes and
download bytes. Update bilingual README, GUI/analysis/testing reports, the gap
matrix, original examples and real screenshots. Keep all private evidence and
earlier versions on F:. Install the final verified wheel into the existing F
desktop environment used by the user launcher.
