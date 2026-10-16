"""Isolated snapshot jobs. Launch with an argv array: python -m molscene.worker JOB.json."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from . import analysis, artifacts, engine
from .snapshot import scene_fingerprint

_PARAMETERS = {
    'geometry': {'kind', 'selections', 'state'},
    'contacts': {'selection_a', 'selection_b', 'state', 'cutoff', 'heavy_only', 'max_pairs'},
    'sasa': {'selection', 'context', 'state', 'altloc'},
    'interface': {'selection_a', 'selection_b', 'state', 'altloc'},
    'inspect': {'selection', 'state'},
    'export': {'width', 'height', 'dpi', 'background', 'ray', 'overwrite', 'analysis_records'},
    'batch': {'input_dir', 'render_options', 'overwrite', 'cancel_file'},
}


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
