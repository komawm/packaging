default: check

metadata:
    python3 scripts/verify.py

shellcheck:
    shellcheck -s bash -e SC2034,SC2154 archlinux/PKGBUILD.in
    shellcheck -s bash vm/*.sh

actionlint:
    actionlint .github/workflows/*.yml

check: metadata shellcheck actionlint

vm-help:
    vm/arch-vm.sh --help

hooks-install:
    lefthook install
    carryctx hooks install --compose
