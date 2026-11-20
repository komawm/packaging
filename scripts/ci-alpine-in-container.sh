#!/bin/sh
# Build Alpine .apk packages inside a digest-pinned Alpine container.
# Expects /in/APKBUILD, /in/koma-<SOURCE_COMMIT>.tar.gz, and /out mounted.
# shellcheck shell=sh
set -eu

: "${SOURCE_COMMIT:?SOURCE_COMMIT must be set}"

apk add --no-cache \
    abuild build-base sudo git rust cargo pkgconf eudev-dev libinput-dev \
    seatd-dev mesa-dev libdrm-dev wayland-dev libxkbcommon-dev dbus-dev \
    fontconfig-dev pipewire-dev clang-dev

adduser -D -G abuild builder
mkdir -p /etc/sudoers.d
printf '%s\n' '%abuild ALL=(ALL) NOPASSWD: /usr/bin/abuild-apk' > /etc/sudoers.d/abuild
chmod 0440 /etc/sudoers.d/abuild

mkdir -p /home/builder/build /home/builder/src /home/builder/packages
cp /in/APKBUILD /home/builder/build/APKBUILD
cp "/in/koma-${SOURCE_COMMIT}.tar.gz" "/home/builder/src/koma-${SOURCE_COMMIT}.tar.gz"
chown -R builder:abuild /home/builder

su builder -c '
    set -eu
    export HOME=/home/builder
    export SRCDEST=/home/builder/src
    export REPODEST=/home/builder/packages
    abuild-keygen -a -n -q
    cd /home/builder/build
    abuild -r
'

find /home/builder/packages -type f -name '*.apk' -exec cp {} /out/ \;
cp /home/builder/build/APKBUILD /out/APKBUILD
