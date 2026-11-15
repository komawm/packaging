default: check

metadata:
    python3 scripts/verify.py

shellcheck:
    shellcheck -s bash -e SC2034,SC2154 archlinux/PKGBUILD.in archlinux/PKGBUILD.jiku.in archlinux/PKGBUILD.koma-bin.in archlinux/PKGBUILD.jiku-bin.in
    shellcheck -s sh -e SC2034,SC2154 alpine/APKBUILD.in
    shellcheck -s sh scripts/ci-alpine-in-container.sh
    shellcheck -s bash scripts/ci-rpm-in-container.sh scripts/download-source.sh scripts/resolve-tag.sh apt/setup.sh
    shellcheck -s bash vm/*.sh

actionlint:
    actionlint .github/workflows/*.yml

check: metadata shellcheck actionlint

vm-help:
    vm/arch-vm.sh --help

hooks-install:
    lefthook install
    carryctx hooks install --compose
