"""Offline deposited assembly and coordinate-state analysis; no rendering or network.

Requires a separately supplied compatible PyMOL/NumPy runtime. Keep each complete
bundle together when moving it; replay.pml uses only public native PyMOL calls.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if (ROOT/'molscene').is_dir():
    sys.path.insert(0, str(ROOT))

PINNED = {
    '3c70.cif': 'a3e5e2bc4997710ba916b6f8b6fd01acd85d9e6912fd063b2ac79b4d581fbb88',
    'trajectory-tiny-v06.pdb': 'c727ef32e82804bee48930c824c757d7b52d6f3c72a5aa130ef3001a7d65c5c5',
    'trajectory-tiny-v06.dcd': 'd377bd8f6ed80a5fee5d691e6b2eb7f0d02c792a3c95a631e211a8bebfc1501d',
}
TRAJECTORY_FILES = {'source.pse', 'frames.csv', 'atoms.csv', 'residues.csv',
    'rmsd.svg', 'rmsf.svg', 'rg.svg', 'report.json', 'manifest.json', 'replay.pml'}
BUNDLES = {
    'assembly-3c70': {'inputs/source.cif', 'copies/copy_001.cif', 'copies/copy_002.cif',
        'assembly.pse', 'copies.csv', 'replay.pml', 'report.json', 'manifest.json'},
    'trajectory-fit': TRAJECTORY_FILES,
    'trajectory-dcd': TRAJECTORY_FILES | {'inputs/trajectory.dcd', 'inputs/original.pdb'},
}


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def data_path(name):
    """Resolve checksum-pinned coordinates beside this source/installed script."""
    if name not in PINNED: raise ValueError('Unknown pinned example input')
    path = HERE/'data'/name
    if not path.is_file() or _sha(path) != PINNED[name]:
        raise ValueError('Missing input or checksum mismatch: '+name)
    return path


def audit_bundle(root):
    """Check the exact portable manifest inventory and every file hash/size."""
    from molscene.artifacts import checked_path
    root = checked_path(root)
    manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    if set(manifest) != {'schema_version','files'} or manifest['schema_version'] != 1:
        raise ValueError('Invalid manifest')
    names = set(manifest['files']) | {'manifest.json'}
    actual = set()
    directories = set()
    for folder, children, files in os.walk(root, followlinks=False):
        for child in children:
            path = checked_path(Path(folder)/child)
            directories.add(path.relative_to(root).as_posix())
        for filename in files:
            path = checked_path(Path(folder)/filename)
            if not path.is_file() or path.stat().st_nlink != 1:
                raise ValueError('Bundle payload must be a unique regular file')
            actual.add(path.relative_to(root).as_posix())
    expected_dirs = {p.as_posix() for name in names for p in PurePosixPath(name).parents if str(p) != '.'}
    if actual != names or directories != expected_dirs:
        raise ValueError('Bundle has missing or unexpected entries')
    for name, entry in manifest['files'].items():
        parts = PurePosixPath(name)
        if parts.is_absolute() or '..' in parts.parts or '\\' in name or parts.as_posix() != name:
            raise ValueError('Nonportable manifest path')
        path = checked_path(root/name)
        if entry != {'sha256':_sha(path), 'bytes':path.stat().st_size}:
            raise ValueError('Bundle hash or size mismatch')
    return names


def _fitted_session(path):
    """Original asymmetric tetrahedron, rigid motions only; an analytic example."""
    import pymol2
    from chempy import Atom
    from chempy.models import Indexed
    base = [(0,0,0),(2,0,0),(0,3,0),(0,0,4)]
    states = [base, [(x+5,y-2,z+1) for x,y,z in base],
              [(-y,x+1,z-2) for x,y,z in base]]
    with pymol2.PyMOL() as pm:
        for state, coordinates in enumerate(states,1):
            model = Indexed()
            for ordinal, coord in enumerate(coordinates,1):
                atom = Atom()
                atom.name, atom.symbol, atom.coord = 'CA','C',list(coord)
                atom.chain, atom.resn, atom.resi = 'A','TST',str(ordinal)
                model.atom.append(atom)
            pm.cmd.load_model(model, 'tetrahedron', state=state, zoom=0)
        pm.cmd.save(str(path), quiet=1)


def _check_science(reports):
    """Hand-derived fixture checks, independent of the analysis implementation."""
    assembly, fitted, raw = (reports[name] for name in BUNDLES)
    if (assembly['copy_count'],assembly['atom_count']) != (2,10410):
        raise ValueError('Unexpected deposited assembly counts')
    if (fitted['frame_count'],fitted['atom_count'],raw['frame_count'],raw['atom_count']) != (3,4,6,4):
        raise ValueError('Unexpected analytic trajectory counts')
    def close(actual, expected):
        if not math.isfinite(actual) or not math.isclose(actual,expected,rel_tol=1e-6,abs_tol=1e-6):
            raise ValueError('Analytic fixture check failed')
    for row, expected_raw in zip(fitted['frames'],(0,math.sqrt(30),math.sqrt(11))):
        close(row['raw_rmsd_angstrom'],expected_raw)
        close(row['rmsd_angstrom'],0)
        close(row['rg_angstrom'],math.sqrt(87)/4)
    for row in fitted['atoms']: close(row['rmsf_angstrom'],0)
    for ordinal,row in enumerate(raw['frames']):
        close(row['rmsd_angstrom'],ordinal*math.sqrt(113))
        close(row['rg_angstrom'],math.sqrt(22.5))
    for row in raw['atoms']: close(row['rmsf_angstrom'],math.sqrt(113*35/12))


def generate(output):
    """Publish three fresh closed native bundles and analytic validation together."""
    from molscene.artifacts import checked_path, write_json
    from molscene.bundles import Bundle
    from molscene.assembly_export import export_assembly
    from molscene.trajectory_export import export_trajectory
    output = checked_path(output)
    for protected in (HERE/'data', HERE/'outputs'):
        if output == protected or protected in output.parents or output in protected.parents:
            raise ValueError('Choose a new directory outside historical examples and source data')
    if output.exists(): raise ValueError('Choose a new example output directory')
    sources = {name:data_path(name) for name in PINNED}
    inventory = ['validation.json','manifest.json'] + [name+'/'+relative
        for name, files in BUNDLES.items() for relative in sorted(files)]
    with Bundle(output, protected=tuple(sources.values()), inventory=inventory,
                new_directory_only=True) as bundle:
        reports = {}
        reports['assembly-3c70'] = export_assembly(None,bundle.stage/'assembly-3c70',
            dict(source_path=str(sources['3c70.cif']),assembly_id='1',state=1))
        fit_session = bundle.scratch/'tetrahedron.pse'
        _fitted_session(fit_session)
        reports['trajectory-fit'] = export_trajectory(fit_session,bundle.stage/'trajectory-fit',
            dict(object_name='tetrahedron',selection='all',fit_selection='all',
                 time_step_ps=.5,time_start_ps=0))
        import pymol2
        topology = bundle.scratch/'topology.pse'
        with pymol2.PyMOL() as pm:
            # Explicitly preserve the deliberately nonlexical topology atom order.
            pm.cmd.set('retain_order',1)
            pm.cmd.read_pdbstr(sources['trajectory-tiny-v06.pdb'].read_text(encoding='utf-8'),'ordered')
            pm.cmd.save(str(topology),quiet=1)
        reports['trajectory-dcd'] = export_trajectory(topology,bundle.stage/'trajectory-dcd',
            dict(object_name='ordered',selection='all',fit_selection='',align=False,
                 trajectory_path=str(sources['trajectory-tiny-v06.dcd'])),
            source_input={'path':str(sources['trajectory-tiny-v06.pdb']),
                          'sha256':PINNED['trajectory-tiny-v06.pdb']})
        _check_science(reports)
        for name, expected in BUNDLES.items():
            if audit_bundle(bundle.stage/name) != expected:
                raise ValueError('Unexpected example inventory: '+name)
        write_json(bundle.stage/'validation.json', dict(schema_version=1,gallery_version='0.6.0',
            sources=PINNED,bundles={name:name+'/report.json' for name in BUNDLES},
            fixture_checks='Hand-derived counts, RMSD, population RMSF and equal-weight Rg; finite values checked.',
            limitations=['Original tetrahedron and DCD fixtures are illustrative, not experimental trajectories.',
                'Coordinate states are not camera movie frames; supplied fit-example time spacing is illustrative.',
                'No PBC correction or physical-time inference; DCD requires matching original topology atom order.',
                'No rendering, GUI, optional solvers or encoders are exercised by this generator.']))
        bundle.publish()
    audit_bundle(output)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True,help='New output directory outside historical outputs/data')
    args = parser.parse_args(argv)
    try: generate(args.output)
    except (ValueError,RuntimeError,OSError,ImportError) as exc:
        print(str(exc),file=sys.stderr)
        return 1
    print('Offline assembly and trajectory bundles complete; no rendering performed.')
    return 0


if __name__ == '__main__': raise SystemExit(main())
