"""Command line entry point; parsing and help work without a PyMOL install."""
import argparse
from pathlib import Path
import sys

from . import engine


def _parser():
    parser = argparse.ArgumentParser(prog='molscene', description='Reproducible PyMOL scenes and geometric analysis.')
    parser.add_argument('--version', action='version', version='%(prog)s ' + engine.__version__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('doctor', help='Check the current Python and PyMOL context')
    render = commands.add_parser('render', help='Render a local PDB or mmCIF structure')
    render.add_argument('input')
    render.add_argument('--style', choices=('cartoon', 'surface', 'pocket'), default='cartoon')
    render.add_argument('--ligand', help='PyMOL ligand selection, required for pocket style')
    render.add_argument('--radius', type=float, default=5.0, help='Pocket radius in angstrom (default: 5)')
    measure = commands.add_parser('measure', help='Measure two selections containing exactly one atom each')
    measure.add_argument('input')
    measure.add_argument('--selection-a', required=True)
    measure.add_argument('--selection-b', required=True)
    align = commands.add_parser('align', help='Align similar protein structures')
    align.add_argument('mobile')
    align.add_argument('target')
    align.add_argument('--selection', default='polymer.protein and name CA')
    for command in (render, measure, align):
        command.add_argument('--output', required=True, help='Output directory')
        command.add_argument('--width', type=int, default=1600)
        command.add_argument('--height', type=int, default=1200)
        command.add_argument('--dpi', type=int, default=300)
        command.add_argument('--overwrite', action='store_true', help='Replace owned artifacts in a nonempty directory')
    selftest = commands.add_parser('selftest', help='Run offline geometry, rendering, session, and replay checks')
    selftest.add_argument('--output', required=True)
    selftest.add_argument('--overwrite', action='store_true')
    return parser


def main(argv=None):
    """Return a process exit code: 0 success, 1 operation failure, 2 usage error."""
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code
    try:
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
        options = dict(width=args.width, height=args.height, dpi=args.dpi, overwrite=args.overwrite)
        if args.command == 'render':
            report = engine.render(args.input, args.output, style=args.style, ligand=args.ligand,
                                   radius=args.radius, **options)
            print(f"Rendered {report['structure']['molecule']['atoms']} atoms.")
            if args.style == 'pocket':
                analysis = report['analysis']
                print(f"Pocket: {analysis['ligand_atoms']} ligand atoms; {analysis['pocket_residues']} protein residues. Geometric proximity only.")
        elif args.command == 'measure':
            report = engine.measure(args.input, args.output, selection_a=args.selection_a,
                                    selection_b=args.selection_b, **options)
            print(f"Distance: {report['analysis']['distance_angstrom']:.6g} angstrom.")
        else:
            report = engine.align(args.mobile, args.target, args.output, selection=args.selection, **options)
            print(f"RMSD: {report['analysis']['rmsd_angstrom']:.6g} angstrom; {report['analysis']['aligned_atoms']} aligned atoms.")
        print(f"Artifacts: {Path(args.output).expanduser().resolve()} (scene.png, scene.pse, scene.pml, report.json)")
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        print(f'molscene: {exc}', file=sys.stderr)
        return 1
