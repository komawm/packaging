#!/usr/bin/env python3
"""Render unaccepted local distribution packaging templates from explicit immutable inputs."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import re


COMMIT_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
VERSION_RE = re.compile(r"0\.\d+\.\d+(?:-[a-z0-9.]+)?")


@dataclass(frozen=True)
class DistroTemplateSpec:
    templates: tuple[tuple[str, str, int], ...]
    static_files: tuple[tuple[str, str, int], ...] = ()
    allow_hyphen_version: bool = False
    single_file: bool = True


DISTRO_TEMPLATES: dict[str, DistroTemplateSpec] = {
    "archlinux": DistroTemplateSpec(
        templates=(("archlinux/PKGBUILD.in", "PKGBUILD", 0o644),),
        allow_hyphen_version=False,
        single_file=True,
    ),
    "debian": DistroTemplateSpec(
        templates=(
            ("debian/changelog.in", "changelog", 0o644),
            ("debian/control.in", "control", 0o644),
            ("debian/rules.in", "rules", 0o755),
        ),
        static_files=(
            ("debian/copyright", "copyright", 0o644),
            ("debian/source/format", "source/format", 0o644),
        ),
        allow_hyphen_version=False,
        single_file=False,
    ),
    "rpm": DistroTemplateSpec(
        templates=(("rpm/koma.spec.in", "koma.spec", 0o644),),
        allow_hyphen_version=False,
        single_file=True,
    ),
    "alpine": DistroTemplateSpec(
        templates=(("alpine/APKBUILD.in", "APKBUILD", 0o644),),
        allow_hyphen_version=False,
        single_file=True,
    ),
    "nix": DistroTemplateSpec(
        templates=(
            ("nix/koma.nix.in", "koma.nix", 0o644),
            ("nix/flake.nix.in", "flake.nix", 0o644),
        ),
        allow_hyphen_version=True,
        single_file=False,
    ),
    "freebsd": DistroTemplateSpec(
        templates=(
            ("freebsd/Makefile.in", "Makefile", 0o644),
            ("freebsd/distinfo.in", "distinfo", 0o644),
            ("freebsd/pkg-manifest.ucl.in", "pkg-manifest.ucl", 0o644),
        ),
        static_files=(
            ("freebsd/pkg-descr", "pkg-descr", 0o644),
            ("freebsd/pkg-plist", "pkg-plist", 0o644),
        ),
        allow_hyphen_version=False,
        single_file=False,
    ),
    "openbsd": DistroTemplateSpec(
        templates=(
            ("openbsd/Makefile.in", "Makefile", 0o644),
            ("openbsd/distinfo.in", "distinfo", 0o644),
        ),
        static_files=(
            ("openbsd/pkg/DESCR", "pkg/DESCR", 0o644),
            ("openbsd/pkg/PLIST", "pkg/PLIST", 0o644),
        ),
        allow_hyphen_version=False,
        single_file=False,
    ),
    "homebrew": DistroTemplateSpec(
        templates=(("homebrew/koma.rb.in", "koma.rb", 0o644),),
        allow_hyphen_version=True,
        single_file=True,
    ),
}


def repo_root() -> Path:
    """Return the packaging repository root containing templates."""
    return Path(__file__).resolve().parents[1]


def validate_inputs(
    distro: str,
    source_commit: str,
    source_sha256: str,
    version: str,
    output: Path,
) -> tuple[DistroTemplateSpec, Path]:
    """Validate immutable source inputs and symlink-safe output destination."""
    if distro not in DISTRO_TEMPLATES:
        raise ValueError(f"unsupported distro {distro!r}; choose from {', '.join(sorted(DISTRO_TEMPLATES))}")
    spec = DISTRO_TEMPLATES[distro]
    if not COMMIT_RE.fullmatch(source_commit):
        raise ValueError("--source-commit must be a full lowercase 40-character commit")
    if not SHA256_RE.fullmatch(source_sha256):
        raise ValueError("--source-sha256 must be the actual archive's lowercase SHA-256")
    if not VERSION_RE.fullmatch(version):
        raise ValueError("--version must be a pre-1.0 development version")
    if "-" in version and not spec.allow_hyphen_version:
        raise ValueError(f"{distro} --version cannot contain a hyphen; choose an explicit version mapping")

    abs_output = output.absolute()
    if any(path.is_symlink() for path in (abs_output, *abs_output.parents)):
        raise ValueError("output and its parent directories must not be symlinks")
    return spec, abs_output


def substitute_placeholders(text: str, version: str, source_commit: str, source_sha256: str) -> str:
    """Replace canonical packaging template placeholders."""
    return (
        text.replace("@VERSION@", version)
        .replace("@SOURCE_COMMIT@", source_commit)
        .replace("@SOURCE_SHA256@", source_sha256)
    )


def _write_exclusive(target: Path, content: str, mode: int) -> None:
    """Create a brand-new file exclusively and apply explicit file permissions."""
    with target.open("x", encoding="utf-8") as handle:
        handle.write(content)
    os.chmod(target, mode)


def render_distro(
    distro: str,
    source_commit: str,
    source_sha256: str,
    version: str,
    output: Path,
    root: Path | None = None,
) -> list[Path]:
    """Render all template and static files for the selected distribution."""
    spec, abs_output = validate_inputs(distro, source_commit, source_sha256, version, output)
    base = root if root is not None else repo_root()
    written: list[Path] = []

    if spec.single_file:
        src_rel, _, mode = spec.templates[0]
        rendered = substitute_placeholders((base / src_rel).read_text(encoding="utf-8"), version, source_commit, source_sha256)
        _write_exclusive(abs_output, rendered, mode)
        written.append(abs_output)
        return written

    abs_output.mkdir(parents=False, exist_ok=False)
    for src_rel, out_rel, mode in spec.templates:
        target = abs_output / out_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        rendered = substitute_placeholders((base / src_rel).read_text(encoding="utf-8"), version, source_commit, source_sha256)
        _write_exclusive(target, rendered, mode)
        written.append(target)
    for src_rel, out_rel, mode in spec.static_files:
        target = abs_output / out_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_exclusive(target, (base / src_rel).read_text(encoding="utf-8"), mode)
        written.append(target)
    return written


def add_common_arguments(parser: argparse.ArgumentParser, require_distro: bool) -> None:
    """Register shared immutable-source and destination arguments."""
    if require_distro:
        parser.add_argument("--distro", required=True, choices=sorted(DISTRO_TEMPLATES))
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", required=True, type=Path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser, require_distro=True)
    args = parser.parse_args()
    try:
        written = render_distro(
            args.distro,
            args.source_commit,
            args.source_sha256,
            args.version,
            args.output,
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))

    summary = written[0] if len(written) == 1 else args.output.absolute()
    print(f"Rendered unaccepted {args.distro} review candidate: {summary}")
    print("No archive download, build, install, or publication was performed.")


if __name__ == "__main__":
    main()
