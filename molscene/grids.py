"""Bounded scalar grid I/O and explicit physical coordinate transforms.

No backend is imported here. Values are owned float64 arrays in XYZ order,
with Z varying fastest; callers must treat their contents as read-only.
"""
from array import array
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import struct
import sys

from .artifacts import checked_path, sha256

MAX_BYTES = 512 * 1024 * 1024
MAX_VOXELS = 16_777_216


def _shape(shape):
    if len(shape) != 3 or any(type(n) is not int or not 2 <= n <= 4096 for n in shape):
        raise ValueError('Grid dimensions must be integers in 2..4096')
    count = math.prod(shape)
    if count > MAX_VOXELS:
        raise ValueError('Grid exceeds voxel limit')
    return count


def _finite(values):
    try:
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
            raise ValueError('Expected finite numbers')
        return tuple(float(v) for v in values)
    except (TypeError, OverflowError) as exc:
        raise ValueError('Expected finite numbers') from exc


@dataclass(frozen=True)
class Grid:
    shape: tuple[int, int, int]
    origin: tuple[float, float, float]
    basis: tuple[tuple[float, float, float], ...]
    values: array
    value_unit: str
    metadata: dict

    def __post_init__(self):
        count = _shape(self.shape)
        if len(self.origin) != 3 or len(self.basis) != 3 or any(len(b) != 3 for b in self.basis):
            raise ValueError('Grid geometry requires three coordinates and three basis vectors')
        _finite(self.origin)
        for b in self.basis:
            _finite(b)
        if not isinstance(self.values, array) or self.values.typecode != 'd' or len(self.values) != count:
            raise ValueError('Grid values must be an owned float64 array matching shape')
        if not all(math.isfinite(v) for v in self.values):
            raise ValueError('Grid values must be finite')
        if not isinstance(self.value_unit, str) or not isinstance(self.metadata, dict):
            raise ValueError('Invalid grid units or metadata')


def grid_point(grid, i, j, k):
    indexes = (i, j, k)
    if any(type(v) is not int or not 0 <= v < n for v, n in zip(indexes, grid.shape)):
        raise ValueError('Grid index is out of bounds')
    return tuple(grid.origin[a] + sum(indexes[b]*grid.basis[b][a] for b in range(3)) for a in range(3))


def grid_metadata(grid):
    values = grid.values
    scale = max(abs(min(values)), abs(max(values)))
    mean_scaled = math.fsum(v/scale for v in values)/len(values) if scale else 0.0
    sd_scaled = math.sqrt(math.fsum((v/scale-mean_scaled)**2 for v in values)/len(values)) if scale else 0.0
    digest = hashlib.sha256()
    for start in range(0, len(values), 8192):
        chunk = values[start:start+8192]
        if sys.byteorder != 'little':
            chunk.byteswap()
        digest.update(chunk.tobytes())
    return dict(shape=list(grid.shape), origin_angstrom=list(grid.origin),
                basis_angstrom=[list(v) for v in grid.basis], value_unit=grid.value_unit,
                statistics=dict(min=min(values), max=max(values), mean=scale*mean_scaled,
                                population_sd=scale*sd_scaled), value_sha256=digest.hexdigest(),
                source_format=grid.metadata.get('source_format', 'unknown'),
                interpretation=grid.metadata.get('interpretation', 'native'),
                normalization=grid.metadata.get('normalization', 'none'))


def _input(path):
    path = checked_path(path)
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError('Grid must be a regular file no larger than 512 MiB')
    return path


def _dx_tokens(handle):
    """Incremental whitespace/quoted-string lexer with bounded token storage."""
    token, quote, comment = '', False, False
    total = 0
    while True:
        chunk = handle.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_BYTES:
            raise ValueError('Grid exceeds byte limit')
        for char in chunk:
            if comment:
                if char == '\n':
                    comment = False
                continue
            if char == '"':
                quote = not quote
            elif not quote and (char.isspace() or char == '#'):
                if token:
                    yield token
                    token = ''
                comment = char == '#'
            else:
                token += char
                if len(token) > 4096:
                    raise ValueError('DX token is too long')
    if quote:
        raise ValueError('Unterminated DX string')
    if token:
        yield token


def read_dx(path, *, value_unit='unknown'):
    path = _input(path)
    positions = connections = origin = values = None
    deltas, objects, components = [], {}, {}
    try:
        with path.open('r', encoding='ascii') as handle:
            tokens = iter(_dx_tokens(handle))
            def take(expected=None):
                value = next(tokens)
                if expected is not None and value != expected:
                    raise ValueError('Unsupported or malformed DX: expected '+expected)
                return value
            def vector():
                return _finite(tuple(float(take()) for _ in range(3)))
            for word in tokens:
                if word == 'object':
                    name = take()
                    if name in objects:
                        raise ValueError('Duplicate DX object')
                    take('class')
                    kind = take()
                    objects[name] = kind
                    if kind in ('gridpositions', 'gridconnections'):
                        take('counts')
                        counts = tuple(int(take()) for _ in range(3))
                        _shape(counts)
                        if kind == 'gridpositions' and positions is None:
                            positions = counts
                        elif kind == 'gridconnections' and connections is None:
                            connections = counts
                        else:
                            raise ValueError('Multiple DX grids are unsupported')
                    elif kind == 'array':
                        if values is not None or positions is None:
                            raise ValueError('DX requires one scalar array after gridpositions')
                        take('type')
                        if take() not in ('float', 'double'):
                            raise ValueError('Unsupported DX scalar type')
                        take('rank'); take('0'); take('items')
                        count = int(take())
                        if count != _shape(positions):
                            raise ValueError('DX item count does not match grid')
                        take('data'); take('follows')
                        values = array('d')
                        for _ in range(count):
                            value = float(take())
                            if not math.isfinite(value):
                                raise ValueError('DX values must be finite')
                            values.append(value)
                    elif kind != 'field':
                        raise ValueError('Unsupported DX object class')
                elif word == 'origin' and origin is None:
                    origin = vector()
                elif word == 'delta' and len(deltas) < 3:
                    deltas.append(vector())
                elif word == 'attribute':
                    take('dep'); take('string'); take('positions')
                elif word == 'component':
                    role = take(); take('value'); reference = take()
                    if role not in ('positions', 'connections', 'data') or role in components:
                        raise ValueError('Invalid DX field component')
                    components[role] = reference
                else:
                    raise ValueError('Unexpected DX data or declaration: '+word[:80])
    except (StopIteration, UnicodeError, OverflowError) as exc:
        raise ValueError('Truncated or invalid ASCII DX') from exc
    if positions is None or positions != connections or origin is None or values is None or len(deltas) != 3:
        raise ValueError('Incomplete or inconsistent DX grid')
    for axis, delta in enumerate(deltas):
        if delta[axis] <= 0 or any(delta[j] != 0 for j in range(3) if j != axis):
            raise ValueError('DX requires positive diagonal deltas')
    for role, kind in (('positions', 'gridpositions'), ('connections', 'gridconnections'), ('data', 'array')):
        if role in components and objects.get(components[role]) != kind:
            raise ValueError('DX component refers to the wrong object')
    return Grid(positions, origin, tuple(deltas), values, value_unit,
                dict(source_format='dx', interpretation='native', normalization='none', source_sha256=sha256(path)))


def write_dx(path, grid):
    # Validate before opening a destination: writers never silently discard skew.
    Grid(grid.shape, grid.origin, grid.basis, grid.values, grid.value_unit, grid.metadata)
    for axis, delta in enumerate(grid.basis):
        if delta[axis] <= 0 or any(delta[j] != 0 for j in range(3) if j != axis):
            raise ValueError('DX requires positive diagonal deltas')
    with checked_path(path).open('w', encoding='ascii', newline='\n') as handle:
        counts = ' '.join(map(str, grid.shape))
        handle.write('object 1 class gridpositions counts '+counts+'\n')
        handle.write('origin '+' '.join(format(x, '.17g') for x in grid.origin)+'\n')
        for delta in grid.basis:
            handle.write('delta '+' '.join(format(x, '.17g') for x in delta)+'\n')
        handle.write('object 2 class gridconnections counts '+counts+'\n')
        handle.write('object 3 class array type double rank 0 items '+str(len(grid.values))+' data follows\n')
        for start in range(0, len(grid.values), 3):
            handle.write(' '.join(format(x, '.17g') for x in grid.values[start:start+3])+'\n')
        handle.write('attribute "dep" string "positions"\nobject "grid" class field\n'
                     'component "positions" value 1\ncomponent "connections" value 2\ncomponent "data" value 3\n')


def read_mrc(path, *, format, interpretation='auto', value_unit='unknown'):
    if format not in ('mrc', 'ccp4') or interpretation not in ('auto', 'origin', 'starts'):
        raise ValueError('Invalid MRC format or interpretation')
    if interpretation == 'origin' and format != 'mrc':
        raise ValueError('Origin interpretation requires format=mrc')
    path = _input(path)
    with path.open('rb') as handle:
        header = handle.read(1024)
        if len(header) != 1024 or header[208:212] != b'MAP ':
            raise ValueError('Invalid MRC header or MAP signature')
        stamp = header[212:216]
        if stamp[:2] in (b'DA', b'DD'):
            endian = '<'
        elif stamp[:2] == b'\x11\x11':
            endian = '>'
        else:
            raise ValueError('Unknown MRC machine stamp')
        def unpack(fmt, offset):
            return struct.unpack_from(endian+fmt, header, offset)
        dims = unpack('3i', 0)
        count = _shape(dims)
        mode = unpack('i', 12)[0]
        starts, sampling = unpack('3i', 16), unpack('3i', 28)
        lengths, angles = _finite(unpack('3f', 40)), _finite(unpack('3f', 52))
        axes = unpack('3i', 64)
        spacegroup, extended = unpack('2i', 88)
        origin_header = _finite(unpack('3f', 196))
        if mode != 2 or unpack('i', 96)[0] != 0:
            raise ValueError('Only MRC float32 mode 2 without header skew is supported')
        if sorted(axes) != [1, 2, 3] or any(s <= 0 for s in sampling):
            raise ValueError('Invalid MRC sampling or file-axis mapping')
        if extended < 0 or 1024+extended+count*4 > path.stat().st_size:
            raise ValueError('Invalid MRC extended header or truncated payload')
        if any(v <= 0 for v in lengths) or any(not 0 < a < 180 for a in angles):
            raise ValueError('Invalid or singular MRC cell')
        ca, cb, cg = (math.cos(math.radians(a)) for a in angles)
        sg = math.sin(math.radians(angles[2]))
        z_squared = 1-cb*cb-((ca-cb*cg)/sg)**2
        if z_squared <= 1e-12:
            raise ValueError('Singular MRC cell')
        a, b, c = (lengths[i]/sampling[i] for i in range(3))
        basis = ((a, 0., 0.), (b*cg, b*sg, 0.), (c*cb, c*(ca-cb*cg)/sg, c*math.sqrt(z_squared)))
        shape, xyz_starts = [0]*3, [0]*3
        for index, axis in enumerate(axes):
            shape[axis-1], xyz_starts[axis-1] = dims[index], starts[index]
        if interpretation == 'auto':
            if any(origin_header) and any(starts):
                raise ValueError('MRC origin and starts are ambiguous; choose origin or starts interpretation')
            if any(origin_header) and format != 'mrc':
                raise ValueError('Nonzero ORIGIN requires format=mrc or explicit starts interpretation')
            effective = 'origin' if any(origin_header) else 'starts'
        else:
            effective = interpretation
        actual_format = 'ccp4' if interpretation == 'starts' else format
        origin = origin_header if effective == 'origin' else tuple(sum(xyz_starts[j]*basis[j][i] for j in range(3)) for i in range(3))
        values = array('d', [0.0])*count
        handle.seek(1024+extended)
        # Rows are bounded by the per-dimension limit; do not duplicate the volume.
        for section in range(dims[2]):
            for row in range(dims[1]):
                raw = handle.read(dims[0]*4)
                if len(raw) != dims[0]*4:
                    raise ValueError('Truncated MRC payload')
                for column, (value,) in enumerate(struct.iter_unpack(endian+'f', raw)):
                    if not math.isfinite(value):
                        raise ValueError('MRC values must be finite')
                    xyz = [0, 0, 0]
                    for axis, index in zip(axes, (column, row, section)):
                        xyz[axis-1] = index
                    values[(xyz[0]*shape[1]+xyz[1])*shape[2]+xyz[2]] = value
    metadata = dict(source_format=actual_format, requested_format=format, interpretation=effective,
                    normalization='none', source_sha256=sha256(path), file_axis_mapping=list(axes),
                    header=dict(dimensions=list(dims), mode=mode, starts=list(starts), sampling=list(sampling),
                                cell_lengths=list(lengths), cell_angles=list(angles), origin=list(origin_header),
                                spacegroup=spacegroup, extended_header_bytes=extended,
                                endian='little' if endian == '<' else 'big', machine_stamp=stamp.hex()))
    return Grid(tuple(shape), origin, basis, values, value_unit, metadata)


def _affine(matrix):
    if not isinstance(matrix, (list, tuple)) or len(matrix) != 16:
        raise ValueError('Matrix must contain 16 row-major numbers')
    matrix = _finite(matrix)
    if any(abs(matrix[12+i]-v) > 1e-8 for i, v in enumerate((0, 0, 0, 1))):
        raise ValueError('Matrix must be affine')
    return matrix


def _det(m):
    return m[0]*(m[5]*m[10]-m[6]*m[9])-m[1]*(m[4]*m[10]-m[6]*m[8])+m[2]*(m[4]*m[9]-m[5]*m[8])


def validate_rigid(matrix):
    matrix = _affine(matrix)
    for i in range(3):
        for j in range(3):
            if abs(sum(matrix[k*4+i]*matrix[k*4+j] for k in range(3))-(1 if i == j else 0)) > 1e-5:
                raise ValueError('Rigid rotation must be orthonormal')
    if abs(_det(matrix)-1) > 1e-5:
        raise ValueError('Rigid rotation must have determinant +1')
    return matrix


def compose(a, b):
    a, b = _affine(a), _affine(b)
    return _finite(tuple(sum(a[i*4+k]*b[k*4+j] for k in range(4)) for i in range(4) for j in range(4)))


def inverse_affine(matrix):
    m = _affine(matrix)
    determinant = _det(m)
    if not math.isfinite(determinant) or determinant == 0:
        raise ValueError('Affine matrix is singular')
    inverse = [(m[5]*m[10]-m[6]*m[9])/determinant, (m[2]*m[9]-m[1]*m[10])/determinant, (m[1]*m[6]-m[2]*m[5])/determinant, 0.,
               (m[6]*m[8]-m[4]*m[10])/determinant, (m[0]*m[10]-m[2]*m[8])/determinant, (m[2]*m[4]-m[0]*m[6])/determinant, 0.,
               (m[4]*m[9]-m[5]*m[8])/determinant, (m[1]*m[8]-m[0]*m[9])/determinant, (m[0]*m[5]-m[1]*m[4])/determinant, 0., 0., 0., 0., 1.]
    for i in range(3):
        inverse[i*4+3] = -sum(inverse[i*4+k]*m[k*4+3] for k in range(3))
    return _finite(inverse)
