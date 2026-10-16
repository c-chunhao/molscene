Public APBS v3.4.1 Born-ion fixtures, BSD-3-Clause (LICENSE.md).
Files are unchanged copies of the upstream release's examples.

- https://raw.githubusercontent.com/Electrostatics/apbs/v3.4.1/examples/born/apbs-mol-auto.in
- https://raw.githubusercontent.com/Electrostatics/apbs/v3.4.1/examples/born/ion.pqr
- https://raw.githubusercontent.com/Electrostatics/apbs/v3.4.1/examples/born/README.md (UPSTREAM_README.md)
- https://raw.githubusercontent.com/Electrostatics/apbs/v3.4.1/LICENSE.md

The tabulated numerical regression is -229.7740 kJ/mol. The exact SI Born
oracle for the input's 3 Å sphere and dielectric constants 1/78.54 is
-228.6108009877 kJ/mol. The upstream README's -230.62 analytical column is
not treated as exact truth. Tests require both the numerical regression
(±0.1 kJ/mol) and the independent SI value (within 1%).
