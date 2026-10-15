"""Optional native PyMOL workbench. Importing this package does not import Qt."""
import importlib
import importlib.util
import sys
import os
from pathlib import Path
import threading

_launch_paths = ()
_workspace = None


def _gui_runtime():
    if 'QT_API' not in os.environ:
        bindings = ('PySide6', 'PySide2', 'PyQt6', 'PyQt5')
        loaded = [binding for binding in bindings if binding in sys.modules]
        for binding in loaded or bindings:
            if importlib.util.find_spec(binding) is not None:
                os.environ['QT_API'] = binding.lower()
                break
    try:
        pymol = importlib.import_module('pymol')
        qt = importlib.import_module('pymol.Qt')
    except ImportError as exc:
        raise RuntimeError('MolScene GUI requires PyMOL and PySide6. Install molscene[gui] in your PyMOL Python environment.') from exc
    return pymol, qt


def _main_thread():
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError('MolScene live GUI operations must run on the main thread')


def launch(paths=None, workspace=None):
    """Run official blocking PyMOL Qt startup on the main thread."""
    global _launch_paths, _workspace
    _main_thread()
    pymol, qt = _gui_runtime()
    if qt.QtWidgets.QApplication.instance() is not None:
        raise RuntimeError('A QApplication already exists; use attach_workbench() in the native PyMOL window')
    _launch_paths = tuple(Path(p).expanduser().resolve() for p in (paths or ()))
    _workspace = Path(workspace).expanduser().resolve() if workspace else Path.cwd()
    from pymol import plugins
    startup = str(Path(__file__).parent / 'plugin' / 'startup')
    paths_now = list(plugins.get_startup_path(useronly=True))
    if startup not in plugins.get_startup_path():
        plugins.set_startup_path(paths_now + [startup], autosave=False)
    # Saved plugin preferences may replace user plugin paths during startup.
    # The official deferred route is a fixed bootstrap with no user interpolation.
    bootstrap = "/__import__('molscene.desktop', fromlist=['attach_workbench']).attach_workbench()"
    pymol.launch(['molscene', '-q', '-d', bootstrap])
    return 0


def attach_workbench(parent=None):
    """Attach or reopen the unique dock in an existing native PyMOL window."""
    _main_thread()
    pymol, qt = _gui_runtime()
    app = qt.QtWidgets.QApplication.instance()
    if app is None or qt.QtCore.QThread.currentThread() != app.thread():
        raise RuntimeError('Attach MolScene after the native PyMOL QApplication starts, on its main thread')
    if parent is None:
        from pymol.gui import get_qtwindow
        parent = get_qtwindow()
    if parent is None:
        raise RuntimeError('No native PyMOL Qt window is available')
    dock = parent.findChild(qt.QtWidgets.QDockWidget, 'molscene_workbench')
    if dock is None:
        from .controller import WorkbenchController
        from .panel import WorkbenchPanel
        original_width = parent.width()
        dock = qt.QtWidgets.QDockWidget('MolScene', parent)
        dock.setObjectName('molscene_workbench')
        controller = WorkbenchController(getattr(parent, 'cmd', pymol.cmd), _workspace or Path.cwd())
        dock.setWidget(WorkbenchPanel(controller, dock))
        parent.addDockWidget(qt.QtCore.Qt.RightDockWidgetArea, dock)
        screen = parent.screen()
        available_width = screen.availableGeometry().width() if screen else original_width + 360
        parent.resize(min(original_width + 360, available_width), parent.height())
        parent.resizeDocks([dock], [360], qt.QtCore.Qt.Horizontal)
        # A native QAction survives dock close, and reopens this same instance.
        action = qt.QtWidgets.QAction('MolScene Workbench', parent)
        action.setObjectName('molscene_open_workbench')
        action.triggered.connect(lambda checked=False: attach_workbench(parent))
        menu = getattr(parent, 'menudict', {}).get('PluginQt')
        if menu is None:
            menu = parent.menuBar().addMenu('MolScene')
        menu.addAction(action)
        for path in _launch_paths:
            dock.widget().run(lambda p=path: controller.open_session(p) if p.suffix.lower() == '.pse' else controller.load_coordinates(p))
    dock.show()
    dock.raise_()
    dock.widget().refresh()
    return dock
