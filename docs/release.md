# Packaging and CD acceptance

This repository owns distribution/CD delivery for Koma and accepted companion
components. The local initialization prepares checks, immutable-source template
rendering, and this runbook. Publishing remains disabled. The Arch template has
not been built or accepted; Jiku's daemon is not ready to package.

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

`just check` runs local metadata, Python syntax, and Arch shell-template checks.
This repository does not require generic CI, CodeQL, bot review, or an Issue/PR
workflow. CD preparation remains local with publishing disabled.

## Product release uploads

Use the mapping in [packaging.toml](https://github.com/komawm/packaging/blob/main/packaging.toml):

| Artifacts | GitHub Releases destination |
| --- | --- |
| Accepted Koma build | `komawm/koma` |
| Future accepted Jiku build | `komawm/jiku` |

A later authorized CD task can upload accepted build artifacts, checksums,
and required source/license materials to the existing product release. Record
the exact product revision and distribution target alongside those files.
Future automation must use accepted immutable inputs and explicit release
authorization; never publish a failed or incomplete build.

A workflow's built-in `GITHUB_TOKEN` is limited to the repository containing
that workflow. The packaging repository's token cannot upload to Koma or Jiku
Releases. Cross-repository CD needs a GitHub App installation token or a
fine-grained personal access token restricted to the target product repository
with `Contents: write`. Keep credentials in the authorized publishing
environment and never store token values in this repository. See GitHub's
[GITHUB_TOKEN documentation](https://docs.github.com/en/actions/concepts/security/github_token).

For a later authorized upload to an existing Koma release, after setting the
release tag and accepted artifact/checksum paths:

```bash
gh release upload "$RELEASE_TAG" "$ARTIFACT" "$CHECKSUMS" --repo komawm/koma
```

This is a future command example. The current setup performs no upload. See
the [GitHub CLI upload reference](https://cli.github.com/manual/gh_release_upload).

## Licensing

Packaging infrastructure is MIT. Koma/Jiku product source and binaries retain
their own product licenses. Preserve GPL notices and corresponding-source
requirements, dependencies' license notices, and Phodopus attribution in the
accepted artifact/source distribution. The packaging license never relicenses
bundled products or external dependencies.
