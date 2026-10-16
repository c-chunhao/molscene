"""Timeline controls; the timeline cursor is distinct from native movie state."""
from copy import deepcopy
import time
from pymol.Qt import QtCore, QtWidgets
from .advanced_panel import buttons, check, combo, field, form, integer, text


class TimelinePanel(QtWidgets.QWidget):
    def __init__(self, advanced):
        super().__init__(advanced)
        self.advanced = advanced
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.actions = buttons(layout, [
            ('timeline_new', 'New timeline'), ('timeline_open_project', 'Open project…'),
            ('timeline_save_project', 'Save project'), ('timeline_load_json', 'Load JSON…')])
        layout.addWidget(text('Source snapshot — project Open replaces the full session after validation.'))
        self.table = QtWidgets.QTableWidget(0, 5)
        self.table.setObjectName('timeline_table')
        self.table.setAccessibleName('Timeline keyframes')
        self.table.setHorizontalHeaderLabels(['Frame', 'State', 'Scene', 'Label', 'Interpolation'])
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setMinimumWidth(0)
        self.table.setMinimumHeight(100)
        self.table.setMaximumHeight(180)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        layout.addWidget(self.table)
        details = form(layout)
        self.frame = field(details, 'Keyframe position (frame)', 'timeline_frame', integer(1, 1, 10000))
        self.state = field(details, 'Molecular state', 'timeline_state', integer(1))
        self.scene = field(details, 'Display scene', 'timeline_scene', combo([]))
        self.scene.addItem('Keep previous display', None)
        self.label = field(details, 'Keyframe label', 'timeline_label', QtWidgets.QLineEdit())
        self.label.setMaxLength(256)
        self.interpolation = field(details, 'Outgoing camera interval', 'timeline_interpolation', combo(['smooth', 'hold']))
        self.actions.update(buttons(layout, [
            ('timeline_capture', 'Capture keyframe'), ('timeline_update', 'Update selected'),
            ('timeline_remove', 'Remove selected'), ('timeline_move_up', 'Move earlier'),
            ('timeline_move_down', 'Move later')]))
        playback = form(layout)
        self.fps = field(playback, 'Frame rate (frames/s)', 'timeline_fps', integer(24, 1, 120))
        self.seek = field(playback, 'Preview position (frame)', 'timeline_seek', QtWidgets.QSlider(QtCore.Qt.Horizontal))
        self.seek.setRange(1, 1)
        self.position = field(playback, 'Presented frame / time', 'timeline_position', text('No timeline'))
        self.actions.update(buttons(layout, [
            ('timeline_previous', 'Previous frame'), ('timeline_next', 'Next frame'),
            ('timeline_play', 'Play'), ('timeline_pause', 'Pause')]))
        output = form(layout)
        self.output = field(output, 'New output directory', 'timeline_output', QtWidgets.QLineEdit())
        self.width = field(output, 'Width (px)', 'timeline_width', integer(800, 16, 4096))
        self.height = field(output, 'Height (px)', 'timeline_height', integer(600, 16, 4096))
        self.ray = field(output, 'Ray trace PNG frames', 'timeline_ray', check(True))
        self.mp4 = field(output, 'Also encode MP4 (FFmpeg)', 'timeline_mp4', check())
        self.actions.update(buttons(layout, [('timeline_export', 'Export animation')]))
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(15)
        advanced.controller._timeline_timers.append(self.timer)
        timers, timer = advanced.controller._timeline_timers, self.timer
        self.timer.destroyed.connect(lambda: timers.remove(timer) if timer in timers else None)
        self.document = None
        self._selected_id = None
        self._start_time = None
        self._start_frame = 1
        self.actions['timeline_update'].setToolTip('Update frame, state, scene, label and interpolation; preserve the captured camera. Capture adds a new camera keyframe.')
        for name, callback in (
            ('timeline_new', self.new), ('timeline_capture', self.capture),
            ('timeline_update', lambda: self.edit('update')),
            ('timeline_remove', lambda: self.edit('remove')),
            ('timeline_move_up', lambda: self.edit('move_up')),
            ('timeline_move_down', lambda: self.edit('move_down')),
            ('timeline_previous', lambda: self.seek_to(self.seek.value() - 1)),
            ('timeline_next', lambda: self.seek_to(self.seek.value() + 1)),
            ('timeline_play', self.play),
            ('timeline_load_json', self.load_json),
            ('timeline_open_project', self.open_project),
            ('timeline_save_project', self.save_project),
            ('timeline_export', self.export_animation),
        ):
            self.actions[name].clicked.connect(lambda checked=False, fn=callback: self.advanced.run(fn))
        self.actions['timeline_pause'].clicked.connect(self.stop_preview)
        self.table.itemSelectionChanged.connect(self._select_row)
        self.seek.valueChanged.connect(lambda value: self.advanced.run(lambda: self.seek_to(value)))
        self.fps.valueChanged.connect(lambda value: self.advanced.run(lambda: self.set_fps(value)))
        self.timer.timeout.connect(self._preview_tick)

    def stop_preview(self):
        self.timer.stop()
        self._start_time = None

    def render_parameters(self):
        return dict(width=self.width.value(), height=self.height.value(), dpi=150, ray=self.ray.isChecked())

    def refresh(self, document, scenes):
        previous_scene = self.scene.currentData()
        self.scene.blockSignals(True)
        self.scene.clear()
        self.scene.addItem('Keep previous display', None)
        for scene in scenes:
            self.scene.addItem(scene, scene)
        index = self.scene.findData(previous_scene)
        self.scene.setCurrentIndex(max(0, index))
        self.scene.blockSignals(False)
        if document == self.document:
            return
        self.stop_preview()
        self.document = deepcopy(document)
        self.table.blockSignals(True)
        rows = document['keyframes'] if document else []
        self.table.setRowCount(len(rows))
        selected = -1
        for row_index, row in enumerate(rows):
            values = (row['frame'], row['state'], row['scene'] or 'Keep previous display', row['label'], row['interpolation'])
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(str(value))
                item.setData(QtCore.Qt.UserRole, row['id'])
                self.table.setItem(row_index, column, item)
            if row['id'] == self._selected_id:
                selected = row_index
        self.table.clearSelection()
        if selected >= 0:
            self.table.selectRow(selected)
        self.table.blockSignals(False)
        if selected < 0:
            self._selected_id = None
        else:
            self._select_row()
        self.fps.blockSignals(True)
        if document:
            self.fps.setValue(document['fps'])
        self.fps.blockSignals(False)
        self.seek.blockSignals(True)
        self.seek.setRange(1, document['frame_count'] if document else 1)
        self.seek.blockSignals(False)
        self._position_text()

    def _select_row(self):
        row = self.table.currentRow()
        if not self.document or row < 0 or row >= len(self.document['keyframes']):
            return
        keyframe = self.document['keyframes'][row]
        self._selected_id = keyframe['id']
        self.frame.setValue(keyframe['frame'])
        self.state.setValue(keyframe['state'])
        self.scene.setCurrentIndex(max(0, self.scene.findData(keyframe['scene'])))
        self.label.setText(keyframe['label'])
        self.interpolation.setCurrentText(keyframe['interpolation'])

    def _editor_values(self):
        return dict(frame=self.frame.value(), state=self.state.value(), scene=self.scene.currentData(),
                    label=self.label.text(), interpolation=self.interpolation.currentText())

    def _require_document(self):
        if not self.document:
            raise ValueError('Create or open a timeline first')
        return self.document

    def new(self):
        self.stop_preview()
        self._selected_id = None
        self.advanced.controller.timeline_new(fps=self.fps.value())

    def capture(self):
        self._require_document()
        self.stop_preview()
        self.advanced.controller.timeline_edit('add', values=self._editor_values())

    def edit(self, action):
        self._require_document()
        if self._selected_id is None:
            raise ValueError('Select a keyframe first')
        self.stop_preview()
        self.advanced.controller.timeline_edit(action, keyframe_id=self._selected_id,
            values=self._editor_values() if action == 'update' else None)

    def set_fps(self, value):
        self.stop_preview()
        if self.document:
            self.advanced.controller.timeline_edit('set_fps', values={'fps': value})

    def _position_text(self):
        if self.document:
            cursor = self.seek.value()
            self.position.setText(f"Frame {cursor} / {self.document['frame_count']} · {(cursor - 1) / self.document['fps']:.3f} s")
        else:
            self.position.setText('No timeline')

    def _present(self, frame):
        self.advanced.controller.timeline_seek(frame)
        self.seek.blockSignals(True)
        self.seek.setValue(frame)
        self.seek.blockSignals(False)
        self._position_text()

    def seek_to(self, frame):
        document = self._require_document()
        self.stop_preview()
        self._present(max(1, min(frame, document['frame_count'])))

    def play(self):
        document = self._require_document()
        self.stop_preview()
        self._start_frame = self.seek.value()
        self._present(self._start_frame)
        if self._start_frame >= document['frame_count']:
            return
        self._start_time = time.monotonic()
        self.timer.start()

    def tick(self):
        document = self._require_document()
        if self._start_time is None:
            return
        frame = min(document['frame_count'], self._start_frame + int(
            max(0, time.monotonic() - self._start_time) * document['fps']))
        self._present(frame)
        if frame == document['frame_count']:
            self.stop_preview()

    def _preview_tick(self):
        panel = self.advanced.panel
        if panel.jobs.active or getattr(panel, '_mutation_locked', False):
            self.stop_preview()
            return
        try:
            self.tick()
        except Exception as exc:
            self.stop_preview()
            panel.status.setText('Error: ' + str(exc))
            panel.status.setToolTip(panel.status.text())

    def load_json(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Load timeline JSON', '', 'Timeline JSON (*.json)')
        if path:
            self.stop_preview()
            self.advanced.controller.timeline_load_json(path)

    def open_project(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self,
            'Open validated timeline project — replaces the complete session')
        if path:
            self.stop_preview()
            self.advanced.controller.timeline_open_project(path)

    def _output_directory(self):
        path = self.output.text().strip()
        if not path:
            raise ValueError('Choose a new output directory for the timeline bundle')
        return path

    def save_project(self):
        document = self._require_document()
        output = self._output_directory()
        self.stop_preview()
        self.advanced.panel._launch_job('timeline_project', {'timeline': document}, output,
                                       state=self.state.value())

    def export_animation(self):
        document = self._require_document()
        output = self._output_directory()
        parameters = dict(timeline=document, render=self.render_parameters(), mp4=self.mp4.isChecked())
        if parameters['mp4']:
            parameters['tools'] = self.advanced.potential.tools_configuration()
        self.stop_preview()
        self.advanced.panel._launch_job('animation', parameters, output, state=self.state.value())
