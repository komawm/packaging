# Arch Linux template

[PKGBUILD.in](https://github.com/komawm/packaging/blob/main/archlinux/PKGBUILD.in)
is an unaccepted integration template. There is intentionally no ready-to-build
PKGBUILD until an immutable source commit and the real downloaded archive's
SHA-256 are supplied. A renderer can produce a local review candidate, but
neither rendering nor shell validation establishes a successful package.

The template builds the Koma compositor, controller, MCP adapter, and overview
from a pinned source archive with a committed Cargo.lock. It does not include
Jiku's unimplemented daemon. Product source, binaries, configuration examples,
and license notices must retain their product license; this repository's MIT
license applies to packaging infrastructure.

Use a clean isolated Arch VM or build environment for the first build. Record
source commit, archive checksum, exact dependencies and toolchain, build logs,
package content, and install/remove/upgrade/rollback results. Never install or
replace the host's current Hyprland session to validate this template. See the
[release runbook](https://github.com/komawm/packaging/blob/main/docs/release.md).
