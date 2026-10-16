"""Density display and registration controls with explicit raw-value units."""
import json
import uuid
from pymol.Qt import QtWidgets
from .advanced_panel import buttons, check, combo, decimal, field, form, integer, text


class DensityPanel(QtWidgets.QWidget):
    def __init__(self, advanced):
        super().__init__(advanced)
        self.advanced = advanced
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        source = form(layout)
        self.path = field(source, 'Map file', 'density_path', QtWidgets.QLineEdit())
        self.format = field(source, 'File format', 'density_format', combo(['dx', 'mrc', 'ccp4']))
        self.interpretation = field(source, 'MRC origin / starts', 'density_interpretation', combo(['auto', 'origin', 'starts']))
        self.actions = buttons(layout, [('density_load', 'Load validated map')])
        display = form(layout)
        self.maps = field(display, 'Managed / session map', 'density_maps', combo([]))
        self.state = field(display, 'Source map state', 'density_state', integer(1))
        self.metadata = field(display, 'Geometry / provenance', 'density_metadata', text('No map selected. Raw map values are not automatically sigma.'))
        self.kind = field(display, 'Display kind', 'density_kind', combo(['mesh', 'surface', 'volume']))
        self.level = field(display, 'Contour level (raw units)', 'density_level', decimal(1, -1e12, 1e12, 6))
        self.color = field(display, 'Color name', 'density_color', QtWidgets.QLineEdit('cyan'))
        self.opacity = field(display, 'Opacity (0–1)', 'density_opacity', decimal(1, 0, 1))
        self.volume_ramp = field(display, 'Volume ramp JSON', 'density_volume_ramp', QtWidgets.QLineEdit())
        self.volume_ramp.setPlaceholderText('[value,r,g,b,alpha, value,r,g,b,alpha]')
        self.visible = field(display, 'Owned display visible', 'density_visible', check(True))
        self.actions.update(buttons(layout, [('density_apply_display', 'Apply display')]))
        registration = form(layout)
        self.registration_mode = field(registration, 'Registration method', 'density_registration_mode', combo(['matrix', 'align']))
        self.matrix = field(registration, 'World transform JSON (Å)', 'density_matrix', QtWidgets.QLineEdit(
            '[1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]'))
        self.matrix.setToolTip('Row-major 4×4 proper rigid matrix: source world to registered world. Translation is in angstrom.')
        self.mobile = field(registration, 'Mobile model', 'density_mobile', combo([]))
        self.target = field(registration, 'Target model', 'density_target', combo([]))
        self.mobile_state = field(registration, 'Mobile state', 'density_mobile_state', integer(1))
        self.target_state = field(registration, 'Target state', 'density_target_state', integer(1))
        self.alignment_selection = field(registration, 'Alignment selection', 'density_alignment_selection', QtWidgets.QLineEdit('all'))
        self.actions.update(buttons(layout, [('density_register', 'Register a new copy')]))
        output = form(layout)
        self.output = field(output, 'New output directory', 'density_output', QtWidgets.QLineEdit())
        self.actions.update(buttons(layout, [('density_export', 'Export density bundle')]))
        self.kind.currentTextChanged.connect(self._display_kind)
        self.registration_mode.currentTextChanged.connect(self._registration_kind)
        self._display_kind(self.kind.currentText())
        self._registration_kind(self.registration_mode.currentText())
        self.records = []
        self._preferred_identity = None
        self.state.setReadOnly(True)
        for name, callback in (
            ('density_load', self.load), ('density_apply_display', self.apply_display),
            ('density_register', self.register),
            ('density_export', self.export),
        ):
            self.actions[name].clicked.connect(lambda checked=False, fn=callback: self.advanced.run(fn))
        self.maps.currentIndexChanged.connect(self._show_metadata)

    def _display_kind(self, kind):
        self.level.setEnabled(kind != 'volume')
        self.color.setEnabled(kind != 'volume')
        self.volume_ramp.setEnabled(kind == 'volume')

    def _registration_kind(self, kind):
        self.matrix.setEnabled(kind == 'matrix')
        for widget in (self.mobile, self.target, self.mobile_state, self.target_state, self.alignment_selection):
            widget.setEnabled(kind == 'align')

    def display_request(self):
        from molscene.density import validate_display
        ramp = self.volume_ramp.text().strip()
        return validate_display(dict(kind=self.kind.currentText(), level=self.level.value(),
            color=self.color.text().strip(), opacity=self.opacity.value(), visible=self.visible.isChecked(),
            volume_ramp=json.loads(ramp) if ramp else []))

    def refresh(self, records, objects):
        self.records = records
        selected = self._preferred_identity or self.maps.currentData()
        selected = tuple(selected) if selected is not None else None
        self._preferred_identity = None
        self.maps.blockSignals(True)
        self.maps.clear()
        for record in records:
            self.maps.addItem(f"{record['name']} · state {record['state']}", (record['name'], record['state']))
        # QVariant equality for stored Python tuples differs across bindings;
        # match the explicit identity in Python on both Qt5 and Qt6.
        index = next((i for i, record in enumerate(records)
                      if (record['name'], record['state']) == selected), -1)
        self.maps.setCurrentIndex(max(0, index) if records else -1)
        self.maps.blockSignals(False)
        self._show_metadata()
        for widget in (self.mobile, self.target):
            selected = widget.currentText()
            widget.clear()
            widget.addItems(objects)
            if selected in objects:
                widget.setCurrentText(selected)

    def _selected_record(self):
        identity = tuple(self.maps.currentData() or ())
        for record in self.records:
            if (record['name'], record['state']) == identity:
                return record
        raise ValueError('Load or select a map first')

    def _show_metadata(self, index=None):
        if self.maps.currentIndex() < 0:
            self.metadata.setText('No map selected. Raw map values are not automatically sigma.')
            return
        record = self._selected_record()
        self.state.setValue(record['state'])
        grid = record.get('grid') or {}
        placement = ('Registered / transformed placement' if record.get('world_matrix') != record.get('load_matrix')
                     else 'Original loaded placement')
        self.metadata.setText(
            f"{'Verified source' if record.get('verified') else 'Unverified session map'}\n"
            f"{placement}; Export uses this selected placement once.\n"
            f"World matrix: {record.get('world_matrix', 'unknown')}\n"
            f"Raw units: {grid.get('value_unit', 'unknown')}; normalization: {grid.get('normalization', 'unknown')}\n"
            f"Shape: {grid.get('shape', '?')}; origin (Å): {grid.get('origin_angstrom', '?')}\n"
            f"Basis (Å): {grid.get('basis_angstrom', '?')}\n"
            f"Statistics: {grid.get('statistics', {})}\n"
            f"Format / interpretation: {record.get('format')} / {record.get('interpretation')}\n"
            f"Source SHA256: {record.get('source_sha256') or 'unassociated'}")

    def load(self):
        path = self.path.text().strip()
        if not path:
            raise ValueError('Choose a density map path first')
        record = self.advanced.controller.density_load(path, format=self.format.currentText(),
            interpretation=self.interpretation.currentText(), state=1)
        self._preferred_identity = (record['name'], record['state'])

    def apply_display(self):
        record = self._selected_record()
        return self.advanced.controller.density_display(record['name'], self.display_request(), state=record['state'])

    def registration_request(self):
        token = uuid.uuid4().hex[:12]
        request = dict(kind=self.registration_mode.currentText(), copy_name='density_registered_' + token)
        if request['kind'] == 'matrix':
            request['matrix'] = json.loads(self.matrix.text())
        else:
            request.update(mobile=self.mobile.currentText(), target=self.target.currentText(),
                mobile_state=self.mobile_state.value(), target_state=self.target_state.value(),
                selection=self.alignment_selection.text().strip(), algorithm='align', cutoff=2.0,
                cycles=5, model_copy_name='density_aligned_model_' + token)
        return request

    def register(self):
        record = self._selected_record()
        result = self.advanced.controller.density_register(record['name'], self.registration_request(), state=record['state'])
        self._preferred_identity = (result['record']['name'], result['record']['state'])
        return result

    def export(self):
        record = self._selected_record()
        output = self.output.text().strip()
        if not output:
            raise ValueError('Choose a new output directory for the density bundle')
        request = dict(map_path=record['source_path'], format=record['format'],
            interpretation=record['interpretation'] if record['format'] != 'dx' else 'auto',
            source_state=record['state'], map_name=record['name'], display=self.display_request(),
            registration=None, render=self.advanced.timeline.render_parameters())
        self.advanced.panel._launch_job('density', {'request': request}, output, state=record['state'])
