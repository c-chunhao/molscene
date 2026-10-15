"""Qt-independent operations on the actual cmd bound to the native window."""
from pathlib import Path
import re
import math
import threading
from contextlib import contextmanager


class WorkbenchController:
    def __init__(self, cmd, workspace):
        self.cmd = cmd
        self.workspace = Path(workspace).expanduser().resolve()
        self.captures = []
        self.results = []

    def _thread(self):
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError('Live scene operations must run on the main thread')

    def objects(self):
        self._thread()
        enabled = set(self.cmd.get_names('objects', enabled_only=1))
        return [{'name': name, 'enabled': name in enabled,
                 'states': int(self.cmd.count_states(name)),
                 'chains': list(self.cmd.get_chains(name)),
                 'atoms': int(self.cmd.count_atoms(name))}
                for name in self.cmd.get_names('objects')
                if self.cmd.get_type(name) == 'object:molecule']

    def selections(self):
        self._thread()
        return list(self.cmd.get_names('selections'))

    def _objects(self, names):
        self._thread()
        names = list(names)
        available = {item['name'] for item in self.objects()}
        if not names or any(name not in available for name in names):
            raise ValueError('Select one or more existing molecular objects explicitly')
        return names

    def set_visible(self, names, visible):
        for name in self._objects(names):
            (self.cmd.enable if visible else self.cmd.disable)(name)

    def orient_objects(self, names):
        names = self._objects(names)
        self.cmd.orient(' or '.join('(%' + name + ')' for name in names))

    def rename_object(self, name, new_name):
        self._objects([name])
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', new_name or ''):
            raise ValueError('Use a name starting with a letter or underscore, followed by letters, digits or underscores')
        if new_name in self.cmd.get_names('all'):
            raise ValueError('That object or selection name already exists')
        self.cmd.set_name(name, new_name)

    def delete_objects(self, names):
        for name in self._objects(names):
            self.cmd.delete(name)

    def load_coordinates(self, path):
        self._thread()
        path = Path(path).expanduser().resolve()
        if path.suffix.lower() not in ('.pdb', '.cif', '.mmcif') or not path.is_file():
            raise ValueError('Choose an existing local PDB or mmCIF coordinate file')
        stem = re.sub('[^A-Za-z0-9_]', '_', path.stem).strip('_') or 'structure'
        if stem[0].isdigit():
            stem = 'm_' + stem
        name = self.cmd.get_unused_name(stem)
        self.cmd.load(str(path), name, zoom=0)
        if not self.cmd.count_atoms('%' + name):
            self.cmd.delete(name)
            raise ValueError('The coordinate file contained no atoms')
        return name

    def open_session(self, path):
        self._thread()
        path = Path(path).expanduser().resolve()
        if path.suffix.lower() != '.pse' or not path.is_file():
            raise ValueError('Choose an existing PyMOL PSE session')
        # Official load/set_session retains upstream session security handling.
        self.cmd.load(str(path), format='pse', quiet=0)

    def set_state(self, state):
        self._thread()
        self._state(state)
        if state > self.cmd.count_states('all'):
            raise ValueError('State does not exist in the live scene')
        self.cmd.frame(state)

    @staticmethod
    def _state(state):
        if isinstance(state, bool) or not isinstance(state, int) or state < 1:
            raise ValueError('State must be a positive integer')

    def _selection_atoms(self, selection, state=1, single=False):
        self._thread()
        from molscene.analysis import collect_atoms
        atoms = collect_atoms(self.cmd, selection, state=state)
        if single and len(atoms) != 1:
            raise ValueError('Selection must match exactly one atom')
        return atoms

    def capture_selection(self, selection, *, state=1, single=False):
        atoms = self._selection_atoms(selection, state, single)
        # Identity and named membership come from the same state-resolved rows.
        # Unbound cmd.index(selection) can include other discrete states.
        keys = ('object', 'index', 'chain', 'segi', 'resi', 'resn', 'name', 'element', 'alt', 'state')
        identities = [{key: atom[key] for key in keys} for atom in atoms]
        name = self.cmd.get_unused_name('molscene_capture')
        terms = ['(%' + atom['object'] + ' and index ' + str(atom['index']) + ')' for atom in atoms]
        self.cmd.select(name, ' or '.join(terms), state=state, quiet=1)
        result = {'selection': name, 'source': selection, 'state': state,
                  'atoms': len(atoms), 'objects': sorted({atom['object'] for atom in atoms}),
                  'identities': identities}
        self.captures.append(result)
        return result

    def capture_current(self, *, state=1):
        return self.capture_selection('sele', state=state)

    def capture_pick(self, number, *, state=1):
        if number not in (1, 2, 3, 4):
            raise ValueError('Pick number must be 1, 2, 3 or 4')
        return self.capture_selection('pk' + str(number), state=state, single=True)

    def scenes(self):
        self._thread()
        return list(self.cmd.get_scene_list())

    def store_scene(self, name):
        self._thread()
        if not isinstance(name, str) or not name.strip():
            raise ValueError('Enter a scene bookmark name')
        if name in self.scenes():
            raise ValueError('Scene bookmark already exists; choose another name')
        self.cmd.scene(name, 'store')

    def restore_scene(self, name):
        self._thread()
        if name not in self.scenes():
            raise ValueError('Select an existing scene bookmark')
        self.cmd.scene(name, 'recall', animate=0)

    def delete_scene(self, name):
        self._thread()
        if name not in self.scenes():
            raise ValueError('Select an existing scene bookmark')
        self.cmd.scene(name, 'clear')

    def apply_representation(self, selection, representation, *, state=1, visible=True):
        self._thread()
        from molscene.styles import apply_representation
        self._selection_atoms(selection, state)
        with self._bound_selection(selection, state) as bound:
            if visible:
                return apply_representation(self.cmd, bound, representation)
            if representation not in ('cartoon', 'ribbon', 'sticks', 'lines', 'spheres', 'surface', 'mesh', 'dots', 'nonbonded'):
                raise ValueError('Choose a supported representation')
            self.cmd.hide(representation, bound)

    def apply_coloring(self, selection, coloring, *, state=1, color='cyan'):
        self._thread()
        from molscene.styles import apply_coloring
        self._selection_atoms(selection, state)
        with self._bound_selection(selection, state) as bound:
            return apply_coloring(self.cmd, bound, coloring=coloring, color=color)

    def set_appearance(self, selection, *, background='white', surface_opacity=1.0,
                       stick_radius=0.25, state=1):
        self._selection_atoms(selection, state)
        if background not in ('white', 'black', 'transparent'):
            raise ValueError('Background must be white, black or transparent')
        if not math.isfinite(surface_opacity) or not 0 <= surface_opacity <= 1:
            raise ValueError('Surface opacity must be finite and between 0 and 1')
        if not math.isfinite(stick_radius) or stick_radius <= 0:
            raise ValueError('Stick radius must be finite and positive')
        self.cmd.bg_color('white' if background == 'transparent' else background)
        self.cmd.set('ray_opaque_background', 0 if background == 'transparent' else 1)
        atoms = self._selection_atoms(selection, state)
        members = {}
        for atom in atoms:
            members.setdefault(atom['object'], []).append(atom)
        for name, rows in members.items():
            # PyMOL distinguishes exact object settings from atom settings.
            # Preserve that behavior for complete objects, while partial and
            # spatial selections are frozen to the requested-state members.
            if len(rows) == self.cmd.count_atoms('%' + name):
                self.cmd.set('transparency', 1 - surface_opacity, name)
                self.cmd.set('stick_radius', stick_radius, name)
            else:
                expression = ' or '.join(self._atom_expression(atom) for atom in rows)
                with self._bound_selection(expression, state) as bound:
                    self.cmd.set('transparency', 1 - surface_opacity, bound)
                    self.cmd.set('stick_radius', stick_radius, bound)

    def set_labels(self, selection, expression='', *, state=1):
        self._selection_atoms(selection, state)
        with self._bound_selection(selection, state) as bound:
            self.cmd.label(bound, expression)

    def _fingerprint(self, state):
        self._thread()
        from molscene.snapshot import scene_fingerprint
        return scene_fingerprint(self.cmd, state=state)

    def record_result(self, operation, result, parameters=None, *, state=1, source_fingerprint=None):
        """Retain complete scientific rows; file identity and semantic identity differ."""
        from copy import deepcopy
        from datetime import datetime, timezone
        self._thread()
        self._state(state)
        request = {key: deepcopy(value) for key, value in (parameters or {}).items()
                   if key != 'analysis_records'}
        if operation in ('export', 'batch'):
            # A job history entry links to its durable report. Embedding that
            # report's scientific history here would duplicate complete tables
            # and recursively include earlier export reports on the next run.
            fields = ('schema_version', 'tool', 'tool_version', 'pymol_version',
                      'operation', 'parameters', 'created_at', 'calculated_at',
                      'artifacts', 'inputs', 'output_path', 'snapshot_id', 'source_fingerprint',
                      'state', 'view', 'replay_contract', 'passed', 'items',
                      'completed', 'failed', 'cancelled', 'error')
            record = {key: deepcopy(result[key]) for key in fields if key in result}
        else:
            record = deepcopy(result)
        record.setdefault('operation', operation)
        record.setdefault('state', state)
        record.setdefault('calculated_at', datetime.now(timezone.utc).isoformat())
        record.setdefault('parameters', deepcopy(request))
        record.setdefault('request_parameters', request)
        if source_fingerprint is not None:
            record.setdefault('source_fingerprint', source_fingerprint)
        self.result_status(record)
        self.results.append(record)
        return record

    def result_status(self, record, *, state=None):
        self._thread()
        from molscene.snapshot import record_snapshot_status
        status = record_snapshot_status(self.cmd, record, state=state)
        record['snapshot_status'] = status
        return status

    @staticmethod
    def _atom_expression(atom):
        return '(%' + atom['object'] + ' and index ' + str(atom['index']) + ')'

    def annotate_result(self, record):
        """Only draw data tied to the current semantic molecular scene and state."""
        self._thread()
        if self.result_status(record) == 'stale':
            raise ValueError('This result is stale; rerun the analysis before annotating')
        state = record['state']
        kind = record.get('kind')
        if kind in ('distance', 'angle', 'dihedral'):
            atoms = record['atoms']
            # Even unverified legacy records must validate atom coordinates now.
            selections = [self._atom_expression(atom) for atom in atoms]
            from molscene.analysis import geometry
            current = geometry(self.cmd, kind, selections, state=state)
            if current['atoms'] != atoms:
                raise ValueError('Result atoms have changed; rerun before annotating')
            name = self.cmd.get_unused_name('molscene_' + kind)
            getattr(self.cmd, kind)(name, *selections, state=state, quiet=1)
            record['annotation'] = name
            record['displayed_count'] = 1
        elif record.get('operation') == 'contacts':
            pairs = record['pairs'][:200]
            from molscene.analysis import collect_atoms
            for pair in pairs:
                for key in ('atom_a', 'atom_b'):
                    atom = pair[key]
                    if collect_atoms(self.cmd, self._atom_expression(atom), state=state) != [atom]:
                        raise ValueError('Result atoms have changed; rerun before annotating')
            name = self.cmd.get_unused_name('molscene_contacts')
            for pair in pairs:
                self.cmd.distance(name, self._atom_expression(pair['atom_a']),
                                  self._atom_expression(pair['atom_b']), state=state, quiet=1)
            record.update(annotation=name if pairs else None, displayed_count=len(pairs))
        else:
            raise ValueError('Choose a geometry or contacts result to annotate')
        return record

    def geometry(self, kind, selections, *, state=1, annotate=False):
        self._thread()
        from molscene.analysis import geometry
        fingerprint = self._fingerprint(state)
        result = geometry(self.cmd, kind, selections, state=state)
        record = self.record_result('geometry', result, dict(selections=list(selections), kind=kind),
                                    state=state, source_fingerprint=fingerprint)
        if annotate and record['snapshot_status'] != 'stale':
            self.annotate_result(record)
        return record

    def inspect(self, selection, *, state=1):
        self._thread()
        from molscene.analysis import inspect_structure
        fingerprint = self._fingerprint(state)
        return self.record_result('inspect', inspect_structure(self.cmd, selection, state=state),
                                  dict(selection=selection), state=state, source_fingerprint=fingerprint)

    def align(self, mobile, target, *, algorithm='align', selection='polymer.protein and name CA',
              mobile_state=1, target_state=1, cutoff=2.0, cycles=5):
        self._thread()
        self._objects([mobile, target])
        if mobile == target:
            raise ValueError('Choose distinct mobile and target objects')
        if algorithm not in ('align', 'super'):
            raise ValueError('Algorithm must be align or super')
        if isinstance(cutoff, bool) or not math.isfinite(cutoff) or cutoff <= 0:
            raise ValueError('Cutoff must be a finite positive number')
        if type(cycles) is not int or cycles < 0:
            raise ValueError('Cycles must be a nonnegative integer')
        first, second = '(%' + mobile + ') and (' + selection + ')', '(%' + target + ') and (' + selection + ')'
        self._selection_atoms(first, mobile_state)
        self._selection_atoms(second, target_state)
        with self._bound_selection(first, mobile_state) as bound_mobile, self._bound_selection(second, target_state) as bound_target:
            values = getattr(self.cmd, algorithm)(bound_mobile, bound_target, cutoff=cutoff, cycles=cycles,
                                                  mobile_state=mobile_state, target_state=target_state,
                                                  transform=1, quiet=1)
        fingerprints = {str(state): self._fingerprint(state) for state in sorted({mobile_state, target_state})}
        result = dict(align_result=list(values), rmsd_angstrom=float(values[0]), aligned_atoms=int(values[1]),
                      source_fingerprints=fingerprints)
        parameters = dict(mobile=mobile, target=target, algorithm=algorithm, selection=selection,
                          mobile_state=mobile_state, target_state=target_state, cutoff=cutoff, cycles=cycles)
        return self.record_result('align', result, parameters, state=mobile_state,
                                  source_fingerprint=fingerprints[str(mobile_state)])

    @staticmethod
    def _write_export(path, content, overwrite):
        """Single-file summaries use the same lexical link checks as artifacts."""
        import os
        import tempfile
        from molscene.engine import _no_link
        lexical = Path(os.path.abspath(os.path.expanduser(os.fspath(path))))
        def checked():
            _no_link(lexical)
            if lexical.exists():
                if not lexical.is_file() or lexical.stat().st_nlink > 1:
                    raise ValueError('Export destination must be an unlinked regular file')
                if not overwrite:
                    raise ValueError('Export exists; explicitly enable overwrite')
        checked()
        lexical.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='.molscene-table-', dir=lexical.parent)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as handle:
                handle.write(content)
            checked()
            os.replace(temporary, lexical)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return str(lexical)

    def export_table(self, record, key, path, *, overwrite=False):
        import csv
        import io
        import json
        self._thread()
        self.result_status(record)
        rows = record.get(key)
        if not isinstance(rows, list):
            raise ValueError('Choose an available result table')
        fields = list(dict.fromkeys(field for row in rows for field in row))
        buffer = io.StringIO(newline='')
        writer = csv.DictWriter(buffer, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
                              if isinstance(value, (list, dict)) else value for key, value in row.items()})
        return self._write_export(path, buffer.getvalue(), overwrite)

    def export_fasta(self, record, path, *, overwrite=False):
        self._thread()
        self.result_status(record)
        sequences = record.get('sequences')
        if sequences is None:
            raise ValueError('Choose an inspection result with coordinate sequences')
        content = ''.join('>' + row['id'] + '\n' + row['sequence'] + '\n' for row in sequences)
        return self._write_export(path, content, overwrite)


    def prepare_job(self, operation, parameters, *, output=None, state=1):
        """Freeze native PSE before QProcess starts; each job owns unique files."""
        import json
        import os
        import tempfile
        from copy import deepcopy
        from molscene.engine import _no_link
        self._thread()
        self._state(state)
        if operation not in ('geometry', 'contacts', 'sasa', 'inspect', 'export', 'batch'):
            raise ValueError('Unsupported worker operation')
        parameters = deepcopy(parameters)
        if operation == 'batch':
            source = parameters.get('input_dir')
            if not isinstance(source, (str, os.PathLike)) or not os.fspath(source):
                raise ValueError('Batch input must be an existing directory')
            # Freeze the UI cwd before QProcess switches to the private job
            # directory. Check lexical links before resolve can erase them.
            lexical = Path(os.path.abspath(os.path.expanduser(os.fspath(source))))
            _no_link(lexical)
            if not lexical.is_dir():
                raise ValueError('Batch input must be an existing directory')
            parameters['input_dir'] = str(lexical.resolve())
        if operation in ('geometry', 'contacts', 'sasa', 'inspect'):
            if parameters.get('state') != state:
                raise ValueError('Job and selection states must match explicitly')
            selections = parameters.get('selections', []) if operation == 'geometry' else (
                [parameters.get('selection_a'), parameters.get('selection_b')] if operation == 'contacts'
                else [parameters.get('selection', 'all')])
            if operation == 'sasa' and parameters.get('context'):
                selections.append(parameters['context'])
            for selection in selections:
                self._selection_atoms(selection, state)
        if operation in ('export', 'batch') and output is None:
            raise ValueError('Choose an output directory')
        parent = self.workspace / '.molscene-jobs'
        _no_link(parent)
        parent.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix='job-', dir=parent))
        if output is not None:
            lexical = Path(os.path.abspath(os.path.expanduser(os.fspath(output))))
            _no_link(lexical)
            destination = lexical.resolve()
        else:
            destination = directory / 'result.json'
        job = dict(schema_version=1, operation=operation, output=str(destination), parameters=parameters,
                   result_path=str(directory / 'result.json'))
        if operation != 'batch':
            session = directory / 'snapshot.pse'
            self.cmd.save(str(session), format='pse', quiet=1)
            job['input_session'] = str(session)
        else:
            parameters['cancel_file'] = str(directory / 'cancel.request')
        path = directory / 'job.json'
        path.write_text(json.dumps(job, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        return path

    @contextmanager
    def _bound_selection(self, selection, state):
        atoms = self._selection_atoms(selection, state)
        name = self.cmd.get_unused_name('_molscene_display')
        try:
            expression = ' or '.join(self._atom_expression(atom) for atom in atoms)
            self.cmd.select(name, expression, state=state, enable=0, quiet=1)
            yield name
        finally:
            self.cmd.delete(name)
