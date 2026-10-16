"""Compact host for advanced workflow views; backend work belongs to the controller."""
from pymol.Qt import QtCore, QtWidgets
from .navigation_panel import disclosure


def form(layout):
    result = QtWidgets.QFormLayout()
    result.setProperty('advanced_form', True)
    result.setRowWrapPolicy(QtWidgets.QFormLayout.WrapAllRows)
    result.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
    result.setContentsMargins(0, 0, 0, 0)
    layout.addLayout(result)
    return result


def field(layout, label, name, widget):
    widget.setObjectName(name)
    widget.setAccessibleName(label)
    widget.setMinimumWidth(0)
    widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
    if isinstance(widget, QtWidgets.QComboBox):
        widget.setMinimumContentsLength(8)
        widget.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
    layout.addRow(label, widget)
    return widget


def combo(items):
    widget = QtWidgets.QComboBox()
    widget.addItems(items)
    return widget


def integer(value, minimum=1, maximum=100000):
    widget = QtWidgets.QSpinBox()
    widget.setRange(minimum, maximum)
    widget.setValue(value)
    return widget


def decimal(value, minimum=0, maximum=1000000, decimals=3):
    widget = QtWidgets.QDoubleSpinBox()
    widget.setDecimals(decimals)
    widget.setRange(minimum, maximum)
    widget.setValue(value)
    return widget


def check(checked=False):
    widget = QtWidgets.QCheckBox()
    widget.setChecked(checked)
    return widget


def text(label):
    widget = QtWidgets.QLabel(label)
    widget.setProperty('advanced_dynamic_text', True)
    widget.setTextFormat(QtCore.Qt.PlainText)
    widget.setWordWrap(True)
    widget.setMinimumWidth(0)
    widget.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
    widget.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
    return widget


def buttons(layout, entries):
    """Use a narrow grid so long labels cannot force the native dock wider."""
    row = QtWidgets.QGridLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(3)
    layout.addLayout(row)
    result = {}
    for index, (name, label) in enumerate(entries):
        widget = QtWidgets.QPushButton(label)
        widget.setObjectName(name)
        widget.setAccessibleName(label)
        widget.setMinimumWidth(0)
        row.addWidget(widget, index, 0)
        result[name] = widget
    return result


class AdvancedPanel(QtWidgets.QScrollArea):
    """One scrollable tab with three independently disclosed workflow sections."""

    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.controller = panel.controller
        self.setObjectName('advanced_panel')
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setMinimumWidth(0)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        content = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(content)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        from .timeline_panel import TimelinePanel
        from .density_panel import DensityPanel
        from .potential_panel import PotentialPanel
        self.timeline = TimelinePanel(self)
        self.density = DensityPanel(self)
        self.potential = PotentialPanel(self)
        for title, name, section, opened in (
            ('Timeline', 'advanced_timeline', self.timeline, True),
            ('Density maps', 'advanced_density', self.density, False),
            ('Potential', 'advanced_potential', self.potential, False),
        ):
            disclosure(layout, title, name, opened).addWidget(section)
        layout.addStretch(1)
        self.setWidget(content)
        self._epoch = None
        self._dock = panel.parentWidget()
        if isinstance(self._dock, QtWidgets.QDockWidget):
            self._dock.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is getattr(self, '_dock', None) and event.type() in (QtCore.QEvent.Close, QtCore.QEvent.Hide):
            self.stop_preview()
        return super().eventFilter(watched, event)

    def run(self, callback):
        def guarded():
            try:
                return callback()
            except Exception:
                self.stop_preview()
                raise
        return self.panel.run(guarded)

    def refresh(self):
        try:
            inventory = self.controller.advanced_inventory()
            epoch = inventory['session_epoch']
            if self._epoch is not None and epoch != self._epoch:
                self.stop_preview()
                self.potential.loaded_record = None
                self.potential.actions['potential_ramp_apply'].setEnabled(False)
            self._epoch = epoch
            self.timeline.refresh(inventory['timeline'], self.controller.scenes())
            self.density.refresh(inventory['maps'], [row['name'] for row in self.controller.objects()])
        except Exception:
            self.stop_preview()
            raise

    def stop_preview(self):
        self.timeline.stop_preview()

    def set_job_active(self, active):
        if active:
            self.stop_preview()
        self.setEnabled(not active)

    def closeEvent(self, event):
        self.stop_preview()
        super().closeEvent(event)
