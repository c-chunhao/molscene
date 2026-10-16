# Advanced workflows

MolScene 0.4 adds camera timelines, validated scalar maps and optional LPBE
electrostatics. These workflows share the CLI, native **Advanced** tab and
snapshot worker. PyMOL and external tools are installed separately. The
[testing record](TESTING.md) distinguishes acceptance requirements from actual
recorded runs; historical v0.3 evidence does not validate the new workflows.

## Tools and runtime

Copy [the example configuration](../examples/advanced-tools.example.json), replace
every `/ABSOLUTE/PLACEHOLDER` path and remove unused tool entries. This is schema
1 JSON, with exactly `schema_version` and `tools`. Supported names are `apbs`,
`pdb2pqr`, `ffmpeg` and `ffprobe`. Each entry has an `argv` array, actual `x86` or
`x64` architecture, `path_dirs`, `runtime_files`, `timeout_seconds` and
`memory_limit_mib`. Runtime files name an existing DLL, interpreter or package
metadata file with the corresponding `role`. All configured paths must be
absolute regular files/directories without links or reparse points. Resolve
trusted installed symlinks before recording paths; use a copied Python
interpreter when a virtual environment would otherwise depend on symlinks.

`argv[0]` is an executable, followed only by fixed literal prefixes such as
`-B`, `-m`, `pdb2pqr`. Feature arguments are appended by MolScene. There are no
command strings, formatting placeholders, shell scripts or arbitrary environment
fields. Configuration does not install tools or alter the desktop environment.

```sh
python -m molscene tools --config /absolute/path/tools.json
```

The probe checks actual executable architecture, parsed version, runtime files
and hashes. A label in JSON cannot establish compatibility. Child processes get
their own PATH and temporary directory; the parent PATH, user home and registry
are unchanged. PDB2PQR 3.7.1 needs an independent **Python 3.11 or newer**
interpreter. The native desktop can keep Python 3.10.

The observed official Windows APBS 3.4.1 executable is **32 bit** and requires
the matching Python 3.9 x86 DLL. That legacy DLL is a solver dependency, not a
desktop Python upgrade/downgrade. Its supported grid counts are 33, 65, 97 or
129 on each axis, with a 1536 MiB budget ceiling. The x64 path additionally
allows 161 and 193, with a 4096 MiB ceiling. The estimate is
`128 MiB + 512 × nx × ny × nz bytes`; requests exceeding the configured budget
fail. MolScene records requested/actual counts, architecture, estimate and any
solver-reported high water separately. It never silently reduces resolution.

The required Linux acceptance environment uses Python 3.11, PyMOL 3.1/3.2 with
Qt5, conda-forge APBS `3.4.1=py311ha92c01f_1`, PDB2PQR 3.7.1, Pillow and real
FFmpeg/ffprobe with libx264. Windows Qt6 and Linux Qt5 are separate acceptance
paths. A successful package solve is not evidence that numerical/native tests ran.

## Timeline: JSON and a project

Timeline JSON stores schema 1, `baseline_scene`, integer FPS and frame count,
plus ordered keyframes containing ID, label, frame, molecular state, camera
view, scene reference and `hold` or `smooth` interpolation. Frames are 1-based.
The first row is frame 1; the last row defines duration. A single row is one
frame. FPS is 1–120, with at most 10,000 frames/keyframes and an 8 MiB JSON limit.

`smooth` uses shortest-arc quaternion camera interpolation at linear time;
projection flags must agree. `hold` keeps the left camera until the next keyframe.
Molecular states and scene displays change at keyframes. This is a camera and
display sequence, not a trajectory interpolation or molecular dynamics analysis.
**Keep previous display** inherits the preceding explicit scene, or the uniquely
named baseline captured by **New**. Scene references must still exist when
seeking/exporting. The timeline cursor is separate from native PyMOL movie frames.

In the desktop, **Capture** adds a keyframe and the controller captures the
current camera. **Update details** changes the selected keyframe's label,
frame/state, scene or interpolation while preserving its captured camera.
Moving the live camera and then updating details does not replace that view.

```sh
python -m molscene timeline-validate timeline.json
python -m molscene timeline-validate timeline.json --session source.pse
python -m molscene timeline-project source.pse --timeline timeline.json --output results/project
python -m molscene animate source.pse --timeline timeline.json --width 800 --height 600 --dpi 150 --output results/frames
python -m molscene animate source.pse --timeline timeline.json --mp4 --tools /absolute/path/tools.json --output results/movie
```

JSON validation alone needs no backend; `--session` also validates native scenes
and states. JSON does not contain the session/display scenes. A **project** is a
closed bundle of `source.pse`, `timeline.json`, `replay.pml`, `report.json` and
`manifest.json`. **Load JSON** uses the current session; **Open project** validates
the bundle and explicitly replaces the session. **Source snapshot** identifies
the frozen display source. A live display cannot be labelled equivalent merely
because its atoms match a previously saved PSE.

Animation adds exactly `frames/frame_000001.png` through the declared final
frame. MP4 additionally includes `animation.mp4`, tool provenance and four
encoder/probe logs. MP4 requires even dimensions, actual libx264, H.264/yuv420p,
decoded frame-count and duration checks; missing encoders fail. PNG-only export
does not require an encoder. `--no-ray` disables ray tracing. Default render is
800 × 600, 150 DPI. Bounds are 16–4096 per dimension, at most 16,777,216 pixels,
1–1200 DPI and at most 8 GiB estimated raw RGBA sequence.

Advanced explicit no-ray exports and any volume-bearing export require a valid
Qt/OpenGL context in a supervised official PyMOL child. Other ray-enabled
advanced exports retain CPU ray rendering. A timeline is checked for volumes
across its scene changes and its whole sequence uses one child, with per-frame
progress. The child renders on the Qt main thread and closes its owned window
normally; cancellation, timeout or worker death stops its owned process tree.
Thus a CLI no-ray/volume export also needs native Qt resources and a working
display, including Xvfb/GLX on Linux CI.

Reports retain `analysis.rendering` with `backend`, `requested_ray`,
`effective_ray`, dimensions and, for `native_opengl`, the actual renderer/GPU,
Qt/PyMOL versions, decoded `images` diagnostics/hashes and process close outcome.
OpenGL output can be opaque, and pixels/opacity can differ with GPU/driver;
it is not CPU ray tracing. Volume is never silently substituted with an
isosurface. Deliberately hidden or transparent flat images remain valid, but
malformed, partial or no-context output fails before publication. Native
acceptance compares visible versus hidden volume and differing timeline frames;
PNG headers or the existence of a map object alone cannot prove content.

## Density and scalar fields

```sh
python -m molscene density field.dx --format dx --kind mesh --level 1 --color cyan --output results/map
python -m molscene density field.mrc --format mrc --interpretation origin --kind surface --level 1.5 --output results/mrc
python -m molscene density field.dx --format dx --matrix registration.json --output results/registered
python -m molscene density field.dx --format dx --session models.pse --mobile moving --target reference --mobile-state 1 --target-state 1 --alignment-selection "name CA" --algorithm align --cutoff 2 --cycles 5 --output results/aligned
python -m molscene density field.dx --format dx --kind volume --volume-ramp ramp.json --output results/volume
```

Map coordinates use Å. Scalar values retain their declared raw unit; a contour
level is a raw value, not automatically a sigma threshold or normalized density.
Mesh/surface use an isolevel. Volume uses an explicit transfer ramp of ordered
`value,r,g,b,alpha` stops and ignores the contour level. It has at least two and
at most 32 stops; color and alpha channels lie in 0–1. An electron-density
interpretation requires appropriate source data and metadata. A synthetic field
only demonstrates map geometry/display.

DX order is `(i*ny+j)*nz+k`; physical coordinates are
`origin + i*basis[0] + j*basis[1] + k*basis[2]`. MRC/CCP4 handling retains file
axis mapping, basis, original header and source hash. `auto` rejects simultaneous
nonzero ORIGIN and starts. Choose `origin` explicitly with MRC or `starts` with
the CCP4 interpretation. An origin-only file requested as CCP4 needs an explicit
choice rather than an implicit origin shift. No normalization is hidden.

Registration JSON is a rigid row-major 4 × 4 matrix, acting on column coordinates.
Registered maps/models are copies. For an associated map, the physical delta is
`world_matrix × inverse(load_matrix)`; applying the whole native matrix directly
would double-count an origin already present in source geometry. Alignment uses
the selected mobile/target states and records its transform and statistics.
Aligned-copy `align`/`super` reject effective `matrix_mode` outside -1, 0 or 1;
PyMOL 3.2 mode 2 has an observed native crash. Settings are preserved. Numeric
map registration remains available when its separate transform checks pass.

A freshly loaded file is map state 1. Associated maps retain explicit state,
source bytes, field values, native geometry and matrices. An unassociated map
reopened from a session can display but cannot authorize verified registration.
Reassociation compares actual native float32 values and complete captured
geometry against the validated source, including the quantization bound.
Supported native ObjectMap layouts are PyMOL 3.1/3.2; unknown layouts fail
verification. Inputs are limited to 512 MiB/16,777,216 voxels, dimensions 2–4096.

Density export creates an owned display with the requested settings against the
verified map, retaining existing source-scene objects. A default-name collision
uses a fresh result name and records a warning. Existing and exported layers may
coexist, including compounded volume opacity. Use Figure/current-view export
when the exact visible scene is the intended result. Figure uses the supervised
native renderer for no-ray or volume scenes, with dimensions 16–4096 and DPI
1–1200, and records the actual rendering path; other ray scenes retain CPU ray.

## Electrostatic potential

Three explicit modes distinguish supplied charge/radius data, protein preparation
and imported maps. Selections are quoted as a single shell argument.

```sh
python -m molscene potential --mode prepared --pqr molecule.pqr --tools /absolute/path/tools.json --grid 65 65 65 --output results/prepared
python -m molscene potential --mode prepare --session protein.pse --selection "protein and polymer.protein" --state 1 --force-field PARSE --ph 7 --protonation propka --drop-water --tools /absolute/path/tools.json --output results/potential
python -m molscene potential --mode import --dx potential.dx --pqr molecule.pqr --units kBT/e --output results/imported
python -m molscene potential --mode import --dx volts.dx --session protein.pse --units V --display-units kBT/e --temperature 298.15 --output results/converted
```

`prepared` requires PQR/tools; `prepare` requires a session, explicit protein
selection and tools; `import` requires DX, explicit units and exactly one PQR or
session radius source. Preparation validates supported residues before invoking
tools and rejects unsupported selected ligands/metals. Force fields are PARSE,
AMBER or CHARMM; protonation is `propka` or `preserve`, with pH 0–14. Dropping
water is explicit. PQR values require finite charge/radii; heavy-atom radii are
positive. Force-field zero-radius hydrogens are retained and reported.

The computed model is **linearized Poisson–Boltzmann (LPBE)**, `mg-auto`, `bcfl
mdh`, `chgm spl2`, `srfm smol`, `swin .3`, `sdens 10`, centered on the explicit
state's PQR bounds. Defaults are solute/solvent dielectric 2/78.5, 298.15 K,
0.15 M symmetric ±1 ions with 2 Å exclusion radii, 1.4 Å solvent probe and
40/20 Å coarse/fine total axis expansions. The coarse padding exceeds the fine
padding and must contain displayed radii plus probe. No nonlinear PB convergence,
binding affinity, free energy or validated ligand parameterization is claimed.

The corresponding CLI controls are `--solute-dielectric`, `--solvent-dielectric`,
`--salt`, `--ion-radius`, `--probe-radius`, `--coarse-padding`, `--fine-padding`,
`--temperature`, `--grid NX NY NZ` and `--ramp-limit`. Temperature is 250–400 K,
salt 0–2 M; other dielectric/radius/ramp values are positive finite numbers.

Computed raw maps are always in **kBT/e**. Use `--display-units` for an explicit
conversion to V/mV; the raw map remains unchanged and `display.dx` is added.
Imported unknown units support same-unit display only. A conversion involving
kBT/e needs a stated temperature. The default diverging range is ±5 kBT/e;
`ramp_limit` is in the selected display units. The legend labels sign/range/unit,
and reports retain clipped-grid fraction and the radius/surface sampling
convention. Whole-grid extrema include near-charge/interior values and must not
be described as surface extrema. **Apply ramp** changes only the owned ramp and
legend after source-identity verification.

## Results, cancellation and replay

Every advanced export requires a new directory; there is no advanced
`--overwrite`. Publication validates a closed inventory of regular files and
hashes, stages beside the destination and uses a no-replace directory rename.
Links, reparse points, hardlink aliases, source/output collisions and unexpected
files fail. A race-created destination is preserved. Failure/cancellation leaves
no completed output; bounded logs are retained separately in a private failure
directory, without copied scientific inputs.

GUI work runs asynchronously through the existing worker/job manager. Mutation
controls are locked during jobs; Results and Cancel remain available. Preview
stops on pause, end, a job, session replacement, dock/window close or an error.
Cancel cooperatively stops owned descendants; timeout or worker death also
terminates that process tree. The default limits are 15 s probing, 600 s
preparation and 1200 s solving/encoding; execution timeouts are configurable
within 1–3600 s. Each stdout/stderr log is bounded at 16 MiB; overflow fails
rather than silently truncating a successful report.

Advanced reports use schema 2 and retain the input/session hashes, explicit
states, map values/geometry/source associations, timeline/display identity,
selection membership/coordinates/radii, parameters, units, warnings and limits.
Source edits produce `stale`; insufficient provenance produces `unverified`.
Camera movement alone does not stale a selected potential result. Adding an
owned result copy does not change the original bound membership. Loading a
result reconstructs its owned copies without replacing the live session.

`artifact_hashes` covers every payload except report/manifest; `manifest.json`
hashes every payload including the report, excluding itself. Keep each bundle
together. In a fresh compatible PyMOL, run `@replay.pml`; timeline replay exposes
`molscene_seek(frame)` and `molscene_play()`. Replay needs PyMOL and Python's
standard library, with no MolScene/solver/encoder dependency. Source PSE use in
standalone replay is explicit. Pixel identity across backends is not promised.

Original tool reports/logs can contain local runtime paths. The public example
generator retains these private originals and creates separate derivatives with
`public_projection` metadata, original manifest SHA-256, redactions and rebuilt
hashes. It never rewrites an original bundle or the pinned public coordinates.
Scientific inputs/maps/PQR/PDB and sessions keep their original bytes, including
line endings, so input digests and standalone replay references remain valid.
Public derivatives validate as portable bundles; they have no private GUI job
context and are `unverified` for current-scene result application. Use standalone
replay, Open project for a timeline, or explicitly import the original map/PQR/DX
through the relevant controls. Do not relabel a public derivative as current.
Review binary sessions as well as text before publishing a derivative.
