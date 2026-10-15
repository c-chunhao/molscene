"""Official PyMOL startup-plugin hook, registered only for this process."""
def __init_plugin__(app=None):
    from pymol.Qt import QtCore
    from molscene import desktop
    QtCore.QTimer.singleShot(0, desktop.attach_workbench)
