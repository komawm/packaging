default: check

metadata:
    python3 scripts/verify.py

shellcheck:
    shellcheck -s bash -e SC2034,SC2154 archlinux/PKGBUILD.in

check: metadata shellcheck

hooks-install:
    lefthook install
    carryctx hooks install --compose
