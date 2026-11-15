#!/usr/bin/env python3
"""Render prebuilt multi-arch AUR PKGBUILD and .SRCINFO files for koma-bin or jiku-bin."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from render_template import (
    add_bin_arguments,
    render_bin_template,
    repo_root,
    validate_bin_inputs,
    write_safe,
)


@dataclass(frozen=True)
class AurBinSpec:
    template: str
    pkgname: str
    pkgdesc: str
    url: str
    depends: tuple[str, ...]
    optdepends: tuple[str, ...]


AUR_BIN_SPECS: dict[str, AurBinSpec] = {
    "koma": AurBinSpec(
        template="archlinux/PKGBUILD.koma-bin.in",
        pkgname="koma-bin",
        pkgdesc="Wayland tiling compositor with explicit desktop automation interfaces (prebuilt release binaries)",
        url="https://komawm.org",
        depends=(
            "gcc-libs",
            "glibc",
            "libdrm",
            "libinput",
            "libxkbcommon",
            "mesa",
            "seatd",
            "systemd-libs",
            "libglvnd",
            "wayland",
        ),
        optdepends=(
            "xwayland-satellite: X11 client support",
            "xdg-desktop-portal-gnome: screencast and file portals",
            "polkit: VT switching under systemd-logind without seatd",
            "jiku: optional desktop orchestration companion daemon",
        ),
    ),
    "jiku": AurBinSpec(
        template="archlinux/PKGBUILD.jiku-bin.in",
        pkgname="jiku-bin",
        pkgdesc="Desktop orchestration daemon, CLI/MCP controller, and SQLite FTS5 memory companion for Koma (prebuilt release binaries)",
        url="https://komawm.org/jiku/",
        depends=("gcc-libs", "glibc", "ca-certificates"),
        optdepends=(
            "koma: Wayland tiling compositor host",
            "libnotify: desktop alert notifications",
            "bubblewrap: outer filesystem sandbox",
        ),
    ),
}


def build_srcinfo(
    product: str,
    spec: AurBinSpec,
    version: str,
    tag: str,
    cdn_origin: str,
    sha256_x86_64: str,
    sha256_aarch64: str,
) -> str:
    """Render .SRCINFO matching `makepkg --printsrcinfo` attribute ordering."""
    lines = [
        f"pkgbase = {spec.pkgname}",
        f"\tpkgdesc = {spec.pkgdesc}",
        f"\tpkgver = {version}",
        "\tpkgrel = 1",
        f"\turl = {spec.url}",
        "\tarch = x86_64",
        "\tarch = aarch64",
        "\tlicense = GPL-3.0-or-later",
    ]
    for dep in spec.depends:
        lines.append(f"\tdepends = {dep}")
    for opt in spec.optdepends:
        lines.append(f"\toptdepends = {opt}")
    lines.extend(
        [
            f"\tprovides = {product}",
            f"\tconflicts = {product}",
            "\toptions = !debug",
            "\toptions = !strip",
            f"\tsource_x86_64 = {product}-{version}-x86_64.tar.zst::{cdn_origin}/releases/{product}/{tag}/{product}-{version}-x86_64-unknown-linux-gnu.tar.zst",
            f"\tsha256sums_x86_64 = {sha256_x86_64}",
            f"\tsource_aarch64 = {product}-{version}-aarch64.tar.zst::{cdn_origin}/releases/{product}/{tag}/{product}-{version}-aarch64-unknown-linux-gnu.tar.zst",
            f"\tsha256sums_aarch64 = {sha256_aarch64}",
            "",
            f"pkgname = {spec.pkgname}",
            "",
        ]
    )
    return "\n".join(lines)


def _assert_spec_matches_pkgbuild(spec: AurBinSpec, pkgbuild: str) -> None:
    """Prevent drift between AUR_BIN_SPECS metadata and the PKGBUILD template."""
    checks = (
        f"pkgname={spec.pkgname}",
        f"pkgdesc='{spec.pkgdesc}'",
        f"url='{spec.url}'",
    )
    for needle in checks:
        if needle not in pkgbuild:
            raise ValueError(f"Rendered PKGBUILD missing expected attribute: {needle}")
    for dep in (*spec.depends, *spec.optdepends):
        if f"'{dep}'" not in pkgbuild:
            raise ValueError(f"Rendered PKGBUILD missing dependency entry: {dep}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_bin_arguments(parser)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        cdn_origin, abs_out_dir = validate_bin_inputs(
            args.version,
            args.tag,
            args.sha256_x86_64,
            args.sha256_aarch64,
            args.cdn_origin,
            args.out_dir,
        )
        spec = AUR_BIN_SPECS[args.product]
        pkgbuild = render_bin_template(
            repo_root() / spec.template,
            args.version,
            args.tag,
            cdn_origin,
            args.sha256_x86_64,
            args.sha256_aarch64,
        )
        _assert_spec_matches_pkgbuild(spec, pkgbuild)
        srcinfo = build_srcinfo(
            args.product,
            spec,
            args.version,
            args.tag,
            cdn_origin,
            args.sha256_x86_64,
            args.sha256_aarch64,
        )
        abs_out_dir.mkdir(parents=True, exist_ok=True)
        write_safe(abs_out_dir / "PKGBUILD", pkgbuild, 0o644, overwrite=True)
        write_safe(abs_out_dir / ".SRCINFO", srcinfo, 0o644, overwrite=True)
    except (OSError, ValueError) as error:
        parser.error(str(error))

    print(f"Rendered AUR binary recipe ({spec.pkgname}): {abs_out_dir / 'PKGBUILD'} and .SRCINFO")


if __name__ == "__main__":
    main()
