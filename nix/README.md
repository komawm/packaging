# Nix / NixOS template

The [Nix template directory](https://github.com/komawm/packaging/tree/main/nix)
provides `koma.nix.in` and `flake.nix.in` for evaluating and building
[Koma](https://github.com/komawm/koma) from an immutable commit and tarball
checksum without inventing hashes.

## Upstream `flake.nix` status

[komawm/koma](https://github.com/komawm/koma) does not currently ship a root
`flake.nix`. This directory provides an out-of-tree flake template pinned to an
immutable `@SOURCE_COMMIT@` (`koma-src.flake = false`) and a standalone
`koma.nix` derivation verifying `@SOURCE_SHA256@`. When an upstream root
`flake.nix` is added to `komawm/koma`, `packaging` can track pinned `flake.lock`
inputs directly.

Because `Cargo.toml` references Git dependencies (`phodopus`, `smithay`),
`koma.nix.in` sets `cargoLock.allowBuiltinFetchGit = true` and honors the
committed `Cargo.lock` with `--locked`. `RUSTFLAGS` retains `libEGL`,
`libwayland-client`, and `libwayland-server` in `RPATH` for runtime `dlopen()`.

## Rendering and Cachix notes

```bash
python3 scripts/render_template.py \
  --distro nix \
  --source-commit <40-hex-commit> \
  --source-sha256 <real-archive-sha256> \
  --version 0.0.1 \
  --output /tmp/koma/nix-candidate
```

Optional Cachix binary cache pushes after future `v0.1.0` acceptance require an
environment secret `CACHIX_AUTH_TOKEN` in the protected `release` environment;
see the [release runbook](https://github.com/komawm/packaging/blob/main/docs/release.md)
and [CD matrix workflow](https://github.com/komawm/packaging/blob/main/.github/workflows/cd-release-matrix.yml).
