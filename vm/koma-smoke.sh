#!/usr/bin/env bash
# Host-side Koma package smoke test against a running arch-vm.sh guest.
# Licensed under the MIT license of the packaging repository.
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
vm="$script_dir/arch-vm.sh"

usage() {
    cat <<'EOF'
Usage: vm/koma-smoke.sh <step>...

Runs Koma package checks inside a VM started with `vm/arch-vm.sh boot`.
Each step writes smoke-<step>.log into $VM_EVIDENCE_DIR and appends a line to
results.tsv. Steps run in the order given; `all` runs every step.

Steps:
  copy       Copy package/tarball artifacts and checksums into /root/koma-artifacts
  verify     sha256sum -c on the copied artifacts
  install    pacman -U the package (records resolved dependencies), pacman -Ql
  namcap     namcap on the package file
  cli        --headless-check, komactl validate/gen-types, koma-mcp --print-tools
  tty        Launch `koma --tty` in tester's autologin session on tty1 (logind),
             probe Wayland globals, spawn foot, IPC, screendumps
  tty-stop   Stop the tty1 compositor and collect its log
  tarball    Extract the release tarball into /opt and run bin/koma --headless-check
  remove     pacman -R koma and verify that the package files are gone
  reinstall  pacman -U the same package again (same-version reinstall path)

Environment:
  KOMA_PKG          Arch package file (koma-<ver>-<rel>-x86_64.pkg.tar.zst)
  KOMA_PKG_SUMS     SHA256SUMS-archlinux next to the package [dirname/SHA256SUMS-archlinux]
  KOMA_TARBALL      Release tarball (koma-<ver>-x86_64-unknown-linux-gnu.tar.gz)
  KOMA_TARBALL_SUMS Release SHA256SUMS [dirname/SHA256SUMS]
  KOMA_RUST_LOG     RUST_LOG for the tty launch [debug]
  KOMA_LIBSEAT_BACKEND  LIBSEAT_BACKEND for the tty launch, e.g. seatd [libseat default]
  KOMA_TTY_TAG      Suffix for tty screenshots and logs, to keep variant runs apart [empty]
  VM_GUEST_PROXY    Optional HTTP proxy for pacman downloads in the guest
  Other VM_* variables are passed through to vm/arch-vm.sh.
EOF
}

die() {
    printf 'koma-smoke: %s\n' "$*" >&2
    exit 1
}

evidence="$("$vm" status | sed -n 's/^evidence=//p')"
mkdir -p "$evidence"
proxy_env=""
if [[ -n "${VM_GUEST_PROXY:-}" ]]; then
    proxy_env="http_proxy='$VM_GUEST_PROXY' https_proxy='$VM_GUEST_PROXY'"
fi
artifacts=/root/koma-artifacts
tag="${KOMA_TTY_TAG:+-$KOMA_TTY_TAG}"

gssh() { "$vm" ssh "$@"; }

result() {
    # step, status, note
    printf '%s\t%s\t%s\t%s\n' "$(date -u +%FT%TZ)" "$1" "$2" "$3" >>"$evidence/results.tsv"
    printf 'koma-smoke: %-10s %s %s\n' "$1" "$2" "$3" >&2
}

run_step() {
    local name="$1"
    shift
    local log="$evidence/smoke-$name.log"
    if "$@" >"$log" 2>&1; then
        result "$name" PASS "$log"
    else
        result "$name" FAIL "$log (rc=$?)"
        return 1
    fi
}

pkg_name() { basename -- "${KOMA_PKG:?set KOMA_PKG}"; }

step_copy() {
    : "${KOMA_PKG:?set KOMA_PKG}" "${KOMA_TARBALL:?set KOMA_TARBALL}"
    local pkg_sums="${KOMA_PKG_SUMS:-$(dirname -- "$KOMA_PKG")/SHA256SUMS-archlinux}"
    local tar_sums="${KOMA_TARBALL_SUMS:-$(dirname -- "$KOMA_TARBALL")/SHA256SUMS}"
    local manifest
    manifest="$(dirname -- "$KOMA_PKG")/PACKAGE-MANIFEST.toml"
    gssh "rm -rf $artifacts && mkdir -p $artifacts/pkg $artifacts/tarball"
    local pkg_files=("$KOMA_PKG" "$pkg_sums")
    [[ -f "$manifest" ]] && pkg_files+=("$manifest")
    "$vm" scp-in "${pkg_files[@]}" "$artifacts/pkg/"
    "$vm" scp-in "$KOMA_TARBALL" "$tar_sums" "$artifacts/tarball/"
    sha256sum "${pkg_files[@]}" "$KOMA_TARBALL" "$tar_sums" | sed 's#  .*/#  #'
    gssh "ls -la $artifacts/pkg $artifacts/tarball"
}

step_verify() {
    gssh "set -e; cd $artifacts/pkg && sha256sum -c SHA256SUMS-archlinux --ignore-missing;
        cd $artifacts/tarball && sha256sum -c SHA256SUMS --ignore-missing;
        [ -f $artifacts/pkg/PACKAGE-MANIFEST.toml ] && cat $artifacts/pkg/PACKAGE-MANIFEST.toml || true"
}

step_install() {
    local pkg
    pkg="$(pkg_name)"
    gssh "set -e; pacman -Qq > /root/pkgs-before.txt
        $proxy_env pacman -U --noconfirm $artifacts/pkg/$pkg
        pacman -Qq > /root/pkgs-after.txt
        echo '--- packages added by pacman -U (dependency resolution) ---'
        comm -13 /root/pkgs-before.txt /root/pkgs-after.txt | xargs pacman -Q
        echo '--- pacman -Qi koma ---'; pacman -Qi koma
        echo '--- pacman -Ql koma ---'; pacman -Ql koma
        echo '--- pacman -Qkk koma ---'; pacman -Qkk koma"
}

step_namcap() {
    gssh "namcap $artifacts/pkg/$(pkg_name); echo '--- namcap -i (tags) ---'; namcap -i $artifacts/pkg/$(pkg_name) 2>&1 | head -80"
}

step_cli() {
    gssh -u tester "set -e
        echo '\$ koma --headless-check'; koma --headless-check
        echo '\$ komactl validate /usr/share/doc/koma/examples/init.lua'
        komactl validate /usr/share/doc/koma/examples/init.lua
        echo '\$ komactl gen-types | head -n 20'; komactl gen-types | head -n 20
        echo '\$ komactl gen-types | wc -l'; komactl gen-types | wc -l
        echo '\$ koma-mcp --print-tools | head -c 2000'; koma-mcp --print-tools | head -c 2000; echo
        echo '\$ komactl (no args, usage, expected rc=1)'; komactl || echo rc=\$?
        echo '\$ ldd /usr/bin/koma'; ldd /usr/bin/koma"
}

step_tty() {
    local rust_log="${KOMA_RUST_LOG:-debug}"
    # One-shot hook in tester's login shell: tty1 autologin runs koma --tty once.
    gssh "set -e
        cat > /home/tester/.bash_profile <<'EOF'
[[ -f ~/.bashrc ]] && . ~/.bashrc
if [[ \"\$(tty)\" == /dev/tty1 && -f ~/.koma-tty ]]; then
    rust_log=\"\$(cat ~/.koma-tty)\"
    rm -f ~/.koma-tty
    if [[ -s ~/.koma-libseat ]]; then export LIBSEAT_BACKEND=\"\$(cat ~/.koma-libseat)\"; fi
    RUST_LOG=\"\$rust_log\" koma --tty >\"\$HOME/koma-tty.log\" 2>&1
    echo \"koma exited rc=\$?\" >>\"\$HOME/koma-tty.log\"
fi
EOF
        printf '%s' '$rust_log' > /home/tester/.koma-tty
        printf '%s' '${KOMA_LIBSEAT_BACKEND:-}' > /home/tester/.koma-libseat
        chown tester:tester /home/tester/.bash_profile /home/tester/.koma-tty /home/tester/.koma-libseat
        systemctl restart getty@tty1.service"
    sleep 15
    "$vm" screendump "$evidence/tty$tag-01-after-start.png"
    gssh "echo '--- koma process ---'; pgrep -a -u tester -x koma || { echo 'koma not running'; tail -n 60 /home/tester/koma-tty.log; }
        echo '--- loginctl ---'; loginctl list-sessions --no-pager; loginctl show-session \$(loginctl list-sessions --no-legend | awk '\$3==\"tester\" && \$5==\"tty1\"{print \$1}' | head -n1) -p Active -p VTNr -p Type -p Seat 2>/dev/null || true
        echo '--- DRM ---'; ls -la /dev/dri; cat /sys/class/drm/card*/device/uevent 2>/dev/null | grep -E 'DRIVER|PCI_ID' || true
        echo '--- runtime dir ---'; ls -la /run/user/1000 /run/user/1000/koma 2>/dev/null || true"
    local tty_fail=0
    gssh -u tester "export XDG_RUNTIME_DIR=/run/user/1000
        sock=\$(cd \$XDG_RUNTIME_DIR && ls wayland-* 2>/dev/null | grep -v lock | head -n1)
        echo \"WAYLAND_DISPLAY=\$sock\"
        [ -n \"\$sock\" ] || exit 1
        export WAYLAND_DISPLAY=\$sock
        fail=0
        echo '--- wayland-info ---'; timeout 10 wayland-info > /tmp/wayland-info.txt 2>&1; head -n 200 /tmp/wayland-info.txt
        grep -q \"'wl_output'\" /tmp/wayland-info.txt || { echo 'FAIL: no wl_output global advertised'; fail=1; }
        echo '--- spawn foot ---'; setsid -f foot -e bash -c 'echo koma vm smoke; uname -a; sleep 600' >/tmp/foot.log 2>&1
        sleep 5; pgrep -a foot || { echo 'FAIL: foot not running'; cat /tmp/foot.log; fail=1; }
        echo '--- komactl config-status ---'; timeout 10 komactl config-status 2>&1 || fail=1
        echo '--- komactl inventory ---'; timeout 10 komactl inventory 2>&1 || fail=1
        exit \$fail" || tty_fail=1
    "$vm" screendump "$evidence/tty$tag-02-with-foot.png"
    gssh "pgrep -a -u tester -x koma && echo 'koma still alive after client spawn'" || tty_fail=1
    return "$tty_fail"
}

step_tty_stop() {
    gssh "pkill -TERM -u tester -x koma || true; sleep 3; pgrep -a -u tester -x koma && echo 'koma still running' || echo 'koma stopped'
        echo '--- koma-tty.log (tail 300) ---'; tail -n 300 /home/tester/koma-tty.log
        echo '--- journal (koma/seat/drm, this boot) ---'; journalctl -b --no-pager -o short-monotonic -t kernel -t systemd-logind -t seatd | grep -iE 'koma|seat|drm|bochs|session' | tail -n 80"
    "$vm" scp-out /home/tester/koma-tty.log "$evidence/koma-tty$tag-full.log" || true
}

step_tarball() {
    local tarball
    tarball="$(basename -- "${KOMA_TARBALL:?set KOMA_TARBALL}")"
    gssh "set -e; rm -rf /opt/koma-tarball && mkdir -p /opt/koma-tarball
        tar -xzf $artifacts/tarball/$tarball -C /opt/koma-tarball
        find /opt/koma-tarball -maxdepth 3 | sort
        bin=\$(find /opt/koma-tarball -path '*/bin/koma' -type f | head -n1)
        echo \"\\\$ \$bin --headless-check\"; \"\$bin\" --headless-check
        ini=\$(find /opt/koma-tarball -name init.lua | head -n1)
        echo \"\\\$ \$(dirname \$bin)/komactl validate \$ini\"
        \"\$(dirname \"\$bin\")/komactl\" validate \"\$ini\""
}

step_remove() {
    gssh "set -e; pacman -Qlq koma | grep -v '/\$' > /root/koma-files.txt
        wc -l < /root/koma-files.txt
        pacman -R --noconfirm koma
        if pacman -Q koma 2>/dev/null; then echo 'koma still installed'; exit 1; fi
        left=0; while read -r f; do [ -e \"\$f\" ] && { echo \"left behind: \$f\"; left=1; }; done < /root/koma-files.txt
        [ \$left -eq 0 ] && echo 'all package files removed'
        command -v koma || echo 'koma no longer on PATH'
        exit \$left"
}

step_reinstall() {
    gssh "set -e; $proxy_env pacman -U --noconfirm $artifacts/pkg/$(pkg_name); pacman -Q koma; koma --headless-check
        echo '--- same-version reinstall over an installed package ---'
        $proxy_env pacman -U --noconfirm $artifacts/pkg/$(pkg_name) 2>&1; pacman -Q koma; pacman -Qkk koma"
}

main() {
    (($# > 0)) || {
        usage >&2
        exit 2
    }
    local steps=("$@") s rc=0
    if [[ "$1" == -h || "$1" == --help ]]; then
        usage
        return
    fi
    if [[ "$1" == all ]]; then
        steps=(copy verify install namcap cli tty tty-stop tarball remove reinstall)
    fi
    for s in "${steps[@]}"; do
        case "$s" in
            copy | verify | install | namcap | cli | tarball | remove | reinstall)
                run_step "$s" "step_$s" || rc=1
                ;;
            tty) run_step "tty$tag" step_tty || rc=1 ;;
            tty-stop) run_step "tty-stop$tag" step_tty_stop || rc=1 ;;
            *) die "unknown step: $s (see --help)" ;;
        esac
    done
    return "$rc"
}

main "$@"
