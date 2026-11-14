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
target distros, syntax checks, and CD workflow invariants), `shellcheck` across
shell templates, CI container scripts, and `vm/*.sh`, and `actionlint` across
`.github/workflows/*.yml`. This repository does not require generic CI, CodeQL,
bot review, or an Issue/PR workflow.

## Development prerelease CD (`v0.0.x`)

Both [cd-archlinux.yml](https://github.com/komawm/packaging/blob/main/.github/workflows/cd-archlinux.yml)
and [cd-release-matrix.yml](https://github.com/komawm/packaging/blob/main/.github/workflows/cd-release-matrix.yml)
build packages for an existing product development tag and can attach them to
that product's existing GitHub Release. Both are `workflow_dispatch` only; there
is no push, tag, or schedule trigger. A development prerelease is not
acceptance: `accepted = false` and `first_accepted_version = "0.1.0"` stay in
[packaging.toml](https://github.com/komawm/packaging/blob/main/packaging.toml).

### Arch Linux workflow (`cd-archlinux.yml`)

| Input | Meaning |
| --- | --- |
| `product` | Product repository under `komawm` (currently only `koma`) |
| `tag` | Existing product tag such as `v0.0.1`; must match `v0.MINOR.PATCH` |
| `publish` | `false` (default) builds only; `true` also runs the `publish` job |

The `build` job runs in a digest-pinned `archlinux:base-devel` container with
read-only token permissions. It resolves the tag to a full 40-character commit
(dereferencing annotated tags), downloads
`https://github.com/komawm/<product>/archive/<commit>.tar.gz`, computes its
SHA-256, renders the PKGBUILD with `scripts/render_arch.py`, and runs
`makepkg --syncdeps --noconfirm` as an unprivileged `builder` user. `namcap`
output is logged but non-fatal. The `archlinux-package` workflow artifact holds
the package, rendered `PKGBUILD`, `namcap.log`, `SHA256SUMS-archlinux`, and
`PACKAGE-MANIFEST.toml` (product, tag, commit, source SHA-256, package filename
and SHA-256, build time, container digest, packaging commit, run URL).

### Multi-distro matrix workflow (`cd-release-matrix.yml`)

| Input | Meaning |
| --- | --- |
| `product` | Product repository under `komawm` (`koma`) |
| `tag` | Existing product tag matching `v0.MINOR.PATCH` |
| `channel` | Target channel group: `linux` (default), `all` (includes experimental `freebsd`), or a single channel (`debian`, `rpm`, `alpine`, `nix`, `freebsd`, `homebrew`) |
| `publish` | `false` (default) builds/validates only; `true` runs `publish` under `environment: release` |

The `prepare` job resolves `tag` to an immutable 40-character commit via
`scripts/resolve-tag.sh`, downloads the source tarball once via
`scripts/download-source.sh`, and exports `version`, `commit`, and
`source_sha256` to channel jobs:

- `build-debian` (`ubuntu-24.04`): installs C/Wayland apt deps and the pinned
  `rust-toolchain.toml` Rust compiler, renders `debian/`, builds unsigned `.deb`
  with `dpkg-buildpackage -b -uc -us`, inspects via `dpkg-deb`, and writes
  `SHA256SUMS-debian` + `PACKAGE-MANIFEST.toml`.
- `build-rpm` (`ubuntu-24.04` -> digest-pinned `fedora:43` container): renders
  `rpm/koma.spec.in` and runs `scripts/ci-rpm-in-container.sh` as unprivileged
  `builder` to produce `.x86_64.rpm` and `.src.rpm`.
- `build-alpine` (`ubuntu-24.04` -> digest-pinned `alpine:edge` container):
  renders `alpine/APKBUILD.in` and runs `scripts/ci-alpine-in-container.sh` as
  unprivileged `builder` (`abuild -r`) with `-C target-feature=-crt-static`.
- `validate-nix` (`ubuntu-24.04`): renders `nix/`, parses `.nix` files with
  `nix-instantiate --parse`, checks `nix flake metadata`, and evaluates the
  derivation graph (`nix eval .#packages.x86_64-linux.koma.drvPath`).
- `validate-homebrew` (`ubuntu-24.04`): renders `homebrew/koma.rb.in`, runs
  `ruby -c`, and runs `brew style` when Linuxbrew is present.
- `build-freebsd` (`ubuntu-24.04` -> `cross-platform-actions/action` FreeBSD
  `15.1` `amd64` VM, opt-in via `channel=all` or `channel=freebsd`): builds with
  `cargo build --release --locked` and stages a `.pkg` archive with
  `pkg create -M pkg-manifest.ucl`. FreeBSD runtime behavior remains
  unverified/experimental (`runtime_verified = false`).

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

All credentials MUST be scoped to the protected `release` environment in
`komawm/packaging` and MUST NOT be passed to build or validation jobs.

| Secret / Variable | Scope | Purpose | Status in `v0.0.x` |
| --- | --- | --- | --- |
| `PRODUCT_RELEASE_TOKEN` (Secret) | `release` environment | Fine-grained PAT or GitHub App token with `Contents: Read and write` on `komawm/koma` (and `komawm/jiku` after acceptance) for `gh release upload` | **Required when `publish=true`** |
| `AUR_SSH_PRIVATE_KEY` (Secret) | `release` environment | SSH deploy key for pushing accepted Arch `PKGBUILD`/`.SRCINFO` updates to AUR | Reserved for post-`v0.1.0` acceptance |
| `APT_GPG_PRIVATE_KEY` (Secret) | `release` environment | Armored PGP signing key for signing accepted APT `Release.gpg` / `InRelease` metadata | Reserved for post-`v0.1.0` acceptance |
| `COPR_CONFIG` (Secret) | `release` environment | Fedora COPR API token INI configuration (`~/.config/copr`) for submitting accepted SRPM builds | Reserved for post-`v0.1.0` acceptance |
| `HOMEBREW_TAP_TOKEN` (Secret) | `release` environment | Fine-grained token scoped strictly to `komawm/homebrew-tap` to update accepted formula commits | Reserved for post-`v0.1.0` acceptance |
| `CACHIX_AUTH_TOKEN` (Secret) | `release` environment | Cachix upload token for pushing accepted Nix derivation closures | Reserved for post-`v0.1.0` acceptance |
| `KOMA_COPR_PROJECT`, `KOMA_APT_SUITE`, `KOMA_CACHIX_CACHE`, `KOMA_HOMEBREW_TAP_REPO` (Environment Variables) | `release` environment | Non-secret target coordinates for optional downstream channel publishing after `v0.1.0` | Optional / Reserved for post-`v0.1.0` |

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
