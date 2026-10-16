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
        self._advanced_maps = []
        self._timeline = None
        self._session_epoch = 0
        self._advanced_contexts = {}
        self._timeline_timers = []

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
        for timer in self._timeline_timers:
            timer.stop()
        self.cmd.load(str(path), format='pse', quiet=0)
        self._advanced_maps = []
        self._timeline = None
        self._session_epoch += 1

    def set_state(self, state):
        self._thread()
        self._state(state)
        if state > self.cmd.count_states('all'):
            raise ValueError('State does not exist in the live scene')
        self.cmd.set('state', state)

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

    def residues(self, selection='all', *, state=1):
        self._thread()
        from .structure import residues
        return residues(self.cmd, selection, state=state)

    def select_residues(self, rows, *, state=1):
        self._thread()
        from .structure import select_residues
        result = select_residues(self.cmd, rows, state=state)
        self.captures.append(result)
        return result

    def selection_summary(self, selection, *, state=1):
        self._thread()
        from .structure import selection_summary
        return selection_summary(self.cmd, selection, state=state)

    def save_selection(self, name, selection, *, state=1):
        self._thread()
        from .structure import save_selection
        result = save_selection(self.cmd, name, selection, state=state)
        self.captures.append(result)
        return result

    def delete_selection(self, name):
        self._thread()
        from .structure import delete_selection
        return delete_selection(self.cmd, name)

    def focus_selection(self, selection, *, state=1, buffer=3.0, orient=True):
        self._thread()
        from .structure import focus_selection
        return focus_selection(self.cmd, selection, state=state, buffer=buffer, orient=orient)

    def hide_context(self, selection, *, state=1, solvent=True, hydrogens=True):
        self._thread()
        from .structure import hide_context
        return hide_context(self.cmd, selection, state=state, solvent=solvent, hydrogens=hydrogens)

    def apply_preset(self, selection, preset, *, state=1, ligand=None, radius=5.0, frame=True):
        self._thread()
        from .structure import apply_preset
        return apply_preset(self.cmd, selection, preset, state=state, ligand=ligand,
                            radius=radius, frame=frame)

    def prepare_structure(self, selection, *, object_name, state=1, remove_solvent=True,
                          remove_hydrogens=False, altloc='primary'):
        self._thread()
        from .structure import prepare_structure
        return prepare_structure(self.cmd, selection, object_name=object_name, state=state,
                                 remove_solvent=remove_solvent, remove_hydrogens=remove_hydrogens,
                                 altloc=altloc)

    def update_scene(self, name):
        self._thread()
        from .structure import update_scene
        return update_scene(self.cmd, name)

    def rename_scene(self, name, new_name):
        self._thread()
        from .structure import rename_scene
        result = rename_scene(self.cmd, name, new_name)
        if self._timeline is not None:
            from molscene.timeline import rename_scene_reference
            self._timeline = rename_scene_reference(self._timeline, name, new_name)
        return result

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
        if self._timeline is not None:
            from molscene.timeline import scene_is_referenced
            if scene_is_referenced(self._timeline, name):
                raise ValueError('Scene is referenced by the timeline')
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
        if record.get('operation') in ('timeline_project', 'animation', 'density', 'potential'):
            from molscene.advanced_snapshot import result_status
            context = self._advanced_contexts.get(record.get('_report_path', ''))
            status = result_status(self.cmd, context or {}, maps=self._advanced_maps,
                                   timeline=self._timeline, session_epoch=self._session_epoch)
            record['snapshot_status'] = status
            return status
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
        if operation in ('timeline_project', 'animation', 'density', 'potential', 'tools'):
            return self._prepare_advanced_job(operation, parameters, output=output)
        if operation not in ('geometry', 'contacts', 'sasa', 'interface', 'inspect', 'export', 'batch'):
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
        if operation in ('geometry', 'contacts', 'sasa', 'interface', 'inspect'):
            if operation == 'interface':
                self._state(parameters.get('state'))
            if parameters.get('state') != state:
                raise ValueError('Job and selection states must match explicitly')
            selections = parameters.get('selections', []) if operation == 'geometry' else (
                [parameters.get('selection_a'), parameters.get('selection_b')] if operation in ('contacts', 'interface')
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

    def timeline_new(self, fps=24):
        self._thread()
        from copy import deepcopy
        from molscene.timeline import new_timeline
        self._timeline = new_timeline(self.cmd, fps=fps)
        return deepcopy(self._timeline)

    def timeline_edit(self, action, keyframe_id=None, values=None):
        self._thread()
        from copy import deepcopy
        from molscene.timeline import capture_keyframe, edit_keyframe, validate_timeline
        editor_fields = {'frame', 'state', 'scene', 'label', 'interpolation'}
        if action == 'add' and isinstance(values, dict) and set(values) == editor_fields:
            values = capture_keyframe(self.cmd, **values)
        document = edit_keyframe(self._timeline, action, keyframe_id, values)
        self._timeline = validate_timeline(document, self.cmd)
        return deepcopy(self._timeline)

    def timeline_seek(self, frame):
        self._thread()
        from molscene.timeline import seek_timeline
        return seek_timeline(self.cmd, self._timeline, frame)

    def timeline_load_json(self, path):
        self._thread()
        from copy import deepcopy
        from molscene.advanced import read_json
        from molscene.timeline import validate_timeline
        self._timeline = validate_timeline(read_json(path), self.cmd)
        return deepcopy(self._timeline)

    def timeline_open_project(self, path):
        self._thread()
        from molscene.advanced import validate_bundle, read_json
        from molscene.timeline import validate_timeline
        from molscene.engine import _runtime
        root = Path(path)
        if root.is_file():
            root = root.parent
        report = validate_bundle(root / 'report.json')
        if report['operation'] not in ('timeline_project', 'animation'):
            raise ValueError('Choose a timeline project bundle')
        document = read_json(root / 'timeline.json')
        with _runtime().PyMOL() as pm:
            pm.cmd.load(str(root / 'source.pse'))
            validate_timeline(document, pm.cmd)
        self.open_session(root / 'source.pse')
        self._timeline = validate_timeline(document, self.cmd)
        return self.advanced_inventory()

    def density_load(self, path, **options):
        self._thread()
        from copy import deepcopy
        from molscene.density import load_density
        record = load_density(self.cmd, path, **options)
        self._advanced_maps.append(record)
        return deepcopy(record)

    def _map_record(self, name):
        self._thread()
        records = [r for r in self.advanced_inventory()['maps'] if r['name'] == name]
        if len(records) != 1:
            raise ValueError('Choose an unambiguous map state')
        return records[0]

    def density_display(self, name, display):
        self._thread()
        from molscene.density import display_density
        record = self._map_record(name)
        result = display_density(self.cmd, record, display)
        for stored in self._advanced_maps:
            if (stored['name'], stored['state']) == (record['name'], record['state']):
                stored['derived'] = record['derived']
        return result

    def density_register(self, name, registration):
        self._thread()
        from molscene.density import register_density
        result = register_density(self.cmd, self._map_record(name), registration)
        self._advanced_maps.append(result['record'])
        return result

    def advanced_inventory(self):
        self._thread()
        from copy import deepcopy
        from molscene.density import map_inventory
        return dict(maps=map_inventory(self.cmd, self._advanced_maps), timeline=deepcopy(self._timeline),
                    session_epoch=self._session_epoch)

    def load_advanced_result(self, report_path):
        self._thread()
        from copy import deepcopy
        from molscene.advanced import validate_bundle
        from molscene.artifacts import checked_path
        path = checked_path(report_path)
        report = validate_bundle(path)
        record = deepcopy(report)
        record['_report_path'] = str(path)
        context = self._advanced_contexts.get(str(path))
        if context is not None:
            if (report.get('operation') != context.get('operation')
                    or report.get('operation') == 'potential'
                    and report.get('parameters', {}).get('mode') != context.get('mode')):
                raise ValueError('Report operation or mode does not match the prepared job')
            if report.get('snapshot_id') != context.get('snapshot_id'):
                raise ValueError('Report does not belong to the prepared source snapshot')
            roles = {'map_path': 'density', 'pqr_path': 'pqr', 'dx_path': 'potential'}
            expected = {roles[row['role']]: row['sha256'] for row in context['original_inputs']}
            if context['snapshot_id'] is not None:
                expected['session'] = context['snapshot_id']
            inputs = report.get('inputs')
            if (not isinstance(inputs, dict) or set(inputs) != set(expected)
                    or any(not isinstance(inputs[role], dict) or inputs[role].get('sha256') != digest
                           for role, digest in expected.items())):
                raise ValueError('Report input roles and hashes do not match the prepared job')
        if self.result_status(record) != 'current':
            raise ValueError('Result source is stale or unverified. Rerun from the current session, or import the original PQR/DX/map using its controls.')
        if report['operation'] == 'density':
            record['loaded'] = self._load_density_copy(path.parent, report['analysis']['density'])
        elif report['operation'] == 'potential':
            record['loaded'] = self._load_potential_copy(path.parent, report, context)
        else:
            raise ValueError('Use Open project to explicitly replace the session with a validated timeline project')
        self.results.append(record)
        return record

    def _load_potential_copy(self, root, report, context):
        from molscene.artifacts import checked_path
        from molscene.potential_display import load_potential_copy
        spec = report['parameters']
        display = report['analysis']['display']
        def payload(relative):
            path = checked_path(root / relative)
            if root not in path.parents or relative not in report['artifact_hashes']:
                raise ValueError('Potential payload escapes validated bundle')
            return path
        options = dict(dx_path=payload(display['dx_path']), units=spec['display_units'],
                       temperature=spec['temperature'], ramp_limit=spec['ramp_limit'],
                       probe_radius=spec['solver']['probe_radius'], state=spec['state'])
        if spec['mode'] != 'import':
            options['pqr_path'] = payload('prepared.pqr')
        elif spec['pqr_path'] is not None:
            options['pqr_path'] = payload(spec['pqr_path'])
        else:
            members = context['selection']['members']
            options['selection'] = ' or '.join(self._atom_expression(atom) for atom in members)
        view = self.cmd.get_view()
        try:
            return load_potential_copy(self.cmd, **options)
        finally:
            self.cmd.set_view(view)

    def _load_density_copy(self, root, metadata):
        from molscene import density
        from molscene.grids import compose, inverse_affine
        from molscene.artifacts import checked_path
        registration = metadata['registration']
        desired = registration['record'] if registration else metadata['source']
        path = checked_path(root / desired['source_path'])
        if root not in path.parents:
            raise ValueError('Density source escapes bundle')
        owned = []
        try:
            name = self.cmd.get_unused_name('molscene_density')
            suffix = '_' + metadata['display']['display']['kind']
            names = set(self.cmd.get_names('all'))
            while name + suffix in names:
                import uuid
                name = self.cmd.get_unused_name('molscene_density_' + uuid.uuid4().hex[:12])
            fresh = density.load_density(self.cmd, path, format=desired['format'],
                interpretation=desired['interpretation'] if desired['format'] != 'dx' else 'auto',
                name=name, units=desired['grid']['value_unit'])
            owned.append(name)
            transform = compose(inverse_affine(fresh['load_matrix']), desired['world_matrix'])
            self.cmd.transform_object(name, list(transform), state=1, homogenous=1, transpose=0)
            fresh['world_matrix'] = density._matrix(self.cmd, name, 1)
            if not density._close(fresh['world_matrix'], desired['world_matrix']):
                raise ValueError('Density result matrix could not be reconstructed')
            density._verify_record(self.cmd, fresh)
            alignment = registration.get('alignment') if registration else None
            model = None
            if alignment:
                args = alignment['parameters']
                density._alignment_modes(self.cmd, args)
                model = self.cmd.get_unused_name('molscene_aligned')
                owned.append(model)
                self.cmd.create(model, '%' + args['mobile'], source_state=args['mobile_state'], target_state=1, zoom=0)
                if self.cmd.get_setting_int('matrix_mode', model, state=1) != alignment['matrix_mode']:
                    raise ValueError('Molecular matrix mode changed; rerun alignment')
                self.cmd.transform_object(model, alignment['replay_transform'], state=1, homogenous=1, transpose=0)
                if not density._close(density._matrix(self.cmd, model, 1), alignment['after_matrix']):
                    raise ValueError('Aligned model matrix could not be reconstructed')
            display = density.display_density(self.cmd, fresh, metadata['display']['display'])
            owned.extend(fresh['derived'])
            self._advanced_maps.append(fresh)
            return dict(record=fresh, display=display, model_copy=model)
        except BaseException:
            for name in reversed(owned):
                if name in self.cmd.get_names('objects'):
                    self.cmd.delete(name)
            raise

    def potential_ramp(self, record, *, ramp_limit):
        self._thread()
        if record.get('operation') != 'potential' or self.result_status(record) != 'current':
            raise ValueError('Potential result is stale or unverified; rerun from the current sources')
        from molscene.potential_display import update_potential_ramp
        record['loaded'] = update_potential_ramp(self.cmd, record['loaded'], ramp_limit=ramp_limit)
        return record

    def _prepare_advanced_job(self, operation, parameters, *, output=None):
        import json
        import shutil
        import tempfile
        from molscene.advanced import validate_request, source_paths, validate_cancel
        from molscene.advanced_snapshot import capture_identity
        from molscene.artifacts import checked_path, sha256, result_path
        parameters = validate_request(operation, parameters)
        if 'cancel_file' in parameters:
            raise ValueError('Controller owns its private cancellation marker')
        if operation != 'tools' and output is None:
            raise ValueError('Choose an output directory')
        request = parameters.get('request', {})
        maps, states = [], set()
        if operation in ('timeline_project', 'animation'):
            from molscene.timeline import validate_timeline
            parameters['timeline'] = validate_timeline(parameters['timeline'], self.cmd)
            states.update(row['state'] for row in parameters['timeline']['keyframes'])
        if operation == 'density':
            if request['map_name']:
                maps = [r for r in self._advanced_maps if r['name'] == request['map_name'] and r['state'] == request['source_state']]
                if not maps:
                    raise ValueError('Map has no verified source association; reload its source')
            registration = request.get('registration') or {}
            if registration.get('kind') == 'align':
                states.update((registration['mobile_state'], registration['target_state']))
        selection = request.get('selection') if operation == 'potential' and not request.get('pqr_path') else None
        context = capture_identity(self.cmd, states=states, maps=maps,
            timeline=parameters.get('timeline'), selection=selection,
            state=request.get('state', 1), session_epoch=self._session_epoch)
        if selection is not None:
            request['selection'] = ' or '.join(self._atom_expression(atom) for atom in context['selection']['members'])
        sources = source_paths(parameters)
        for source in sources:
            if operation != 'tools' and source.is_file():
                context['files'][str(source)] = sha256(source)
        parent = checked_path(self.workspace / '.molscene-jobs')
        parent.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix='job-', dir=parent))
        envelope = directory / 'result.json'
        destination = envelope if operation == 'tools' else checked_path(output)
        path = directory / 'job.json'
        protected = [path, *sources]
        if operation != 'tools' and any(destination == p or destination in p.parents for p in [*protected, envelope]):
            raise ValueError('Output must not contain sources, job or result envelope')
        result_path(envelope, protected)
        originals = []
        for key in ('map_path', 'pqr_path', 'dx_path'):
            if request.get(key):
                source = checked_path(request[key])
                before = sha256(source)
                target = directory / (key + source.suffix.lower())
                shutil.copyfile(source, target)
                if sha256(target) != before or sha256(source) != before:
                    raise ValueError('External input changed during snapshot copy')
                originals.append(dict(role=key, path=str(source), sha256=before, copy=str(target)))
                request[key] = str(target)
        parameters['cancel_file'] = str(directory / 'cancel.request')
        validate_cancel(parameters['cancel_file'], destination, [*protected, envelope])
        job = dict(schema_version=1, operation=operation, output=str(destination), result_path=str(envelope),
                   parameters=parameters, protected_paths=[str(p) for p in sources], original_inputs=originals)
        if operation != 'tools':
            context['operation'] = operation
            context['mode'] = request.get('mode') if operation == 'potential' else None
            context['snapshot_id'] = None
            if operation != 'potential' or not request.get('pqr_path'):
                session = directory / 'snapshot.pse'
                self.cmd.save(str(session), format='pse', quiet=1)
                job['input_session'] = str(session)
                context['snapshot_id'] = sha256(session)
            context['original_inputs'] = originals
            job['gui_context'] = context
            self._advanced_contexts[str(destination / 'report.json')] = context
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
