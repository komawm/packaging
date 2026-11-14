# Changelog

All notable changes to the Koma packaging repository are documented here. The
format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
versions follow the Koma `v0.0.x` development series until `v0.1.0` acceptance.

## [Unreleased]

### Added

- Multi-distribution unaccepted packaging templates (`debian/`, `rpm/`,
  `alpine/`, `nix/`, experimental `freebsd/`, and Linuxbrew `homebrew/`)
  alongside `archlinux/PKGBUILD.in`, with metadata in `packaging.toml`.
- Shared multi-distro template renderer (`scripts/render_template.py`, used by
  `scripts/render_arch.py`), manifest/checksum generator
  (`scripts/write_manifest.py`), guarded release asset stager
  (`scripts/stage_release.py`), and shared tag/download scripts
  (`scripts/resolve-tag.sh`, `scripts/download-source.sh`,
  `scripts/ci-rpm-in-container.sh`, `scripts/ci-alpine-in-container.sh`).
- Guarded multi-distro CD workflow (`.github/workflows/cd-release-matrix.yml`)
  with `workflow_dispatch`-only triggers, `publish: false` default, digest-pinned
  Fedora and Alpine containers, FreeBSD 15.1 opt-in VM packaging, and
  `PRODUCT_RELEASE_TOKEN`-gated `release` environment uploads.
- Expanded `scripts/verify.py` and `just check` coverage validating all distro
  templates, smoke-rendering outputs, shellcheck across `alpine/` and
  `scripts/`, and workflow security invariants across `.github/workflows/*.yml`.
- Updated `docs/release.md` with matrix workflow documentation and GitHub Actions
  Secrets / `release` environment variable scopes.
- Headless QEMU/KVM Arch Linux VM harness (`vm/arch-vm.sh`, `vm/guest-install.sh`)
  and package smoke test (`vm/koma-smoke.sh`), `just vm-help`, shellcheck of
  `vm/*.sh` in `just check`, and [VM testing](docs/vm-testing.md) documentation
  with the `v0.0.1` VM results.

### Fixed

- Arch template installs `koma-portals.conf` to `/usr/share/xdg-desktop-portal/`
  (matching Koma's session installer) and `greetd-config.toml` as a documentation
  example; found by the `v0.0.1` VM run, not yet in a built package.
- Arch template runtime dependencies now match the `v0.0.1` binaries (`seatd`
  provides `libseat`), and the build honors Koma's pinned `rust-toolchain.toml`
  through `rustup`; the empty `koma-debug` split package is disabled.

## [0.0.1] - 2026-11-14

### Added

- Manually dispatched `cd-archlinux.yml` workflow: builds the Arch Linux package
  for an existing product tag in a digest-pinned `archlinux:base-devel`
  container, records `SHA256SUMS-archlinux` and `PACKAGE-MANIFEST.toml`, and,
  only with `publish=true`, uploads to the product's existing GitHub Release via
  the protected `release` environment and `PRODUCT_RELEASE_TOKEN` secret.
- `[development_prerelease]` metadata in `packaging.toml` and matching
  validation in `scripts/verify.py` (dispatch-only trigger, SHA-pinned actions,
  read-only token permissions, release-environment publish job, no inline
  expressions in scripts, no committed secret values).
- `actionlint` in `just check`.
- Development prerelease CD, credential setup, dispatch, and verification steps
  in the release runbook.

### Unchanged

- Packages remain unaccepted (`accepted = false`); the first accepted version is
  `0.1.0`.
