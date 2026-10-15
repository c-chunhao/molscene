# Contributing to MolScene

Small, reproducible improvements to scientific correctness, portability, error messages, and documentation are welcome. Discuss large scope changes in an issue first so the intended behavior is clear.

## Development setup

Use Python 3.10+ and run from a checkout. The core package has no extra runtime dependencies beyond an independently installed PyMOL backend.

```sh
python -m pip install -e .
python -m unittest discover -s tests -v
```

For rendering and integration work, use a Python environment that imports `pymol2`, install Pillow for PNG checks, and run:

```sh
python -m molscene doctor
python -m unittest discover -s tests -v
python -m molscene selftest --output results/contribution-selftest
```

See [docs/TESTING.md](docs/TESTING.md) for the baseline, headless CI, and what a skipped integration test means. The optional [example downloader](examples/README.md) is separate from tests; core tests must remain offline.

## Pull requests

- Describe the concrete problem and resulting behavior, including scientific assumptions and units when relevant.
- Add a focused regression test for substantive behavior changes. For small documentation or presentation-only edits, verify the affected commands or links.
- Keep command examples synchronized in English and Chinese. Avoid unsupported compatibility or performance claims.
- Include the actual validation command, result, environment, and any skips. If a change affects rendering, inspect the figure as well as the numerical report.
- Do not commit private structures, credentials, local environment directories, commercial binaries/source, license files, or copied UI assets. Use compact public or generated fixtures with provenance.
- Keep PyMOL optional at import time for unit tests and CLI help. Use its normal supported API; do not patch or bypass backend licensing.

MolScene's original contributions are released under the [MIT license](LICENSE). Third-party data and dependencies retain their own terms and attribution.

## Reporting a bug or requesting a feature

Use the [GitHub issue templates](https://github.com/c-chunhao/molscene/issues/new/choose). For bugs, provide a minimal command, expected/observed behavior, backend/Python/OS versions, and a public or synthetic reproducer. Explain the scientific workflow for feature requests and what a correct result would look like. Review attachments before sharing to remove private data and license material.

For a suspected security issue, avoid posting exploitable details or sensitive files publicly. Use GitHub's private vulnerability reporting if it is enabled for the repository; otherwise open a minimal issue requesting a private contact route without including the sensitive details.
