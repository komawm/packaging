#!/usr/bin/env python3
"""Render prebuilt multi-arch Linuxbrew formula files for koma or jiku."""

from __future__ import annotations

import argparse
from pathlib import Path

from render_template import (
    add_bin_arguments,
    render_bin_template,
    repo_root,
    validate_bin_inputs,
    write_safe,
)

HOMEBREW_BIN_TEMPLATES: dict[str, str] = {
    "koma": "homebrew/koma-bin.rb.in",
    "jiku": "homebrew/jiku-bin.rb.in",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_bin_arguments(parser)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        cdn_origin, abs_output = validate_bin_inputs(
            args.version,
            args.tag,
            args.sha256_x86_64,
            args.sha256_aarch64,
            args.cdn_origin,
            args.output,
        )
        rendered = render_bin_template(
            repo_root() / HOMEBREW_BIN_TEMPLATES[args.product],
            args.version,
            args.tag,
            cdn_origin,
            args.sha256_x86_64,
            args.sha256_aarch64,
        )
        abs_output.parent.mkdir(parents=True, exist_ok=True)
        write_safe(abs_output, rendered, 0o644, overwrite=True)
    except (OSError, ValueError) as error:
        parser.error(str(error))

    print(f"Rendered Linuxbrew binary formula ({args.product}): {abs_output}")


if __name__ == "__main__":
    main()
