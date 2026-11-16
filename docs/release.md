# Packaging and CD acceptance

This repository owns distribution/CD delivery for Koma and accepted companion
components. The local initialization prepares checks, immutable-source template
rendering, a manually dispatched Arch Linux CD workflow for development
prereleases, and this runbook. Nothing publishes automatically. The Arch
package is not accepted; Jiku's daemon is not ready to package.

## From development to an accepted package

1. Select a reviewed product revision and development `0.0.x` version. Resolve
   source compilation and dependency blockers first; retain the committed lockfile.
2. Download its exact source archive in an authorized build task and calculate
   the actual SHA-256. Pin the full immutable commit and record archive provenance.
   Never substitute a branch name, `SKIP`, or invented checksum.
3. Render a review candidate with explicit inputs into a new disposable file.
   Review dependency names, resource paths, license/source notices, and package
   contents against that exact source revision.
4. Build in an isolated target distribution VM or clean build environment. Record
   build inputs, compiler and dependency versions, exit status, logs, and artifact
   checksums. Syntax validation and rendering do not replace a build.
5. Exercise install, launch in the selected nested/backend setup, shutdown,
   removal, upgrade, and rollback. Preserve the host Hyprland session and boot
   configuration. Keep VM images and evidence in ignored local storage.
6. Obtain independent review. Before `0.1.0`, establish the selected supported
   platform/backend matrix, runtime security boundaries, reproducibility,
   configuration/IPC compatibility, consumer documentation, and known limits.
7. Only after an explicitly authorized release task should a publishing workflow
   upload accepted artifacts, checksums, manifests, and required license/source
   materials. Publish the exact reviewed source revision and rerun required checks.

The initial template targets an Arch Linux x86_64 review candidate. Jiku
delivery becomes a task only after its runtime exists and is accepted.

## Local workflow

`just check` runs `scripts/verify.py` (TOML, Python AST, canonical links, secret
scan, placeholder/license validation, disposable smoke rendering across all
target distros, nFPM/APT/Dependabot validation, syntax checks, and CD workflow
invariants), `shellcheck` across shell templates, `apt/setup.sh`, CI container
scripts, and `vm/*.sh`, and `actionlint` across `.github/workflows/*.yml`.
[ci.yml](https://github.com/komawm/packaging/blob/main/.github/workflows/ci.yml)
runs these same checks on pushes and pull requests to `main`.

## Development prerelease CD (`v0.0.x`)

Three manually dispatched (`workflow_dispatch`-only) workflows handle builds
and guarded publishing:
[cd-archlinux.yml](https://github.com/komawm/packaging/blob/main/.github/workflows/cd-archlinux.yml),
[cd-release.yml](https://github.com/komawm/packaging/blob/main/.github/workflows/cd-release.yml),
and [cd-release-matrix.yml](https://github.com/komawm/packaging/blob/main/.github/workflows/cd-release-matrix.yml).
There is no push, tag, or schedule trigger for publishing. A development
prerelease is not acceptance: `accepted = false` and
`first_accepted_version = "0.1.0"` stay in
[packaging.toml](https://github.com/komawm/packaging/blob/main/packaging.toml).

### Arch Linux workflow (`cd-archlinux.yml`)

| Input | Meaning |
| --- | --- |
| `product` | Product repository under `komawm` (`koma` or `jiku`) |
| `tag` | Existing product tag such as `v0.0.1`; must match `v0.MINOR.PATCH` |
| `publish` | `false` (default) builds only; `true` also runs the `publish` job |

The `build` job runs in a digest-pinned `archlinux:base-devel` container with
read-only token permissions. It resolves the tag to a full 40-character commit
(dereferencing annotated tags), downloads
`https://github.com/komawm/<product>/archive/<commit>.tar.gz`, computes its
SHA-256, renders `archlinux/PKGBUILD.in` (`koma`) or
`archlinux/PKGBUILD.jiku.in` (`jiku`) with `scripts/render_arch.py --product <product>`,
and runs `makepkg --syncdeps --noconfirm` as an unprivileged `builder` user.
`namcap` output is logged but non-fatal. The `archlinux-<product>-package`
workflow artifact holds the package, rendered `PKGBUILD`, `namcap.log`,
`SHA256SUMS-archlinux`, and `PACKAGE-MANIFEST.toml` (product, tag, commit,
source SHA-256, package filename and SHA-256, build time, container digest,
packaging commit, run URL).

### Unified R2 CDN & nFPM workflow (`cd-release.yml`)

| Input | Meaning |
| --- | --- |
| `product` | Product repository under `komawm` (`koma` or `jiku`) |
| `tag` | Existing product tag matching `v[0-9]+.[0-9]+.[0-9]+` (optional `-pre` suffix) |
| `channel` | Release channel (`dev` default, or `stable`) |
| `publish_r2` | `false` (default) skips upload; `true` uploads release bundles, pointers, and APT metadata to Cloudflare R2 (`https://cdn.komawm.org`) |
| `publish_github` | `false` (default) skips GitHub Release upload; `true` attaches `.tar.zst`, `.deb`, `.rpm`, `.apk`, `.pkg.tar.zst`, and `SHA256SUMS-linux-packages` to `komawm/<product>` |
| `publish_distros` | `false` (default) skips AUR/Copr/Cachix sync; `true` (on `channel=stable`) updates AUR `<product>-bin`, triggers Fedora Copr, and prepares Nix Cachix push when upstream carries `flake.nix` |

The workflow compiles Linux binaries across `x86_64` (`ubuntu-24.04`) and
`aarch64` (`ubuntu-24.04-arm`), builds native Arch `.pkg.tar.zst` in
`archlinux:base-devel`, packages `.deb`, `.rpm`, and `.apk` via
[nfpm/koma.yaml](https://github.com/komawm/packaging/blob/main/nfpm/koma.yaml)
and [nfpm/jiku.yaml](https://github.com/komawm/packaging/blob/main/nfpm/jiku.yaml),
and aggregates SHA-256 manifests.

#### Cloudflare R2 CDN layout (`https://cdn.komawm.org`, bucket `komawm`)

- `releases/<product>/<tag>/`: immutable release tarballs, `.deb`, `.rpm`,
  `.apk`, `.pkg.tar.zst`, and sorted `SHA256SUMS` (`max-age=31536000, immutable`).
- `install/<product>/latest.txt` (`channel=stable`) and
  `install/<product>/latest-dev.txt` (`channel=dev`): short-TTL (`max-age=300`)
  tag pointers.
- `apt/`: GPG-signed deb822 APT repository refreshed on `channel=stable` when
  `APT_GPG_PRIVATE_KEY` is configured (`apt/komawm-archive-keyring.gpg`,
  `apt/komawm.sources`, `apt/setup.sh`, `apt/pool/main/*.deb` preserving both
  `koma` and `jiku` `.deb` packages, and `apt/dists/stable/...` signed with
  `Release.gpg` + `InRelease`).

#### Nix `flake.nix` delegation

`komawm/koma` owns any upstream root `flake.nix`. In `cd-release.yml`,
`resolve` checks whether `komawm/<product>@<commit>` ships a root `flake.nix`
(`has_flake`); `packaging` delegates Nix/Cachix binary cache pushes to that
upstream flake while retaining out-of-tree template evaluation in
`cd-release-matrix.yml` (`validate-nix`).

#### npm distribution (`@koma-wm/jiku`)

`cd-release.yml` can publish Jiku to the npm registry in addition to R2 and
GitHub Releases. The job is `publish-npm`, gated on `publish=true`,
`publish_npm=true`, and `product=jiku`, and it runs under the same protected
`release` environment.

Three packages are produced. Metadata is generated by
[`scripts/stage_npm.py`](https://github.com/komawm/packaging/blob/main/scripts/stage_npm.py)
from the artifacts the build jobs already checksummed; nothing is hand-maintained
in the Jiku repository:

| Package | Contents |
| --- | --- |
| `@koma-wm/jiku` | Launcher (`bin/jiku.js`, `bin/jikud.js`), bundled agent skill, `optionalDependencies` on the platform packages |
| `@koma-wm/jiku-linux-x64` | `jiku` and `jikud` for `x86_64-unknown-linux-gnu` |
| `@koma-wm/jiku-linux-arm64` | `jiku` and `jikud` for `aarch64-unknown-linux-gnu` |

Design notes:

- The scope is `@koma-wm` because `komawm` is already registered on the npm
  registry.
- Platform packages are selected through `os`, `cpu`, and `libc` fields rather
  than a postinstall download, so installation is offline-safe and reproducible.
- Each platform archive is verified against `SHA256SUMS-<arch>` before its
  binaries are extracted. A mismatch aborts the job.
- The launcher bundles `skills/jiku-desktop-orchestration` only when the tagged
  revision contains it. `v0.0.1` predates the skill catalog, so that package ships
  no skill; a present-but-broken skill directory aborts the stage instead of shipping
  half a skill, and the skill's MIT `LICENSE` is asserted to travel with it.
- `publishConfig.provenance` is enabled and the job publishes with
  `--provenance`. Because provenance binds to the workflow repository, the
  generated `repository` field points at `komawm/packaging` with
  `directory: npm/jiku`; `homepage` and `bugs` point at `komawm/jiku`.
- Development prereleases publish under the `development` dist-tag
  (`channel=development`); `channel=stable` publishes `latest`.
- Republishing an existing version is a no-op, not a failure, so a partially
  failed run can be re-dispatched.

Verification after dispatch:

```bash
npm view @koma-wm/jiku version dist-tags --registry https://registry.npmjs.org
npm view @koma-wm/jiku@0.0.1 dependencies optionalDependencies
npm pack @koma-wm/jiku-linux-x64@0.0.1 --pack-destination /tmp/koma/npm-verify
tar -tzf /tmp/koma/npm-verify/koma-wm-jiku-linux-x64-0.0.1.tgz
```

Local dry run without credentials (stage the trees, then pack them):

```bash
just stage-npm 0.0.1 /tmp/koma/npm-artifacts /path/to/jiku-source /tmp/koma/npm-out
just pack-npm /tmp/koma/npm-out
```

Installing the package does not install Koma, does not start a session, and does
not establish that `jiku status` works. npm distribution is packaging evidence
only.

### Multi-distro matrix workflow (`cd-release-matrix.yml`)

| Input | Meaning |
| --- | --- |
| `product` | Product repository under `komawm` (`koma`) |
| `tag` | Existing product tag matching `v0.MINOR.PATCH` |
| `channel` | Target channel group: `linux` (default), `all` (includes experimental `freebsd` and `openbsd`), or a single channel (`debian`, `rpm`, `alpine`, `nix`, `freebsd`, `openbsd`, `homebrew`) |
| `publish` | `false` (default) builds/validates only; `true` runs `publish` under `environment: release` |

The `prepare` job resolves `tag` to an immutable 40-character commit via
`scripts/resolve-tag.sh`, downloads the source tarball once via
`scripts/download-source.sh`, and exports `version`, `commit`, and
`source_sha256` to channel jobs:

- `build-debian` (`ubuntu-24.04` `amd64` and `ubuntu-24.04-arm` `arm64`):
  installs C/Wayland apt deps and the pinned `rust-toolchain.toml` Rust
  compiler, renders `debian/`, builds unsigned `.deb` with
  `dpkg-buildpackage -b -uc -us`, inspects via `dpkg-deb`, and writes
  `SHA256SUMS-debian-<arch>` + `PACKAGE-MANIFEST.toml`.
- `build-rpm` (`ubuntu-24.04` `x86_64` and `ubuntu-24.04-arm` `aarch64` ->
  multi-arch digest-pinned `fedora:43` container): renders `rpm/koma.spec.in`
  and runs `scripts/ci-rpm-in-container.sh` as unprivileged `builder` (`-ba` on
  `x86_64` for binary + `.src.rpm`, `-bb` on `aarch64` for binary `.aarch64.rpm`).
- `build-alpine` (`ubuntu-24.04` `x86_64` and `ubuntu-24.04-arm` `aarch64` ->
  multi-arch digest-pinned `alpine:edge` container): renders `alpine/APKBUILD.in`,
  runs `scripts/ci-alpine-in-container.sh` as unprivileged `builder`
  (`abuild -r`) with `-C target-feature=-crt-static`, and suffixes `.apk`
  outputs by architecture (`<pkg>.<arch>.apk`).
- `validate-nix` (`ubuntu-24.04`): renders `nix/`, parses `.nix` files with
  `nix-instantiate --parse`, checks `nix flake metadata`, and evaluates the
  derivation graph for both `x86_64-linux` and `aarch64-linux`
  (`packages.<system>.koma.drvPath` and `checks.<system>.koma.drvPath`).
- `validate-homebrew` (`ubuntu-24.04`): renders `homebrew/koma.rb.in`, runs
  `ruby -c`, and runs `brew style` when Linuxbrew is present.
- `build-freebsd` (`ubuntu-24.04` -> `cross-platform-actions/action` FreeBSD
  `15.1` `amd64` VM, opt-in via `channel=all` or `channel=freebsd`): builds with
  `cargo build --release --locked` and stages a `.pkg` archive with
  `pkg create -M pkg-manifest.ucl`, writing `SHA256SUMS-freebsd-amd64`. FreeBSD
  runtime behavior remains unverified/experimental (`runtime_verified = false`).
- `validate-openbsd` (`ubuntu-24.04` -> `cross-platform-actions/action` OpenBSD
  `8.0` `amd64` VM, opt-in via `channel=all` or `channel=openbsd`): renders the
  OpenBSD port templates inside the OpenBSD guest, verifies the source checksum
  and `Cargo.lock`, builds portable workspace crates (`koma-proto`, `komactl`,
  `koma-mcp`, `koma-overview`, plus `koma` when all OpenBSD Wayland/seat ports
  build), and packages an experimental tarball with `SHA256SUMS-openbsd-amd64`.
  OpenBSD runtime behavior remains unverified/experimental
  (`runtime_verified = false`).

Each channel writes `SHA256SUMS-<distro>` and `PACKAGE-MANIFEST.toml` through
`scripts/write_manifest.py`.

The `publish` job in either workflow runs only when `publish` is `true` on manual
`workflow_dispatch`. It runs under the protected `release` environment,
verifies `PRODUCT_RELEASE_TOKEN`, re-checks checksums and manifest identity
(`scripts/stage_release.py` in the matrix workflow), confirms that
`komawm/<product>` has a release for that exact tag pointing to the built
commit, then uploads assets (`--clobber`) with distribution-suffixed manifest
and checksum filenames (`PACKAGE-MANIFEST-<distro>.toml`, `SHA256SUMS-<distro>`)
so product release files are never clobbered.

The product repository's own tag workflow creates the release (for Koma,
`koma-<version>-x86_64-unknown-linux-gnu.tar.gz` and `SHA256SUMS`). Dispatch
packaging CD only after that release exists.

### GitHub Actions Secrets and `release` environment variables

All credentials MUST be scoped centrally to the protected `release` environment
in `komawm/packaging` (shared across `koma` and `jiku` CD workflows) and MUST NOT
be passed to build or validation jobs.

| Secret / Variable | Scope | Purpose | Status in `v0.0.x` |
| --- | --- | --- | --- |
| `CLOUDFLARE_ACCOUNT_ID` (Secret) | `release` environment | Cloudflare account ID for R2 bucket `komawm` (`https://cdn.komawm.org`) uploads via Wrangler in `cd-release.yml` | **Required when `publish_r2=true`** |
| `CLOUDFLARE_API_TOKEN` (Secret) | `release` environment | Scoped Cloudflare R2 Object Write API token for bucket `komawm` | **Required when `publish_r2=true`** |
| `PRODUCT_RELEASE_TOKEN` (Secret) | `release` environment | Fine-grained PAT or GitHub App token with `Contents: Read and write` on `komawm/koma` and `komawm/jiku` for `gh release upload` | **Required when `publish=true` or `publish_github=true`** |
| `GH_PAT` (Secret) | `release` environment | Optional fallback cross-repo release upload token accepted by `cd-release.yml` when `PRODUCT_RELEASE_TOKEN` is unset | Optional fallback (`publish_github=true`) |
| `APT_GPG_PRIVATE_KEY` (Secret) | `release` environment | Armored PGP private signing key for signing R2 APT `Release.gpg`, `InRelease`, and exporting `komawm-archive-keyring.gpg` | Optional on `channel=stable` / Post-`v0.1.0` |
| `AUR_SSH_PRIVATE_KEY` (Secret) | `release` environment | SSH deploy key for pushing `koma-bin` / `jiku-bin` `PKGBUILD` updates to `aur.archlinux.org` | Optional on `channel=stable` / Post-`v0.1.0` |
| `COPR_CONFIG` (Secret) | `release` environment | Fedora Copr API token INI configuration (`~/.config/copr`) for submitting `komawm/<product>` SCM builds | Optional on `channel=stable` / Post-`v0.1.0` |
| `CACHIX_AUTH_TOKEN` (Secret) | `release` environment | Cachix upload token for pushing Nix derivation closures when upstream `komawm/<product>` ships `flake.nix` | Reserved for upstream `flake.nix` / Post-`v0.1.0` |
| `HOMEBREW_TAP_TOKEN` (Secret) | `release` environment | Fine-grained token scoped strictly to `komawm/homebrew-tap` to update accepted formula commits | Reserved for post-`v0.1.0` acceptance |
| `NPM_TOKEN` (Secret) | `release` environment | npm automation token with publish rights on the `@koma-wm` scope, used by the `publish-npm` job in `cd-release.yml` | **Required when `publish_npm=true`** |
| `KOMA_COPR_PROJECT`, `KOMA_APT_SUITE`, `KOMA_CACHIX_CACHE`, `KOMA_HOMEBREW_TAP_REPO` (Environment Variables) | `release` environment | Non-secret target coordinates for optional downstream channel publishing | Optional |

Unaccepted `v0.0.x` builds intentionally restrict publishing to `komawm/<product>`
GitHub Releases via `PRODUCT_RELEASE_TOKEN` and never push to external package
indexes (AUR, public APT, COPR, Homebrew taps, or binary caches) prior to
`v0.1.0` acceptance.

### One-time credential setup

The built-in `GITHUB_TOKEN` of this repository cannot write Koma or Jiku
Releases, so the publish job needs a separate credential:

1. Create a credential limited to the product repository:
   - Fine-grained personal access token: resource owner `komawm`, repository
     access *Only select repositories* → `komawm/koma` (add `komawm/jiku` only
     once Jiku is accepted), repository permission **Contents: Read and write**
     (Metadata: read is implied), with a short expiry; or
   - A GitHub App installed only on `komawm/koma` with **Contents: Read and
     write**, whose installation token is supplied as the secret.
2. In `komawm/packaging` → Settings → Environments, create the `release`
   environment. Optionally add required reviewers, disallow self-review, and
   restrict deployment branches to `main`.
3. Add the credential as environment secret `PRODUCT_RELEASE_TOKEN` in that
   environment (not as a repository secret), for example:

   ```bash
   gh secret set PRODUCT_RELEASE_TOKEN --env release --repo komawm/packaging
   ```

   Paste the value at the prompt; never write it to a file or shell history.
4. Rotate it before expiry and revoke it immediately if exposed.

### Dispatch and verification

Build only (no credential needed):

```bash
gh workflow run cd-archlinux.yml -f product=koma -f tag=v0.0.1 -f publish=false --repo komawm/packaging
```

Build and publish to the existing Koma `v0.0.1` release:

```bash
gh workflow run cd-archlinux.yml -f product=koma -f tag=v0.0.1 -f publish=true --repo komawm/packaging
gh run list --workflow cd-archlinux.yml --repo komawm/packaging --limit 1
gh run watch <run-id> --repo komawm/packaging
```

Publish Jiku to npm as well (Linux platform packages plus the launcher):

```bash
gh workflow run cd-release.yml -f product=jiku -f tag=v0.0.1 -f channel=development \
  -f publish=true -f publish_r2=true -f publish_github=true -f publish_npm=true \
  --repo komawm/packaging
```

Approve the `release` deployment if reviewers are required. Then read back:

```bash
gh release view v0.0.1 --repo komawm/koma --json assets --jq '.assets[].name'
gh release download v0.0.1 --repo komawm/koma --pattern 'koma-*.pkg.tar.zst' \
  --pattern SHA256SUMS-archlinux --pattern PACKAGE-MANIFEST-archlinux.toml --dir /tmp/koma/verify
cd /tmp/koma/verify && sha256sum --check SHA256SUMS-archlinux
```

Compare `commit` in the manifest with `git ls-remote https://github.com/komawm/koma refs/tags/v0.0.1^{}`
(or the tag ref itself for lightweight tags). Installation of the package belongs
in an isolated Arch VM, never on the host Hyprland session. A successful CD run
is build evidence only; install/upgrade/rollback acceptance remains separate.

## Product release uploads

Use the mapping in [packaging.toml](https://github.com/komawm/packaging/blob/main/packaging.toml):

| Artifacts | GitHub Releases destination |
| --- | --- |
| Accepted Koma build | `komawm/koma` |
| Future accepted Jiku build | `komawm/jiku` |

The development prerelease CD above uploads to the existing product release
only on explicit manual dispatch. Record the exact product revision and
distribution target alongside those files (the manifest does this). Never
publish a failed or incomplete build; the publish job depends on a successful
build job.

A workflow's built-in `GITHUB_TOKEN` is limited to the repository containing
that workflow. The packaging repository's token cannot upload to Koma or Jiku
Releases. Cross-repository CD needs a GitHub App installation token or a
fine-grained personal access token restricted to the target product repository
with `Contents: write`. Keep credentials in the authorized publishing
environment and never store token values in this repository. See GitHub's
[GITHUB_TOKEN documentation](https://docs.github.com/en/actions/concepts/security/github_token).

The publish job's upload is equivalent to:

```bash
gh release upload "$TAG" "$PKG_FILE" SHA256SUMS-archlinux PACKAGE-MANIFEST-archlinux.toml \
  --repo "komawm/$PRODUCT" --clobber
```

See the [GitHub CLI upload reference](https://cli.github.com/manual/gh_release_upload).

## Licensing

Packaging infrastructure is MIT. Koma/Jiku product source and binaries retain
their own product licenses. Preserve GPL notices and corresponding-source
requirements, dependencies' license notices, and Phodopus attribution in the
accepted artifact/source distribution. The packaging license never relicenses
bundled products or external dependencies.
