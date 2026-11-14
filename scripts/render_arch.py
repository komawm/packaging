#!/usr/bin/env python3
"""Render an unaccepted local Arch template from explicit immutable inputs."""

import argparse

from render_template import add_common_arguments, render_distro


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser, require_distro=False)
    args = parser.parse_args()
    try:
        written = render_distro(
            "archlinux",
            args.source_commit,
            args.source_sha256,
            args.version,
            args.output,
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f"Rendered unaccepted review candidate: {written[0]}")
    print("No archive download, build, install, or publication was performed.")


if __name__ == "__main__":
    main()
