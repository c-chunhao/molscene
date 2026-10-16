# PyMOL comparison and MolScene priorities / 功能差距与优先级

Assessment date: 2026-10-15; v0.4 workflow documentation updated 2026-10-16.
Baseline: MolScene v0.2.0. The installed
Incentive PyMOL reports 3.1.6.1 through its public version API. This is a
workflow comparison based on official documentation, the installed public API
and MolScene's code/tests; it is not a claim that every commercial feature has
been independently exercised. Official pages describe different releases, so
their feature lists are not treated as an exhaustive current SKU matrix.

## Daily workflow priorities

| Workflow | Official PyMOL capability | MolScene v0.2 baseline | v0.3 workflow | Priority |
|---|---|---|---|---|
| Structure and residue navigation | Content panel, object/selection controls, sequence display | Flat object list; coordinate sequence available in inspect results | Searchable observed-residue browser, exact membership, selection handoff | First |
| Styling | Content controls and preset panel | Nine representations, five coloring modes; pocket presets through CLI/batch | Live overview, surface and ligand-pocket presets scoped to explicit selections | First |
| Focus and reusable selections | Selection, center, zoom, named selections | Raw selection fields and captures; object orient | State-aware focus, selection preview, named selection save/delete | First |
| Structure preparation | Public create/remove operations and builder tools | Native commands available, no MolScene preparation workflow | New cleaned copy with water/hydrogen/blank+A options and source membership report | First |
| Scenes | Recall, update and scene organization | Store, recall and delete | Update/rename and double-click recall; retain PSE persistence | First |
| Geometry and proximity | Measurement and interaction tools | Distance, angle, dihedral, geometric contacts | Retain validated semantics and easier A/B input selection | First |
| Surface/interface analysis | Surface area and scripting APIs | Context-aware SASA with residue table | Partner SASA loss, total buried area and explicitly labelled half-area interface convention | First |
| Result exploration | Upstream console and structure inspection | Complete JSON/CSV/FASTA plus read-only tables | Search and sort previews while exporting full original tables | First |
| Animation | Scenes and multitrack Timeline | Native upstream commands only; no MolScene editor | Future: a separately tested scene/movie workflow | Later |
| Density and volumes | Map, mesh, surface and volume workflows | Native commands may be available; no validated MolScene map pipeline | Future: map format, origin, level and structure-registration validation | Later |
| Chemical editing and interactions | Builder, atom/bond tools, specialized interactions | Upstream tools only; geometric contacts are not chemical H-bonds | Future: chemistry-aware typing and explicit validation | Later |
| Electrostatics and specialized integrations | Plugins and distribution-dependent components | No validated MolScene electrostatics pipeline | Future: independently installed/calibrated optional tools | Later |
| Distribution and support | Supported installers, commercial support and documentation | Python wheel, source, examples and community issue tracker | Improve documentation and actual cross-platform package acceptance | Ongoing |

The first priority serves the common sequence: load a structure, locate a
residue/ligand, select it precisely, style/focus the view, analyze a partner and
export reproducible artifacts. The priority order is an engineering judgment,
not usage telemetry or a claim of numerical feature-count parity.

## v0.3 implementation and validation scope

The implemented release workflows add exact observed-residue navigation and
selection handoff, live overview/surface/pocket presets, state-aware focus and
named selections, clean copies, scene update/rename, interface SASA loss and
searchable/sortable result previews with complete exports. The priority table
above retains the v0.2 baseline for comparison. Availability is not evidence
that every upstream or commercial variant has been tested. Actual integrated
source/native/package evidence is recorded in [TESTING](TESTING.md); historical
v0.2 counts do not close v0.3 validation rows.

The 2026-10-16 integrated source run passed 177 of 194 tests, with 17 local
filesystem capability skips and all four required native tests executed. The
plain run passed 43, with 151 expected dependency/filesystem skips. Isolated
installed-wheel native acceptance also exercised the selected daily workflow.
These results validate the scoped additions above; they do not validate the
later animation/density/chemistry/electrostatics rows or every native command.
Exact source hashes and the local evidence are in
[validation-v03.json](validation-v03.json). Linux Qt5 execution remains a remote
CI release gate, separate from the observed Windows Qt6 checks.

The table above preserves the historical v0.3 priority assessment. The scoped
v0.4 additions are described below. No feature count, popularity ranking or
commercial-parity percentage is claimed. The
navigation browser is an observed-coordinate view; the native application
continues to supply its own separate sequence display and command capabilities.

## v0.4 scope and remaining boundaries

| Workflow | v0.4 contract | Boundary |
|---|---|---|
| Camera timeline | Explicit scenes/states, keyframes, preview, project, PNG/H.264 and standalone replay | No trajectory interpolation/statistics or multitrack commercial parity |
| Density/scalar maps | DX/MRC/CCP4 physical geometry, raw contours, volume ramps and verified copy registration | No implicit sigma normalization, guessed source association or experimental validity for synthetic maps |
| Electrostatics | Explicit prepared PQR/protein preparation/import, real APBS/PDB2PQR, unit-labelled potential display and replay | LPBE assumptions; no binding-affinity/free-energy claim or ligand force-field assignment |
| Reproducibility | Closed inventories, no-replace bundles, source identity, cancellation and public derivatives | Native/PSE privacy/scientific review and exact-commit Linux execution remain independent release gates |
| Advanced rendering | Supervised official Qt/OpenGL child for explicit no-ray/volume, with requested/effective renderer and decoded image evidence | Requires a valid graphics context; GPU/opacity differences remain, and OpenGL is not CPU ray tracing |

The required Linux path uses actual Qt5/PyMOL 3.1/3.2, APBS 3.4.1 x64,
PDB2PQR 3.7.1 and H.264. Windows Qt6 evidence cannot substitute for it.
The [advanced guide](ADVANCED.md) states controls, limits and scientific
assumptions. [TESTING](TESTING.md) separates implemented acceptance tests from
recorded executions. Chemical hydrogen-bond assignment, automatic biological
assembly, trajectory analysis and specialized commercial components remain open.

## Scientific boundaries

- Residue navigation shows coordinates actually present, including insertion
  codes and segment identities. It does not invent missing SEQRES residues.
- A cleaned copy is an explicit atom filter on a selected coordinate state.
  It does not repair missing atoms, optimize a ligand or assign protonation.
- Interface area is derived from SASA loss of two disjoint, non-solvent heavy
  atom sets in their union. Both total loss and half total are named explicitly;
  neither is a binding affinity or free energy.
- A pocket is a geometric neighborhood expanded to selected residue sites.
  Existing contacts remain geometric proximity, without a new chemical H-bond
  claim. The public `find_pairs` implementation requires chemical filtering
  beyond its orientation option.
- Native upstream functionality remains governed by its own build and license.
  MolScene ships original workflow code and does not redistribute proprietary
  binaries, interfaces, license files or artwork.

## Primary sources

- [PyMOL 3 documentation](https://learn.schrodinger.com/public/pymol/current/Content/pymol/pymol_home.htm)
- [Content panel](https://learn.schrodinger.com/public/pymol/current/Content/pymol/Features/content_panel.htm)
- [Scenes panel](https://learn.schrodinger.com/public/pymol/2023-4/Content/pymol/Features/scenes.htm)
- [Timeline](https://learn.schrodinger.com/public/pymol/current/Content/pymol/Features/timeline.htm)
- [Official PyMOL 3 introduction: interface, presets and scenes](https://www.schrodinger.com/life-science/learn/webinar/introducing-pymol-3-bring-your-biochemistry-to-life-with-3d-molecular-visualization-and-movie-making/)
- [Official course: selections, pockets, presets and image export](https://www.schrodinger.com/life-science/learn/education/courses/pymol/)
- [Builder panel](https://learn.schrodinger.com/public/pymol/current/Content/pymol/Features/builder.htm)
- [Public command reference](https://pymol.org/pymol-command-ref.html)
- [Public querying implementation](https://github.com/schrodinger/pymol-open-source/blob/master/modules/pymol/querying.py)
- [Historical release features, explicitly versioned](https://pymol.org/features.html)

本报告优先解决日常制图与分析中的操作障碍。商业版还有动画时间线、专用格式、
建模及其他组件；本轮不将原生窗口中存在的命令包装成已经由 MolScene 验证的功能。
