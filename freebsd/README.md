# FreeBSD 15+ ports/pkg template

The [FreeBSD template directory](https://github.com/komawm/packaging/tree/main/freebsd)
provides `Makefile.in`, `distinfo.in`, `pkg-manifest.ucl.in`, `pkg-descr`, and
`pkg-plist` targeting **FreeBSD 15+ `amd64` and `aarch64`** (with CI VM
packaging currently targeting `15.1 amd64`).

## Portability and support status

FreeBSD packaging and runtime behavior are **unverified and experimental**
(`accepted = false`, `runtime_verified = false`). Koma's compositor backend uses
Smithay DRM/KMS/libinput/seatd interfaces that rely on FreeBSD compatibility
shims (`sysutils/seatd`, `devel/libudev-devd`). Isolated
FreeBSD 15.1 VM build and unit verification belongs in a follow-up VM harness
task, not in this template commit alone.

Linux systemd user units (`koma.service`, `koma-shutdown.target`) are omitted;
`bin/koma-session` provides direct session startup fallback.

## Ports vs. staged `pkg create`

- **Ports tree**: render this directory into a ports overlay (`x11-wm/koma`)
  and run `make makesum` (to populate archive `SIZE` alongside the pinned
  `SHA256` in `distinfo`), then `make stage check-plist package`.
- **Standalone CI build**: build `cargo build --release --locked` with FreeBSD
  packages (`pkgconf`, `rust`, `libudev-devd`, `libinput`, `seatd`, `mesa-libs`,
  `libdrm`, `wayland`, `libxkbcommon`, `dbus`, `fontconfig`),
  stage into `PREFIX=/usr/local` matching `pkg-plist`, and package via
  `pkg create -M pkg-manifest.ucl -p pkg-plist`.

```bash
python3 scripts/render_template.py \
  --distro freebsd \
  --source-commit <40-hex-commit> \
  --source-sha256 <real-archive-sha256> \
  --version 0.0.1 \
  --output /tmp/koma/freebsd-port
```

See the [release runbook](https://github.com/komawm/packaging/blob/main/docs/release.md)
and [CD matrix workflow](https://github.com/komawm/packaging/blob/main/.github/workflows/cd-release-matrix.yml).
