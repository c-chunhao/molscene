"""Compact deposited assembly and coordinate trajectory controls."""
from pymol.Qt import QtWidgets
from .advanced_panel import form, field, combo, integer, decimal, check, text, buttons
from .ui_helpers import path_browse


class MolecularControls(QtWidgets.QWidget):
    def __init__(self, panel, prefix):
        super().__init__(panel)
        self.panel = panel
        self.prefix = prefix
        self.setMinimumWidth(0)
        self.layout = QtWidgets.QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.form = form(self.layout)

    def field(self, label, name, widget):
        return field(self.form, label, self.prefix + '_' + name, widget)

    def path_field(self, label, name, output=False, filters='All files (*)'):
        widget = self.field(label, name, QtWidgets.QLineEdit())
        path_browse(self.form, widget, self.panel, filters=filters, output=output, runner=self.panel.run)
        return widget

    def action(self, label, name, callback):
        button = buttons(self.layout, [(self.prefix + '_' + name, label)])[self.prefix + '_' + name]
        button.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        button.clicked.connect(lambda checked=False: self.panel.run(callback))
        return button


class AssemblyControls(MolecularControls):
    def __init__(self, panel):
        super().__init__(panel, 'assembly')
        self.source = self.path_field('Local deposited source', 'source', filters='Deposited coordinates (*.pdb *.cif *.mmcif)')
        self.assembly_id = self.field('Deposited assembly ID', 'id', combo(['1']))
        self.state = self.field('Coordinate state', 'state', integer(1, maximum=1000000))
        self.output = self.path_field('New output directory', 'output', output=True)
        self.metadata = text('Read deposited IDs, chains and operator counts before building.')
        self.metadata.setObjectName('assembly_metadata')
        self.layout.addWidget(self.metadata)
        self.action('Read assembly catalog', 'catalog', self.catalog)
        self.action('Build biological assembly', 'build', self.build)
        self.action('Load selected assembly copies', 'load', self.load)
        self._catalog = None
        self.source.textChanged.connect(self.invalidate)
        self.assembly_id.currentTextChanged.connect(self.summary)

    def invalidate(self):
        self._catalog = None
        self.metadata.setText('Source changed · read the deposited catalog before building.')

    def catalog(self):
        self._catalog = self.panel.controller.assembly_catalog(self.source.text().strip())
        previous = self.assembly_id.currentText()
        self.assembly_id.clear()
        self.assembly_id.addItems([r['assembly_id'] for r in self._catalog['assemblies']])
        if self.assembly_id.findText(previous) >= 0: self.assembly_id.setCurrentText(previous)
        self.summary()

    def summary(self):
        if not self._catalog: return
        row = next((r for r in self._catalog['assemblies'] if r['assembly_id'] == self.assembly_id.currentText()), None)
        if row:
            self.metadata.setText(f"Assembly {row['assembly_id']} · {row['chain_count']} chains · {row['copy_count']} copies · "
                f"{self._catalog['operator_count']} deposited operators\nChain namespace: {self._catalog['chain_namespace']}. Original source is preserved; copies load only on request.")

    def build(self):
        if not self.source.text().strip() or not self.output.text().strip():
            raise ValueError('Choose a deposited source and a new assembly output directory')
        if not self._catalog: self.catalog()
        self.panel._launch_job('assembly', dict(request=dict(source_path=self.source.text().strip(),
            assembly_id=self.assembly_id.currentText(),state=self.state.value())), output=self.output.text().strip())

    def load(self):
        record = self.panel._record()
        if record.get('operation') != 'assembly' or not record.get('_report_path') or record.get('cancelled'):
            raise ValueError('Select a completed biological assembly result in Results first')
        names = self.panel.controller.load_assembly_result(record['_report_path'])
        record['loaded'] = names
        self.panel._populate_result(record)
        self.panel.status.setText(f'Loaded {len(names)} verified assembly copies; original objects preserved')


class TrajectoryControls(MolecularControls):
    def __init__(self, panel):
        super().__init__(panel, 'trajectory')
        self.object = self.field('One molecular object / topology', 'object', combo([]))
        self.path = self.path_field('Optional local DCD (single-state topology)', 'path', filters='CHARMM DCD (*.dcd)')
        self.selection = self.field('Measure selection', 'selection', QtWidgets.QLineEdit('name CA'))
        self.fit_selection = self.field('Fit selection', 'fit_selection', QtWidgets.QLineEdit('name CA'))
        self.reference = self.field('Reference state', 'reference', integer(1, maximum=1000000))
        self.start = self.field('First state', 'start', integer(1, maximum=1000000))
        self.stop = self.field('Last state (0 = final state)', 'stop', integer(0, minimum=0, maximum=1000000))
        self.stride = self.field('Analysis state stride', 'stride', integer(1, maximum=1000000))
        self.align = self.field('Fit copied arrays to reference', 'align', check(True))
        self.time_enabled = self.field('Supply physical time (ps)', 'time_enabled', check(False))
        self.time_step = self.field('Time step per source state (ps)', 'time_step', decimal(1, minimum=.000001, decimals=6))
        self.time_start = self.field('Time at state 1 (ps)', 'time_start', decimal(0, minimum=-1000000000, maximum=1000000000, decimals=6))
        for widget in (self.time_step,self.time_start):
            widget.setEnabled(False)
            self.time_enabled.toggled.connect(widget.setEnabled)
        self.align.toggled.connect(self.fit_selection.setEnabled)
        self.output = self.path_field('New output directory', 'output', output=True)
        self.layout.addWidget(text('Use one multi-state object, or a DCD whose original atom order matches the single-state topology. No PBC correction; use pre-imaged/unwrapped molecules. Without supplied time, plots use source state. Analysis does not change coordinates or create fitted copies.'))
        self.action('Analyze trajectory', 'analyze', self.analyze)

    def refresh(self, names):
        self.panel._refresh_combo(self.object, names)

    def analyze(self):
        if not self.object.currentText() or not self.output.text().strip():
            raise ValueError('Choose one molecular object and a new trajectory output directory')
        request = dict(object_name=self.object.currentText(),selection=self.selection.text(),fit_selection=self.fit_selection.text(),
            reference_state=self.reference.value(),start=self.start.value(),stop=self.stop.value() or None,stride=self.stride.value(),
            align=self.align.isChecked(),time_step_ps=self.time_step.value() if self.time_enabled.isChecked() else None,
            time_start_ps=self.time_start.value() if self.time_enabled.isChecked() else 0.)
        if self.path.text().strip(): request['trajectory_path'] = self.path.text().strip()
        self.panel._launch_job('trajectory',dict(request=request),output=self.output.text().strip())


def preview(value, *, depth=0, budget=None, summarize_tables=False):
    """Bound nested GUI text without copying or changing stored report data."""
    if budget is None: budget = [128]
    budget[0] -= 1
    if budget[0] < 0 or depth > 8:
        return 'Preview limit · complete data retained in report and CSV'
    if isinstance(value, list):
        if len(value) > 16 or (summarize_tables and value and isinstance(value[0], dict)):
            return f'{len(value)} rows/items · complete data retained in report and CSV'
        return [preview(item,depth=depth+1,budget=budget,summarize_tables=summarize_tables) for item in value]
    if isinstance(value, dict):
        if len(value) > 64: return f'{len(value)} fields · complete data retained in report'
        return {key:preview(item,depth=depth+1,budget=budget,summarize_tables=summarize_tables) for key,item in value.items()}
    if isinstance(value,str) and len(value)>2000: return value[:2000]+'… (complete value in report)'
    return value


def preview_json(value, *, limit=1800, indent=None, summarize_tables=False):
    import json
    rendered = json.dumps(preview(value,summarize_tables=summarize_tables), indent=indent, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if len(rendered) <= limit:
        return rendered
    return json.dumps(f'{len(value)} structured items/fields · preview limit · complete data retained in report and CSV', ensure_ascii=False)
