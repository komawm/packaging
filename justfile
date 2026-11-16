default: check

metadata:
    python3 scripts/verify.py

npm-selftest:
    python3 scripts/selftest_stage_npm.py

shellcheck:
    shellcheck -s bash -e SC2034,SC2154 archlinux/PKGBUILD.in archlinux/PKGBUILD.jiku.in archlinux/PKGBUILD.koma-bin.in archlinux/PKGBUILD.jiku-bin.in
    shellcheck -s sh -e SC2034,SC2154 alpine/APKBUILD.in
    shellcheck -s sh scripts/ci-alpine-in-container.sh
    shellcheck -s bash scripts/ci-rpm-in-container.sh scripts/download-source.sh scripts/resolve-tag.sh apt/setup.sh
    shellcheck -s bash vm/*.sh

actionlint:
    actionlint .github/workflows/*.yml

check: metadata npm-selftest shellcheck actionlint

vm-help:
    vm/arch-vm.sh --help

hooks-install:
    lefthook install
    carryctx hooks install --compose

# Stage @koma-wm/jiku npm package trees from local release artifacts (never publishes).
stage-npm version artifacts source out="dist/npm":
    python3 scripts/stage_npm.py --version "{{version}}" --artifacts "{{artifacts}}" --source-root "{{source}}" --out "{{out}}"

# Verify the staged npm trees pack cleanly without contacting the registry.
pack-npm out="dist/npm":
    for dir in "{{out}}"/@koma-wm/*; do (cd "$dir" && npm pack --dry-run >/dev/null && echo "packed $dir"); done
