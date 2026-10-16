# MolScene v0.5 daily workflows and compact desktop

## Intent and authority

Continue the verified v0.4.0 release (`603e588`) by closing common workflow gaps
against publicly documented PyMOL capabilities. The user delegates prioritization,
implementation, testing and GitHub publication without further approval checkpoints.
New local source, dependencies, temporary files, caches, examples and evidence stay
on F:. Existing runtimes on other drives may be read/run; do not change system or
user profiles, registry, global PATH, or the established working environment.

This is original MolScene workflow code using public APIs. Native commands already
available in the host are distinguished from missing MolScene integration. Current
official documentation and public source are comparison evidence, not proof that
every command is available in the tested community backend. No numerical parity
percentage or complete commercial-equivalence claim is made.

## Builder choices

The following scope and implementation choices are engineering decisions, rather
than preferences attributed to the user. Keep Python >=3.10, the existing public
PyMOL API, Qt5/Qt6 compatibility, lazy backend imports and no new runtime dependencies.
Target release 0.5.0. Preserve the five existing pages and indices: Structure,
Figure, Analysis, Results, Advanced. Preserve v0.2/v0.3/v0.4 workflows and examples.

Deliver three integrated daily workflows and a compact interaction layer. Defer
RCSB downloading, biological assembly, CE alignment, group editing, trajectory
statistics, chemistry repair and commercial-only interactions/reflection import.
Existing raw label expressions remain available as advanced controls.

## 1. PyMOL-typed polar-contact candidates

Add a distinct `polar` operation alongside distance-only `contacts`; never change
the meaning of existing geometric contact results. It accepts explicit selections
A/B and one positive coordinate state, default distance cutoff 3.5 Å, native
orientation angle 45 degrees, primary altloc policy (blank+A), and a 100,000-pair
maximum. Angle must be finite and within 0..90; cutoff finite and within 0.1..10 Å.
An explicit `all` altloc option includes all conformers and is reported.

Freeze requested-state atom membership before applying donor/acceptor predicates.
Use native `donors` and `acceptors`, exclude H/D/T endpoints, query donor-to-acceptor
orientation with public `find_pairs(mode=1)` in both A/B directions, and retain
original object/index/residue/alt/state identities. Native orientation alone does
not check chemical types. Reject absent states instead of singleton fallback.
The native method and its actual cutoff/angle conventions must be recorded.
The tested backend uses a strict distance cutoff (`distance < cutoff`), not an
inclusive boundary. State that convention explicitly and test the boundary.
Native orientation is a permissive coarse tolerance: either endpoint can supply
favorable local orientation. It is not a measured D-H-A angle, and 45 degrees
must not be relabelled as D-H-A >=135. Test a controlled acceptor rotation of
44/46 degrees at native tolerances 45/60, and both endpoints unfavorably oriented.

Return unique directed donor->acceptor pairs, preserving reciprocal directions
when both atoms have both native roles. Deduplicate overlaps, exclude self and
directly covalently bonded endpoints, and sort deterministically by distance and
identity. Empty typed endpoints are a valid zero-row result; empty original A/B
selections fail. Bound the geometric candidate set before a potentially large
native pair query; exceeding the declared cap fails rather than truncates.
For `all`, retain all endpoint conformers but exclude pairs with different
nonblank alt labels within the same original object. This is a declared
conservative same-label convention, not proof of correlated conformers. Do not
infer cross-object alt-label correlation; report that limitation explicitly.
The preflight cap counts unique undirected non-self pairs between typed heavy
endpoints of A/B with distance <= cutoff, before orientation and direct-bond
exclusion; directly bonded pairs count toward this conservative resource cap.
The same declared maximum separately caps final unique directed output pairs.

Each pair records donor, acceptor, actual donor-acceptor distance and A/B membership.
Report backend version, native-role counts, filtering, cutoff, angle, state,
altloc, candidate/output caps and interpretation limitations. These are candidates
dependent on existing topology/typing, not confirmed hydrogen bonds, protonation,
binding energy or affinity. Do not add hydrogens or change source valence/charges.

Provide CLI `polar`, asynchronous GUI analysis using A/B and the current state,
complete JSON/CSV, bounded visual annotation (first 200 pairs with the cap recorded),
scene/replay outputs and freshness checks. Saved replay uses frozen rows and public
PyMOL calls, independent of MolScene and analysis recalculation. Live annotation
rejects stale results. Coordinate/typing changes relevant to this result must make
its freshness status stale; camera movement alone must not change chemistry.
In addition to coordinate-state fingerprints, bind a chemistry fingerprint of
native donor/acceptor memberships, formal charges and molecular bond topology
(including bonded neighbors influencing orientation). A same-coordinate bond or
typing edit must invalidate a previously calculated polar result.

Validate with actual bonded donor/hydrogen and carbonyl acceptor fragments, a
nonpolar distance-only negative, wrong orientation, out-of-cutoff geometry,
direct covalent exclusion, reciprocal/overlapping selections, duplicate indices
across objects, alternate conformers, state-dependent membership, invalid inputs,
pair-cap failure, source preservation and real worker/CLI/replay parity. Pure
distance and known fragment geometry supply independent expectations; do not use
the implementation itself as the expected-value generator.

## 2. Guided labels

Add label recipes `atom`, `residue`, `chain_residue` and `custom`. Atom recipe
labels every selected atom. Residue recipes choose exactly one selected anchor
per observed residue: selected CA, else selected P, else lowest-index selected
heavy atom, else lowest-index selected atom. Group by full original object,
segment, chain, residue string and residue name; preserve insertions and blank
identifiers. Do not expand outside the selected requested-state membership.
Literal recipe text is exact: `atom` uses the atom name; `residue` concatenates
residue name and the full residue string; `chain_residue` uses
`<chain-or-(blank)>:<residue-name><full-residue-string>`. Object and segment remain
part of anchor identity even when their text is not printed in the label.
Prefer blank/A altloc anchors deterministically. Recipe labels are literal data,
never executable expressions assembled from residue strings. Custom remains the
existing explicitly chosen native expression behavior.

Preview returns exact label targets, text and count without changing the scene.
For `custom`, preview returns targets/count and the unevaluated native expression;
it does not execute that expression. Applying custom retains native expression
execution semantics rather than promising a safe literal-template evaluation.
Applying accepts size 8..48 points, color and a maximum of 500 labels; validate
everything before mutation. Label color must resolve through the native color
API. Style applies only to selected label targets. Clear affects the explicit
selected membership. Retain raw expressions and existing `set_labels` compatibility.
Label mutation intentionally changes labels/style, preserving coordinates, bonds,
charges, B-factors, camera, current frame and unrelated labels/settings.

Support GUI preview/apply/clear plus readable controls. Tests cover CA/P/fallback,
hetero residues, partial selection, insertion/blank-chain/segment/alt identities,
literal unusual text, absent states, limits, no-change preview/validation failure,
unrelated label preservation and PSE persistence.

## 3. Coordinate and full-session persistence without rendering

Add new-directory-only closed result bundles using the existing guarded Bundle
publication contract. No PNG, ray or OpenGL is required for these saves. Do not
weaken link/reparse/hardlink/alias, protected-input, no-replace or cancellation
guards. Failure/cancellation preserves originals and previous valid outputs.

Coordinate export uses an explicit selected positive state and format `cif` or
`pdb`. Default cif. Export one file per original molecular object to numbered
paths (`objects/0001.cif`, etc.), preserving source object mapping in report JSON
and complete atom membership CSV. Never use arbitrary object names as filenames.
Write selected world-space coordinates deliberately; do not bake transforms into
the original objects. Export at most 200,000 atoms and 1,000 objects; fail before
writing output if exceeded.

Reload files in an independent headless backend and verify atom count, elements,
atom/residue/chain/alt identities supported by the format and world coordinates
within 0.001 Å. Native writer precision, unsupported identity fields and known
format losses must be explicit. Fail on unsupported/lossy core identity instead
of silently publishing a nominally successful file. Source object/index and
format-excluded fields remain in the membership CSV. PDB fixed-width limits are
validated before writing; mmCIF is preferred for wider identifiers. Native writer
behavior is checked against the actual backend, not assumed from the file extension.
Core reload identity is exactly element, atom name, residue name, full residue
string (including insertion), chain, segment and alternate-conformer identifier.
Object/index identity is carried by numbered-file mapping and membership CSV,
not by assuming reloaded atom indices match the original. Coordinates are
compared by Euclidean error <=0.001 Å. Native B-factor/occupancy precision and
format-excluded charges/radii/bonds are reported limitations, not a chemistry or
complete topology round-trip promise. PDB core text fields must be ASCII and
fit their actual widths; reject rather than truncate. mmCIF must also pass the
same core identity check, including segment IDs, on the actual writer/reader.

A session save captures the complete native PSE snapshot (all states, display,
objects, maps, scenes and labels), saves `session.pse` plus report/manifest/replay,
and preserves the captured PSE bytes. It does not claim that a coordinate file
preserves session styling or that a frozen save verifies later live scene state.
Persistence results are labelled `Saved snapshot` in Results and cannot be
annotated as a live scientific analysis. Opening a saved PSE remains an explicit
normal session-open action; saving does not replace or reorient the live session.

Provide CLI `coordinates INPUT --selection ... --state ... --format cif|pdb
--output NEW_DIRECTORY` for local coordinate/session inputs, and
`session-save SESSION.pse --output NEW_DIRECTORY`. GUI uses asynchronous frozen
snapshot jobs with progress/cancel and explicit selection/format/output controls.
Normal import/help/validation still work without PyMOL/Qt.

Tests cover transformed objects, different requested states, multiple objects
sharing residue identifiers, alternate/insertion/blank identifiers, unsupported
PDB widths, precision/reload, complete source mapping, preserved source/camera,
saved session content, Unicode/apostrophe paths, standalone relocated replay,
pre-cancel/mid-cancel, prior outputs, source ancestry and supported link mechanisms.

## 4. Compact desktop interaction

At narrow dock widths (360..540 logical pixels), provide a synchronized page
selector showing complete page names, and hide the overflowing tab bar. The five
QTabWidget pages and indices remain intact. Wider docks can show the normal tab
bar. Changing either navigation control updates the other. Job mutation locks
remain effective; Results and Cancel remain reachable and disabled pages cannot
be reached through an alternate selector. Preserve preview shutdown on close.

Add browse buttons beside map, PQR, DX, tools JSON and timeline input/output
paths while retaining original QLineEdit object names and request semantics.
Dialog cancel leaves values unchanged. New-output browse chooses a prospective
uncreated leaf directory, never reinterprets an existing directory as permission
to overwrite. Input mode changes hide the entire relevant label/field/button row.

Tools check displays a concise status line per configured tool including version
and actual architecture. Full argv, paths, package/runtime identities and errors
remain available in a clearly labelled collapsed details area and tooltips;
do not repeat long argv in the summary. Density summaries retain dimensions,
origin, units and raw-level semantics; full matrices/hashes go in expandable
details. Warnings, failure and scientific units remain visible.

Integrate polar analysis, label recipes and persistence actions into the existing
pages. Do not add a sixth main tab. Preserve current result sorting/search/full
CSV behavior. User-facing labels describe the workflow, not internal worker or
schema implementation unless needed for an actual decision.

## Evidence and release contract

Update FEATURE-GAPS with assessed v0.4 baseline, v0.5 additions, remaining limits,
dated primary sources and native-versus-integrated distinctions. Update English/
Chinese README, GUI/INTERNALS/TESTING and a real v0.5 example generator. Preserve
all previous gallery bytes; new examples use separate v0.5 outputs and source
attribution. Publish actual source, installed wheel and extracted-sdist evidence;
never count a dependency skip as backend/native acceptance.

Keep all existing twelve v0.4 required native/science/replay/encoder identities
and eight Linux protection identities. Add one exact required native daily gate:
`test_daily_gui.NativeDailyTests.test_native_daily_polar_labels_exports_and_narrow_navigation`.
It must exercise real widgets at a narrow dock, polar rows/annotation, literal
recipe labels, coordinate/session save+reload, job lock/cancel, file chooser
cancellation and normal close with owned process cleanup. Actual framebuffer
evidence must show meaningful content and usable controls, not just object names.

Before publishing v0.5.0 require independent task and whole-branch reviews,
frozen source full backend+plain runs, isolated installed-wheel native/science
workflows, fresh source archive import/examples/replay, original and new examples,
image/session privacy/appearance review, exact-final-commit five-job GitHub CI,
and all 21 required Linux identities actually passing. Preserve exact versions,
hashes, commands, failures and skip reasons. Build release wheel/sdist/examples,
source and publication evidence with no self-hash cycle. Verify server digests
and full downloaded asset bytes before publishing. Upgrade F desktop/launcher
only from the verified wheel; preserve old versions and workspaces.

## Primary comparison sources (checked 2026-10-16)

- https://learn.schrodinger.com/public/pymol/current/Content/pymol/Features/content_panel.htm
- https://pymol.org/pymol-command-ref.html
- https://github.com/schrodinger/pymol-open-source/blob/master/modules/pymol/querying.py
- https://github.com/schrodinger/pymol-open-source/blob/master/modules/pymol/viewing.py
- https://github.com/schrodinger/pymol-open-source/blob/master/modules/pymol/exporting.py
- https://github.com/schrodinger/pymol-open-source/blob/master/modules/pymol/importing.py

Current public source marks pi interactions and MTZ loading as Incentive-only;
these are deferred. No dependency on those APIs is introduced. The public
`find_pairs` description explicitly warns that orientation mode omits atom-type
checks; native donor/acceptor filtering is part of the MolScene contract.
