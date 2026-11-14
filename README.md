# Koma packaging

This repository owns distribution packaging and release delivery for
[Koma](https://github.com/komawm/koma) and, when implemented and accepted,
[Jiku](https://github.com/komawm/jiku). It is one of the independent repositories
in the [Koma workspace](https://github.com/komawm/.github).

It contains unaccepted packaging templates for Arch Linux, Debian/Ubuntu, Fedora
RPM, Alpine Linux, Nix/NixOS, FreeBSD 15+ (experimental), and Homebrew on Linux,
deterministic template renderers requiring an immutable source commit and real
archive checksum, metadata and safety checks, a CD acceptance runbook, and
manually dispatched CD workflows
([Arch Linux CD](https://github.com/komawm/packaging/blob/main/.github/workflows/cd-archlinux.yml)
and [multi-distro CD matrix](https://github.com/komawm/packaging/blob/main/.github/workflows/cd-release-matrix.yml))
for `v0.0.x` development prereleases. It contains no release artifacts, invented
checksums, automatic publishing, or claim of an accepted package; acceptance
starts at `v0.1.0`.

The workflows build packages from an existing product tag and, only with
`publish=true`, the protected `release` environment, and the
`PRODUCT_RELEASE_TOKEN` environment secret, attach them to that product's
existing release:

```bash
gh workflow run cd-archlinux.yml -f product=koma -f tag=v0.0.1 -f publish=true --repo komawm/packaging
gh workflow run cd-release-matrix.yml -f product=koma -f tag=v0.0.1 -f channel=linux -f publish=false --repo komawm/packaging
```

Credential setup and verification steps are in the
[release runbook](https://github.com/komawm/packaging/blob/main/docs/release.md#development-prerelease-cd-v00x).

Checks run locally. This repository owns CD; Koma build artifacts
belong in [Koma Releases](https://github.com/komawm/koma/releases), and accepted
future Jiku artifacts belong in [Jiku Releases](https://github.com/komawm/jiku/releases).
The release target mapping lives in
[packaging.toml](https://github.com/komawm/packaging/blob/main/packaging.toml).

Run `just check` to validate the repository and `just hooks-install` to compose
the local gate with CarryCtx hooks. Rendering is an explicit local operation:

```bash
python3 scripts/render_arch.py --help
python3 scripts/render_template.py --help
```

The renderer writes only a new explicitly named output file or directory and
refuses existing destinations. It does not download, build, install, or publish
anything. See the
[changelog](https://github.com/komawm/packaging/blob/main/CHANGELOG.md),
[Arch template](https://github.com/komawm/packaging/blob/main/archlinux/README.md),
[Debian template](https://github.com/komawm/packaging/blob/main/debian/README.md),
[RPM template](https://github.com/komawm/packaging/blob/main/rpm/README.md),
[Alpine template](https://github.com/komawm/packaging/blob/main/alpine/README.md),
[Nix template](https://github.com/komawm/packaging/blob/main/nix/README.md),
[FreeBSD template](https://github.com/komawm/packaging/blob/main/freebsd/README.md),
[Homebrew template](https://github.com/komawm/packaging/blob/main/homebrew/README.md),
[release and credential boundaries](https://github.com/komawm/packaging/blob/main/docs/release.md),
and [agent guidance](https://github.com/komawm/packaging/blob/main/AGENTS.md).

Packaging scripts and configuration are MIT licensed. Packaged Koma/Jiku source
and binary licensing remains governed by the product repositories; this license
does not relicense them. See [LICENSE](https://github.com/komawm/packaging/blob/main/LICENSE)
and [Koma's license](https://github.com/komawm/koma/blob/main/LICENSE).
