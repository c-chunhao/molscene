"""Electrostatic requests with visible units, model assumptions and tool provenance."""
import os
import json
from copy import deepcopy
from pathlib import Path
from pymol.Qt import QtWidgets
from .advanced_panel import buttons, check, combo, decimal, field, form, integer, text


class PotentialPanel(QtWidgets.QWidget):
    def __init__(self, advanced):
        super().__init__(advanced)
        self.advanced = advanced
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        source = form(layout)
        self._source_form = source
        self.mode = field(source, 'Potential source', 'potential_mode', combo(['prepared', 'prepare', 'import']))
        self.pqr = field(source, 'Prepared PQR file', 'potential_pqr', QtWidgets.QLineEdit())
        self.dx = field(source, 'Imported DX file', 'potential_dx', QtWidgets.QLineEdit())
        self.selection = field(source, 'Explicit molecular selection', 'potential_selection', QtWidgets.QLineEdit())
        self.state = field(source, 'Molecular state', 'potential_state', integer(1))
        self.units = field(source, 'Raw potential units', 'potential_units', combo(['kBT/e', 'V', 'mV', 'unknown']))
        self.display_units = field(source, 'Displayed potential units', 'potential_display_units', combo(['kBT/e', 'V', 'mV', 'unknown']))
        self.temperature = field(source, 'Temperature (K)', 'potential_temperature', decimal(298.15, 250, 400, 2))
        self.preparation_group = QtWidgets.QWidget()
        preparation_layout = QtWidgets.QVBoxLayout(self.preparation_group)
        preparation_layout.setContentsMargins(0, 0, 0, 0)
        preparation = form(preparation_layout)
        self.force_field = field(preparation, 'Force field', 'potential_force_field', combo(['PARSE', 'AMBER', 'CHARMM']))
        self.ph = field(preparation, 'pH', 'potential_ph', decimal(7, 0, 14, 2))
        self.protonation = field(preparation, 'Protonation', 'potential_protonation', combo(['propka', 'preserve']))
        self.drop_water = field(preparation, 'Drop selected waters', 'potential_drop_water', check())
        layout.addWidget(self.preparation_group)
        self.solver_group = QtWidgets.QWidget()
        solver_layout = QtWidgets.QVBoxLayout(self.solver_group)
        solver_layout.setContentsMargins(0, 0, 0, 0)
        solver_layout.addWidget(text('Linearized Poisson–Boltzmann (LPBE); 65³ is preview quality. Symmetric ±1 salt ions.'))
        solver = form(solver_layout)
        self.grid = [field(solver, f'Grid {axis.upper()} (points)', 'potential_grid_' + axis,
            combo(['33', '65', '97', '129', '161', '193'])) for axis in ('x', 'y', 'z')]
        for widget in self.grid:
            widget.setCurrentText('65')
        self.solute_dielectric = field(solver, 'Solute dielectric', 'potential_solute_dielectric', decimal(2, 0.001))
        self.solvent_dielectric = field(solver, 'Solvent dielectric', 'potential_solvent_dielectric', decimal(78.5, 0.001))
        self.salt = field(solver, 'Salt concentration (mol/L)', 'potential_salt', decimal(0.15, 0, 2))
        self.ion_radius = field(solver, 'Ion radius (Å)', 'potential_ion_radius', decimal(2, 0.001))
        self.probe_radius = field(solver, 'Solvent probe radius (Å)', 'potential_probe_radius', decimal(1.4, 0.001))
        self.coarse_padding = field(solver, 'Coarse total padding / axis (Å)', 'potential_coarse_padding', decimal(40, 0.001))
        self.fine_padding = field(solver, 'Fine total padding / axis (Å)', 'potential_fine_padding', decimal(20, 0.001))
        layout.addWidget(self.solver_group)
        display = form(layout)
        self.ramp_limit = field(display, 'Symmetric ramp ± limit (display units)', 'potential_ramp_limit', decimal(5, 0.000001, 1e12, 6))
        configured = os.environ.get('MOLSCENE_TOOLS_CONFIG', '')
        initial = configured if configured and Path(configured).is_file() else ''
        self.tools_path = field(display, 'Tools schema 1 JSON file', 'potential_tools_path', QtWidgets.QLineEdit(initial))
        self.tools_path.setPlaceholderText('Select a configured tools JSON path, then Check tools')
        self.tools_status = field(display, 'Tools / runtime status', 'potential_tools_status', text(
            'Configuration supplied; not checked.' if initial else 'No tools configuration. Supply schema 1 JSON and select Check tools.'))
        self.actions = buttons(layout, [('potential_tools_check', 'Check tools')])
        self.tools_table = QtWidgets.QTreeWidget()
        self.tools_table.setObjectName('potential_tools_configuration')
        self.tools_table.setAccessibleName('Structured tools configuration and actual probe results')
        self.tools_table.setColumnCount(2)
        self.tools_table.setHeaderLabels(['Tool / field', 'Value'])
        self.tools_table.setMinimumWidth(0)
        self.tools_table.setMaximumHeight(180)
        self.tools_table.setRootIsDecorated(True)
        self.tools_table.header().setStretchLastSection(True)
        layout.addWidget(self.tools_table)
        self._tool_rows = {}
        self._shown_tools_config = None
        output = form(layout)
        self.output = field(output, 'New output directory', 'potential_output', QtWidgets.QLineEdit())
        self.actions.update(buttons(layout, [
            ('potential_compute', 'Compute potential'), ('potential_load_result', 'Load result copy…'),
            ('potential_ramp_apply', 'Apply ramp')]))
        self.diagnostics = text('No potential result loaded.')
        self.diagnostics.setObjectName('potential_diagnostics')
        self.diagnostics.setAccessibleName('Potential diagnostics and radius provenance')
        layout.addWidget(self.diagnostics)
        self.mode.currentTextChanged.connect(self._mode_changed)
        self.loaded_record = None
        self.actions['potential_ramp_apply'].setEnabled(False)
        self.actions['potential_tools_check'].clicked.connect(lambda: self.advanced.run(self.check_tools))
        self.actions['potential_compute'].clicked.connect(lambda: self.advanced.run(self.compute))
        self.actions['potential_load_result'].clicked.connect(lambda: self.advanced.run(self.load_result))
        self.actions['potential_ramp_apply'].clicked.connect(lambda: self.advanced.run(self.apply_ramp))
        self._mode_changed(self.mode.currentText())

    def _source_visible(self, widget, visible):
        widget.setVisible(visible)
        label = self._source_form.labelForField(widget)
        if label is not None:
            label.setVisible(visible)

    def _mode_changed(self, mode):
        imported = mode == 'import'
        self._source_visible(self.pqr, mode != 'prepare')
        self._source_visible(self.dx, imported)
        self._source_visible(self.selection, mode != 'prepared')
        self._source_visible(self.state, mode != 'prepared')
        self.preparation_group.setVisible(mode == 'prepare')
        self.solver_group.setVisible(not imported)
        self.units.setEnabled(imported)
        if not imported:
            self.units.setCurrentText('kBT/e')
        self.actions['potential_compute'].setText('Import potential' if imported else 'Compute potential')
        self.diagnostics.setText(
            'Import: declare DX units. PQR uses PQR radii; a molecular selection uses its existing vdw radii. No solver or protonation provenance is inferred.'
            if imported else 'Surface uses an owned prepared molecular copy with PQR radii and the stated solvent probe. Original molecules are preserved.')

    def request_parameters(self):
        """Assemble visible settings; core validation remains the controller's job."""
        mode = self.mode.currentText()
        return dict(mode=mode, pqr_path=(self.pqr.text().strip() or None) if mode != 'prepare' else None,
            dx_path=(self.dx.text().strip() or None) if mode == 'import' else None,
            selection=('all' if mode == 'prepared' or (mode == 'import' and self.pqr.text().strip())
                       else self.selection.text().strip()), state=self.state.value(), units=self.units.currentText(),
            display_units=self.display_units.currentText(), temperature=self.temperature.value(),
            preparation=dict(force_field=self.force_field.currentText(), ph=self.ph.value(),
                protonation=self.protonation.currentText(), drop_water=self.drop_water.isChecked()),
            solver=dict(grid=[int(widget.currentText()) for widget in self.grid],
                solute_dielectric=self.solute_dielectric.value(), solvent_dielectric=self.solvent_dielectric.value(),
                salt_molar=self.salt.value(), ion_radius=self.ion_radius.value(), probe_radius=self.probe_radius.value(),
                coarse_padding=self.coarse_padding.value(), fine_padding=self.fine_padding.value()),
            ramp_limit=self.ramp_limit.value(), render=self.advanced.timeline.render_parameters())

    def tools_configuration(self):
        from molscene.external_tools import load_tools
        path = self.tools_path.text().strip()
        if not path:
            raise ValueError('Supply a tools configuration JSON path before checking or computing')
        config = load_tools(Path(path))
        if config == self._shown_tools_config:
            return config
        self._shown_tools_config = deepcopy(config)
        self.tools_status.setText('Configuration supplied; not checked.')
        self.tools_table.clear()
        self._tool_rows = {}
        for name, entry in config['tools'].items():
            root = QtWidgets.QTreeWidgetItem([name, 'Configured; not checked'])
            self.tools_table.addTopLevelItem(root)
            self._tool_rows[name] = root
            for key in ('argv', 'architecture', 'runtime_files', 'path_dirs', 'timeout_seconds', 'memory_limit_mib'):
                value = json.dumps(entry[key], ensure_ascii=False) if isinstance(entry[key], list) else str(entry[key])
                child = QtWidgets.QTreeWidgetItem([key, value])
                child.setToolTip(1, value)
                root.addChild(child)
            root.setExpanded(True)
        return config

    def check_tools(self):
        self._shown_tools_config = None
        config = self.tools_configuration()
        self.advanced.panel._launch_job('tools', {'tools': config}, state=1)

    def compute(self):
        from molscene.electrostatics import validate_potential_request
        output = self.output.text().strip()
        if not output:
            raise ValueError('Choose a new output directory for the potential bundle')
        request = self.request_parameters()
        request['tools'] = self.tools_configuration() if request['mode'] != 'import' else {}
        request = validate_potential_request(request)
        self.advanced.panel._launch_job('potential', {'request': request}, output, state=request['state'])

    def show_tools_result(self, result):
        lines = []
        for name, entry in result.get('tools', {}).items():
            manifest = entry.get('manifest') or {}
            lines.append(f"{name}: {'available' if entry.get('available') else 'unavailable'} · "
                         f"version {manifest.get('version', '?')} · {manifest.get('architecture', '?')}")
            if entry.get('error'):
                lines.append(str(entry['error']))
            if manifest:
                lines.append('argv: ' + json.dumps(manifest.get('argv', []), ensure_ascii=False))
            if name in self._tool_rows:
                root = self._tool_rows[name]
                root.setText(1, ('Available' if entry.get('available') else 'Unavailable') + ' · ' + str(manifest.get('version', '?')))
                for key, value in (('actual_version', manifest.get('version', '?')), ('status', entry.get('error') or 'available')):
                    root.addChild(QtWidgets.QTreeWidgetItem([key, str(value)]))
        self.tools_status.setText('\n'.join(lines) or ('Tools check cancelled.' if result.get('cancelled') else 'No tools configured.'))

    def load_result(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Load a verified density or potential result copy',
            self.output.text().strip(), 'Bundle report (report.json);;JSON (*.json)')
        if not path:
            return
        record = self.advanced.controller.load_advanced_result(path)
        if record['operation'] == 'potential':
            self.loaded_record = record
            self.actions['potential_ramp_apply'].setEnabled(True)
            self._loaded_diagnostics()
        self.advanced.panel._show_record(record)

    def _loaded_diagnostics(self):
        loaded = self.loaded_record['loaded']
        self.diagnostics.setText(f"Owned surface: {loaded['molecule']}\n"
            f"{loaded['legend_text']}\nRadius convention: {loaded['radius_convention']}\n"
            f"Solvent probe: {loaded['probe_radius']:g} Å; molecular surface; ramp above mode 0\n"
            f"Source status: {self.loaded_record['snapshot_status']}")

    def apply_ramp(self):
        if self.loaded_record is None:
            raise ValueError('Load a current potential result copy first')
        self.loaded_record = self.advanced.controller.potential_ramp(self.loaded_record,
            ramp_limit=self.ramp_limit.value())
        self._loaded_diagnostics()
        self.advanced.panel._show_record(self.loaded_record)
