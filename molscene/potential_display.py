"""Owned potential surfaces with explicit radii, coverage and numeric legends."""
import copy
import hashlib
import json
import math
from pathlib import Path
import uuid

from .artifacts import sha256
from .grids import read_dx, grid_metadata
from .pqr import read_pqr
from .native_maps import load_native_dx


def _positive(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(label+' must be positive and finite')
    return float(value)


def selected_atoms(cmd, selection, state):
    if not isinstance(selection, str) or not selection.strip() or any(c in selection for c in '\r\n\0'):
        raise ValueError('A nonempty single-line selection is required')
    if type(state) is not int or not 1 <= state <= 100000:
        raise ValueError('State must be a positive integer')
    model = cmd.get_model(selection, state)
    atoms = []
    for atom in model.atom:
        xyz = [float(c) for c in atom.coord]
        radius = float(atom.vdw)
        if not all(math.isfinite(c) for c in xyz) or not math.isfinite(radius) or radius < 0:
            raise ValueError('Selected coordinates and vdw must be finite with nonnegative radii')
        atoms.append(dict(name=atom.name, resn=atom.resn, chain=atom.chain, resi=atom.resi,
                          element=atom.symbol, alt=atom.alt, x=xyz[0], y=xyz[1], z=xyz[2], vdw=radius))
    if not atoms:
        raise ValueError('Selected state has no atoms')
    return atoms


def selection_digest(atoms):
    return hashlib.sha256(json.dumps(atoms, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def validate_coverage(grid, atoms, probe_radius, *, radius_key='radius'):
    """Require the full atom+probe bounding spheres inside the physical map."""
    _positive(probe_radius, 'Probe radius')
    upper = [grid.origin[a]+(grid.shape[a]-1)*grid.basis[a][a] for a in range(3)]
    for atom in atoms:
        radius = atom[radius_key]+probe_radius
        for axis, coordinate in enumerate(('x','y','z')):
            if atom[coordinate]-radius < grid.origin[axis]-1e-5 or atom[coordinate]+radius > upper[axis]+1e-5:
                raise ValueError('Potential map does not cover every displayed atom plus its actual radius and probe')
    return dict(passed=True, atom_count=len(atoms), probe_radius=probe_radius,
                criterion='all displayed atom spheres expanded by actual radius plus probe inside grid')


def _legend(units, limit, fraction):
    return f'Potential [{units}]: {-limit:g} / 0 / +{limit:g}\nClipped grid fraction: {fraction:.2%}'


def _set_ramp(cmd, record, grid, limit):
    fraction = sum(abs(value) > limit for value in grid.values)/len(grid.values)
    cmd.ramp_new(record['ramp'], record['map'], [-limit, 0., limit], ['red', 'white', 'blue'], quiet=1)
    text = _legend(record['units'], limit, fraction)
    cmd.label(record['legend'], repr(text))
    record.update(ramp_limit=limit, ramp_values=[-limit,0.,limit], ramp_colors=['red','white','blue'],
                  clipped_grid_fraction=fraction, legend_text=text)


def load_potential_copy(cmd, *, pqr_path=None, selection=None, state=1, dx_path, units, temperature,
                        ramp_limit, probe_radius=1.4):
    if (pqr_path is None) == (selection is None):
        raise ValueError('Choose exactly one radius source: PQR or selected molecule')
    if units not in ('kBT/e', 'V', 'mV', 'unknown'):
        raise ValueError('Unsupported potential units')
    if isinstance(temperature, bool) or not isinstance(temperature, (int,float)) or not 250 <= temperature <= 400:
        raise ValueError('Temperature must be in 250..400 K')
    limit = _positive(ramp_limit, 'Ramp limit')
    probe_radius = _positive(probe_radius, 'Probe radius')
    grid = read_dx(dx_path, value_unit=units)
    if any(abs(value) > 3.4028234663852886e38 for value in grid.values):
        raise ValueError('Potential values overflow native float32 map storage')
    if pqr_path is not None:
        parsed = read_pqr(pqr_path)
        atoms = parsed['atoms']
        radius_key = 'radius'
        convention = 'PQR electrostatic radii (vdw=elec_radius)'
    else:
        atoms = selected_atoms(cmd, selection, state)
        radius_key = 'vdw'
        convention = 'existing molecule vdw radii'
    if not any(atom[radius_key] > 0 for atom in atoms):
        raise ValueError('Molecular surface requires at least one positive display radius')
    coverage = validate_coverage(grid, atoms, probe_radius, radius_key=radius_key)
    token = uuid.uuid4().hex
    prefix = 'molscene_potential_'+token
    names = {role: prefix+'_'+role for role in ('molecule','map','ramp','legend')}
    record = dict(names, owner=token, units=units, temperature=float(temperature), state=state,
                  dx_path=str(Path(dx_path).resolve()), dx_sha256=sha256(dx_path), grid=grid_metadata(grid),
                  radius_convention=convention, probe_radius=probe_radius, coverage=coverage,
                  selection_sha256=selection_digest(atoms), owned=list(names.values()))
    record['legend_settings'] = dict(label_color='black',label_size=18,label_font_id=7,
        label_outline_color='white',label_relative_mode=1,
        label_screen_point=[-.95,.90,0.],label_position=[1.,0.,0.],label_multiline_justification=1)
    try:
        if pqr_path is not None:
            from chempy import Atom
            from chempy.models import Indexed
            model = Indexed()
            for source in atoms:
                atom = Atom()
                atom.name, atom.resn, atom.chain, atom.resi = source['name'], source['resn'], source['chain'], source['resi']
                atom.symbol = source['element'] or 'X'
                atom.coord = [source[c] for c in ('x','y','z')]
                atom.vdw = atom.elec_radius = source['radius']
                atom.partial_charge = source['charge']
                model.add_atom(atom)
            cmd.load_model(model, record['molecule'], state=1)
            # chempy load_model does not carry elec_radius in every supported
            # PyMOL build; explicitly set both public atom properties.
            radius_by_identity = {(a['chain'],a['resi'],a['resn'],a['name']):a['radius'] for a in atoms}
            cmd.alter(record['molecule'], 'elec_radius=radii[(chain,resi,resn,name)]; vdw=elec_radius',
                      space={'radii':radius_by_identity})
        else:
            cmd.create(record['molecule'], selection, source_state=state, target_state=1)
        record.update(load_native_dx(cmd,grid,name=record['map'],state=1))
        cmd.hide('everything', record['molecule'])
        cmd.set('surface_solvent', 0, record['molecule'])
        cmd.set('solvent_radius', probe_radius, record['molecule'])
        cmd.set('surface_ramp_above_mode', 0, record['molecule'])
        cmd.show('surface', record['molecule'])
        center = [sum(a[c] for a in atoms)/len(atoms) for c in ('x','y','z')]
        center[1] = max(a['y']+a[radius_key] for a in atoms)+probe_radius+2
        cmd.pseudoatom(record['legend'], pos=center, label='Potential', vdw=0.)
        cmd.hide('everything', record['legend'])
        cmd.show('labels', record['legend'])
        for key,value in record['legend_settings'].items():
            cmd.set(key,value,record['legend'])
        cmd.set_title(record['molecule'], 1, token)
        cmd.set_title(record['legend'], 1, token)
        _set_ramp(cmd, record, grid, limit)
        cmd.set('surface_color', record['ramp'], record['molecule'])
        return record
    except BaseException:
        for name in names.values():
            cmd.delete(name)
        raise


def update_potential_ramp(cmd, record, *, ramp_limit):
    limit = _positive(ramp_limit, 'Ramp limit')
    result = copy.deepcopy(record)
    token = result.get('owner')
    if not isinstance(token, str) or len(token) != 32 or any(c not in '0123456789abcdef' for c in token):
        raise ValueError('Invalid potential ownership record')
    types = {'molecule':'object:molecule', 'map':'object:map', 'ramp':'object:ramp', 'legend':'object:molecule'}
    for role, expected in types.items():
        name = 'molscene_potential_'+token+'_'+role
        if result.get(role) != name or name not in cmd.get_names('objects') or cmd.get_type(name) != expected:
            raise ValueError('Owned potential objects are missing or changed')
    if any(cmd.get_title(result[role], 1) != token for role in ('molecule','legend')):
        raise ValueError('Potential ownership changed')
    if sha256(result['dx_path']) != result['dx_sha256']:
        raise ValueError('Potential source map changed')
    grid = read_dx(result['dx_path'], value_unit=result['units'])
    _set_ramp(cmd, result, grid, limit)
    return result
