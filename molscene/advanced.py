"""Shared, Qt-free advanced request validation and service dispatch."""
from copy import deepcopy
import json
from pathlib import Path

from .artifacts import checked_path, sha256
from .bundles import CancelledError

EXPORTS = frozenset(('timeline_project', 'animation', 'density', 'potential', 'coordinates', 'session_save'))
PARAMETERS = {
    'timeline_project': {'timeline', 'cancel_file'},
    'animation': {'timeline', 'render', 'mp4', 'tools', 'cancel_file'},
    'density': {'request', 'cancel_file'},
    'potential': {'request', 'cancel_file'},
    'tools': {'tools', 'cancel_file'},
    'coordinates': {'request', 'cancel_file'},
    'session_save': {'request', 'cancel_file'},
}


def read_json(path, maximum=8 * 1024 * 1024):
    path = checked_path(path)
    if not path.is_file() or path.stat().st_size > maximum:
        raise ValueError('JSON must be a bounded regular file')
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError('Duplicate JSON key: ' + key)
            result[key] = value
        return result
    return json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=pairs,
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError('Nonfinite JSON: ' + x)))


def validate_request(operation, parameters):
    if operation not in PARAMETERS or not isinstance(parameters, dict) or set(parameters) - PARAMETERS[operation]:
        raise ValueError('Unexpected advanced operation or parameters')
    result = deepcopy(parameters)
    if 'cancel_file' in result:
        marker = result['cancel_file']
        if not isinstance(marker, str) or not Path(marker).is_absolute():
            raise ValueError('cancel_file must be an absolute path')
    if operation in ('coordinates', 'session_save'):
        from .persistence import validate_persistence_request
        result['request'] = validate_persistence_request(operation, result.get('request'))
    elif operation in ('timeline_project', 'animation'):
        from .timeline import validate_timeline
        result['timeline'] = validate_timeline(result.get('timeline'))
        if operation == 'animation':
            from .animation import _render_spec
            result['render'] = _render_spec(result.get('render', {}))
            result.setdefault('mp4', False)
            if type(result['mp4']) is not bool:
                raise ValueError('mp4 must be boolean')
            if result['mp4'] and 'tools' not in result:
                raise ValueError('MP4 requires explicit tools configuration')
    elif operation == 'density':
        from .density_export import validate_density_request
        result['request'] = validate_density_request(result.get('request'))
    elif operation == 'potential':
        from .electrostatics import validate_potential_request
        raw = result.get('request')
        result['request'] = validate_potential_request(raw)
        if result['request']['mode'] == 'import' and 'units' not in raw:
            raise ValueError('Potential import requires explicit units')
        if result['request']['mode'] != 'import' and not result['request']['tools']:
            raise ValueError('Computed potential requires explicit tools configuration')
    if operation == 'tools' or 'tools' in result:
        from .external_tools import _config
        result['tools'] = _config(result.get('tools'))
    return result


def source_paths(parameters):
    """Enumerate original scientific inputs and configured tool runtime files."""
    request = parameters.get('request', {})
    result = [checked_path(request[key]) for key in ('map_path', 'pqr_path', 'dx_path') if request.get(key)]
    config = parameters.get('tools') or request.get('tools') or {}
    for entry in config.get('tools', {}).values():
        result.append(checked_path(entry['argv'][0]))
        result.extend(checked_path(row['path']) for row in entry['runtime_files'])
        result.extend(checked_path(path) for path in entry['path_dirs'])
    return result


def validate_cancel(path, output, protected, *, tools=False):
    if path is None:
        return None
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise ValueError('cancel_file must be an absolute path')
    marker = checked_path(path)
    for source in [*protected, output]:
        source = checked_path(source)
        if (marker == source or marker in source.parents or source in marker.parents
                or marker.exists() and source.exists() and marker.samefile(source)):
            raise ValueError('cancel_file must be outside output and exclusive of sources/job/envelope')
    if marker.exists() and not marker.is_file():
        raise ValueError('cancel_file must be a regular marker')
    return marker.exists


def probe_tools(config, work_dir, *, cancelled=None, progress=None):
    from .external_tools import _config, probe_tool
    config = _config(config)
    result = dict(operation='tools', passed=True, cancelled=False, tools={})
    names = list(config['tools'])
    if progress:
        progress(dict(stage='tools', completed=0, total=len(names), message='Probing configured tools'))
    for index, name in enumerate(names):
        if cancelled and cancelled():
            result.update(passed=False, cancelled=True)
            break
        try:
            manifest = probe_tool(name, config, work_dir=Path(work_dir), cancelled=cancelled, progress=progress)
            result['tools'][name] = dict(available=True, manifest=manifest, error=None)
        except CancelledError as exc:
            result['tools'][name] = dict(available=False, manifest=getattr(exc, 'result', None), error=str(exc))
            result.update(passed=False, cancelled=True)
            break
        except (ValueError, RuntimeError, OSError) as exc:
            result['tools'][name] = dict(available=False, manifest=getattr(exc, 'result', None), error=str(exc))
            result['passed'] = False
        if progress:
            progress(dict(stage='tools', completed=index + 1, total=len(names), message='Probed ' + name))
    if cancelled and cancelled():
        result.update(passed=False, cancelled=True)
    return result


def run_advanced(operation, input_session, output_dir, parameters, *, cancelled=None, progress=None, protected=(), source_input=None):
    parameters = validate_request(operation, parameters)
    if operation not in EXPORTS:
        raise ValueError('Use probe_tools for read-only tool probes')
    parameters.pop('cancel_file', None)
    protected = tuple(protected) + tuple(source_paths(parameters))
    if cancelled and cancelled():
        raise CancelledError('Operation cancelled')
    if operation in ('coordinates', 'session_save'):
        from .persistence import export_persistence
        return export_persistence(input_session, output_dir, operation, parameters['request'],
                                  cancelled=cancelled, progress=progress, protected=protected, source_input=source_input)
    if operation in ('timeline_project', 'animation'):
        if input_session is None:
            raise ValueError('Timeline export requires a session')
        from .animation import save_timeline_project, export_animation
        if operation == 'timeline_project':
            if progress:
                progress(dict(stage='project', completed=0, total=1, message='Saving timeline project'))
            return save_timeline_project(input_session, parameters['timeline'], output_dir,
                                         cancelled=cancelled, protected=protected)
        return export_animation(input_session, output_dir, parameters.pop('timeline'),
                                cancelled=cancelled, progress=progress, protected=protected, **parameters)
    if operation == 'density':
        from .density_export import export_density
        return export_density(input_session, output_dir, parameters['request'], cancelled=cancelled,
                              progress=progress, protected=protected)
    from .electrostatics import export_potential
    return export_potential(input_session, output_dir, parameters['request'], cancelled=cancelled,
                            progress=progress, protected=protected)


def validate_bundle(report_path):
    """Check closed inventory before any live-scene mutation."""
    report_path = checked_path(report_path)
    root = report_path.parent
    manifest = read_json(root / 'manifest.json')
    if (type(manifest.get('schema_version')) is not int or manifest['schema_version'] != 1
            or set(manifest) != {'schema_version', 'files'} or not isinstance(manifest['files'], dict)):
        raise ValueError('Invalid bundle manifest')
    inventory = manifest['files']
    if 'report.json' not in inventory or report_path.name != 'report.json':
        raise ValueError('Bundle must contain report.json')
    actual = set()
    for path in root.rglob('*'):
        checked_path(path)
        if path.is_file():
            if path.stat().st_nlink > 1:
                raise ValueError('Bundle contains hardlinked payload')
            actual.add(path.relative_to(root).as_posix())
    if actual != set(inventory) | {'manifest.json'}:
        raise ValueError('Bundle inventory does not match manifest')
    for relative, row in inventory.items():
        candidate = checked_path(root / relative)
        if (Path(relative).is_absolute() or '..' in Path(relative).parts or root not in candidate.parents
                or not candidate.is_file() or candidate.stat().st_size != row.get('bytes')
                or sha256(candidate) != row.get('sha256')):
            raise ValueError('Bundle payload integrity failure: ' + relative)
    report = read_json(report_path)
    if report.get('schema_version') != 2 or report.get('passed') is not True or report.get('operation') not in EXPORTS:
        raise ValueError('Not a successful advanced report')
    hashes = report.get('artifact_hashes', {})
    if set(hashes) != set(inventory) - {'report.json'}:
        raise ValueError('Report artifact inventory mismatch')
    if any(inventory[name]['sha256'] != digest for name, digest in hashes.items()):
        raise ValueError('Report artifact digest mismatch')
    return report
