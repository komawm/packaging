# OpenBSD 8+ ports template

The [OpenBSD template directory](https://github.com/komawm/packaging/tree/main/openbsd)
provides `Makefile.in`, `distinfo.in`, `pkg/DESCR`, and `pkg/PLIST` targeting
**OpenBSD 8+ `amd64` and `aarch64`**.

## Portability and support status

OpenBSD packaging and runtime behavior are **unverified and experimental**
(`accepted = false`, `runtime_verified = false`). The pure-Rust control,
protocol, and overview crates (`koma-proto`, `komactl`, `koma-overview`) are
portable, whereas the full `koma` compositor depends on OpenBSD
Wayland/DRM/KMS and compatibility shims (`sysutils/seatd`,
`sysutils/libudev-openbsd`, `wayland/libinput-openbsd`).

Linux systemd user units (`koma.service`, `koma-shutdown.target`) are omitted;
`bin/koma-session` provides direct session startup fallback.

## Local rendering and CI validation

```bash
python3 scripts/render_template.py \
  --distro openbsd \
  --source-commit <40-hex-commit> \
  --source-sha256 <real-archive-sha256> \
  --version 0.0.1 \
  --output /tmp/koma/openbsd-port
```

For official OpenBSD ports tree submission, regenerate `distinfo` (`make makesum`)
and `crates.inc` (`make modcargo-gen-crates`) from the verified archive and
`Cargo.lock`. See the
[release runbook](https://github.com/komawm/packaging/blob/main/docs/release.md)
and [CD matrix workflow](https://github.com/komawm/packaging/blob/main/.github/workflows/cd-release-matrix.yml).
