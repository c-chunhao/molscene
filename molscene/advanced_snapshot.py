"""Private live identities bound to original members, not later result copies."""
from copy import deepcopy
import hashlib
import json


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
    if not rows:
        return True
    expression = ' or '.join('(%' + row['object'] + ' and index ' + str(row['index']) + ')' for row in rows)
    return _rows(cmd, expression, state) == rows


def result_status(cmd, identity, *, maps=(), timeline=None, session_epoch=None):
    from .artifacts import sha256
    from .density import map_fingerprint, _verify_record
    required = {'schema_version', 'session_epoch', 'members', 'maps', 'files', 'timeline',
                'display_snapshot', 'selection', 'verified'}
    if (not isinstance(identity, dict) or not required <= set(identity)
            or type(identity.get('schema_version')) is not int or identity['schema_version'] != 1
            or type(identity.get('verified')) is not bool
            or any(not isinstance(identity[key], dict) for key in ('members', 'maps', 'files'))):
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
