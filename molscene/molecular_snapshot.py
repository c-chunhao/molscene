"""Compact private identities for the original molecular workflow sources.

Only frozen native indices and one digest per required state are retained.
Checking currentness always reads the original members; there is no live cache.
"""
import hashlib
import json
from pathlib import Path
import re

from .artifacts import checked_path, sha256


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def frozen_selection(name, indices):
    return '(%' + name + ') and index ' + '+'.join(map(str, indices))


def _members(cmd, name, indices, state):
    from .analysis import collect_atoms
    rows = collect_atoms(cmd, frozen_selection(name, indices), state=state)
    if sorted(row['index'] for row in rows) != indices or any(row['object'] != name for row in rows):
        raise ValueError('Original trajectory membership changed')
    return rows


def capture(cmd, operation, request, *, session_epoch):
    """Return compact context plus the privately frozen executable request."""
    from copy import deepcopy
    context = dict(molecular_schema=1, operation=operation, session_epoch=session_epoch, files={}, trajectory=None)
    frozen = deepcopy(request)
    if operation == 'trajectory':
        from .trajectory import trajectory_plan, trajectory_topology
        name = request['object_name']
        if request['trajectory_path'] is not None:
            topology = trajectory_topology(cmd, name)
            indices = sorted(row['index'] for row in topology)
            required = [1]
            count = len(topology)
        else:
            plan = trajectory_plan(cmd, request)
            indices = sorted({row['index'] for row in plan['measurement_atoms'] + plan['fit_atoms']})
            required = plan['required_states']
            count = len(plan['topology_atoms'])
            for key, rows in [('selection', plan['measurement_atoms']), ('fit_selection', plan['fit_atoms'])]:
                if rows:
                    frozen[key] = frozen_selection(name, sorted(row['index'] for row in rows))
        context['trajectory'] = dict(object_name=name, indices=indices, topology_count=count,
            state_count=int(cmd.count_states('%' + name)),
            dcd=request['trajectory_path'] is not None,
            states={str(state): _digest(_members(cmd, name, indices, state)) for state in required})
    return context, frozen


def valid(context):
    """Reject missing, malformed or weakened evidence before any status claim."""
    from .trajectory import MAX_ATOMS, MAX_FRAMES, MAX_SAMPLES, validate_trajectory_request
    def digest(value):
        return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None
    if (not isinstance(context, dict) or type(context.get('molecular_schema')) is not int
            or context['molecular_schema'] != 1 or context.get('operation') not in ('assembly', 'trajectory')
            or type(context.get('session_epoch')) is not int or context['session_epoch'] < 0
            or not isinstance(context.get('files'), dict)
            or any(not isinstance(p, str) or '\0' in p or not Path(p).is_absolute() or not digest(h) for p, h in context['files'].items())):
        return False
    request = context.get('parameters')
    originals = context.get('original_inputs')
    if not isinstance(request, dict) or not isinstance(originals, list):
        return False
    expected_roles = {key for key in ('source_path', 'trajectory_path') if request.get(key) is not None}
    if (len(originals) != len(expected_roles)
            or any(not isinstance(item, dict) or set(item) != {'role', 'path', 'copy', 'sha256'}
                   or item['role'] not in expected_roles or not isinstance(item['path'], str)
                   or '\0' in item['path'] or not Path(item['path']).is_absolute() or not isinstance(item['copy'], str)
                   or '\0' in item['copy'] or not Path(item['copy']).is_absolute() or not digest(item['sha256'])
                   or request[item['role']] != item['copy'] for item in originals)
            or {item['role'] for item in originals} != expected_roles
            or context['files'] != {item['path']: item['sha256'] for item in originals}):
        return False
    if context['operation'] == 'assembly':
        from .assembly import validate_assembly_request
        try:
            return (context.get('trajectory', False) is None and len(context['files']) == 1
                    and context.get('snapshot_id', False) is None and validate_assembly_request(request) == request)
        except (ValueError, TypeError):
            return False
    if not digest(context.get('snapshot_id')):
        return False
    row = context.get('trajectory')
    if (not isinstance(row, dict) or set(row) != {'object_name', 'indices', 'topology_count', 'state_count', 'dcd', 'states'}
            or not isinstance(row['object_name'], str) or re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,254}', row['object_name']) is None
            or type(row['dcd']) is not bool or type(row['topology_count']) is not int
            or type(row['state_count']) is not int or row['state_count'] < 1
            or not 1 <= row['topology_count'] <= MAX_ATOMS
            or not isinstance(row['indices'], list) or not 1 <= len(row['indices']) <= row['topology_count']
            or any(type(i) is not int or i < 1 for i in row['indices'])
            or sorted(set(row['indices'])) != row['indices'] or not isinstance(row['states'], dict)
            or not 1 <= len(row['states']) <= MAX_FRAMES + 1
            or len(row['indices']) * len(row['states']) > MAX_SAMPLES
            or any(not isinstance(s, str) or re.fullmatch('[1-9][0-9]{0,5}', s) is None
                   or int(s) > 100000 or not digest(h) for s, h in row['states'].items())):
        return False
    try:
        request = validate_trajectory_request(context.get('parameters'))
        if request != context['parameters'] or request['object_name'] != row['object_name'] or row['dcd'] != (request['trajectory_path'] is not None):
            return False
        if row['dcd']:
            return (set(row['states']) == {'1'} and row['state_count'] == 1
                    and len(row['indices']) == row['topology_count'] and len(context['files']) == 1)
        stop = row['state_count'] if request['stop'] is None else request['stop']
        sampled = range(request['start'], stop + 1, request['stride'])
        if (not 2 <= len(sampled) <= MAX_FRAMES or stop > row['state_count']
                or request['reference_state'] > row['state_count']
                or set(row['states']) != {str(s) for s in [*sampled, request['reference_state']]}):
            return False
        prefix = '(%' + row['object_name'] + ') and index '
        union = set()
        for key in ['selection'] + (['fit_selection'] if request['align'] else []):
            expression = request[key]
            if not expression.startswith(prefix):
                return False
            indices = [int(i) for i in expression[len(prefix):].split('+')]
            if indices != sorted(set(indices)) or any(i < 1 for i in indices):
                return False
            union.update(indices)
        return sorted(union) == row['indices'] and not context['files']
    except (ValueError, TypeError, KeyError, OverflowError):
        return False


def result_status(cmd, context, *, session_epoch):
    if not valid(context) or type(session_epoch) is not int:
        return 'unverified'
    if context['session_epoch'] != session_epoch:
        return 'stale'
    try:
        if any(sha256(checked_path(path)) != digest for path, digest in context['files'].items()):
            return 'stale'
        row = context['trajectory']
        if row is not None:
            name = row['object_name']
            if (name not in cmd.get_names('objects') or cmd.get_type(name) != 'object:molecule'
                    or cmd.count_atoms('%' + name) != row['topology_count']
                    or row['dcd'] and cmd.count_states('%' + name) != 1):
                return 'stale'
            for state, digest in row['states'].items():
                # collect_atoms rejects absent states instead of native fallback.
                if _digest(_members(cmd, name, row['indices'], int(state))) != digest:
                    return 'stale'
    except Exception:
        return 'stale'
    return 'current'


def report_matches(context, report):
    """Bind a portable report's public paths/request to its private source roles."""
    from copy import deepcopy
    if not valid(context) or not isinstance(report, dict) or report.get('operation') != context['operation']:
        return False
    expected = {}
    request = deepcopy(context['parameters'])
    for item in context['original_inputs']:
        role = {'source_path': 'source', 'trajectory_path': 'trajectory'}[item['role']]
        relative = ('inputs/source' + Path(item['copy']).suffix.lower()
                    if role == 'source' else 'inputs/trajectory.dcd')
        expected[role] = (relative, item['sha256'])
        request[item['role']] = relative
    if context['snapshot_id'] is not None:
        expected['session'] = ('source.pse', context['snapshot_id'])
    inputs, hashes = report.get('inputs'), report.get('artifact_hashes')
    if (report.get('snapshot_id') != context['snapshot_id'] or report.get('parameters') != request
            or not isinstance(inputs, dict) or set(inputs) != set(expected) or not isinstance(hashes, dict)):
        return False
    for role, (relative, digest) in expected.items():
        row = inputs[role]
        if (not isinstance(row, dict) or row.get('path') != relative or row.get('sha256') != digest
                or type(row.get('bytes')) is not int or row['bytes'] <= 0 or hashes.get(relative) != digest):
            return False
    return context['operation'] != 'assembly' or report.get('source_sha256') == expected['source'][1]
