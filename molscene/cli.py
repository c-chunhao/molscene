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
    return parser


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
