# Koma packaging

This repository owns distribution packaging and release delivery for
[Koma](https://github.com/komawm/koma) and, when implemented and accepted,
[Jiku](https://github.com/komawm/jiku). It is one of the independent repositories
in the [Koma workspace](https://github.com/komawm/.github).

The initial local setup contains an unaccepted Arch Linux packaging template,
a renderer requiring an immutable source commit and real archive checksum,
metadata and safety checks, and a CD acceptance runbook. It contains no release
artifacts, invented checksums, automatic publishing, or claim of a working
package. Existing product compilation/runtime blockers must be resolved before
an installation or release can be accepted.

Checks run locally. This repository prepares CD; accepted Koma build artifacts
belong in [Koma Releases](https://github.com/komawm/koma/releases), and accepted
future Jiku artifacts belong in [Jiku Releases](https://github.com/komawm/jiku/releases).
The release target mapping lives in
[packaging.toml](https://github.com/komawm/packaging/blob/main/packaging.toml).

Run `just check` to validate the repository and `just hooks-install` to compose
the local gate with CarryCtx hooks. Rendering is an explicit local operation:

```bash
python3 scripts/render_arch.py --help
```

The renderer writes only a new explicitly named output file and refuses existing
destinations. It does not download, build, install, or publish anything. See
[Arch template guidance](https://github.com/komawm/packaging/blob/main/archlinux/README.md),
[release and credential boundaries](https://github.com/komawm/packaging/blob/main/docs/release.md),
and [agent guidance](https://github.com/komawm/packaging/blob/main/AGENTS.md).

Packaging scripts and configuration are MIT licensed. Packaged Koma/Jiku source
and binary licensing remains governed by the product repositories; this license
does not relicense them. See [LICENSE](https://github.com/komawm/packaging/blob/main/LICENSE)
and [Koma's license](https://github.com/komawm/koma/blob/main/LICENSE).
