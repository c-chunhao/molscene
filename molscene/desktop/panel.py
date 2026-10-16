"""Native workbench controls. All callbacks run serially on Qt's main thread."""
from pymol.Qt import QtCore, QtWidgets
from .navigation_panel import NavigationPanel, disclosure


class WorkbenchPanel(QtWidgets.QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setObjectName('molscene_panel')
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)
        self.selection_summary = QtWidgets.QLabel('No selection preview')
        self.selection_summary.setObjectName('selection_summary')
        self.selection_summary.setWordWrap(True)
        self.selection_summary.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        root.addWidget(self.selection_summary)
        self.page_selector = QtWidgets.QComboBox()
        self.page_selector.setObjectName('page_selector')
        self.page_selector.setAccessibleName('Workbench page')
        self.page_selector.addItems(['Structure', 'Figure', 'Analysis', 'Results', 'Advanced'])
        root.addWidget(self.page_selector)
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setObjectName('workbench_tabs')
        root.addWidget(self.tabs)
        self.status = QtWidgets.QLabel('Ready')
        self.status.setObjectName('status')
        self.status.setWordWrap(True)
        self.status.setMaximumHeight(self.status.fontMetrics().lineSpacing() * 4)
        self.status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        root.addWidget(self.status)
        from .jobs import JobManager
        self.jobs = JobManager(self)
        self.jobs.started.connect(self._job_started)
        self.jobs.progress.connect(self._job_progress)
        self.jobs.finished.connect(self._job_finished)
        self.jobs.failed.connect(self._job_failed)
        self.jobs.cancelled.connect(self._job_cancelled)
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setObjectName('job_progress')
        self.progress_bar.setRange(0, 1)
        row = self._row(root)
        row.addWidget(self.progress_bar)
        self.cancel_job = self._button(row, 'Cancel', 'cancel_job', self._cancel)
        self.cancel_job.setEnabled(False)
        self._structure_tab()
        self._scene_tab()
        self._analysis_tab()
        self._results_tab()
        from .advanced_panel import AdvancedPanel
        self.advanced = AdvancedPanel(self)
        self.tabs.addTab(self.advanced, 'Advanced')
        self.page_selector.currentIndexChanged.connect(self._select_page)
        self.tabs.currentChanged.connect(self._tab_changed)
        self._compact_navigation()
        # Long molecule names and output paths must never resize the native dock.
        for label in self.findChildren(QtWidgets.QLabel):
            label.setWordWrap(True)
            label.setMinimumWidth(0)
            label.setSizePolicy(QtWidgets.QSizePolicy.Ignored if label.property('advanced_dynamic_text')
                                else QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Preferred)
        for label in (self.status, self.selection_summary, self.result_source, self.result_summary, self.row_total):
            label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        for combo in self.findChildren(QtWidgets.QComboBox):
            combo.setMinimumContentsLength(10)
            combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.currentTextChanged.connect(combo.setToolTip)
        for form in self.findChildren(QtWidgets.QFormLayout):
            form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapAllRows if form.property('advanced_form') else QtWidgets.QFormLayout.WrapLongRows)
            form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        self.refresh()
        self.refresh_timer = QtCore.QTimer(self)
        self.refresh_timer.setInterval(1500)
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start()
        # Native PyMOL exits from C. Dispose our signal-bearing dock before that
        # exit, while Qt and Python are still alive to disconnect it safely.
        if isinstance(parent, QtWidgets.QDockWidget) and parent.parentWidget() is not None:
            self.close_guard = NativeCloseGuard(self, parent, parent.parentWidget())

    def _select_page(self, index):
        if getattr(self, '_mutation_locked', False) and index != 3:
            index = 3
        self.tabs.setCurrentIndex(index)
        self._tab_changed(index)

    def _tab_changed(self, index):
        if getattr(self, '_mutation_locked', False) and index != 3:
            self.tabs.setCurrentIndex(3)
            index = 3
        self.page_selector.blockSignals(True)
        self.page_selector.setCurrentIndex(index)
        self.page_selector.blockSignals(False)

    def _compact_navigation(self):
        compact = self.width() <= 540
        self.page_selector.setVisible(compact)
        self.tabs.tabBar().setVisible(not compact)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'advanced'):
            self._compact_navigation()

    def closeEvent(self, event):
        self.advanced.stop_preview()
        super().closeEvent(event)

    def _scroll(self, widget):
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        scroll.setMinimumWidth(0)
        scroll.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Expanding)
        return scroll

    def _button(self, layout, label, name, callback):
        button = QtWidgets.QPushButton(label)
        button.setObjectName(name)
        button.clicked.connect(lambda checked=False: self.run(callback))
        layout.addWidget(button)
        return button

    def _row(self, layout):
        row = QtWidgets.QHBoxLayout()
        layout.addLayout(row)
        return row

    def _structure_tab(self):
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        row = self._row(layout)
        self._button(row, 'Load…', 'load_coordinates', self._load)
        self._button(row, 'Open PSE…', 'open_session', self._session)
        self._button(row, 'Refresh', 'refresh', self.refresh)
        label = QtWidgets.QLabel('Molecular objects — select explicitly to change')
        label.setWordWrap(True)
        layout.addWidget(label)
        self.object_list = QtWidgets.QListWidget()
        self.object_list.setObjectName('objects')
        self.object_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.object_list.setMaximumHeight(90)
        self.object_list.setTextElideMode(QtCore.Qt.ElideMiddle)
        layout.addWidget(self.object_list)
        row = self._row(layout)
        self._button(row, 'Show', 'show_objects', lambda: self.controller.set_visible(self.selected_objects(), True))
        self._button(row, 'Hide', 'hide_objects', lambda: self.controller.set_visible(self.selected_objects(), False))
        self._button(row, 'Orient', 'orient_objects', lambda: self.controller.orient_objects(self.selected_objects()))
        row = self._row(layout)
        self._button(row, 'Rename…', 'rename_object', self._rename)
        self._button(row, 'Delete', 'delete_objects', lambda: self.controller.delete_objects(self.selected_objects()))
        self.named_selections = QtWidgets.QComboBox()
        self.named_selections.setObjectName('named_selections')
        self.named_selections.activated.connect(lambda index: self.selection.setText(self.named_selections.itemText(index)))
        label = QtWidgets.QLabel('Named selections (choose to copy below)')
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addWidget(self.named_selections)
        self.selection = QtWidgets.QLineEdit('all')
        self.selection.setObjectName('selection')
        self.selection.setPlaceholderText('PyMOL selection, e.g. chain A and resi 10-20')
        layout.addWidget(self.selection)
        row = self._row(layout)
        row.addWidget(QtWidgets.QLabel('State'))
        self.state = QtWidgets.QSpinBox()
        self.state.setObjectName('state')
        self.state.setRange(1, 1000000)
        self.state.setValue(max(1, int(self.controller.cmd.get_state())))
        row.addWidget(self.state)
        self._button(row, 'Go', 'set_state', lambda: self.controller.set_state(self.state.value())).setToolTip('Display the requested state')
        row = self._row(layout)
        self._button(row, 'Capture', 'capture_selection', lambda: self._capture(self.selection.text()))
        self._button(row, 'Current sele', 'capture_current', lambda: self._capture('sele'))
        picks = disclosure(layout, 'Native picks', 'picks_section')
        row = self._row(picks)
        for number in range(1, 5):
            if number == 3:
                row = self._row(picks)
            self._button(row, 'Pick ' + str(number), 'capture_pk' + str(number),
                         lambda n=number: self._capture('pk' + str(n), single=True))
        self.navigation = NavigationPanel(self)
        layout.addWidget(self.navigation)
        advanced = disclosure(layout, 'Representation & color', 'representation_section')
        form = QtWidgets.QFormLayout()
        form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        advanced.addLayout(form)
        self.representation = QtWidgets.QComboBox()
        self.representation.setObjectName('representation')
        self.representation.addItems(['cartoon', 'ribbon', 'sticks', 'lines', 'spheres', 'surface', 'mesh', 'dots', 'nonbonded'])
        form.addRow('Representation', self.representation)
        row = self._row(advanced)
        self._button(row, 'Show', 'show_representation', self._representation)
        self._button(row, 'Hide', 'hide_representation', self._hide_representation)
        self.coloring = QtWidgets.QComboBox()
        self.coloring.setObjectName('coloring')
        self.coloring.addItems(['chain', 'element', 'single', 'rainbow', 'bfactor'])
        self.color = QtWidgets.QLineEdit('marine')
        self.color.setObjectName('single_color')
        form.addRow('Coloring', self.coloring)
        form.addRow('Single color', self.color)
        self._button(advanced, 'Apply coloring', 'apply_coloring', self._coloring)
        self.tabs.addTab(self._scroll(tab), 'Structure')
        self.tabs.setTabToolTip(0, 'Structure & display')

    def _scene_tab(self):
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        label = QtWidgets.QLabel('Scene bookmarks retain the live view, state and display in PSE sessions.')
        label.setWordWrap(True)
        layout.addWidget(label)
        self.scene_name = QtWidgets.QLineEdit()
        self.scene_name.setObjectName('scene_name')
        self.scene_name.setPlaceholderText('New bookmark name')
        layout.addWidget(self.scene_name)
        self._button(layout, 'Store bookmark', 'store_scene', lambda: self.controller.store_scene(self.scene_name.text()))
        self.scene_list = QtWidgets.QListWidget()
        self.scene_list.setObjectName('scenes')
        self.scene_list.setMaximumHeight(90)
        self.scene_list.itemDoubleClicked.connect(lambda item: self.run(lambda: self.controller.restore_scene(item.text())))
        layout.addWidget(self.scene_list)
        row = self._row(layout)
        self._button(row, 'Restore', 'restore_scene', lambda: self.controller.restore_scene(self._scene()))
        self._button(row, 'Delete', 'delete_scene', lambda: self.controller.delete_scene(self._scene()))
        row = self._row(layout)
        self._button(row, 'Update selected', 'scene_update', lambda: self.controller.update_scene(self._scene()))
        self._button(row, 'Rename…', 'scene_rename', self._rename_scene)
        appearance = disclosure(layout, 'Appearance and labels', 'appearance_section')
        form = QtWidgets.QFormLayout()
        form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        appearance.addLayout(form)
        self.background = QtWidgets.QComboBox()
        self.background.setObjectName('background')
        self.background.addItems(['white', 'black', 'transparent'])
        form.addRow('Background', self.background)
        self.surface_opacity = QtWidgets.QDoubleSpinBox()
        self.surface_opacity.setObjectName('surface_opacity')
        self.surface_opacity.setRange(0, 1)
        self.surface_opacity.setSingleStep(0.1)
        self.surface_opacity.setValue(1)
        form.addRow('Surface opacity', self.surface_opacity)
        self.stick_radius = QtWidgets.QDoubleSpinBox()
        self.stick_radius.setObjectName('stick_radius')
        self.stick_radius.setRange(0.01, 3)
        self.stick_radius.setSingleStep(0.05)
        self.stick_radius.setValue(0.25)
        form.addRow('Stick radius (Å)', self.stick_radius)
        self._button(appearance, 'Apply appearance', 'apply_appearance', lambda: self.controller.set_appearance(
            self.selection.text(), background=self.background.currentText(),
            surface_opacity=self.surface_opacity.value(), stick_radius=self.stick_radius.value(), state=self.state.value()))
        self.label_text = QtWidgets.QLineEdit('name')
        self.label_text.setObjectName('label_expression')
        self.label_text.setPlaceholderText('Native label expression, e.g. resn + resi')
        form.addRow('Label expression', self.label_text)
        self._button(appearance, 'Apply labels', 'apply_labels', lambda: self.controller.set_labels(
            self.selection.text(), self.label_text.text(), state=self.state.value()))
        self._daily_figure_controls(layout)
        self._export_controls(layout)
        layout.addStretch()
        self.tabs.addTab(self._scroll(tab), 'Figure')
        self.tabs.setTabToolTip(1, 'Figure & session')

    def _daily_figure_controls(self, layout):
        from .advanced_panel import form, field, combo, integer, text
        from .ui_helpers import path_browse, details
        labels = disclosure(layout, 'Guided labels', 'guided_labels_section', True)
        fields = form(labels)
        self.label_recipe = field(fields, 'Recipe', 'label_recipe', combo(['atom', 'residue', 'chain_residue', 'custom']))
        self.label_recipe.setCurrentText('residue')
        self.label_size = field(fields, 'Native label size (positive)', 'label_size', integer(14, 8, 48))
        self.label_color = field(fields, 'Label color', 'label_color', QtWidgets.QLineEdit('black'))
        self.label_scope = text('Size is shared by all labels in the same object/state. Text and color affect targets only; ordinary multistate atoms share label text/color across states.')
        self.label_scope.setObjectName('label_size_scope')
        labels.addWidget(self.label_scope)
        note = text('Uses Structure selection and state. Custom uses the native expression above; preview never executes it.')
        labels.addWidget(note)
        row = self._row(labels)
        self._button(row, 'Preview', 'preview_labels', self._preview_labels)
        self._button(row, 'Apply recipe', 'apply_label_recipe', self._apply_label_recipe)
        self._button(row, 'Clear', 'clear_labels', self._clear_labels)
        self.label_count = text('No label preview')
        self.label_count.setObjectName('label_count')
        labels.addWidget(self.label_count)
        self.label_preview = details(labels, 'Exact label targets', 'label_preview')
        saves = disclosure(layout, 'Save coordinates / PSE', 'persistence_section', True)
        fields = form(saves)
        self.coordinates_format = field(fields, 'Coordinate format', 'coordinates_format', combo(['cif', 'pdb']))
        self.coordinates_output = field(fields, 'New coordinate output directory', 'coordinates_output', QtWidgets.QLineEdit())
        path_browse(fields, self.coordinates_output, self, output=True, runner=self.run)
        self._button(saves, 'Save selected coordinates', 'save_coordinates', self._save_coordinates)
        self.session_output = field(fields, 'New session output directory', 'session_output', QtWidgets.QLineEdit())
        path_browse(fields, self.session_output, self, output=True, runner=self.run)
        self._button(saves, 'Save complete session', 'save_session', self._save_session)
        saves.addWidget(text('Coordinates: Structure selection and one state, one file per object. Session: complete captured PSE. Both save to a new directory without rendering.'))

    def _label_parameters(self):
        return dict(template=self.label_recipe.currentText(), state=self.state.value(), expression=self.label_text.text())

    def _label_result(self, result):
        from .ui_helpers import show_details
        show_details(self.label_preview, result)
        scope = ('; size shared by object/state: ' + ', '.join(result['size_objects'])) if result.get('size_objects') else ''
        self.label_count.setText(str(result['count']) + ' label target(s)' + scope)
        return result

    def _preview_labels(self):
        return self._label_result(self.controller.preview_labels(self.selection.text(), **self._label_parameters()))

    def _apply_label_recipe(self):
        return self._label_result(self.controller.apply_label_recipe(self.selection.text(), **self._label_parameters(),
            size=self.label_size.value(), color=self.label_color.text(), max_labels=500))

    def _clear_labels(self):
        return self._label_result(self.controller.clear_labels(self.selection.text(), state=self.state.value()))

    def _save_coordinates(self):
        if not self.coordinates_output.text().strip():
            raise ValueError('Choose a new coordinate output directory')
        self._launch_job('coordinates', {'request': dict(selection=self.selection.text(), state=self.state.value(),
            format=self.coordinates_format.currentText())}, self.coordinates_output.text(), state=self.state.value())

    def _save_session(self):
        if not self.session_output.text().strip():
            raise ValueError('Choose a new session output directory')
        self._launch_job('session_save', {'request': {}}, self.session_output.text(), state=self.state.value())

    def selected_objects(self):
        return [item.data(QtCore.Qt.UserRole) for item in self.object_list.selectedItems()]

    def _capture(self, selection, single=False):
        result = self.controller.capture_selection(selection, state=self.state.value(), single=single)
        self.selection.setText(result['selection'])
        self.navigation.summary(result)
        self.status.setText(f"Captured {result['atoms']} atom(s), state {result['state']}: {', '.join(result['objects'])}")
        return result

    def _representation(self):
        self.controller.apply_representation(self.selection.text(), self.representation.currentText(), state=self.state.value())

    def _hide_representation(self):
        self.controller.apply_representation(self.selection.text(), self.representation.currentText(), state=self.state.value(), visible=False)

    def _coloring(self):
        result = self.controller.apply_coloring(self.selection.text(), self.coloring.currentText(),
                                               color=self.color.text(), state=self.state.value())
        self.status.setText('Coloring applied: ' + str(result))

    def _load(self):
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(self, 'Load local coordinates', str(self.controller.workspace),
                                                        'Coordinates (*.pdb *.cif *.mmcif)')
        for path in paths:
            self.controller.load_coordinates(path)

    def _session(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Open PyMOL session', str(self.controller.workspace), 'PyMOL session (*.pse)')
        if path:
            self.controller.open_session(path)
            record = getattr(self, '_current_record', None)
            if record:
                self.controller.result_status(record)
                self._source_status(record)
            # A PSE may replace identities without changing object names/counts.
            self.navigation.update_inventory(self.controller.objects(), force=True)

    def _rename(self):
        names = self.selected_objects()
        if len(names) != 1:
            raise ValueError('Select exactly one object to rename')
        name, ok = QtWidgets.QInputDialog.getText(self, 'Rename object', 'New object name', text=names[0])
        if ok:
            self.controller.rename_object(names[0], name)

    def _scene(self):
        item = self.scene_list.currentItem()
        if item is None:
            raise ValueError('Select a scene bookmark')
        return item.text()

    def _rename_scene(self):
        old = self._scene()
        name, ok = QtWidgets.QInputDialog.getText(self, 'Rename bookmark', 'New bookmark name', text=old)
        if ok:
            self.controller.rename_scene(old, name)
            self.refresh()
            found = self.scene_list.findItems(name, QtCore.Qt.MatchExactly)
            if found:
                self.scene_list.setCurrentItem(found[0])

    def run(self, callback):
        if getattr(self, '_mutation_locked', False) and callback not in (self._cancel, self._csv, self._fasta):
            self.status.setText('Wait for the active worker or cancel it before changing the live scene')
            return None
        self.status.setText('Working…')
        try:
            result = callback()
            if self.status.text() == 'Working…':
                self.status.setText('Done')
            self.refresh()
            return result
        except Exception as exc:
            self.status.setText('Error: ' + str(exc))
            self.status.setToolTip(self.status.text())
            return None

    def refresh(self):
        try:
            if getattr(self, '_mutation_locked', False):
                return
            inventory = self.controller.objects()
            selected = set(self.selected_objects())
            names = [obj['name'] for obj in inventory]
            if [self.object_list.item(i).data(QtCore.Qt.UserRole) for i in range(self.object_list.count())] != names:
                self.object_list.clear()
                for obj in inventory:
                    item = QtWidgets.QListWidgetItem()
                    item.setData(QtCore.Qt.UserRole, obj['name'])
                    self.object_list.addItem(item)
                    item.setSelected(obj['name'] in selected)
            for index, obj in enumerate(inventory):
                item = self.object_list.item(index)
                item.setText(f"{'●' if obj['enabled'] else '○'} {obj['name']} · {obj['atoms']} atoms")
                item.setToolTip(f"{obj['name']} · {obj['states']} state(s) · chains {', '.join(obj['chains'])}")
            for combo in (self.align_mobile, self.align_target):
                self._refresh_combo(combo, names)
            self._refresh_combo(self.named_selections, self.controller.selections())
            scenes = self.controller.scenes()
            if [self.scene_list.item(i).text() for i in range(self.scene_list.count())] != scenes:
                scene = self.scene_list.currentItem().text() if self.scene_list.currentItem() else ''
                self.scene_list.clear()
                self.scene_list.addItems(scenes)
                found = self.scene_list.findItems(scene, QtCore.Qt.MatchExactly)
                if found:
                    self.scene_list.setCurrentItem(found[0])
                elif len(scenes) == 1:
                    self.scene_list.setCurrentRow(0)
            self.navigation.update_inventory(inventory)
            self.advanced.refresh()
            record = getattr(self, '_current_record', None)
            if record and self.tabs.currentIndex() == 3 and not self.jobs.active:
                previous = record.get('snapshot_status')
                current = self.controller.result_status(record)
                if previous != current:
                    self._source_status(record)
        except Exception as exc:
            self.status.setText('Error refreshing live scene: ' + str(exc))

    def _refresh_combo(self, combo, values):
        if [combo.itemText(i) for i in range(combo.count())] == values:
            return
        previous = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(values)
        if previous in values:
            combo.setCurrentText(previous)
        combo.blockSignals(False)
    def _edit(self, form, label, name, text=''):
        entry = QtWidgets.QLineEdit(text)
        entry.setObjectName(name)
        form.addRow(label, entry)
        return entry

    def _spin(self, form, label, name, minimum, maximum, value):
        entry = QtWidgets.QSpinBox()
        entry.setObjectName(name)
        entry.setRange(minimum, maximum)
        entry.setValue(value)
        form.addRow(label, entry)
        return entry

    def _analysis_tab(self):
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        form = QtWidgets.QFormLayout()
        form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        layout.addLayout(form)
        self.analysis_state = self._spin(form, 'Analysis state', 'analysis_state', 1, 1000000, self.state.value())
        self.analysis_state.valueChanged.connect(self.state.setValue)
        self.state.valueChanged.connect(self.analysis_state.setValue)
        self.analysis_selection = self._edit(form, 'Selection / target', 'analysis_selection', 'all')
        self.contacts_b = self._edit(form, 'Partner B', 'contacts_b', 'organic')
        self._button(layout, 'Inspect structure', 'run_inspect', self._inspect).setToolTip('Inspect structure and observed coordinate sequence')
        geometry = disclosure(layout, 'Geometry', 'geometry_section')
        form = QtWidgets.QFormLayout()
        form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        geometry.addLayout(form)
        self.geometry_kind = QtWidgets.QComboBox()
        self.geometry_kind.setObjectName('geometry_kind')
        self.geometry_kind.addItems(['distance', 'angle', 'dihedral'])
        form.addRow('Geometry', self.geometry_kind)
        self.geometry_atoms = []
        for index in range(4):
            row = QtWidgets.QHBoxLayout()
            entry = QtWidgets.QLineEdit('pk' + str(index + 1))
            entry.setObjectName('geometry_atom' + str(index + 1))
            row.addWidget(entry)
            self._button(row, 'Pick ' + str(index + 1), 'use_pick' + str(index + 1),
                         lambda n=index: self._geometry_pick(n))
            form.addRow(chr(65 + index) + ' (one atom)', row)
            self.geometry_atoms.append(entry)
        self.geometry_annotation = QtWidgets.QCheckBox('Add live annotation')
        self.geometry_annotation.setChecked(True)
        geometry.addWidget(self.geometry_annotation)
        self._button(geometry, 'Measure geometry', 'run_geometry', self._geometry)
        contacts = disclosure(layout, 'Contacts / interface', 'contacts_section', True)
        form = QtWidgets.QFormLayout()
        form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        contacts.addLayout(form)
        self.contact_cutoff = QtWidgets.QDoubleSpinBox()
        self.contact_cutoff.setObjectName('contact_cutoff')
        self.contact_cutoff.setRange(0.01, 10000)
        self.contact_cutoff.setValue(4)
        form.addRow('Contact cutoff (Å)', self.contact_cutoff)
        self.contact_heavy = QtWidgets.QCheckBox('Heavy atoms only')
        self.contact_heavy.setChecked(True)
        contacts.addWidget(self.contact_heavy)
        self._button(contacts, 'Calculate contacts', 'run_contacts', self._contacts)
        self.interface_altloc = QtWidgets.QComboBox()
        self.interface_altloc.setObjectName('interface_altloc')
        self.interface_altloc.addItems(['primary', 'all'])
        form.addRow('Interface altloc', self.interface_altloc)
        self._button(contacts, 'Calculate interface area', 'run_interface', self._interface)
        note = QtWidgets.QLabel('Choose non-overlapping A and B. Interface area = (buried A + buried B) / 2; total buried area = sum (Å²).')
        note.setToolTip('Non-solvent heavy atoms; primary altloc = blank + A; probe 1.4 Å, dot density 3. Surface loss is not affinity or binding energy. Complete methods are retained in result details and exports.')
        note.setWordWrap(True)
        contacts.addWidget(note)
        polar = disclosure(layout, 'Polar candidates', 'polar_section', True)
        form = QtWidgets.QFormLayout()
        form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapAllRows)
        polar.addLayout(form)
        self.polar_cutoff = QtWidgets.QDoubleSpinBox()
        self.polar_cutoff.setObjectName('polar_cutoff')
        self.polar_cutoff.setRange(0.1, 10)
        self.polar_cutoff.setValue(3.5)
        form.addRow('Strict distance cutoff (Å)', self.polar_cutoff)
        self.polar_angle = QtWidgets.QDoubleSpinBox()
        self.polar_angle.setObjectName('polar_angle')
        self.polar_angle.setRange(0, 90)
        self.polar_angle.setValue(45)
        form.addRow('Native orientation tolerance (degrees)', self.polar_angle)
        self.polar_altloc = QtWidgets.QComboBox()
        self.polar_altloc.setObjectName('polar_altloc')
        self.polar_altloc.addItems(['primary', 'all'])
        form.addRow('Polar altloc', self.polar_altloc)
        self._button(polar, 'Calculate polar candidates', 'run_polar', self._polar)
        note = QtWidgets.QLabel('Uses Selection A / Partner B above. Typed donor→acceptor candidates; distance < cutoff. Native orientation is coarse, not a measured D–H–A angle. Maximum 100000 pairs; annotate at most 200. Not confirmed hydrogen bonds or affinity.')
        note.setWordWrap(True)
        polar.addWidget(note)
        sasa = disclosure(layout, 'SASA', 'sasa_section')
        form = QtWidgets.QFormLayout()
        form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        sasa.addLayout(form)
        self.sasa_context = self._edit(form, 'SASA context', 'sasa_context')
        self.sasa_context.setPlaceholderText('Blank: byobject target')
        self.sasa_altloc = QtWidgets.QComboBox()
        self.sasa_altloc.addItems(['primary', 'all'])
        self.sasa_altloc.setObjectName('sasa_altloc')
        form.addRow('SASA altloc', self.sasa_altloc)
        self._button(sasa, 'Calculate SASA (Å²)', 'run_sasa', self._sasa)
        note = QtWidgets.QLabel('SASA: non-solvent heavy atoms, primary = blank+A; probe 1.4 Å, dot density 3. Target must be a subset of context. Contacts: unique undirected pairs, maximum 100000; geometric proximity.')
        note.setWordWrap(True)
        sasa.addWidget(note)
        alignment = disclosure(layout, 'Alignment', 'alignment_section')
        form = QtWidgets.QFormLayout()
        form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        alignment.addLayout(form)
        self.align_mobile = QtWidgets.QComboBox()
        self.align_mobile.setObjectName('align_mobile')
        self.align_target = QtWidgets.QComboBox()
        self.align_target.setObjectName('align_target')
        form.addRow('Mobile object', self.align_mobile)
        form.addRow('Target object', self.align_target)
        self.target_state = self._spin(form, 'Target state', 'target_state', 1, 1000000, 1)
        self.align_selection = self._edit(form, 'Alignment selection', 'align_selection', 'polymer.protein and name CA')
        self.align_algorithm = QtWidgets.QComboBox()
        self.align_algorithm.addItems(['align', 'super'])
        self.align_algorithm.setObjectName('align_algorithm')
        form.addRow('Algorithm', self.align_algorithm)
        self.align_cutoff = QtWidgets.QDoubleSpinBox()
        self.align_cutoff.setRange(0.01, 10000)
        self.align_cutoff.setValue(2)
        self.align_cutoff.setObjectName('align_cutoff')
        form.addRow('Alignment cutoff (Å)', self.align_cutoff)
        self.align_cycles = self._spin(form, 'Refinement cycles', 'align_cycles', 0, 100, 5)
        self._button(alignment, 'Align live mobile object', 'run_align', self._align)
        layout.addStretch()
        self.tabs.addTab(self._scroll(tab), 'Analysis')

    def _results_tab(self):
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        self.result_list = QtWidgets.QComboBox()
        self.result_list.setObjectName('result_list')
        self.result_list.currentIndexChanged.connect(self._result_selected)
        layout.addWidget(self.result_list)
        self.result_source = QtWidgets.QLabel('No analysis selected')
        self.result_source.setObjectName('result_source')
        self.result_source.setWordWrap(True)
        layout.addWidget(self.result_source)
        self.result_details = QtWidgets.QPlainTextEdit()
        self.result_details.setReadOnly(True)
        self.result_details.setObjectName('result_details')
        self.result_details.setMaximumHeight(180)
        self.result_summary = QtWidgets.QLabel()
        self.result_summary.setObjectName('result_summary')
        self.result_summary.setWordWrap(True)
        layout.addWidget(self.result_summary)
        details = disclosure(layout, 'Details', 'result_details_section')
        details.addWidget(self.result_details)
        self.result_tables = QtWidgets.QComboBox()
        self.result_tables.setObjectName('result_tables')
        self.result_tables.currentTextChanged.connect(self._table_selected)
        layout.addWidget(self.result_tables)
        self.result_filter = QtWidgets.QLineEdit()
        self.result_filter.setObjectName('result_filter')
        self.result_filter.setPlaceholderText('Search complete result table…')
        self.result_filter.setClearButtonEnabled(True)
        layout.addWidget(self.result_filter)
        self.row_total = QtWidgets.QLabel('0 rows')
        self.row_total.setObjectName('result_row_total')
        self.row_total.setWordWrap(True)
        layout.addWidget(self.row_total)
        self.result_table = QtWidgets.QTableView()
        self.result_table.setObjectName('result_table')
        self._table_model = ResultTableModel(parent=self.result_table)
        self._result_proxy = ResultFilter(self.result_table)
        self._result_proxy.setSourceModel(self._table_model)
        self._result_proxy.setFilterKeyColumn(-1)
        self._result_proxy.setFilterCaseSensitivity(QtCore.Qt.CaseInsensitive)
        self.result_table.setModel(self._result_proxy)
        self.result_table.setSortingEnabled(True)
        self.result_table.sortByColumn(-1, QtCore.Qt.AscendingOrder)
        self.result_filter.textChanged.connect(self._filter_results)
        self.result_table.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.result_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        layout.addWidget(self.result_table)
        self._button(layout, 'Export complete table CSV…', 'export_csv', self._csv)
        self._button(layout, 'Export coordinate FASTA…', 'export_fasta', self._fasta)
        self.annotate_button = self._button(layout, 'Annotate selected result', 'annotate_result', self._annotate)
        self.result_overwrite = QtWidgets.QCheckBox('Overwrite CSV / FASTA')
        self.result_overwrite.setObjectName('result_overwrite')
        layout.addWidget(self.result_overwrite)
        self.tabs.addTab(tab, 'Results')

    def _export_controls(self, layout):
        image_settings = disclosure(layout, 'Image settings', 'image_section')
        form = QtWidgets.QFormLayout()
        form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        image_settings.addLayout(form)
        self.image_width = self._spin(form, 'PNG width (px)', 'image_width', 1, 10000, 1600)
        self.image_height = self._spin(form, 'PNG height (px)', 'image_height', 1, 10000, 1200)
        self.image_dpi = self._spin(form, 'PNG DPI', 'image_dpi', 1, 2400, 300)
        form = QtWidgets.QFormLayout()
        form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        layout.addLayout(form)
        self.image_ray = QtWidgets.QCheckBox('Ray tracing')
        self.image_ray.setToolTip('Render in an independent worker')
        self.image_ray.setObjectName('image_ray')
        self.image_ray.setChecked(True)
        layout.addWidget(self.image_ray)
        self.export_directory = self._edit(form, 'Export directory', 'export_directory')
        self._button(layout, 'Browse export directory…', 'choose_export_directory', lambda: self._directory(self.export_directory))
        self.export_overwrite = QtWidgets.QCheckBox('Overwrite owned outputs')
        self.export_overwrite.setObjectName('export_overwrite')
        layout.addWidget(self.export_overwrite)
        self._button(layout, 'Export view bundle', 'run_export', self._export_scene)
        note = QtWidgets.QLabel('PML replays the accompanying PSE. Camera and native session are preserved. Result records retain stale/current status.')
        note.setWordWrap(True)
        layout.addWidget(note)
        layout = disclosure(layout, 'Render directory', 'batch_section')
        layout.addWidget(QtWidgets.QLabel('Uses the image settings above'))
        form = QtWidgets.QFormLayout()
        form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        layout.addLayout(form)
        self.batch_input = self._edit(form, 'Batch input directory', 'batch_input')
        self._button(layout, 'Browse input…', 'choose_batch_input', lambda: self._directory(self.batch_input))
        self.batch_output = self._edit(form, 'Batch output directory', 'batch_output')
        self._button(layout, 'Browse output…', 'choose_batch_output', lambda: self._directory(self.batch_output))
        self.batch_preset = QtWidgets.QComboBox()
        self.batch_preset.addItems(['cartoon', 'surface', 'pocket'])
        self.batch_preset.setObjectName('batch_preset')
        form.addRow('Batch preset', self.batch_preset)
        self.batch_coloring = QtWidgets.QComboBox()
        self.batch_coloring.addItems(['chain', 'element', 'single', 'rainbow', 'bfactor'])
        self.batch_coloring.setObjectName('batch_coloring')
        form.addRow('Batch coloring', self.batch_coloring)
        self.batch_ligand = self._edit(form, 'Pocket ligand selection', 'batch_ligand', 'organic')
        self.batch_radius = QtWidgets.QDoubleSpinBox()
        self.batch_radius.setRange(0.01, 1000)
        self.batch_radius.setValue(5)
        form.addRow('Pocket radius (Å)', self.batch_radius)
        self._button(layout, 'Render directory', 'run_batch', self._batch)

    def _directory(self, entry):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, 'Choose directory', str(self.controller.workspace))
        if path:
            entry.setText(path)

    def _geometry_pick(self, index):
        record = self.controller.capture_pick(index + 1, state=self.analysis_state.value())
        self.geometry_atoms[index].setText(record['selection'])
        self.status.setText('Captured ' + str(record['atoms']) + ' atom for ' + chr(65 + index))

    def _geometry(self):
        count = {'distance': 2, 'angle': 3, 'dihedral': 4}[self.geometry_kind.currentText()]
        result = self.controller.geometry(self.geometry_kind.currentText(), [entry.text() for entry in self.geometry_atoms[:count]],
                                          state=self.analysis_state.value(), annotate=self.geometry_annotation.isChecked())
        self._show_record(result)

    def _inspect(self):
        self._show_record(self.controller.inspect(self.analysis_selection.text(), state=self.analysis_state.value()))

    def _align(self):
        self._show_record(self.controller.align(self.align_mobile.currentText(), self.align_target.currentText(),
            algorithm=self.align_algorithm.currentText(), selection=self.align_selection.text(),
            mobile_state=self.analysis_state.value(), target_state=self.target_state.value(),
            cutoff=self.align_cutoff.value(), cycles=self.align_cycles.value()))

    def _contacts(self):
        self._launch_job('contacts', dict(selection_a=self.analysis_selection.text(), selection_b=self.contacts_b.text(),
            state=self.analysis_state.value(), cutoff=self.contact_cutoff.value(), heavy_only=self.contact_heavy.isChecked(), max_pairs=100000))

    def _polar(self):
        self._launch_job('polar', dict(selection_a=self.analysis_selection.text(), selection_b=self.contacts_b.text(),
            state=self.analysis_state.value(), cutoff=self.polar_cutoff.value(), angle=self.polar_angle.value(),
            altloc=self.polar_altloc.currentText(), max_pairs=100000))

    def _sasa(self):
        self._launch_job('sasa', dict(selection=self.analysis_selection.text(), context=self.sasa_context.text() or None,
            state=self.analysis_state.value(), altloc=self.sasa_altloc.currentText()))

    def _interface(self):
        self._launch_job('interface', dict(selection_a=self.analysis_selection.text(), selection_b=self.contacts_b.text(),
            state=self.analysis_state.value(), altloc=self.interface_altloc.currentText()))

    def _image_parameters(self):
        return dict(width=self.image_width.value(), height=self.image_height.value(), dpi=self.image_dpi.value())

    def _export_scene(self):
        if not self.export_directory.text().strip():
            raise ValueError('Choose a figure/session output directory')
        parameters = self._image_parameters()
        parameters.update(background=self.background.currentText(), ray=self.image_ray.isChecked(),
                          overwrite=self.export_overwrite.isChecked(), analysis_records=[
                              record for record in self.controller.results
                              if record.get('operation') in ('geometry', 'contacts', 'polar', 'sasa', 'interface', 'inspect', 'align')
                              and not record.get('cancelled')])
        self._launch_job('export', parameters, output=self.export_directory.text())

    def _batch(self):
        if not self.batch_input.text().strip() or not self.batch_output.text().strip():
            raise ValueError('Choose batch input and output directories')
        options = self._image_parameters()
        options.update(style=self.batch_preset.currentText(), coloring=self.batch_coloring.currentText(),
                       state=self.analysis_state.value(), color=self.color.text())
        if self.batch_preset.currentText() == 'pocket':
            options.update(ligand=self.batch_ligand.text(), radius=self.batch_radius.value())
        self._launch_job('batch', dict(input_dir=self.batch_input.text(), render_options=options,
                                      overwrite=self.export_overwrite.isChecked()), output=self.batch_output.text())

    def _launch_job(self, operation, parameters, output=None, *, state=None):
        if self.jobs.active:
            raise ValueError('Wait for the active job or cancel it first')
        self._active_job_path = self.controller.prepare_job(operation, parameters, output=output,
                                                          state=self.analysis_state.value() if state is None else state)
        self.jobs.start(self._active_job_path)

    def _job_started(self, job):
        self._mutation_locked = True
        self.advanced.set_job_active(True)
        self.annotate_button.setEnabled(False)
        self._job_operation = job['operation']
        self._job_output = job['output']
        self._job_state = job.get('parameters', {}).get('state', self.analysis_state.value())
        if job['operation'] == 'potential':
            self._job_state = job['parameters']['request']['state']
        elif job['operation'] == 'density':
            self._job_state = job['parameters']['request']['source_state']
        elif job['operation'] in ('timeline_project', 'animation'):
            self._job_state = job['parameters']['timeline']['keyframes'][0]['state']
        elif job['operation'] == 'tools':
            self._job_state = 1
        if job['operation'] == 'export':
            self._job_state = int(self.controller.cmd.get_state())
        for index in (0, 1, 2, 4):
            self.tabs.setTabEnabled(index, False)
            self.page_selector.model().item(index).setEnabled(False)
        self._select_page(3)
        self.progress_bar.setRange(0, 0)
        self.cancel_job.setEnabled(True)
        self.status.setText('Running ' + job['operation'] + ' in an independent worker…')

    def _job_progress(self, report):
        if isinstance(report, dict) and 'items' in report:
            total = len(report['items'])
            self.progress_bar.setRange(0, max(1, total))
            self.progress_bar.setValue(report.get('completed', 0) + report.get('failed', 0))
            self.status.setText(f"Batch: {report.get('completed', 0)}/{total} completed; {report.get('failed', 0)} failed")
            self._show_transient(report)
        elif isinstance(report, dict):
            self.status.setText(str(report.get('message', report)))

    def _job_idle(self):
        self._mutation_locked = False
        self.advanced.set_job_active(False)
        self.annotate_button.setEnabled(True)
        for index in (0, 1, 2, 4):
            self.tabs.setTabEnabled(index, True)
            self.page_selector.model().item(index).setEnabled(True)
        if getattr(self, '_current_record', None):
            self._annotation_available(self._current_record)
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)
        self.cancel_job.setEnabled(False)

    def _job_finished(self, result):
        self._job_idle()
        if not isinstance(result, dict):
            self.status.setText('Error: worker result is not an object')
            return
        if self._job_operation == 'tools':
            self.advanced.potential.show_tools_result(result)
        result.setdefault('output_path', self._job_output)
        record = self.controller.record_result(self._job_operation, result, self.jobs.job.get('parameters', {}), state=self._job_state)
        self._show_record(record)
        self.status.setText('Completed ' + self._job_operation + '; complete results and output paths are in Results')

    def _job_failed(self, message):
        self._job_idle()
        if self._job_operation in ('batch', 'tools') and isinstance(self.jobs.last_result, dict):
            if self._job_operation == 'tools':
                self.advanced.potential.show_tools_result(self.jobs.last_result)
            record = self.controller.record_result(self._job_operation, self.jobs.last_result, self.jobs.job.get('parameters', {}), state=self._job_state)
            self._show_record(record)
        self.status.setText('Error: ' + message)
        self.status.setToolTip(self.status.text())

    def _job_cancelled(self, result):
        self._job_idle()
        if self._job_operation == 'tools':
            self.advanced.potential.show_tools_result(result)
        result.setdefault('output_path', self._job_output)
        record = self.controller.record_result(self._job_operation, result, self.jobs.job.get('parameters', {}), state=self._job_state)
        self._show_record(record)
        self.status.setText('Cancelled; completed outputs retained, unfinished items remain pending')

    def _cancel(self):
        self.jobs.cancel()
        self.cancel_job.setEnabled(False)
        self.status.setText('Cancelling… batch cancellation waits for the current item to finish')

    def _show_transient(self, result):
        self._current_record = result
        self._populate_result(result)

    def _show_record(self, result):
        self.result_list.blockSignals(True)
        self.result_list.clear()
        for index, record in enumerate(self.controller.results):
            label = f"{index + 1}. {record.get('kind', record.get('operation', 'result'))} · state {record.get('state', '?')}"
            self.result_list.addItem(label)
        self.result_list.setCurrentIndex(len(self.controller.results) - 1)
        self.result_list.blockSignals(False)
        self._current_record = result
        self._populate_result(result)
        self.status.setText('Result ready: ' + result.get('kind', result.get('operation', 'analysis')))

    def _record(self):
        if not getattr(self, '_current_record', None):
            raise ValueError('Run an analysis or select a result first')
        return self._current_record

    def _result_selected(self, index):
        if 0 <= index < len(self.controller.results):
            self._current_record = self.controller.results[index]
            self.controller.result_status(self._current_record)
            self._populate_result(self._current_record)

    def _source_status(self, result):
        status = 'cancelled' if result.get('cancelled') else result.get('snapshot_status', 'unverified')
        self.result_source.setText(f"State {result.get('state', '?')} · {status} · {result.get('calculated_at', '')}")
        self.result_source.setToolTip('Source fingerprint: ' + str(result.get('source_fingerprint', 'unverified')))

    def _annotation_available(self, result):
        supported = result.get('operation') in ('geometry', 'contacts', 'polar')
        self.annotate_button.setEnabled(supported and not getattr(self, '_mutation_locked', False) and not result.get('cancelled'))
        self.annotate_button.setToolTip('Saved snapshots cannot annotate the live scene' if result.get('operation') in ('coordinates', 'session_save') else 'Annotate current supported analysis')

    def _populate_result(self, result):
        import json
        self._source_status(result)
        self._annotation_available(result)
        if result.get('operation') in ('coordinates', 'session_save') and result.get('cancelled'):
            self.result_summary.setText('Cancelled save · no successful snapshot published')
        elif result.get('operation') in ('coordinates', 'session_save'):
            self.result_summary.setText('Saved snapshot · ' + ('coordinates' if result['operation'] == 'coordinates' else 'complete session') + '\nComplete files and output directory are in Details and tables.')
        elif result.get('operation') == 'interface':
            def area(value):
                return format(value, '.6g') if isinstance(value, (int, float)) else '?'
            self.result_summary.setText(f"Interface area: {area(result.get('interface_area_angstrom2'))} Å²\n"
                f"(buried A + buried B) / 2\nTotal buried area: {area(result.get('total_buried_area_angstrom2'))} Å²\n"
                f"Buried A: {area(result.get('partner_a', {}).get('buried_area_angstrom2'))} · "
                f"Buried B: {area(result.get('partner_b', {}).get('buried_area_angstrom2'))} Å²")
        else:
            self.result_summary.setText(str(result.get('kind', result.get('operation', 'Analysis'))))
        summary = {key: (f'{len(value)} rows' if isinstance(value, list) else value)
                   for key, value in result.items()}
        self.result_details.setPlainText(json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False))
        self.result_tables.blockSignals(True)
        previous = self.result_tables.currentText()
        self.result_tables.clear()
        self.result_tables.addItems([key for key, value in result.items() if isinstance(value, list) and (not value or isinstance(value[0], dict))])
        if self.result_tables.findText(previous) >= 0:
            self.result_tables.setCurrentText(previous)
        self.result_tables.blockSignals(False)
        self._table_selected(self.result_tables.currentText())

    def _table_selected(self, key):
        result = getattr(self, '_current_record', {})
        rows = result.get(key, [])
        if not isinstance(rows, list):
            rows = []
        old_model = self._table_model
        self._table_model = ResultTableModel(rows, self.result_table)
        self._result_proxy.setSourceModel(self._table_model)
        old_model.deleteLater()
        self._filter_results(self.result_filter.text())

    def _filter_results(self, text):
        self._result_proxy.setFilterFixedString(text)
        self.row_total.setText(f'{self._result_proxy.rowCount()} / {self._table_model.rowCount()} rows shown · CSV exports all rows')

    def _csv(self):
        record = self._record()
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, 'Export complete table CSV', str(self.controller.workspace / (self.result_tables.currentText() + '.csv')), 'CSV (*.csv)')
        if path:
            output = self.controller.export_table(record, self.result_tables.currentText(), path, overwrite=self.result_overwrite.isChecked())
            self.status.setText('Saved ' + output + '; snapshot ' + record['snapshot_status'])

    def _fasta(self):
        record = self._record()
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, 'Export coordinate sequence FASTA', str(self.controller.workspace / 'sequence.fasta'), 'FASTA (*.fasta)')
        if path:
            output = self.controller.export_fasta(record, path, overwrite=self.result_overwrite.isChecked())
            self.status.setText('Saved ' + output + '; snapshot ' + record['snapshot_status'])

    def _annotate(self):
        if getattr(self, '_mutation_locked', False):
            raise ValueError('Wait for the active worker before annotating the live scene')
        record = self.controller.annotate_result(self._record())
        self._populate_result(record)
        self.status.setText(f"Annotated {record['displayed_count']} item(s); total contacts {record.get('pair_count', 'n/a')}")


class ResultFilter(QtCore.QSortFilterProxyModel):
    def lessThan(self, left, right):
        model = self.sourceModel()
        a = model.rows[left.row()].get(model.columns[left.column()])
        b = model.rows[right.row()].get(model.columns[right.column()])
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return a < b
        return str(left.data()).casefold() < str(right.data()).casefold()


class ResultTableModel(QtCore.QAbstractTableModel):
    """Read-only lazy cells; complete contacts remain available without widgets."""
    def __init__(self, rows=None, parent=None):
        super().__init__(parent)
        self.rows = rows or []
        self.columns = list(dict.fromkeys(key for row in self.rows for key in row))

    def rowCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if not index.isValid() or role not in (QtCore.Qt.DisplayRole, QtCore.Qt.ToolTipRole):
            return None
        import json
        value = self.rows[index.row()].get(self.columns[index.column()], '')
        if role == QtCore.Qt.DisplayRole and isinstance(value, dict) and 'object' in value:
            site = '/'.join(str(value.get(key, '')) for key in ('segi', 'chain', 'resi', 'resn'))
            identity = value['object'] + '/' + site
            if 'index' in value:
                identity += f"/{value.get('name', '')} #{value['index']} {value.get('element', '')} alt={value.get('alt') or '-'} state={value.get('state', '?')}"
            return identity
        if role == QtCore.Qt.DisplayRole and isinstance(value, float):
            return format(value, '.8g')
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
        return str(value)

    def headerData(self, section, orientation, role=QtCore.Qt.DisplayRole):
        if role == QtCore.Qt.DisplayRole:
            labels = {'distance_angstrom': 'Distance (Å)', 'min_distance_angstrom': 'Min distance (Å)',
                      'area_angstrom2': 'Area (Å²)', 'atom_a': 'Atom A', 'atom_b': 'Atom B',
                      'isolated_area_angstrom2': 'Isolated SASA (Å²)', 'complex_area_angstrom2': 'Complex SASA (Å²)',
                      'buried_area_angstrom2': 'Buried area (Å²)'}
            return labels.get(self.columns[section], self.columns[section]) if orientation == QtCore.Qt.Horizontal else str(section + 1)
        return None


class NativeCloseGuard(QtCore.QObject):
    """Quiesce workers and release workbench Qt objects before native C exit."""
    def __init__(self, panel, dock, window):
        super().__init__(window)
        self.panel = panel
        self.dock = dock
        self.window = window
        self.closing = False
        window.installEventFilter(self)
        for signal in (panel.jobs.finished, panel.jobs.failed, panel.jobs.cancelled):
            signal.connect(self._worker_stopped)

    def eventFilter(self, watched, event):
        if watched is not self.window or event.type() != QtCore.QEvent.Close:
            return False
        self.panel.advanced.stop_preview()
        if self.panel.jobs.active:
            self.closing = True
            event.ignore()
            self.panel._cancel()
            self.panel.status.setText('Closing after the active worker stops; completed batch outputs are retained')
            return True
        self.panel.refresh_timer.stop()
        self.dock.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(self.dock, QtCore.QEvent.DeferredDelete)
        # The original native main-window closeEvent still executes normally.
        return False

    def _worker_stopped(self, result=None):
        if self.closing:
            QtCore.QTimer.singleShot(0, self.window.close)
