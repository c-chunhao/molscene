#!/usr/bin/env python3
"""Download only the three pinned, public MolScene example coordinate files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


SOURCES = (
    ("1ubq.pdb", "https://files.rcsb.org/download/1UBQ.pdb",
     "d4a6812d8951cf6594e6a0763f089e35f5a80b62acb3c117b2c5565228a7b161"),
    ("1ubq.cif", "https://files.rcsb.org/download/1UBQ.cif",
     "056f98710cb2b36f633c45e41902a02eb446e82871da21ff2dd44f74a56ca0f6"),
    ("1hsg.pdb", "https://files.rcsb.org/download/1HSG.pdb",
     "d430675c358060808ef614002cfdcfcfa824714d89f1240adf5749d2ba60fed2"),
)
MAX_BYTES = 5 * 1024 * 1024


class FixedSourceRedirects(HTTPRedirectHandler):
    """Never follow redirects outside the original HTTPS source host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if (parsed.scheme != "https" or parsed.hostname != "files.rcsb.org"
                or parsed.username or parsed.password or parsed.port not in (None, 443)):
            raise ValueError("Refusing a redirect outside HTTPS files.rcsb.org")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    """Replace one validated file without exposing a partial download."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".molscene-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "data",
                        help="Destination directory (default: examples/data)")
    parser.add_argument("--overwrite", action="store_true",
                        help="Explicitly replace changed example files and provenance manifest")
    args = parser.parse_args(argv)
    output = args.output.resolve()
    records = [{"file": name, "url": url, "sha256": expected} for name, url, expected in SOURCES]
    manifest = (json.dumps({"source": "RCSB PDB", "files": records}, indent=2) + "\n").encode("utf-8")
    try:
        # Check every destination before making network requests or replacing any file.
        desired = {name: expected for name, _, expected in SOURCES}
        desired["sources.json"] = digest(manifest)
        for name, expected in desired.items():
            path = output / name
            if path.exists() and (not path.is_file() or (digest(path.read_bytes()) != expected and not args.overwrite)):
                raise ValueError(f"Refusing to replace changed destination: {path}; review it or use --overwrite")

        opener = build_opener(FixedSourceRedirects())
        pending = []
        for name, url, expected in SOURCES:
            path = output / name
            if path.is_file() and digest(path.read_bytes()) == expected:
                print(f"Verified {name}: {expected}")
                continue
            request = Request(url, headers={"User-Agent": "MolScene-examples/0.1"})
            with opener.open(request, timeout=30) as response:
                if urlsplit(response.url).scheme != "https":
                    raise ValueError("Refusing a non-HTTPS response")
                data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError(f"Download exceeds {MAX_BYTES} bytes: {name}")
            actual = digest(data)
            if actual != expected:
                raise ValueError(f"SHA-256 mismatch for {name}: expected {expected}, got {actual}; no files replaced")
            pending.append((path, data))
        # No destination is changed until all downloads have passed verification.
        output.mkdir(parents=True, exist_ok=True)
        for path, data in pending:
            atomic_write(path, data)
            print(f"Downloaded {path.name}: {digest(data)}")
        atomic_write(output / "sources.json", manifest)
        print(f"Provenance: {output / 'sources.json'}")
        return 0
    except (OSError, ValueError) as exc:
        print(f"Example download failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
