# MolScene v0.4: timeline, density registration and electrostatic potential

Date: 2026-10-16. Baseline: published v0.3.0 (`56ff6bc`).

## Intent and authorization

Extend the existing native PyMOL workbench with three usable scientific and
presentation workflows: animation, density-map registration and electrostatic
potential. Integrate them into GUI and reproducible CLI/worker exports, test
them with real backends and update GitHub. The user explicitly delegates
engineering decisions and subsequent publication without item confirmations.
The choices below are engineering decisions, rather than additional user
requirements. All new task files, worktrees, dependencies, temporary files,
caches, packages and evidence remain on F:. Preserve earlier versions/evidence.

## Architecture and compatibility

Keep Python >=3.10 and the existing native PyMOL/Qt dock. PyMOL and Qt remain
lazy optional dependencies; parsing, help, validation and pure numerical tests
work without them. Support actual Windows Qt6 and Linux Qt5. External APBS,
PDB2PQR and optional FFmpeg run as independent, explicitly configured tools;
their binaries are not redistributed in MolScene packages. No commercial
software copying or license changes are involved.

Retain old API defaults, schema-2 reports, four existing tab indexes/widget
identities and all four required native test gates. Add an Advanced workspace
after those tabs, with compact Timeline, Density and Potential sections.
Scientific helpers stay Qt-free, thin controller wrappers use the main-thread
guard, long exports/parameterization/solves run in the existing independent
worker model, and widgets use the active `pymol.Qt` binding.

Use separate focused modules for regular grids, density handling, timelines,
animation export, external process lifetime, electrostatic computation and
new output bundles. Avoid restructuring legacy render/analysis behavior.

Advanced OpenGL image export uses a dedicated supervised official PyMOL Qt
child. Explicit no-ray exports and volume-bearing scenes require a valid
graphics context: ordinary headless CPU ray rendering can omit volume objects.
Report requested versus effective render settings and actual GPU/opacity
behavior. Preserve the CPU ray path for other ray-enabled advanced exports.
Validate decoded image data and prove visible-volume content against a hidden
control in native acceptance; PNG headers and object existence are insufficient.
Reuse the existing guardian for cancellation, timeout and worker-death cleanup.
One child renders an entire animation sequence with per-frame progress and
closes its owned native window normally before publication.

## Shared grids and output bundles

Define regular scalar grid geometry explicitly: shape `(nx,ny,nz)`, physical
origin `o`, basis/delta vectors and values with flat index `(i*ny+j)*nz+k`.
Physical position is `o+i*b0+j*b1+k*b2`, in angstrom. Semantic value units and
normalization are separate metadata. At least two grid points per axis are
required for a contourable map. Bound bytes/voxel count before allocating.

OpenDX support initially means ASCII rank-0 regular scalar data with matching
gridpositions/gridconnections, exact item count and finite values. Accept
strictly positive diagonal spacings. Reject binary/external/vector/irregular,
ambiguous/multiple arrays, negative/zero/offdiagonal deltas, mismatched counts,
truncated/excess/nonfinite data before loading or altering the live scene.
Do not infer electrostatic units from a `.dx` extension.

New advanced exports target a new or empty directory. They do not overwrite a
nonempty destination. This deliberate initial policy keeps prior outputs and
unrelated files intact without weakening the legacy overwrite implementation.
Validate lexical paths, links/reparse points, types, hardlink/source aliases
and input/job/envelope ancestry; stage complete bundles beside the destination
and publish the complete directory only after all required artifacts validate.
An empty destination may be removed only with nonrecursive empty-directory
removal after repeated checks. A race/cancellation must never replace a newly
nonempty directory. Each operation has a closed artifact inventory; animation
frames are an exact bounded declared set, not a recursive directory whitelist.

Reports record source SHA256, actual backend/tool versions and parameters,
geometry/units, method limitations, artifact hashes and snapshot identity.
Full tool logs remain part of the local bundle; public examples use synthetic
or existing public coordinates and exclude private paths. Replay uses stored
relative inputs and public PyMOL calls without importing MolScene or rerunning
external solvers. No generated text normalization changes pinned coordinates.

## Animation timeline

Provide capture/add/update/remove/reorder keyframes, editable frame positions,
FPS, seek slider, frame stepping, play/pause and a separate timeline cursor.
Capture an exact 18-number view, explicit positive molecular state and an
optional existing named scene. Validate finite values, proper orthonormal
rotation, clipping order, unique bounded frames, states and scene availability
before preview/export. JSON persistence is a versioned plain-data timeline
sidecar; it is not executable movie commands or an arbitrary script editor.

Creating a timeline captures one uniquely named managed baseline display scene.
The versioned document requires `baseline_scene`. A keyframe without a scene
inherits the nearest preceding explicit scene, or that baseline for the first
segment. Resolve this from document order on every seek; always recall the
resolved display scene, including backward/random seek. The GUI labels this
choice "Keep previous display". Renaming a referenced scene updates references;
deleting a referenced scene is refused. Validate externally removed scenes at
the next seek/export. Save/open a timeline project as a new-directory bundle
containing the PSE snapshot and JSON; loading JSON alone in a live session
requires all referenced scenes/states and is not a self-contained project.

Offer hold and smooth-camera interpolation. Smooth interpolation uses shortest
arc normalized quaternion SLERP, with stable near-equal handling; camera
translation/origin/clipping interpolate linearly. Endpoints remain exact.
Projection modes must agree across a smooth interval; do not interpolate the
projection flag. Named display scenes and molecular states change discretely
at the keyframe boundary. Camera animation does not interpolate atoms or claim
a molecular dynamics trajectory.

Live preview stops an actively running native movie, recalls the resolved scene
display with `view=0, frame=0, animate=0`, sets molecular `state` explicitly,
then applies the sampled view. It does not install `mset/mview/mdo`, replace a
pre-existing movie definition, mutate coordinates/maps or treat `cmd.frame(n)`
as molecular state n. Existing state navigation must retain its intended
state semantics when a native movie already exists. Pause/stop leaves the
current presented view; seek-to-first is explicit. Stop timers when a worker
starts, the dock closes, a session is replaced or the native window closes.

Export an immutable source PSE, timeline JSON, complete numbered PNG sequence,
standalone replay PML and schema-2 report. A source PSE is labelled a source
snapshot, not falsely advertised as a compiled native movie. A self-contained
replay loads that snapshot and offers the same deterministic seek/play path
using public PyMOL APIs and stored plain data. It must not execute arbitrary
scene names as command-language fragments.

MP4 is explicit and optional: detect a usable FFmpeg H.264 encoder, validate
even output dimensions/codec settings before rendering and record actual
encoder/version/FPS/frame count. Fail an explicitly requested unavailable MP4;
do not silently omit it. PNG sequence export always works without FFmpeg.
Render in the worker with per-frame progress and cooperative cancellation;
cancelled/failed exports never publish a completed bundle or orphan an encoder.

## Density maps and rigid registration

Support validated scalar OpenDX and MRC/CCP4 float32 mode 2. Parse dimensions,
endianness, starts, full-cell sampling, cell lengths/angles, axis permutation,
origin and extended-header offset before loading. Support all six valid axis
permutations, partial maps and nonsingular nonorthogonal cells. Reject unknown
encodings/header skew and ambiguous simultaneous nonzero MRC origin/start
interpretations unless an explicit supported interpretation is selected.
Record actual `mrc` versus `ccp4` interpretation. Keep raw values by temporarily
setting `normalize_ccp4_maps=0` for load, restoring its exact prior value even
on failure. Raw values are not automatically sigma or electron density units.

Expose managed map inventory, source/geometry/statistics, mesh/isosurface and
volume display, level/color/opacity and visibility of owned derived objects.
Use explicit source state and derived state 1; no state=0 fallback. Volume has
a transfer ramp rather than an isolevel; demonstrate real native volume
appearance separately from mere object creation. Existing molecular object
controls remain molecule-only. Imported session maps without validated source
association may be displayed but cannot claim verified registration/provenance.

Registration creates a named map copy, preserving source values, matrices,
representations and associated molecular originals. Accept a finite proper
rigid world transform `D=[R,t;0,1]` (rotation determinant +1, no scale/shear or
reflection), source world to registered world, translation in angstrom.
Provide a numeric matrix path and a convenient new aligned-model-copy path.
For the latter align an explicit mobile-state copy to an explicit reference
state, capture before/after matrices and verify the derived world delta on
all copy atoms; never infer a delta from an old RMSD-only alignment record.

Map intrinsic geometry matrix `B` must survive registration. For a controlled
map with no object TTT, PyMOL postmultiplies its state matrix; therefore apply
`T=inverse(B) @ D @ B` with explicit-state `transform_object`, and verify final
matrix equals `D @ B`. Tests must contain nonzero MRC origin and rotation plus
translation; identity-origin tests cannot establish this contract. Reject
unsupported pre-existing TTT/movie transformations before changing anything.

Report source/copy names, source files/hashes, states, native geometry, world
matrix convention, source-to-copy transform, alignment parameters/statistics
where used and bounds diagnostics. Registration is coordinate-frame mapping;
it does not optimize map correlation, fit/refine a model against density or
infer experimental quality. Export a replayable registered map/model bundle
with originals and explicit matrices. Derived raw map values remain unchanged.

## Electrostatic potential

Provide prepared-PQR -> APBS solve, explicit protein selection -> PDB2PQR ->
APBS solve, and molecule/PQR plus imported potential-DX display. Never use
vacuum Coulomb coloring as an unlabelled fallback for a missing PB solver.
An independent current APBS 3.4.1/PDB2PQR 3.7.1 runtime is installed on F for
actual local acceptance; external Python may differ from desktop Python.
Record executable/package hashes, version, architecture and required child
runtime paths. No system-wide PATH, registry, HOME or USERPROFILE changes.

PQR coordinates/radii are angstrom and charge is elementary-charge units.
Validate finite coordinates/charges, nonnegative radii, unique atom identity
and valid optional-chain records; require at least one positive radius for a
molecular-surface workflow. PARSE legitimately uses zero-radius hydrogens,
including some charged centers: retain their charges and explicitly report
zero-radius sites rather than treating zero alone as a missing assignment.
Preparation validates heavy-atom retention/radii against the original chemical
identities and its force-field diagnostics. Zero individual or total charge is
valid. Do not silently discard selected ligand/metal/unknown residues or accept
unparameterized atoms. Protein preparation exposes force field, explicit pH
and protonation method; preserve original coordinates/file/session and report
retained heavy atoms, added/repaired atoms, exclusions, warnings and failures.
User-supplied PQR bypasses parameterization and is labelled accordingly.

Initial PB solver is the linearized equation (LPBE), with explicit temperature,
solute/solvent dielectric, monovalent salt concentration and radii, charge and
surface discretization, boundary condition, coarse/fine domain and valid grid
counts. Defaults are 298.15 K, dielectrics 2/78.5, symmetric +/-1 salt 0.15 M,
ion radius 2 A and solvent probe 1.4 A. These are stated model assumptions.
Default 65 points per axis is a preview-quality solve; optional valid finer
counts must be bounded by the selected tool architecture/memory. Report actual
DX spacing/counts and convergence diagnostics; do not silently coarsen or
describe LPBE as nonlinear. NPBE/general custom ions are deferred.

APBS potential units are kBT/e. V and mV conversion uses exact kB/e constants
and an explicit temperature. Imported maps require declared units (including
explicit unknown); no solver/protonation provenance is invented. Keep original
map bytes; any converted display map is a separately recorded artifact. Use
an explicit symmetric red/white/blue ramp with numeric limits, unit and clipping
information, not an uncalibrated red/blue image.

Display a new owned prepared molecular copy with PQR `elec_radius` transferred
to its display `vdw` radii. Specify molecular surface, solvent probe and
`surface_ramp_above_mode=0`; preserve original atom properties/settings. Check
map coverage of the displayed molecule/radii and reject unsupported geometry.
Report the display convention. Potential is not binding affinity/free energy.

For ordinary molecule plus imported DX, copy the explicitly selected state and
its existing per-atom PyMOL `vdw` radii into an owned display object. Validate and
report that actual radius model as "existing molecule vdw radii"; it carries no
PQR, protonation or charge-assignment claim. PQR and computed paths instead use
PQR radii. Both paths validate finite nonnegative radii with a positive-radius
surface, use the stated probe convention and check coverage with the same
radii actually used for display. Imported units and radius provenance remain
visible in GUI and reports.

Run tools with argv arrays, shell=False, hidden Windows windows and fixed safe
staged basenames. Each tool gets its own child environment, bounded logs and
monotonic timeout. Track an owned process group (POSIX) or kill-on-close Job
Object (Windows) so timeout, cooperative cancellation and forced worker exit
also terminate descendants. Do not kill by executable name. Check cancellation
before stages/commit; no process or completed output remains after cancellation.

GUI exposes tool availability/settings, preparation/solver parameters, charge
and grid diagnostics, compute/import, ramp controls and loading a result copy.
Computations use an immutable snapshot; stale source status blocks automatic
application. Loading a result never replaces the complete native session.
Persist provenance in exported sidecars/bundles; PSE preserves native objects
and visuals, while unassociated session maps remain explicitly unverified.

## Verification and release

Start with a fresh v0.3 baseline. Pure tests cover malformed/large/nonfinite
grids and numerical geometry; independent asymmetric-grid and analytic
rotation/SLERP oracles prevent self-comparison. Actual PyMOL tests cover map
axis/origin/cell interpretation, transform composition, contour geometry,
source/state preservation, PSE/replay and relocated punctuation paths.

Actual APBS tests use homogeneous dielectric zero-salt Coulomb limit as a PB
oracle, charge sign/dielectric/translation/convergence checks and an official
Born-ion dielectric-boundary example. These are validation oracles, not a
replacement solver. Parameterize public ubiquitin with real PDB2PQR and solve
with APBS. Process failure, timeout, cancellation/descendants and output races
use bounded fake tools only for process behavior. Scientific acceptance never
substitutes mocks. Native GUI tests verify timeline controls, rendered frame
differences, density volume/registration visuals, computed potential surface,
responsive job/cancel and normal close. Keep all existing native assertions.

One final frozen-source full suite, plain environment, installed-wheel isolated
GUI/science, extracted-source checks, original examples and privacy/hash audits
precede release. Linux CI must install real APBS/PDB2PQR and optional encoder,
assert their availability, execute new numerical/advanced-native tests and
reject dependency skips for required gates. Preserve Qt5/Qt6 actual evidence.
Independent per-task and final whole-branch review precedes public integration.
Publish v0.4.0 only after the exact public commit's CI succeeds. Supply wheel,
sdist, example ZIP, source/publication evidence and checksums; verify uploaded
digests and downloaded bytes. Upgrade the existing F launcher runtime and
retain all earlier private evidence. Do not claim commercial feature parity.

## Source basis

Discovery used public PyMOL command/source documentation, the MRC2014/CCP4
format specifications, APBS/PDB2PQR official documentation and release metadata,
and finite real headless probes. The implementation plan must link the exact
primary references and pin observed versions; mutable source pages are not
assumed identical to the installed backend. Discovery evidence is retained
privately on F rather than shipping downloaded source/runtime binaries.

- PyMOL public command API: https://pymol.org/pymol-command-ref.html
- MRC2014 format: https://www.ccpem.ac.uk/mrc-format/mrc2014/
- CCP4 map header: https://www.ccp4.ac.uk/html/maplib.html
- APBS OpenDX data ordering: https://apbs.readthedocs.io/en/latest/formats/opendx.html
- APBS grid counts: https://apbs.readthedocs.io/en/latest/using/input/old/elec/dime.html
- APBS potential output: https://apbs.readthedocs.io/en/latest/using/input/old/elec/write.html
- PDB2PQR options: https://pdb2pqr.readthedocs.io/en/latest/using/index.html
- PARSE radii: https://raw.githubusercontent.com/Electrostatics/pdb2pqr/v3.7.1/pdb2pqr/dat/PARSE.DAT
- APBS Born reference: https://github.com/Electrostatics/apbs/tree/v3.4.1/examples/born
