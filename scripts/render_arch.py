#!/usr/bin/env python3
"""Render an unaccepted local Arch PKGBUILD template for koma or jiku from explicit immutable inputs."""

from __future__ import annotations

import argparse
from pathlib import Path
import re

from render_template import (
    COMMIT_RE,
    SHA256_RE,
    _write_exclusive,
    add_common_arguments,
    render_distro,
    repo_root,
    substitute_placeholders,
)

JIKU_TEMPLATE = "archlinux/PKGBUILD.jiku.in"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", choices=("koma", "jiku"), default="koma")
    add_common_arguments(parser, require_distro=False)
    args = parser.parse_args()
    try:
        if args.product == "koma":
            written = render_distro(
                "archlinux",
                args.source_commit,
                args.source_sha256,
                args.version,
                args.output,
            )
            out_path = written[0]
        else:
            if not COMMIT_RE.fullmatch(args.source_commit):
                raise ValueError("--source-commit must be a full lowercase 40-character commit")
            if not SHA256_RE.fullmatch(args.source_sha256):
                raise ValueError("--source-sha256 must be the actual archive's lowercase SHA-256")
            if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[a-z0-9.]+)?", args.version):
                raise ValueError("--version must be X.Y.Z")
            if "-" in args.version:
                raise ValueError("Arch --version cannot contain a hyphen; choose an explicit pkgver mapping")
            abs_output = args.output.absolute()
            if any(path.is_symlink() for path in (abs_output, *abs_output.parents)):
                raise ValueError("output and its parent directories must not be symlinks")
            template = repo_root() / JIKU_TEMPLATE
            rendered = substitute_placeholders(
                template.read_text(encoding="utf-8"),
                args.version,
                args.source_commit,
                args.source_sha256,
            )
            _write_exclusive(abs_output, rendered, 0o644)
            out_path = abs_output
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f"Rendered unaccepted review candidate ({args.product}): {out_path}")
    print("No archive download, build, install, or publication was performed.")


if __name__ == "__main__":
    main()
