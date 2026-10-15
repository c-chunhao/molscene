"""Sequential, cancellable batch renders with portable persisted progress."""
from copy import deepcopy
import csv
import io
import json
import os
from pathlib import Path, PureWindowsPath
import re
import tempfile
import time

from . import engine

_SUFFIXES = {'.pdb', '.cif', '.mmcif'}
_SUMMARIES = ('batch.json', 'batch.csv')


def _checked_path(value):
    lexical = Path(os.path.abspath(os.path.expanduser(os.fspath(value))))
    engine._no_link(lexical)
    if lexical.is_file() and lexical.stat().st_nlink > 1:
        raise ValueError('Batch paths must not contain hard links: ' + str(lexical))
    return lexical.resolve()


def _safe_stem(source):
    return re.sub(r'[^A-Za-z0-9_-]+', '-', source.stem).strip('-_')[:80] or 'structure'


def _check_output(output, sources, overwrite):
    _checked_path(output)
    if output.exists():
        if not output.is_dir():
            raise ValueError('Batch output must be a directory')
        if any(output.iterdir()) and not overwrite:
            raise ValueError('Batch output directory is nonempty; choose a new directory or use overwrite=True')
        # Check lexical descendants before any replacement. No symlink/junction
        # may disappear behind resolve, even in a previous batch item directory.
        for root, directories, files in os.walk(output, followlinks=False):
            for name in (*directories, *files):
                _checked_path(Path(root) / name)
    for source in sources:
        _checked_path(source)
    for name in _SUMMARIES:
        target = output / name
        _checked_path(target)
        if target.exists() and not target.is_file():
            raise ValueError('Batch summary must be a regular file: ' + name)


def _error_message(exc, source, output, input_dir):
    message = str(exc)
    # Backend diagnostics may include absolute staged paths as well as inputs.
    for path, replacement in ((source, source.name), (output, '.'), (input_dir, '.')):
        for value in (str(path), path.as_posix(), repr(str(path))[1:-1]):
            message = message.replace(value, replacement)
    # Strip UNC and device namespaces before the drive/POSIX rules can match
    # only their suffixes. Keep the basename useful for dependency diagnostics.
    windows_prefix = r'(?:\\\\|//|\\\?\?\\|\\Device\\)'
    def windows_basename(value):
        basename = PureWindowsPath(value).name
        return '<local path>' + (' (' + basename + ')' if basename else '')
    message = re.sub(
        rf"(?P<quote>['\"])(?P<path>{windows_prefix}[^'\"\r\n<>]*)(?P=quote)",
        lambda match: match['quote'] + windows_basename(match['path']) + match['quote'],
        message, flags=re.IGNORECASE)
    message = re.sub(windows_prefix + r'[^\s\'\"<>;]+',
                     lambda match: windows_basename(match[0]), message, flags=re.IGNORECASE)
    message = re.sub(r'[A-Za-z]:[\\/][^\s\'\"<>]+', '<local path>', message)
    message = re.sub(r'(?<![A-Za-z0-9])/(?:[^\s/\'\"<>]+/)+[^\s\'\"<>]*', '<local path>', message)
    return message


def _persist(output, report, sources, progress):
    _check_output(output, sources, True)
    csv_buffer = io.StringIO(newline='')
    writer = csv.DictWriter(csv_buffer, fieldnames=('input', 'output', 'status', 'error', 'report'))
    writer.writeheader()
    for item in report['items']:
        writer.writerow({name: item.get(name, '') for name in writer.fieldnames})
    contents = (json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n', csv_buffer.getvalue())
    for name, content in zip(_SUMMARIES, contents):
        # Unique owned temporary files prevent replacement of a caller's files.
        descriptor, temporary = tempfile.mkstemp(prefix='.molscene-batch-', dir=output)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as handle:
                handle.write(content)
            deadline = time.monotonic() + 2.0
            while True:
                # A Windows progress reader can briefly prevent replacement,
                # including on exFAT with delete-sharing enabled. Revalidate
                # every path before retrying the same fully written file.
                _check_output(output, sources, True)
                _checked_path(temporary)
                try:
                    os.replace(temporary, output / name)
                    break
                except OSError as exc:
                    remaining = deadline - time.monotonic()
                    if os.name != 'nt' or getattr(exc, 'winerror', None) not in (5, 32, 33) or remaining <= 0:
                        raise
                    time.sleep(min(0.025, remaining))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    if progress is not None:
        progress(deepcopy(report))


def run_batch(input_dir, output_dir, *, render_options=None, overwrite=False,
              cancelled=None, progress=None):
    """Render sorted immediate PDB/CIF/mmCIF children and save batch.json/csv.

    ``cancelled()`` is checked between items. ``progress(report)`` receives an
    independent snapshot only after it has been persisted. Reopening batch.json
    exposes completed, failed and still-pending rows without inferring success.
    Overwrite reruns the batch; individual successful artifacts are replaced by
    the engine only after rendering succeeds. Existing unrelated files survive.
    """
    source_dir, output = _checked_path(input_dir), _checked_path(output_dir)
    if not source_dir.is_dir():
        raise ValueError('Batch input must be an existing directory')
    if source_dir == output or source_dir in output.parents or output in source_dir.parents:
        raise ValueError('Batch input and output directories must not overlap or contain one another')
    sources = []
    for child in sorted(source_dir.iterdir(), key=lambda path: (path.name.casefold(), path.name)):
        if child.suffix.lower() in _SUFFIXES:
            checked = _checked_path(child)
            if checked.is_file():
                sources.append(checked)
    if not sources:
        raise ValueError('No local PDB/CIF/mmCIF input structures found')
    options = dict(render_options or {})
    if {'input_path', 'output_dir', 'overwrite'} & options.keys():
        raise ValueError('render_options must not override input, output or overwrite')
    _check_output(output, sources, overwrite)
    report = {'schema_version': 2, 'passed': False, 'items': [
        {'input': source.name, 'output': f'{index:04d}-{_safe_stem(source)}', 'status': 'pending'}
        for index, source in enumerate(sources, 1)], 'completed': 0, 'failed': 0, 'cancelled': False}
    # Check every deterministic destination before modifying summaries.
    for item in report['items']:
        target = output / item['output']
        _checked_path(target)
        if target.exists() and not target.is_dir():
            raise ValueError('Batch item output must be a directory: ' + item['output'])
    output.mkdir(parents=True, exist_ok=True)
    _persist(output, report, sources, progress)
    for source, item in zip(sources, report['items']):
        if cancelled is not None and cancelled():
            report['cancelled'] = True
            break
        item['status'] = 'running'
        _persist(output, report, sources, progress)
        try:
            _checked_path(source)
            engine.render(source, output / item['output'], overwrite=overwrite, **options)
        except (ValueError, RuntimeError, OSError, ImportError) as exc:
            item.update(status='failed', error=_error_message(exc, source, output, source_dir))
            report['failed'] += 1
        else:
            item.update(status='completed', report=item['output'] + '/report.json')
            report['completed'] += 1
        _persist(output, report, sources, progress)
    report['passed'] = not report['cancelled'] and report['failed'] == 0 and report['completed'] == len(sources)
    _persist(output, report, sources, progress)
    return report
