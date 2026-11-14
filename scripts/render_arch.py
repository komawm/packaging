#!/usr/bin/env python3
"""Render an unaccepted local Arch template from explicit immutable inputs."""

import argparse
from pathlib import Path
import re


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_commit):
        parser.error("--source-commit must be a full lowercase 40-character commit")
    if not re.fullmatch(r"[0-9a-f]{64}", args.source_sha256):
        parser.error("--source-sha256 must be the actual archive's lowercase SHA-256")
    if not re.fullmatch(r"0\.\d+\.\d+(?:-[a-z0-9.]+)?", args.version):
        parser.error("--version must be a pre-1.0 development version")
    # Arch pkgver excludes hyphens even when the product SemVer uses them.
    if "-" in args.version:
        parser.error("Arch --version cannot contain a hyphen; choose an explicit pkgver mapping")
    output = args.output.absolute()
    if any(path.is_symlink() for path in (output, *output.parents)):
        parser.error("output and its parent directories must not be symlinks")
    template = Path(__file__).resolve().parents[1] / "archlinux/PKGBUILD.in"
    text = template.read_text().replace("@VERSION@", args.version)
    text = text.replace("@SOURCE_COMMIT@", args.source_commit)
    text = text.replace("@SOURCE_SHA256@", args.source_sha256)
    try:
        with output.open("x") as handle:
            handle.write(text)
    except OSError as error:
        parser.error(str(error))
    print(f"Rendered unaccepted review candidate: {output}")
    print("No archive download, build, install, or publication was performed.")


if __name__ == "__main__":
    main()
