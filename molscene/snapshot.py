"""Semantic scene identity shared by desktop jobs and session export."""
import hashlib
import json

from . import analysis


def scene_fingerprint(cmd, state=1):
    """Hash source atom identities/properties/coordinates, excluding camera/cache."""
    atoms = analysis._collect_atoms(cmd, 'all', state, allow_empty=True)
    payload = json.dumps({'state': state, 'atoms': atoms}, sort_keys=True,
                         separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def record_snapshot_status(cmd, record, state=None, fingerprints=None):
    """Verify all explicitly recorded states, retaining legacy global-state semantics.

    Multi-state records describe explicit calculation states independently of the
    displayed frame. Older single-state records still require that frame to match.
    An older mixed-state alignment cannot verify its unrecorded target state.
    """
    if record.get('operation') == 'polar' or record.get('kind') == 'polar':
        from .polar import chemistry_fingerprint
        requested = record.get('state')
        source = record.get('chemistry_fingerprint')
        if type(requested) is not int or requested < 1 or not isinstance(source, str) or not source:
            return 'unverified'
        try:
            return 'current' if chemistry_fingerprint(cmd, requested) == source else 'stale'
        except (ValueError, RuntimeError):
            return 'stale'
    current_state = int(cmd.get_state()) if state is None else state
    cache = {} if fingerprints is None else fingerprints

    def fingerprint(value):
        if value not in cache:
            cache[value] = scene_fingerprint(cmd, value)
        return cache[value]

    required = [record.get('state')]
    if record.get('operation') in ('align', 'alignment') or record.get('kind') == 'alignment':
        parameters = record.get('parameters', {})
        if not isinstance(parameters, dict):
            return 'unverified'
        required.extend(parameters.get(key, record.get(key, record.get('state')))
                        for key in ('mobile_state', 'target_state'))
    sources = record.get('source_fingerprints')
    if any(type(value) is not int or value < 1 for value in required):
        if sources is None and record.get('source_fingerprint'):
            return 'stale'
        return 'unverified'
    if sources is not None:
        if not isinstance(sources, dict) or not sources:
            return 'unverified'
        if not {str(value) for value in required}.issubset(sources):
            return 'unverified'
        if any(not isinstance(key, str) or not key.isascii() or not key.isdecimal()
               or int(key) < 1 or str(int(key)) != key or not isinstance(value, str) or not value
               for key, value in sources.items()):
            return 'unverified'
        try:
            return 'current' if all(fingerprint(int(key)) == value for key, value in sources.items()) else 'stale'
        except (ValueError, RuntimeError):
            return 'stale'
    source = record.get('source_fingerprint')
    if not source or len(set(required)) > 1:
        return 'unverified'
    try:
        return 'current' if record.get('state') == current_state and fingerprint(current_state) == source else 'stale'
    except (ValueError, RuntimeError):
        return 'stale'
