# Homebrew formula template

[koma.rb.in](https://github.com/komawm/packaging/blob/main/homebrew/koma.rb.in)
is an unaccepted Homebrew formula template for
[Koma](https://github.com/komawm/koma).

## Platform scope

Koma's compositor backend targets Linux DRM/KMS, `libinput`, `seatd`, and
Wayland/Mesa (`depends_on :linux`). It is intended for Homebrew on Linux
(Linuxbrew) on `x86_64` and `aarch64` (`arm64`) hosts; macOS is not a compositor
target.

Building from source requires Rust `>= 1.100.0` (edition `2024`, pinned in
`rust-toolchain.toml`) and a committed `Cargo.lock` (`--locked`).

## Local rendering and validation

```bash
python3 scripts/render_template.py \
  --distro homebrew \
  --source-commit <40-hex-commit> \
  --source-sha256 <real-archive-sha256> \
  --version 0.0.1 \
  --output /tmp/koma/koma.rb
ruby -c /tmp/koma/koma.rb
```

Publishing rendered formulas to an external Homebrew tap repository after `v0.1.0`
acceptance is documented under optional channel credentials (`HOMEBREW_TAP_TOKEN`)
in the [release runbook](https://github.com/komawm/packaging/blob/main/docs/release.md).
