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

`just check` runs local metadata, Python syntax, Arch shell-template, and
`actionlint` workflow checks. This repository does not require generic CI,
CodeQL, bot review, or an Issue/PR workflow.

## Development prerelease CD (`v0.0.x`)

[cd-archlinux.yml](https://github.com/komawm/packaging/blob/main/.github/workflows/cd-archlinux.yml)
builds an Arch Linux package for an existing product development tag and can
attach it to that product's existing GitHub Release. It is `workflow_dispatch`
only; there is no push, tag, or schedule trigger. A development prerelease is
not acceptance: `accepted = false` and `first_accepted_version = "0.1.0"` stay in
[packaging.toml](https://github.com/komawm/packaging/blob/main/packaging.toml).

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

The `publish` job runs only when `publish` is `true`. It uses the protected
`release` environment and its `PRODUCT_RELEASE_TOKEN` secret, fails if that
secret is empty, re-verifies the artifact checksums and manifest identity,
confirms that `komawm/<product>` has a release for exactly that tag and that the
tag still resolves to the built commit, then uploads with `--clobber`:

- `koma-<pkgver>-<pkgrel>-x86_64.pkg.tar.zst`
- `SHA256SUMS-archlinux`
- `PACKAGE-MANIFEST-archlinux.toml` (the manifest, renamed so it cannot
  overwrite the product's own release files)

The product repository's own tag workflow creates the release (for Koma,
`koma-<version>-x86_64-unknown-linux-gnu.tar.gz` and `SHA256SUMS`). Dispatch
packaging CD only after that release exists.

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
