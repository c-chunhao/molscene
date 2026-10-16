"""Command line entry point; parsing and help work without a PyMOL install."""
import argparse
import json
from pathlib import Path
import sys

from . import engine


def _render_arguments(parser):
    parser.add_argument('--style', choices=('cartoon', 'surface', 'pocket'), default='cartoon')
    parser.add_argument('--ligand', help='PyMOL ligand selection, required for pocket style')
    parser.add_argument('--radius', type=float, default=5.0, help='Pocket radius in angstrom (default: 5)')
    # Omit new default keywords for compatibility with v0.1 engine clients.
    parser.add_argument('--representation', default=argparse.SUPPRESS,
                        choices=('cartoon', 'ribbon', 'sticks', 'lines', 'spheres', 'surface',
                                 'mesh', 'dots', 'nonbonded'), help='Representation override')
    parser.add_argument('--coloring', default=argparse.SUPPRESS,
                        choices=('chain', 'element', 'single', 'rainbow', 'bfactor'),
                        help='Coloring mode (default: chain)')
    for name, value_type, help_text in (
            ('selection', str, 'Selection to render (default: all)'),
            ('color', str, 'Uniform color (default: marine)'),
            ('state', int, 'Positive coordinate state (default: 1)'),
            ('background', str, 'Background color (default: white)'),
            ('surface-transparency', float, 'Surface transparency (default: 0.2)'),
            ('stick-radius', float, 'Stick radius in angstrom (default: 0.18)')):
        parser.add_argument('--' + name, type=value_type, default=argparse.SUPPRESS, help=help_text)


def _parser():
    parser = argparse.ArgumentParser(prog='molscene', description='Reproducible PyMOL scenes and geometric analysis.')
    parser.add_argument('--version', action='version', version='%(prog)s ' + engine.__version__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('doctor', help='Check the current Python and PyMOL context')
    render = commands.add_parser('render', help='Render a local PDB or mmCIF structure')
    render.add_argument('input')
    _render_arguments(render)
    measure = commands.add_parser('measure', help='Measure two selections containing exactly one atom each')
    measure.add_argument('input')
    measure.add_argument('--selection-a', required=True)
    measure.add_argument('--selection-b', required=True)
    measure.add_argument('--state', type=int, default=argparse.SUPPRESS, help='Positive coordinate state (default: 1)')
    align = commands.add_parser('align', help='Align similar protein structures')
    align.add_argument('mobile')
    align.add_argument('target')
    align.add_argument('--selection', default='polymer.protein and name CA')
    align.add_argument('--method', choices=('align', 'super'), default=argparse.SUPPRESS)
    for name, kind in (('mobile-state', int), ('target-state', int), ('cutoff', float), ('cycles', int)):
        align.add_argument('--' + name, type=kind, default=argparse.SUPPRESS)
    geometry = []
    for name, count in (('angle', 3), ('dihedral', 4), ('contacts', 2)):
        command = commands.add_parser(name, help=('Measure ' + name + ' (degrees)' if name != 'contacts'
                                      else 'Find unique heavy-atom contacts (angstrom)'))
        command.add_argument('input')
        for letter in 'abcd'[:count]:
            command.add_argument('--selection-' + letter, required=True)
        command.add_argument('--state', type=int, default=1, help='Positive coordinate state (default: 1)')
        if name == 'contacts':
            command.add_argument('--cutoff', type=float, default=4.0, help='Contact cutoff in angstrom (default: 4)')
        geometry.append(command)
    sasa = commands.add_parser('sasa', help='Solvent-accessible surface area in square angstrom')
    sasa.add_argument('input')
    sasa.add_argument('--selection', default='all', help='Target subset of the context')
    sasa.add_argument('--context', help='Context selection (default: non-solvent heavy atoms)')
    sasa.add_argument('--altloc', choices=('primary', 'all'), default='primary', help='Default: blank and A altloc')
    sasa.add_argument('--state', type=int, default=1)
    interface = commands.add_parser('interface', help='Buried SASA of two disjoint partners (square angstrom)')
    interface.add_argument('input')
    interface.add_argument('--selection-a', required=True)
    interface.add_argument('--selection-b', required=True)
    interface.add_argument('--state', type=int, default=1)
    interface.add_argument('--altloc', choices=('primary', 'all'), default='primary',
                           help='Default: exact blank and A altloc')
    batch = commands.add_parser('batch', help='Render sorted local PDB/CIF/mmCIF files, nonrecursive')
    batch.add_argument('input_dir')
    _render_arguments(batch)
    for command in (render, measure, align, *geometry, sasa, interface, batch):
        command.add_argument('--output', required=True, help='Output directory')
        command.add_argument('--width', type=int, default=1600)
        command.add_argument('--height', type=int, default=1200)
        command.add_argument('--dpi', type=int, default=300)
        command.add_argument('--overwrite', action='store_true', help='Replace owned artifacts in a nonempty directory')
    inspect = commands.add_parser('inspect', help='Inspect structure metadata without rendering')
    inspect.add_argument('input')
    inspect.add_argument('--selection', default='all')
    inspect.add_argument('--state', type=int, default=1)
    inspect.add_argument('--output', required=True)
    inspect.add_argument('--overwrite', action='store_true')
    gui = commands.add_parser('gui', help='Open the optional Qt molecular desktop')
    gui.add_argument('paths', nargs='*', help='Structures or sessions to open')
    gui.add_argument('--workspace', help='Desktop workspace directory')
    selftest = commands.add_parser('selftest', help='Run offline geometry, rendering, session, and replay checks')
    selftest.add_argument('--output', required=True)
    selftest.add_argument('--overwrite', action='store_true')
    _advanced_parsers(commands)
    return parser


def _advanced_parsers(commands):
    validate = commands.add_parser('timeline-validate', help='Validate timeline JSON without a backend')
    validate.add_argument('timeline')
    validate.add_argument('--session')
    project = commands.add_parser('timeline-project')
    animate = commands.add_parser('animate')
    for parser in (project, animate):
        parser.add_argument('session')
        parser.add_argument('--timeline', required=True)
        parser.add_argument('--output', required=True)
    animate.add_argument('--mp4', action='store_true')
    animate.add_argument('--tools')
    density = commands.add_parser('density')
    density.add_argument('map')
    density.add_argument('--format', choices=('dx', 'mrc', 'ccp4'), required=True)
    density.add_argument('--output', required=True)
    density.add_argument('--session')
    for key, choices in [('interpretation', ('auto', 'origin', 'starts')), ('kind', ('mesh', 'surface', 'volume')),
                         ('algorithm', ('align', 'super'))]:
        density.add_argument('--' + key, choices=choices, default=argparse.SUPPRESS)
    for key, kind in [('level', float), ('color', str), ('opacity', float), ('volume-ramp', str), ('matrix', str),
                      ('mobile', str), ('target', str), ('mobile-state', int), ('target-state', int),
                      ('alignment-selection', str), ('cutoff', float), ('cycles', int)]:
        density.add_argument('--' + key, type=kind, default=argparse.SUPPRESS)
    potential = commands.add_parser('potential')
    potential.add_argument('--mode', choices=('prepared', 'prepare', 'import'), required=True)
    potential.add_argument('--output', required=True)
    potential.add_argument('--session')
    potential.add_argument('--grid', nargs=3, type=int, default=argparse.SUPPRESS)
    potential.add_argument('--drop-water', action='store_true', default=argparse.SUPPRESS)
    for key, kind in [('pqr', str), ('dx', str), ('selection', str), ('state', int), ('units', str),
                      ('display-units', str), ('temperature', float), ('force-field', str), ('ph', float),
                      ('protonation', str), ('solute-dielectric', float), ('solvent-dielectric', float),
                      ('salt', float), ('ion-radius', float), ('probe-radius', float), ('coarse-padding', float),
                      ('fine-padding', float), ('ramp-limit', float), ('tools', str)]:
        potential.add_argument('--' + key, type=kind, default=argparse.SUPPRESS)
    for parser in (animate, density, potential):
        for key in ('width', 'height', 'dpi'):
            parser.add_argument('--' + key, type=int, default=argparse.SUPPRESS)
        parser.add_argument('--no-ray', action='store_true')
    tools = commands.add_parser('tools')
    tools.add_argument('--config', required=True)


def _advanced_main(args):
    import tempfile
    from .advanced import read_json, run_advanced, probe_tools
    from .artifacts import checked_path
    from .external_tools import load_tools
    from .timeline import validate_timeline
    if args.command == 'tools':
        config = load_tools(checked_path(args.config))
        folder = Path(tempfile.mkdtemp(prefix='molscene-probes-'))
        report = probe_tools(config, folder)
        # Successful probes clean their own scratch. Keep failed probe logs
        # reachable through returned diagnostics instead of deleting them.
        if not any(folder.iterdir()):
            folder.rmdir()
    elif args.command == 'timeline-validate':
        document = read_json(checked_path(args.timeline))
        if args.session:
            with engine._runtime().PyMOL() as pm:
                pm.cmd.load(str(engine._session(checked_path(args.session))))
                document = validate_timeline(document, pm.cmd)
        else:
            document = validate_timeline(document)
        report = dict(passed=True, timeline=document)
    else:
        protected = []
        session = str(checked_path(args.session)) if args.session else None
        render = {name: getattr(args, name) for name in ('width', 'height', 'dpi') if hasattr(args, name)}
        if hasattr(args, 'no_ray'):
            render['ray'] = not args.no_ray
        operation = {'timeline-project': 'timeline_project', 'animate': 'animation'}.get(args.command, args.command)
        if operation in ('timeline_project', 'animation'):
            source = checked_path(args.timeline)
            protected.append(source)
            parameters = dict(timeline=read_json(source))
            if operation == 'animation':
                parameters.update(render=render, mp4=args.mp4)
                if args.tools:
                    protected.append(checked_path(args.tools))
                    parameters['tools'] = load_tools(args.tools)
        elif operation == 'density':
            request = dict(map_path=str(checked_path(args.map)), format=args.format, render=render)
            if hasattr(args, 'interpretation'):
                request['interpretation'] = args.interpretation
            request['display'] = {key: getattr(args, key) for key in ('kind', 'level', 'color', 'opacity') if hasattr(args, key)}
            if hasattr(args, 'volume_ramp'):
                protected.append(checked_path(args.volume_ramp))
                request['display']['volume_ramp'] = read_json(args.volume_ramp)
            if hasattr(args, 'matrix'):
                if hasattr(args, 'mobile') or hasattr(args, 'target'):
                    raise ValueError('Choose matrix or molecular alignment, not both')
                protected.append(checked_path(args.matrix))
                request['registration'] = dict(kind='matrix', matrix=read_json(args.matrix), copy_name='density_registered')
            elif hasattr(args, 'mobile') or hasattr(args, 'target'):
                if not hasattr(args, 'mobile') or not hasattr(args, 'target'):
                    raise ValueError('Alignment requires both mobile and target')
                registration = dict(kind='align', mobile=args.mobile, target=args.target,
                                    copy_name='density_registered', model_copy_name='density_aligned')
                for key in ('mobile_state', 'target_state', 'algorithm', 'cutoff', 'cycles'):
                    if hasattr(args, key):
                        registration[key] = getattr(args, key)
                if hasattr(args, 'alignment_selection'):
                    registration['selection'] = args.alignment_selection
                request['registration'] = registration
            parameters = dict(request=request)
        else:
            request = dict(mode=args.mode, render=render)
            for key in ('selection', 'state', 'units', 'display_units', 'temperature', 'ramp_limit'):
                if hasattr(args, key):
                    request[key] = getattr(args, key)
            for key in ('pqr', 'dx'):
                if hasattr(args, key):
                    request[key + '_path'] = str(checked_path(getattr(args, key)))
            request['preparation'] = {key: getattr(args, key) for key in ('force_field', 'ph', 'protonation', 'drop_water') if hasattr(args, key)}
            request['solver'] = {key: getattr(args, key) for key in ('grid', 'solute_dielectric', 'solvent_dielectric', 'ion_radius', 'probe_radius', 'coarse_padding', 'fine_padding') if hasattr(args, key)}
            if hasattr(args, 'salt'):
                request['solver']['salt_molar'] = args.salt
            if hasattr(args, 'tools'):
                protected.append(checked_path(args.tools))
                request['tools'] = load_tools(args.tools)
            if args.mode == 'prepare' and (session is None or not hasattr(args, 'selection')):
                raise ValueError('prepare requires session and explicit selection')
            if args.mode == 'import' and (not hasattr(args, 'units') or bool(session) == hasattr(args, 'pqr')):
                raise ValueError('import requires explicit units and exactly one PQR/session')
            parameters = dict(request=request)
        report = run_advanced(operation, session, checked_path(args.output), parameters, protected=protected)
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))
    return 0 if report.get('passed', True) else 1


def _present_analysis(command, report):
    analysis = report.get('analysis', {})
    key, label, units = {
        'angle': ('value', 'Angle', 'degrees'),
        'dihedral': ('value', 'Dihedral', 'degrees'),
        'contacts': ('pair_count', 'Contacts', 'pairs; geometric proximity only'),
        'sasa': ('total_area_angstrom2', 'SASA', 'square angstrom'),
        'interface': ('interface_area_angstrom2', 'Interface area',
                      'square angstrom; half the total buried SASA; geometric area only'),
        'inspect': ('atom_count', 'Inspected', 'atoms'),
    }[command]
    if isinstance(analysis.get(key), (int, float)):
        print(f'{label}: {analysis[key]:.6g} {units}.')
        return
    print(f'{command.capitalize()}: {json.dumps(analysis, ensure_ascii=False, sort_keys=True)}')


def _render_options(args):
    result = dict(style=args.style, ligand=args.ligand, radius=args.radius)
    for name in ('representation', 'selection', 'coloring', 'color', 'state', 'background',
                 'surface_transparency', 'stick_radius'):
        if hasattr(args, name):
            result[name] = getattr(args, name)
    return result


def main(argv=None):
    """Return a process exit code: 0 success, 1 operation failure, 2 usage error."""
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code
    try:
        if args.command in ('timeline-validate', 'timeline-project', 'animate', 'density', 'potential', 'tools'):
            return _advanced_main(args)
        if args.command == 'gui':
            try:
                from .desktop import launch
            except ImportError as exc:
                raise RuntimeError('GUI dependencies are unavailable; install MolScene with its GUI extras.') from exc
            return launch(paths=args.paths, workspace=args.workspace)
        if args.command == 'doctor':
            report = engine.doctor()
            print(f"PyMOL {report['pymol_version']}; Python {report['python']} ({report['executable']})")
            return 0
        if args.command == 'selftest':
            from .selftest import run_selftest
            report = run_selftest(args.output, overwrite=args.overwrite)
            location = Path(args.output).expanduser().resolve() / 'selftest.json'
            if not report['passed']:
                for test in report['tests']:
                    if test['status'] == 'failed':
                        print(f"Self-test failed: {test['name']}: {test.get('error', test.get('evidence', {}))}", file=sys.stderr)
                print(f'Self-test report: {location}', file=sys.stderr)
                return 1
            print(f"Self-test passed ({len(report['tests'])} checks). Report: {location}")
            return 0
        if args.command == 'inspect':
            report = engine.inspect(args.input, args.output, selection=args.selection,
                                    state=args.state, overwrite=args.overwrite)
            _present_analysis(args.command, report)
            print(f"Artifacts: {Path(args.output).expanduser().resolve()} "
                  f"({', '.join(str(v) for v in report.get('artifacts', {}).values())})")
            return 0
        options = dict(width=args.width, height=args.height, dpi=args.dpi, overwrite=args.overwrite)
        if args.command == 'batch':
            from .batch import run_batch
            render_options = _render_options(args)
            render_options.update(width=args.width, height=args.height, dpi=args.dpi)
            report = run_batch(args.input_dir, args.output, render_options=render_options,
                               overwrite=args.overwrite)
            print(f"Batch: {report['completed']} completed; {report['failed']} failed; "
                  f"cancelled: {report['cancelled']}.")
            print(f"Artifacts: {Path(args.output).expanduser().resolve()} (batch.json, batch.csv)")
            return 0 if report['passed'] else 1
        if args.command == 'render':
            report = engine.render(args.input, args.output, **_render_options(args), **options)
            print(f"Rendered {report['structure']['molecule']['atoms']} atoms.")
            if args.style == 'pocket':
                analysis = report['analysis']
                print(f"Pocket: {analysis['ligand_atoms']} ligand atoms; {analysis['pocket_residues']} protein residues. Geometric proximity only.")
        elif args.command == 'measure':
            if hasattr(args, 'state'):
                options['state'] = args.state
            report = engine.measure(args.input, args.output, selection_a=args.selection_a,
                                    selection_b=args.selection_b, **options)
            print(f"Distance: {report['analysis']['distance_angstrom']:.6g} angstrom.")
        elif args.command == 'align':
            for name in ('method', 'mobile_state', 'target_state', 'cutoff', 'cycles'):
                if hasattr(args, name):
                    options[name] = getattr(args, name)
            report = engine.align(args.mobile, args.target, args.output, selection=args.selection, **options)
            print(f"RMSD: {report['analysis']['rmsd_angstrom']:.6g} angstrom; {report['analysis']['aligned_atoms']} aligned atoms.")
        else:
            options['state'] = args.state
            if args.command == 'interface':
                options.update(selection_a=args.selection_a, selection_b=args.selection_b, altloc=args.altloc)
            elif args.command == 'sasa':
                options.update(selection=args.selection, context=args.context, altloc=args.altloc)
            else:
                for letter in ('ab' if args.command == 'contacts' else 'abc' if args.command == 'angle' else 'abcd'):
                    options['selection_' + letter] = getattr(args, 'selection_' + letter)
                if args.command == 'contacts':
                    options['cutoff'] = args.cutoff
            report = getattr(engine, args.command)(args.input, args.output, **options)
            _present_analysis(args.command, report)
        print(f"Artifacts: {Path(args.output).expanduser().resolve()} (scene.png, scene.pse, scene.pml, report.json)")
        for key, value in report.get('artifacts', {}).items():
            if 'csv' in key or (isinstance(value, str) and value.endswith('.csv')):
                print(f'CSV: {value}')
        return 0
    except (ValueError, RuntimeError, OSError, ImportError) as exc:
        print(f'molscene: {exc}', file=sys.stderr)
        return 1
