"""Private live identities bound to original members, not later result copies."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _rows(cmd, selection, state):
    from .analysis import collect_atoms
    return [{key: value for key, value in row.items() if key != 'b'}
            for row in collect_atoms(cmd, selection, state)]


def capture_identity(cmd, *, states=(), maps=(), timeline=None, selection=None, state=1, session_epoch=None):
    from .density import map_fingerprint, map_inventory
    from .timeline import validate_timeline
    if session_epoch is not None and (type(session_epoch) is not int or session_epoch < 0):
        raise ValueError('Session epoch must be a nonnegative integer or null')
    identity = dict(schema_version=1, session_epoch=session_epoch, members={}, maps={}, files={},
                    timeline=None, display_snapshot=None, selection=None, verified=True)
    for current in sorted(set(states)):
        names = [name for name in cmd.get_names('objects') if cmd.get_type(name) == 'object:molecule'
                 and cmd.get_coordset(name, state=current, copy=1) is not None]
        identity['members'][str(current)] = _rows(cmd, ' or '.join('(%' + name + ')' for name in names), current) if names else []
    if selection is not None:
        identity['selection'] = dict(state=state, members=_rows(cmd, selection, state))
    records = {(row['name'], row['state']): row for row in map_inventory(cmd, maps)} if maps else {}
    for record in maps:
        current = records.get((record['name'], record['state']))
        key = record['name'] + '/' + str(record['state'])
        if not current or not current['verified']:
            identity['verified'] = False
            continue
        identity['maps'][key] = dict(record=deepcopy(current), digest=map_fingerprint(cmd, current))
        identity['files'][current['source_path']] = current['source_sha256']
    if timeline is not None:
        identity['timeline'] = _digest(validate_timeline(timeline, cmd))
        # A live camera/scene cannot prove equality to a frozen display PSE.
        identity['display_snapshot'] = 'unverified'
    return identity


def _same_members(cmd, rows, state):
    if rows == []:
        return True
    expression = ' or '.join('(%' + row['object'] + ' and index ' + str(row['index']) + ')' for row in rows)
    return _rows(cmd, expression, state) == rows


def _state(value):
    return type(value) is int and 1 <= value <= 100000


def _hash(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def _path(value):
    return isinstance(value, str) and '\0' not in value and Path(value).is_absolute()


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _vector(value, length):
    return isinstance(value, list) and len(value) == length and all(_number(item) for item in value)


def _members(rows, state):
    text_keys = {'object', 'segi', 'chain', 'resi', 'resn', 'name', 'element', 'alt'}
    keys = text_keys | {'index', 'state', 'coord', 'vdw'}
    if not isinstance(rows, list):
        return False
    seen = set()
    for row in rows:
        if (not isinstance(row, dict) or set(row) != keys
                or any(not isinstance(row[key], str) or '\0' in row[key] for key in text_keys)
                or not row['object'] or type(row['index']) is not int or row['index'] < 1
                or not _state(row['state']) or row['state'] != state
                or not _vector(row['coord'], 3) or not _number(row['vdw']) or row['vdw'] < 0):
            return False
        member = row['object'], row['index']
        if member in seen:
            return False
        seen.add(member)
    return True


def _map_evidence(key, evidence):
    if not isinstance(evidence, dict) or set(evidence) != {'record', 'digest'} or not _hash(evidence['digest']):
        return False
    row = evidence['record']
    keys = {'name', 'state', 'source_path', 'source_sha256', 'format', 'interpretation', 'grid',
            'load_matrix', 'world_matrix', 'value_sha256', 'verified', 'derived', 'native_geometry', 'native_precision'}
    if (not isinstance(row, dict) or set(row) != keys or not isinstance(row['name'], str) or not row['name']
            or not _state(row['state']) or key != row['name'] + '/' + str(row['state'])
            or row['verified'] is not True or not _path(row['source_path'])
            or not _hash(row['source_sha256']) or not _hash(row['value_sha256'])
            or row['format'] not in ('dx', 'mrc', 'ccp4') or row['interpretation'] not in ('native', 'auto', 'origin', 'starts')
            or not _vector(row['load_matrix'], 16) or not _vector(row['world_matrix'], 16)
            or not isinstance(row['derived'], list) or any(not isinstance(name, str) or not name for name in row['derived'])):
        return False
    grid = row['grid']
    grid_keys = {'shape', 'origin_angstrom', 'basis_angstrom', 'value_unit', 'statistics', 'value_sha256',
                 'source_format', 'interpretation', 'normalization'}
    if (not isinstance(grid, dict) or set(grid) != grid_keys or not isinstance(grid['shape'], list)
            or len(grid['shape']) != 3 or any(type(n) is not int or not 2 <= n <= 4096 for n in grid['shape'])
            or not _vector(grid['origin_angstrom'], 3) or not isinstance(grid['basis_angstrom'], list)
            or len(grid['basis_angstrom']) != 3 or any(not _vector(v, 3) for v in grid['basis_angstrom'])
            or not _hash(grid['value_sha256'])
            or any(not isinstance(grid[k], str) or not grid[k] for k in ('value_unit', 'source_format', 'interpretation', 'normalization'))):
        return False
    stats = grid['statistics']
    precision = row['native_precision']
    if (not isinstance(stats, dict) or set(stats) != {'min', 'max', 'mean', 'population_sd'}
            or any(not _number(value) for value in stats.values()) or stats['population_sd'] < 0
            or not isinstance(precision, dict) or not {'storage', 'max_abs_quantization_error'} <= set(precision)
            or set(precision) - {'storage', 'max_abs_quantization_error', 'underflow_sites', 'loader'}
            or precision['storage'] != 'float32' or not _number(precision['max_abs_quantization_error'])
            or precision['max_abs_quantization_error'] < 0):
        return False
    if 'underflow_sites' in precision and (type(precision['underflow_sites']) is not int or precision['underflow_sites'] < 0):
        return False
    if 'loader' in precision and not isinstance(precision['loader'], str):
        return False
    geometry = row['native_geometry']
    geometry_keys = {'symmetry', 'origin', 'range', 'dimensions', 'spacing', 'corners', 'minimum', 'maximum',
                     'map_source', 'divisions', 'starts', 'ends', 'shape'}
    if (not isinstance(geometry, dict) or set(geometry) != geometry_keys
            or not isinstance(geometry['shape'], list) or any(type(n) is not int for n in geometry['shape'])
            or geometry['shape'] != grid['shape']
            or not _vector(geometry['corners'], 24) or not _vector(geometry['minimum'], 3)
            or not _vector(geometry['maximum'], 3) or type(geometry['map_source']) is not int):
        return False
    for name in ('origin', 'range', 'dimensions', 'spacing', 'divisions', 'starts', 'ends'):
        if geometry[name] is not None and not _vector(geometry[name], 3):
            return False
    def symmetry_value(value):
        return value is None or isinstance(value, str) or _number(value) or (
            isinstance(value, list) and all(symmetry_value(item) for item in value))
    return symmetry_value(geometry['symmetry'])


def _valid_identity(identity):
    required = {'schema_version', 'session_epoch', 'members', 'maps', 'files', 'timeline',
                'display_snapshot', 'selection', 'verified'}
    if (not isinstance(identity, dict) or not required <= set(identity)
            or type(identity.get('schema_version')) is not int or identity['schema_version'] != 1
            or type(identity.get('verified')) is not bool
            or any(not isinstance(identity[key], dict) for key in ('members', 'maps', 'files'))):
        return False
    for key, rows in identity['members'].items():
        if (not isinstance(key, str) or re.fullmatch('[1-9][0-9]{0,5}', key) is None
                or not _state(int(key)) or not _members(rows, int(key))):
            return False
    selection = identity['selection']
    if selection is not None and (not isinstance(selection, dict) or set(selection) != {'state', 'members'}
            or not _state(selection['state']) or not _members(selection['members'], selection['state'])):
        return False
    if any(not _path(path) or not _hash(digest) for path, digest in identity['files'].items()):
        return False
    if any(not _map_evidence(key, row) for key, row in identity['maps'].items()):
        return False
    if identity['timeline'] is not None and not _hash(identity['timeline']):
        return False
    if identity['display_snapshot'] not in (None, 'unverified'):
        return False
    return True


def result_status(cmd, identity, *, maps=(), timeline=None, session_epoch=None):
    from .artifacts import sha256
    from .density import map_fingerprint, _verify_record
    if not _valid_identity(identity):
        return 'unverified'
    if (type(identity.get('session_epoch')) is not int or type(session_epoch) is not int
            or identity['session_epoch'] < 0 or session_epoch < 0):
        return 'unverified'
    if identity['session_epoch'] != session_epoch:
        return 'stale'
    try:
        for path, digest in identity.get('files', {}).items():
            if sha256(path) != digest:
                return 'stale'
        for state, rows in identity.get('members', {}).items():
            if not _same_members(cmd, rows, int(state)):
                return 'stale'
        selection = identity.get('selection')
        if selection and not _same_members(cmd, selection['members'], selection['state']):
            return 'stale'
        for row in identity.get('maps', {}).values():
            _verify_record(cmd, row['record'])
            if map_fingerprint(cmd, row['record']) != row['digest']:
                return 'stale'
        if identity.get('timeline') is not None:
            from .timeline import validate_timeline
            if timeline is None or _digest(validate_timeline(timeline, cmd)) != identity['timeline']:
                return 'stale'
    except Exception:
        return 'stale'
    if not identity.get('verified') or identity.get('display_snapshot') is not None:
        return 'unverified'
    return 'current'
