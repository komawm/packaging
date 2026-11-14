# Alpine Linux template

[APKBUILD.in](https://github.com/komawm/packaging/blob/main/alpine/APKBUILD.in)
is an unaccepted Alpine Linux `x86_64` musl recipe built from an immutable
[Koma](https://github.com/komawm/koma) source commit and checksum.

## Musl and OpenRC specifics

- `RUSTFLAGS="-C target-feature=-crt-static"` is enabled because Koma uses
  dynamically loaded Wayland/EGL/Mesa drivers at runtime on musl.
- Systemd user units (`koma.service`, `koma-shutdown.target`) are intentionally
  omitted because Alpine runs OpenRC; `resources/koma-session` handles direct
  launch without `systemd --user`.
- `options="net"` is required during `prepare()`/`build()` so `cargo` with
  `--locked` can fetch Cargo crates and pinned Git dependencies (`phodopus`,
  `smithay`).
- Koma requires Rust `>= 1.100.0` (edition `2024`), so the
  [CD matrix workflow](https://github.com/komawm/packaging/blob/main/.github/workflows/cd-release-matrix.yml)
  uses a digest-pinned `alpine:edge` container rather than older stable
  repositories.

## Local rendering

```bash
python3 scripts/render_template.py \
  --distro alpine \
  --source-commit <40-hex-commit> \
  --source-sha256 <real-archive-sha256> \
  --version 0.0.1 \
  --output /tmp/koma/APKBUILD
```

Build in an isolated Alpine `abuild` environment with `abuild -r`; see the
[release runbook](https://github.com/komawm/packaging/blob/main/docs/release.md).
