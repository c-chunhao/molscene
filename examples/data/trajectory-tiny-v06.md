# Original tiny trajectory fixture

Authored for MolScene 0.6.0, 2026-10-17; original data, distributed under the repository MIT license.

`trajectory-tiny-v06.pdb` is a deliberately unsorted atom-name topology (Z9, A1, Y8, B2) with dummy coordinates (-900,-900,-900). `trajectory-tiny-v06.dcd` contains six ordinary little-endian 32-bit CHARMM CORD frames, all four atoms, three float32 axes, no unit cell, fixed atoms or fourth dimension. `trajectory-tiny-v06.json` is the independent coordinate oracle.

For zero-based frame f=0..5 and atom i=0..3 the positions in angstrom are (10f+i+0.25, -3f-i-0.5, 2f+4i+0.75). Every component is exactly representable in float32. These deliberately collinear coordinates require align=False; numeric proper-fitting oracles use a separate asymmetric tetrahedron in tests/test_trajectory.py.

The first DCD frame must replace the dummy topology state. This fixture proves native ordering for these original inputs; DCD contains no atom names and cannot establish chemical correspondence for arbitrary user topology files. No PBC correction is performed.
