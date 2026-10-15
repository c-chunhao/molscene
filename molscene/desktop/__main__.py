"""Run the optional native desktop entry point."""
import argparse
from . import launch

parser = argparse.ArgumentParser(description='MolScene native PyMOL workbench')
parser.add_argument('paths', nargs='*')
parser.add_argument('--workspace')
args = parser.parse_args()
launch(args.paths, args.workspace)
