"""Small Qt-only path and progressive disclosure helpers; no backend imports."""
import json
from pathlib import Path
from pymol.Qt import QtWidgets
from .navigation_panel import disclosure


def browse_input(parent, entry, title, filters):
    path, _ = QtWidgets.QFileDialog.getOpenFileName(parent, title, entry.text(), filters)
    if path:
        entry.setText(path)


def browse_new_directory(parent, entry):
    """Choose a parent and an uncreated leaf, without creating any filesystem entry."""
    from molscene.artifacts import checked_path
    directory = QtWidgets.QFileDialog.getExistingDirectory(parent, 'Choose parent for a new output directory', '')
    if not directory:
        return
    leaf, accepted = QtWidgets.QInputDialog.getText(parent, 'New output directory', 'New directory name (must not exist)')
    if not accepted:
        return
    if not leaf or leaf in ('.', '..') or any(c in leaf for c in '/\\:'):
        raise ValueError('Choose one new directory name without path separators')
    destination = checked_path(Path(directory) / leaf)
    if destination.exists() or destination.is_symlink():
        raise ValueError('Output directory must be new and must not already exist')
    entry.setText(str(destination))


def path_browse(form, entry, parent, *, title='Choose input file', filters='All files (*)', output=False, runner=None):
    """Wrap the existing public field without changing its identity or form label."""
    row_index, _ = form.getWidgetPosition(entry)
    label = form.labelForField(entry)
    form.removeWidget(entry)
    row = QtWidgets.QWidget()
    row.setMinimumWidth(0)
    layout = QtWidgets.QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    entry.setMinimumWidth(0)
    layout.addWidget(entry, 1)
    button = QtWidgets.QPushButton('Browse…')
    button.setObjectName('browse_' + entry.objectName())
    layout.addWidget(button)
    form.setWidget(row_index, QtWidgets.QFormLayout.FieldRole, row)
    entry._path_row, entry._path_label, entry._path_browse = row, label, button
    callback = (lambda: browse_new_directory(parent, entry)) if output else (lambda: browse_input(parent, entry, title, filters))
    button.clicked.connect(lambda checked=False: runner(callback) if runner else callback())
    return row


def set_row_visible(form, widget, visible):
    row = getattr(widget, '_path_row', widget)
    row.setVisible(visible)
    label = getattr(widget, '_path_label', None) or form.labelForField(row)
    if label is not None:
        label.setVisible(visible)


def details(layout, title, name):
    content = disclosure(layout, title, name + '_section')
    widget = QtWidgets.QPlainTextEdit()
    widget.setObjectName(name)
    widget.setReadOnly(True)
    widget.setMinimumWidth(0)
    widget.setMaximumHeight(180)
    content.addWidget(widget)
    return widget


def show_details(widget, value):
    widget.setPlainText(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))
