"""Isolated snapshot jobs. Launch with an argv array: python -m molscene.worker JOB.json."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from . import analysis, artifacts, engine
from .snapshot import scene_fingerprint
from .advanced import PARAMETERS as _ADVANCED_PARAMETERS

_PARAMETERS = {
    'geometry': {'kind', 'selections', 'state'},
    'contacts': {'selection_a', 'selection_b', 'state', 'cutoff', 'heavy_only', 'max_pairs'},
    'sasa': {'selection', 'context', 'state', 'altloc'},
    'interface': {'selection_a', 'selection_b', 'state', 'altloc'},
    'inspect': {'selection', 'state'},
    'export': {'width', 'height', 'dpi', 'background', 'ray', 'overwrite', 'analysis_records'},
    'batch': {'input_dir', 'render_options', 'overwrite', 'cancel_file'},
}
_PARAMETERS.update(_ADVANCED_PARAMETERS)


def _advanced_job(path, job, parameters):
    from .advanced import validate_request, source_paths, validate_cancel, run_advanced, probe_tools
    from .bundles import CancelledError
    operation = job['operation']
    result_path = None
    protected = [path]
    try:
        # Protect declared raw inputs before request validation, including on
        # error paths. Invalid science must still produce a safe envelope.
        def protect_raw(value, key=None):
            if isinstance(value, dict):
                for name, item in value.items():
                    protect_raw(item, name)
            elif isinstance(value, list):
                if key == 'argv' and value and isinstance(value[0], str):
                    protected.append(artifacts.checked_path(value[0]))
                elif key == 'path_dirs':
                    for item in value:
                        if isinstance(item, str):
                            protected.append(artifacts.checked_path(item))
                else:
                    for item in value:
                        protect_raw(item)
            elif key in ('map_path', 'pqr_path', 'dx_path', 'path') and isinstance(value, str):
                protected.append(artifacts.checked_path(value))
        protect_raw(parameters)
        for row in job.get('original_inputs', []):
            if isinstance(row, dict):
                for key in ('path', 'copy'):
                    if isinstance(row.get(key), str):
                        protected.append(artifacts.checked_path(row[key]))
        originals = job.get('protected_paths', [])
        if not isinstance(originals, list) or any(not isinstance(p, str) or not Path(p).is_absolute() for p in originals):
            raise ValueError('protected_paths must be absolute paths')
        protected.extend(artifacts.checked_path(p) for p in originals)
        session = job.get('input_session')
        if session is not None:
            if not isinstance(session, str) or not Path(session).is_absolute():
                raise ValueError('input_session must be an absolute PSE path')
            protected.append(artifacts.checked_path(session))
        output = artifacts.checked_path(job['output'])
        if not isinstance(job.get('result_path'), str) or not Path(job['result_path']).is_absolute():
            raise ValueError('Advanced jobs require explicit absolute result_path')
        envelope = artifacts.result_path(job['result_path'], protected)
        if operation == 'tools':
            if session is not None or output != envelope or envelope.parent != path.parent:
                raise ValueError('Tools requires no session and output=result_path in its private job directory')
        elif (output == envelope or output in envelope.parents
              or any(output == p or output in p.parents for p in protected)):
            raise ValueError('Advanced output must not contain source, job or envelope')
        cancelled = validate_cancel(parameters.get('cancel_file') if isinstance(parameters, dict) else None,
                                    output, [*protected, envelope])
        result_path = envelope
        parameters = validate_request(operation, parameters)
        protected.extend(source_paths(parameters))
        context = job.get('gui_context')
        if context is not None:
            if not isinstance(context, dict):
                raise ValueError('Private snapshot context must be a dictionary')
            actual = artifacts.sha256(session) if session is not None else None
            if actual != context.get('snapshot_id'):
                raise ValueError('Private source snapshot changed after job preparation')
        originals = job.get('original_inputs', [])
        if not isinstance(originals, list):
            raise ValueError('original_inputs must describe private input copies')
        for row in originals:
            if (not isinstance(row, dict) or set(row) != {'role', 'path', 'copy', 'sha256'}
                    or row['role'] not in ('map_path', 'pqr_path', 'dx_path')
                    or parameters.get('request', {}).get(row['role']) != row['copy']
                    or artifacts.sha256(artifacts.checked_path(row['copy'])) != row['sha256']):
                raise ValueError('Private input copy identity does not match prepared job')
        def progress(value):
            print(json.dumps({'progress': value}, allow_nan=False), flush=True)
        if operation == 'tools':
            result = probe_tools(parameters['tools'], path.parent, cancelled=cancelled, progress=progress)
        else:
            result = run_advanced(operation, session, output, parameters, cancelled=cancelled, progress=progress,
                                  protected=(*protected, envelope))
        record = dict(passed=result.get('passed', True), result=result)
        if 'gui_context' in job:
            record['gui_context'] = job['gui_context']
        artifacts.atomic_result(result_path, record, protected)
        return 0 if record['passed'] else 1
    except Exception as exc:
        message = f'{type(exc).__name__}: {exc}'
        print('molscene worker: ' + message, file=sys.stderr)
        if result_path is not None:
            record = dict(passed=False, error=message)
            if isinstance(exc, CancelledError):
                record['result'] = dict(operation=operation, passed=False, cancelled=True)
            for name in ('failure_path', 'result', 'bundle_diagnostics'):
                value = getattr(exc, name, None)
                if value is not None:
                    try:
                        encoded = json.dumps(str(value) if isinstance(value, Path) else value, allow_nan=False)
                        if len(encoded.encode('utf-8')) <= 65536:
                            key = 'diagnostics' if name == 'result' and isinstance(exc, CancelledError) else name
                            record[key] = json.loads(encoded)
                    except (ValueError, TypeError):
                        pass
            try:
                artifacts.atomic_result(result_path, record, protected)
            except (OSError, ValueError) as error:
                print('Cannot safely persist advanced error: ' + str(error), file=sys.stderr)
        return 1


def _read_job(path):
    path = artifacts.checked_path(path)
    if not path.is_file():
        raise ValueError('Job must be an existing JSON file')
    with path.open(encoding='utf-8') as handle:
        job = json.load(handle, parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON: ' + value)))
    if not isinstance(job, dict) or type(job.get('schema_version')) is not int or job['schema_version'] != 1:
        raise ValueError('Job schema_version must be 1')
    operation = job.get('operation')
    parameters = job.get('parameters', {})
    if not isinstance(job.get('output'), str) or not job['output']:
        raise ValueError('Job output must be a path')
    return path, job, parameters


def run_job(job_path):
    """Return an exit status and persist a complete passed/result or error envelope."""
    result_path, protected = None, []
    try:
        path, job, parameters = _read_job(job_path)
        operation = job['operation']
        if operation in _ADVANCED_PARAMETERS:
            return _advanced_job(path, job, parameters)
        protected = [path]
        if operation != 'batch':
            if not isinstance(job.get('input_session'), str):
                raise ValueError('input_session must be a .pse path')
            # Protect even a missing/invalid source before reporting its error.
            protected.append(artifacts.checked_path(job['input_session']))
        else:
            if isinstance(job.get('input_session'), str):
                protected.append(artifacts.checked_path(job['input_session']))
            if isinstance(parameters, dict) and isinstance(parameters.get('input_dir'), str):
                protected.append(artifacts.checked_path(parameters['input_dir']))
        output = artifacts.checked_path(job['output'])
        destination = job.get('result_path', str(path.with_name(path.stem + '.result.json'))
                              if operation in ('export', 'batch') else str(output))
        if not isinstance(destination, str) or not destination:
            raise ValueError('result_path must be a path')
        # Assign only after safety checks pass: errors must never overwrite a source.
        result_path = artifacts.result_path(destination, protected)
        if operation not in _PARAMETERS:
            raise ValueError('Unsupported worker operation: ' + str(operation))
        if not isinstance(parameters, dict) or set(parameters) - _PARAMETERS[operation]:
            raise ValueError('Unexpected parameters for worker ' + operation)
        if isinstance(job.get('input_session'), str):
            session_path = artifacts.checked_path(job['input_session'])
            if session_path == path or session_path.exists() and session_path.samefile(path):
                raise ValueError('Job and input_session must not collide')
        if operation in ('export', 'batch'):
            if (any(source == output or output in source.parents for source in protected)
                    or output in result_path.parents or result_path == output):
                raise ValueError('Export/batch output must not contain the job, sources or envelope')
            if operation == 'export':
                result = engine.export_session(job['input_session'], output, **parameters)
            else:
                from .batch import run_batch
                parameters = dict(parameters)
                cancel_file = parameters.pop('cancel_file', None)
                cancelled = None
                if cancel_file is not None:
                    if not isinstance(cancel_file, str) or not Path(cancel_file).is_absolute():
                        raise ValueError('cancel_file must be an absolute path')
                    cancel_path = artifacts.checked_path(cancel_file)
                    if (any(cancel_path == source or cancel_path in source.parents or source in cancel_path.parents
                            or (cancel_path.exists() and source.exists() and cancel_path.samefile(source))
                            for source in [*protected, result_path]) or cancel_path == output
                            or output in cancel_path.parents or cancel_path in output.parents
                            or cancel_path.is_dir()):
                        raise ValueError('cancel_file must be outside the output and must not collide with sources/results')
                    cancelled = cancel_path.exists
                result = run_batch(output_dir=output, cancelled=cancelled, **parameters)
        else:
            if output != result_path:
                artifacts.result_path(output, [*protected, result_path])
            session = engine._session(job['input_session'])
            with engine._runtime().PyMOL() as pm:
                pm.cmd.load(str(session))
                # No style reset, camera orientation or ray rendering here.
                function = {'geometry': analysis.geometry, 'contacts': analysis.contacts,
                            'sasa': analysis.sasa, 'interface': analysis.interface,
                            'inspect': analysis.inspect_structure}[operation]
                result = function(pm.cmd, **parameters)
                result.update(snapshot_id=artifacts.sha256(session),
                              source_fingerprint=scene_fingerprint(pm.cmd, parameters.get('state', 1)),
                              state=parameters.get('state', 1),
                              calculated_at=datetime.now(timezone.utc).isoformat())
            if output != result_path:
                artifacts.atomic_result(output, result, [*protected, result_path])
        envelope = {'passed': result.get('passed', True), 'result': result}
        artifacts.atomic_result(result_path, envelope, protected)
        return 0 if envelope['passed'] else 1
    except Exception as exc:
        message = f'{type(exc).__name__}: {exc}'
        print('molscene worker: ' + message, file=sys.stderr)
        if result_path is not None:
            try:
                artifacts.atomic_result(result_path, {'passed': False, 'error': message}, protected)
            except Exception as write_error:
                print('molscene worker: Cannot write error result safely: ' + str(write_error), file=sys.stderr)
        return 1


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print('Usage: python -m molscene.worker JOB.json', file=sys.stderr)
        return 2
    return run_job(args[0])


if __name__ == '__main__':
    raise SystemExit(main())
