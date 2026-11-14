# Debian / Ubuntu template

The [Debian template directory](https://github.com/komawm/packaging/tree/main/debian)
(`control.in`, `rules.in`, `changelog.in`, `copyright`, and `source/format`)
is an unaccepted integration template for building `amd64` and `arm64`
(`koma_<version>-1_<arch>.deb`) packages from an immutable
[Koma](https://github.com/komawm/koma) source archive.

## Toolchain and dependency notes

Koma pins Rust `1.100.0` (edition `2024`) in `rust-toolchain.toml`. Because Debian
12/Ubuntu 24.04 distro `rustc` packages are older than the MSRV, `Build-Depends`
lists native C/Wayland development headers (`debhelper-compat`, `pkgconf`,
`libudev-dev`, `libinput-dev`, `libseat-dev`, `libgbm-dev`, `libegl-dev`,
`libgles-dev`, `libwayland-dev`, `libxkbcommon-dev`, `libdbus-1-dev`,
`libfontconfig-dev`) while `cargo` matching
`rust-toolchain.toml` is supplied in `PATH` (for example via `rustup`).

`debian/rules.in` disables `dh_dwz` because Koma's release Cargo profile omits
DWARF sections, and installs the full product `LICENSE` alongside
`debian/copyright`.

## Rendering and local review

Render a disposable `debian/` tree from verified source provenance:

```bash
python3 scripts/render_template.py \
  --distro debian \
  --source-commit <40-hex-commit> \
  --source-sha256 <real-archive-sha256> \
  --version 0.0.1 \
  --output /tmp/koma/koma-src/debian
```

In an isolated Debian/Ubuntu build environment with the verified archive unpacked
to `/tmp/koma/koma-src` and `rustup` toolchain active, build an unsigned binary
`.deb` with `dpkg-buildpackage -b -uc -us`. Neither rendering nor a `.deb`
artifact establishes acceptance before `v0.1.0`; see the
[release runbook](https://github.com/komawm/packaging/blob/main/docs/release.md)
and [CD matrix workflow](https://github.com/komawm/packaging/blob/main/.github/workflows/cd-release-matrix.yml).
