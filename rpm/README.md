# Fedora / RHEL (RPM) template

[koma.spec.in](https://github.com/komawm/packaging/blob/main/rpm/koma.spec.in)
is an unaccepted RPM spec template targeting Fedora `x86_64` from an immutable
[Koma](https://github.com/komawm/koma) source archive and checksum.

## Build and COPR considerations

- `%prep` verifies the downloaded source archive against `@SOURCE_SHA256@` via
  `sha256sum -c -` before unpacking.
- `%build` honors Koma's `rust-toolchain.toml` channel (`1.100.0`, edition
  `2024`) via `rustup` / `rustup-init` and requires a committed `Cargo.lock` with
  `cargo build --release --locked`.
- Note on COPR / Mock: standard Fedora Mock sandboxes disable network access
  during `%build`. For `v0.0.x` container builds in the
  [CD matrix workflow](https://github.com/komawm/packaging/blob/main/.github/workflows/cd-release-matrix.yml),
  `rpmbuild` runs inside a digest-pinned `fedora:43` container with `rustup` and
  locked Cargo fetch enabled. Before `v0.1.0` acceptance on offline COPR/Mock
  chroots, a vendored crate tarball `Source1` (or distro Rust `>= 1.100`) will
  need to be generated alongside the SRPM.

## Local rendering

```bash
python3 scripts/render_template.py \
  --distro rpm \
  --source-commit <40-hex-commit> \
  --source-sha256 <real-archive-sha256> \
  --version 0.0.1 \
  --output /tmp/koma/koma.spec
```

Build inside an isolated Fedora environment with `rpmbuild -ba`; see the
[release runbook](https://github.com/komawm/packaging/blob/main/docs/release.md).
