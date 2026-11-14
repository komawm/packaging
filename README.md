# Koma packaging

This repository owns distribution packaging and release delivery for
[Koma](https://github.com/komawm/koma) and, when implemented and accepted,
[Jiku](https://github.com/komawm/jiku). It is one of the independent repositories
in the [Koma workspace](https://github.com/komawm/.github).

It contains unaccepted packaging templates for multi-architecture Linux
(`x86_64` and `aarch64` / `arm64`), Nix/NixOS, Homebrew on Linux, and
experimental BSD (`FreeBSD 15.1` and `OpenBSD 8.0`), deterministic template
renderers requiring an immutable source commit and real archive checksum,
metadata and safety checks, a CD acceptance runbook, and manually dispatched CD
workflows
([Arch Linux CD](https://github.com/komawm/packaging/blob/main/.github/workflows/cd-archlinux.yml)
and [multi-distro CD matrix](https://github.com/komawm/packaging/blob/main/.github/workflows/cd-release-matrix.yml))
for `v0.0.x` development prereleases. It contains no release artifacts, invented
checksums, automatic publishing, or claim of an accepted package; acceptance
starts at `v0.1.0`.

## Target architectures and platforms

| Channel | Architectures / Targets | Status | Notes |
| --- | --- | --- | --- |
| [Arch Linux](https://github.com/komawm/packaging/blob/main/archlinux/README.md) | `x86_64`, `aarch64` | `template-unaccepted` | Covers `x86_64` and Arch Linux ARM / Asahi Arch; `cd-archlinux.yml` builds `x86_64` |
| [Debian / Ubuntu](https://github.com/komawm/packaging/blob/main/debian/README.md) | `amd64`, `arm64` | `template-unaccepted` | Matrix builds native `.deb` packages on `ubuntu-24.04` and `ubuntu-24.04-arm` |
| [Fedora / RPM](https://github.com/komawm/packaging/blob/main/rpm/README.md) | `x86_64`, `aarch64` | `template-unaccepted` | Covers Fedora Workstation and Fedora Asahi Remix on Apple Silicon |
| [Alpine Linux](https://github.com/komawm/packaging/blob/main/alpine/README.md) | `x86_64`, `aarch64` | `template-unaccepted` | Native musl `.apk` matrix builds in digest-pinned `alpine:edge` containers |
| [Nix / NixOS](https://github.com/komawm/packaging/blob/main/nix/README.md) | `x86_64-linux`, `aarch64-linux` | `template-unaccepted` | Flake exports `packages.<system>.{koma,default}` and `checks.<system>.koma` for both |
| [Homebrew on Linux](https://github.com/komawm/packaging/blob/main/homebrew/README.md) | `x86_64`, `aarch64` (`depends_on :linux`) | `template-unaccepted` | CPU-agnostic Linuxbrew source formula; macOS is excluded |
| [FreeBSD](https://github.com/komawm/packaging/blob/main/freebsd/README.md) | `amd64`, `aarch64` (`FreeBSD 15+`) | `template-unaccepted-experimental` | `runtime_verified = false`; CI builds `FreeBSD 15.1 amd64` in QEMU VM |
| [OpenBSD](https://github.com/komawm/packaging/blob/main/openbsd/README.md) | `amd64`, `aarch64` (`OpenBSD 8+`) | `template-unaccepted-experimental` | `runtime_verified = false`; CI validates `OpenBSD 8.0 amd64` portable crates in VM |

### Apple Silicon (M1–M4) & Intel MacBook Linux notes

- **Apple Silicon M1–M4 (Linux)**: Target distributions include Fedora Asahi
  Remix (`aarch64` RPM), Arch Linux ARM / Asahi Arch (`aarch64` `PKGBUILD`),
  Debian/Ubuntu `arm64`, and NixOS on Apple Silicon (`aarch64-linux` flake
  outputs). Physical Apple Silicon DRM/KMS and Mesa/AGX GPU runtime behavior
  remain unverified until tested on bare-metal hardware.
- **Intel MacBooks (Linux)**: Supported via standard `x86_64` / `amd64` Linux
  targets (`x86_64` Arch, Fedora, Alpine, `x86_64-linux` NixOS, `amd64`
  Debian/Ubuntu).
- **macOS**: Not a Koma compositor target (`depends_on :linux` in Homebrew and
  `lib.platforms.linux` in Nix).

## Workflows and local usage

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
[OpenBSD template](https://github.com/komawm/packaging/blob/main/openbsd/README.md),
[Homebrew template](https://github.com/komawm/packaging/blob/main/homebrew/README.md),
[release and credential boundaries](https://github.com/komawm/packaging/blob/main/docs/release.md),
and [agent guidance](https://github.com/komawm/packaging/blob/main/AGENTS.md).

Packaging scripts and configuration are MIT licensed. Packaged Koma/Jiku source
and binary licensing remains governed by the product repositories; this license
does not relicense them. See [LICENSE](https://github.com/komawm/packaging/blob/main/LICENSE)
and [Koma's license](https://github.com/komawm/koma/blob/main/LICENSE).
