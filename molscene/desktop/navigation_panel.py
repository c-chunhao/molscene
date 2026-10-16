"""Compact native navigation; scientific membership stays in the controller."""
from pymol.Qt import QtCore, QtWidgets


def disclosure(layout, title, name, opened=False):
    group = QtWidgets.QWidget()
    outer = QtWidgets.QVBoxLayout(group)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(3)
    toggle = QtWidgets.QToolButton()
    toggle.setObjectName(name)
    toggle.setText(title)
    toggle.setCheckable(True)
    toggle.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
    content = QtWidgets.QWidget()
    content.setProperty('disclosure_content', True)
    inner = QtWidgets.QVBoxLayout(content)
    inner.setContentsMargins(4, 0, 0, 4)
    inner.setSpacing(4)
    def changed(value):
        content.setVisible(value)
        toggle.setArrowType(QtCore.Qt.DownArrow if value else QtCore.Qt.RightArrow)
    toggle.toggled.connect(changed)
    outer.addWidget(toggle)
    outer.addWidget(content)
    layout.addWidget(group)
    toggle.setChecked(opened)
    changed(opened)
    return inner


class ResidueModel(QtCore.QAbstractTableModel):
    columns = ('resi', 'resn', 'chain', 'object', 'segi', 'one_letter', 'polymer', 'atom_count')
    labels = ('Resi', 'Residue', 'Chain', 'Object', 'Segment', 'Observed', 'Polymer', 'Atoms')

    def __init__(self, parent):
        super().__init__(parent)
        self.rows = []

    def rowCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        if role == QtCore.Qt.UserRole:
            return row
        if role == QtCore.Qt.ToolTipRole:
            return ' · '.join(f'{key}: {row[key]}' for key in ('object', 'segi', 'chain', 'resi', 'resn', 'state', 'atom_count'))
        if role == QtCore.Qt.DisplayRole:
            return str(row[self.columns[index.column()]])

    def headerData(self, section, orientation, role=QtCore.Qt.DisplayRole):
        if role == QtCore.Qt.DisplayRole:
            return self.labels[section] if orientation == QtCore.Qt.Horizontal else str(section + 1)


class ResidueFilter(QtCore.QSortFilterProxyModel):
    def __init__(self, navigator):
        super().__init__(navigator)
        self.navigator = navigator

    def filterAcceptsRow(self, number, parent):
        row = self.sourceModel().rows[number]
        nav = self.navigator
        obj, chain = nav.scope.currentData(), nav.chain.currentData()
        query = nav.search.text().strip().casefold()
        return ((obj is None or row['object'] == obj)
                and (chain is None or row['chain'] == chain)
                and (not nav.polymer.isChecked() or row['polymer'] != 'other')
                and (not query or query in ' '.join(str(row[key]) for key in ResidueModel.columns).casefold()))


class NavigationPanel(QtWidgets.QWidget):
    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.controller = panel.controller
        self.inventory = None
        self.setObjectName('navigation_panel')
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.source = QtWidgets.QComboBox()
        self.source.setObjectName('selection_source')
        self.source.addItems(['Expression', 'Selected objects', 'Browser residues', 'Current sele', 'Named selection'])
        layout.addWidget(self.source)
        panel._button(layout, 'Preview / capture source', 'preview_source', self.capture_source)
        form = QtWidgets.QFormLayout()
        form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        layout.addLayout(form)
        self.scope = QtWidgets.QComboBox()
        self.scope.setObjectName('residue_object')
        self.scope.addItem('All objects', None)
        form.addRow('Object', self.scope)
        self.chain = QtWidgets.QComboBox()
        self.chain.setObjectName('residue_chain')
        self.chain.addItem('All chains', None)
        form.addRow('Chain', self.chain)
        self.search = QtWidgets.QLineEdit()
        self.search.setObjectName('residue_filter')
        self.search.setPlaceholderText('Find observed residues…')
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        row = panel._row(layout)
        self.polymer = QtWidgets.QCheckBox('Polymer only')
        self.polymer.setObjectName('residue_polymer')
        self.polymer.setChecked(True)
        row.addWidget(self.polymer)
        panel._button(row, 'Refresh', 'residue_refresh', self.refresh_residues).setToolTip('Refresh observed residue identities')
        self.browser = QtWidgets.QTableView()
        self.browser.setObjectName('residue_browser')
        self.browser.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.browser.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.browser.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.browser.setMinimumHeight(100)
        self.browser.setMaximumHeight(180)
        self.browser.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.model = ResidueModel(self)
        self.proxy = ResidueFilter(self)
        self.proxy.setSourceModel(self.model)
        self.browser.setModel(self.proxy)
        for index, width in enumerate((48, 56, 48, 95, 65, 65, 65, 50)):
            self.browser.setColumnWidth(index, width)
        self.browser.verticalHeader().hide()
        layout.addWidget(self.browser)
        self.count = QtWidgets.QLabel()
        self.count.setWordWrap(True)
        self.count.setObjectName('residue_count')
        layout.addWidget(self.count)
        panel._button(layout, 'Use selected residues', 'use_residues', self.use_residues)
        row = panel._row(layout)
        panel._button(row, 'Use as A', 'send_analysis_a', lambda: self.handoff(panel.analysis_selection))
        panel._button(row, 'Use as B', 'send_analysis_b', lambda: self.handoff(panel.contacts_b))
        panel._button(layout, 'Use as ligand', 'use_preset_ligand', self.use_ligand)
        panel._button(layout, 'Open Analysis', 'open_analysis', lambda: panel.tabs.setCurrentIndex(2))
        form = QtWidgets.QFormLayout()
        layout.addLayout(form)
        self.save_name = panel._edit(form, 'Save name', 'named_selection_name')
        row = panel._row(layout)
        panel._button(row, 'Save', 'save_named_selection', self.save_selection).setToolTip('Save active selection under the new name')
        self.delete_named = panel._button(row, 'Delete named', 'delete_named_selection', self.delete_selection)
        self.delete_named.setEnabled(False)
        self.chosen_name = None
        panel.named_selections.activated.connect(self.choose_named)
        focus = disclosure(layout, 'Focus and live style', 'focus_section', True)
        row = panel._row(focus)
        panel._button(row, 'Focus', 'focus_selection', lambda: self.focus(True))
        panel._button(row, 'Center', 'center_selection', lambda: self.focus(False))
        form = QtWidgets.QFormLayout()
        focus.addLayout(form)
        self.buffer = QtWidgets.QDoubleSpinBox()
        self.buffer.setObjectName('focus_buffer')
        self.buffer.setRange(0, 1000)
        self.buffer.setValue(3)
        form.addRow('Zoom buffer (Å)', self.buffer)
        self.preset = QtWidgets.QComboBox()
        self.preset.setObjectName('preset_kind')
        self.preset.addItems(['overview', 'surface', 'pocket'])
        form.addRow('Live preset', self.preset)
        self.ligand = panel._edit(form, 'Pocket ligand', 'preset_ligand')
        self.ligand.setPlaceholderText('Ligand subset inside active context')
        self.radius = QtWidgets.QDoubleSpinBox()
        self.radius.setObjectName('preset_radius')
        self.radius.setRange(0.01, 1000)
        self.radius.setValue(5)
        form.addRow('Pocket radius (Å)', self.radius)
        self.frame = QtWidgets.QCheckBox('Frame selection')
        self.frame.setObjectName('preset_frame')
        focus.addWidget(self.frame)
        panel._button(focus, 'Apply to selection', 'apply_preset', self.apply_preset)
        row = panel._row(focus)
        panel._button(row, 'Hide solvent', 'hide_solvent', lambda: self.hide_context(True, False))
        panel._button(row, 'Hide H/D/T', 'hide_hydrogens', lambda: self.hide_context(False, True))
        clean = disclosure(layout, 'Create cleaned copy', 'clean_section')
        form = QtWidgets.QFormLayout()
        clean.addLayout(form)
        self.clean_name = panel._edit(form, 'New object', 'clean_name')
        self.clean_solvent = QtWidgets.QCheckBox('Remove solvent')
        self.clean_solvent.setObjectName('clean_solvent')
        self.clean_solvent.setChecked(True)
        self.clean_h = QtWidgets.QCheckBox('Remove H/D/T')
        self.clean_h.setObjectName('clean_hydrogens')
        clean.addWidget(self.clean_solvent)
        clean.addWidget(self.clean_h)
        self.clean_altloc = QtWidgets.QComboBox()
        self.clean_altloc.setObjectName('clean_altloc')
        self.clean_altloc.addItems(['primary', 'all'])
        form.addRow('Altloc (primary = blank+A)', self.clean_altloc)
        note = QtWidgets.QLabel('Creates state 1 from the requested source state. Sources are preserved. No hydrogen addition or pH assignment.')
        note.setWordWrap(True)
        clean.addWidget(note)
        panel._button(clean, 'Create new object', 'clean_copy', self.clean_copy)
        self.search.textChanged.connect(self.filter_rows)
        self.polymer.toggled.connect(self.filter_rows)
        self.scope.currentIndexChanged.connect(self.scope_changed)
        self.chain.currentIndexChanged.connect(self.filter_rows)
        self.browser.selectionModel().selectionChanged.connect(self.update_count)
        self.source.currentIndexChanged.connect(self.invalidate)
        panel.selection.textChanged.connect(self.expression_changed)
        panel.state.valueChanged.connect(self.state_changed)

    def invalidate(self, *args):
        self.panel.selection_summary.setText('Selection changed — preview required')
        self.panel.selection_summary.setToolTip('')

    def expression_changed(self):
        self.source.setCurrentText('Expression')
        self.invalidate()

    def update_inventory(self, inventory, *, force=False):
        signature = tuple((item['name'], item['atoms'], item['states']) for item in inventory)
        if force or signature != self.inventory:
            self.inventory = signature
            previous = self.scope.currentData()
            self.scope.blockSignals(True)
            self.scope.clear()
            self.scope.addItem('All objects', None)
            for item in inventory:
                self.scope.addItem(item['name'], item['name'])
            self.scope.setCurrentIndex(max(0, self.scope.findData(previous)))
            self.scope.blockSignals(False)
            self.refresh_residues()

    def state_changed(self):
        self.invalidate()
        if not getattr(self.panel, '_mutation_locked', False):
            self.panel.run(self.refresh_residues)

    def refresh_residues(self, invalidate_active=True):
        if invalidate_active:
            self.invalidate()
        self.model.beginResetModel()
        self.model.rows = []
        self.model.endResetModel()
        self.update_count()
        obj = self.scope.currentData()
        # Object names come from the live inventory; residue/chain labels never
        # enter selection grammar. The controller validates requested-state rows.
        rows = self.controller.residues('%' + obj if obj is not None else 'all', state=self.panel.state.value()) if self.inventory else []
        self.model.beginResetModel()
        self.model.rows = rows
        self.model.endResetModel()
        previous = self.chain.currentData()
        self.chain.blockSignals(True)
        self.chain.clear()
        self.chain.addItem('All chains', None)
        for chain in dict.fromkeys(row['chain'] for row in self.model.rows
                                   if self.scope.currentData() is None or row['object'] == self.scope.currentData()):
            self.chain.addItem(chain or '(blank)', chain)
        self.chain.setCurrentIndex(max(0, self.chain.findData(previous)))
        self.chain.blockSignals(False)
        self.filter_rows()

    def scope_changed(self):
        self.panel.run(lambda: self.refresh_residues(invalidate_active=False))

    def filter_rows(self, *args):
        self.browser.clearSelection()
        self.proxy.invalidateFilter()
        self.update_count()

    def update_count(self, *args):
        self.count.setText(f'{self.proxy.rowCount()} / {len(self.model.rows)} residues shown · {len(self.browser.selectionModel().selectedRows())} selected')

    def selected_rows(self):
        rows = [self.model.rows[self.proxy.mapToSource(index).row()]
                for index in self.browser.selectionModel().selectedRows()]
        if not rows:
            raise ValueError('Select observed residue rows first')
        return rows

    def summary(self, capture):
        summary = self.controller.selection_summary(capture['selection'], state=self.panel.state.value())
        self.panel.selection_summary.setText(f"{summary['residue_count']} residues · {summary['atom_count']} atoms · state {summary['state']}")
        self.panel.selection_summary.setToolTip(f"Source: {capture['source']}\nObjects: {', '.join(summary['objects'])}\n{capture['selection']}")
        return capture

    def capture_source(self):
        source = self.source.currentText()
        if source == 'Browser residues':
            return self.use_residues()
        if source == 'Selected objects':
            names = self.panel.selected_objects()
            rows = [row for row in self.controller.residues(state=self.panel.state.value()) if row['object'] in names]
            capture = self.controller.select_residues(rows, state=self.panel.state.value())
        else:
            expression = {'Current sele':'sele', 'Named selection':self.panel.named_selections.currentText()}.get(source, self.panel.selection.text())
            capture = self.controller.capture_selection(expression, state=self.panel.state.value())
        self.panel.selection.setText(capture['selection'])
        return self.summary(capture)

    def use_residues(self):
        capture = self.controller.select_residues(self.selected_rows(), state=self.panel.state.value())
        self.panel.selection.setText(capture['selection'])
        return self.summary(capture)

    def handoff(self, entry):
        capture = self.controller.capture_selection(self.panel.selection.text(), state=self.panel.state.value())
        entry.setText(capture['selection'])
        self.panel.analysis_state.setValue(capture['state'])
        self.summary(capture)
        self.panel.status.setText('Captured exact membership for ' + ('partner A' if entry is self.panel.analysis_selection else 'partner B'))

    def use_ligand(self):
        capture = self.controller.select_residues(self.selected_rows(), state=self.panel.state.value())
        self.ligand.setText(capture['selection'])
        self.panel.status.setText(f"Pocket ligand: {capture['atoms']} atoms; active context retained")

    def save_selection(self):
        self.summary(self.controller.save_selection(self.save_name.text(), self.panel.selection.text(), state=self.panel.state.value()))

    def choose_named(self, index):
        self.chosen_name = self.panel.named_selections.itemText(index)
        self.delete_named.setEnabled(bool(self.chosen_name))

    def delete_selection(self):
        if not self.chosen_name or self.chosen_name != self.panel.named_selections.currentText():
            raise ValueError('Choose an existing named selection explicitly before deleting it')
        self.controller.delete_selection(self.chosen_name)
        self.chosen_name = None
        self.delete_named.setEnabled(False)

    def focus(self, orient):
        self.controller.focus_selection(self.panel.selection.text(), state=self.panel.state.value(), buffer=self.buffer.value(), orient=orient)

    def hide_context(self, solvent, hydrogens):
        self.controller.hide_context(self.panel.selection.text(), state=self.panel.state.value(), solvent=solvent, hydrogens=hydrogens)

    def apply_preset(self):
        result = self.controller.apply_preset(self.panel.selection.text(), self.preset.currentText(), state=self.panel.state.value(),
            ligand=self.ligand.text() or None, radius=self.radius.value(), frame=self.frame.isChecked())
        self.panel.status.setText('Applied ' + result['preset'] + f" · {result['selected_atom_count']} selected atoms")
        self.panel.selection_summary.setText(f"{result['selected_residue_count']} residues · {result['selected_atom_count']} atoms · state {result['state']}")
        self.panel.selection_summary.setToolTip('Validated for live ' + result['preset'] + '\n' + result['selection'])

    def clean_copy(self):
        result = self.controller.prepare_structure(self.panel.selection.text(), object_name=self.clean_name.text(), state=self.panel.state.value(),
            remove_solvent=self.clean_solvent.isChecked(), remove_hydrogens=self.clean_h.isChecked(), altloc=self.clean_altloc.currentText())
        self.panel.status.setText(f"{result['object']} · state 1 from state {result['source_state']} · {result['selected_atom_count']} → {result['retained_atom_count']} atoms")
