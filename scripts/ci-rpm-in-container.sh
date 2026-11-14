#!/usr/bin/env bash
# Build binary and source RPM packages inside a digest-pinned Fedora container.
# Expects /in/koma.spec, /in/koma-<SOURCE_COMMIT>.tar.gz, and /out mounted.
set -euo pipefail

: "${SOURCE_COMMIT:?SOURCE_COMMIT must be set}"
rpmbuild_flag="${RPMBUILD_FLAG:--ba}"
case "$rpmbuild_flag" in
    -ba|-bb) ;;
    *)
        echo "Unsupported RPMBUILD_FLAG: $rpmbuild_flag" >&2
        exit 1
        ;;
esac

dnf install -y --setopt=install_weak_deps=False \
    rpm-build rustup pkgconf-pkg-config systemd-devel libinput-devel \
    libseat-devel mesa-libgbm-devel mesa-libEGL-devel wayland-devel \
    libxkbcommon-devel dbus-devel fontconfig-devel \
    tar gzip shadow-utils findutils sed

useradd --create-home --shell /bin/bash builder
rpmtop="/home/builder/rpmbuild"
mkdir -p "$rpmtop"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
cp /in/koma.spec "$rpmtop/SPECS/koma.spec"
cp "/in/koma-${SOURCE_COMMIT}.tar.gz" "$rpmtop/SOURCES/koma-${SOURCE_COMMIT}.tar.gz"
chown -R builder:builder /home/builder

su -s /bin/bash builder -c \
    "rpmbuild --define '_topdir $rpmtop' ${rpmbuild_flag} '$rpmtop/SPECS/koma.spec'"

find "$rpmtop/RPMS" "$rpmtop/SRPMS" -type f -name '*.rpm' -exec cp {} /out/ \;
cp "$rpmtop/SPECS/koma.spec" /out/koma.spec
