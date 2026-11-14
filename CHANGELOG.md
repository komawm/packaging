# Changelog

All notable changes to the Koma packaging repository are documented here. The
format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
versions follow the Koma `v0.0.x` development series until `v0.1.0` acceptance.

## [Unreleased]

### Fixed

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
