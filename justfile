default: check

metadata:
    python3 scripts/verify.py

shellcheck:
    shellcheck -s bash -e SC2034,SC2154 archlinux/PKGBUILD.in

actionlint:
    actionlint .github/workflows/*.yml

check: metadata shellcheck actionlint

hooks-install:
    lefthook install
    carryctx hooks install --compose
