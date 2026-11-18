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
  cli        --headless-check, komactl validate/gen-types, koma mcp --print-tools
  tty        Launch `koma --tty` in tester's autologin session on tty1 (logind),
             probe Wayland globals, spawn foot, IPC, protocol checks, screendumps
  protocols  Run Wayland protocol completeness checks (foot log, grim/wlr-screencopy,
             wlr-layer-shell exclusive_zone, ext-session-lock-v1) against active tty1 session
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
        echo '\$ koma --help | head -n 5'; koma --help | head -n 5
        echo '\$ koma --version'; koma --version
        echo '\$ koma --bogus (expected rc=2)'; rc=0; koma --bogus >/dev/null 2>&1 || rc=\$?; echo \"rc=\$rc\"; test \"\$rc\" -eq 2
        echo '\$ koma --headless-check'; koma --headless-check
        echo '\$ komactl validate /usr/share/doc/koma/examples/init.lua'
        komactl validate /usr/share/doc/koma/examples/init.lua
        echo '\$ komactl gen-types | head -n 20'; komactl gen-types | head -n 20
        echo '\$ komactl gen-types | wc -l'; komactl gen-types | wc -l
        echo '\$ koma mcp --print-tools | head -c 2000'; koma mcp --print-tools | head -c 2000; echo
        echo '\$ komactl --help | head -n 5'; komactl --help | head -n 5
        echo '\$ komactl --version'; komactl --version
        echo '\$ komactl (no args, usage, expected rc=2)'; rc=0; komactl >/dev/null 2>&1 || rc=\$?; echo \"rc=\$rc\"; test \"\$rc\" -eq 2
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
        echo '--- komactl inventory ---'
        timeout 10 komactl inventory > /tmp/inventory.json 2>&1 || fail=1
        cat /tmp/inventory.json
        grep -qE '\"win_id\":[[:space:]]*\"win_' /tmp/inventory.json || { echo 'FAIL: no mapped window in inventory'; fail=1; }
        echo '--- komactl capture ---'
        timeout 10 komactl capture ws_01 /tmp/capture.rgba 2>&1 || fail=1
        ls -la /tmp/capture.rgba; test -s /tmp/capture.rgba || { echo 'FAIL: empty capture'; fail=1; }
        exit \$fail" || tty_fail=1
    "$vm" screendump "$evidence/tty$tag-02-with-foot.png"
    step_protocols || tty_fail=1
    gssh "pgrep -a -u tester -x koma && echo 'koma still alive after client spawn'" || tty_fail=1
    return "$tty_fail"
}

step_protocols() {
    local proto_fail=0
    local probe_local="$script_dir/wayland-probe.py"
    [[ -f "$probe_local" ]] || die "missing Wayland probe helper: $probe_local"

    "$vm" scp-in "$probe_local" /tmp/wayland-probe.py
    gssh "chmod 0755 /tmp/wayland-probe.py"

    echo '--- (a) globals + foot.log check ---'
    gssh -u tester 'bash -s' <<'EOF' || proto_fail=1
set -eu
export XDG_RUNTIME_DIR=/run/user/1000
sock=$(cd "$XDG_RUNTIME_DIR" && ls wayland-* 2>/dev/null | grep -v lock | head -n1 || true)
[ -n "$sock" ] || { echo "FAIL: no WAYLAND_DISPLAY socket in $XDG_RUNTIME_DIR"; exit 1; }
export WAYLAND_DISPLAY="$sock"

command -v grim >/dev/null 2>&1 || { echo "FAIL: grim missing in guest"; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "FAIL: python3 missing in guest"; exit 1; }

if [ ! -s /tmp/wayland-info.txt ]; then
    timeout 10 wayland-info > /tmp/wayland-info.txt 2>&1
fi
for iface in wl_compositor wl_shm wl_output zwlr_layer_shell_v1 zwlr_screencopy_manager_v1 ext_session_lock_manager_v1; do
    if ! grep -q "$iface" /tmp/wayland-info.txt; then
        echo "FAIL: compositor missing advertised Wayland global: $iface"
        exit 1
    fi
done
echo "PASS: advertised Wayland globals include layer-shell, screencopy, and session-lock"

if [ ! -f /tmp/foot.log ]; then
    setsid -f foot -e bash -c "echo koma proto smoke; sleep 120" </dev/null >/tmp/foot.log 2>&1
    sleep 3
fi
echo '--- /tmp/foot.log ---'
cat /tmp/foot.log
if grep -qiE 'missing required wayland protocol|unsupported.*protocol|failed to connect to a wayland compositor|wayland.*fatal' /tmp/foot.log; then
    echo "FAIL: foot reported critical Wayland protocol error in /tmp/foot.log"
    exit 1
fi
pgrep -u tester -x foot >/dev/null || { echo "FAIL: foot not running for tester"; exit 1; }
EOF

    echo '--- (b) unlocked grim (wlr-screencopy) check ---'
    gssh -u tester 'bash -s' <<'EOF' || proto_fail=1
set -eu
export XDG_RUNTIME_DIR=/run/user/1000
sock=$(cd "$XDG_RUNTIME_DIR" && ls wayland-* 2>/dev/null | grep -v lock | head -n1 || true)
[ -n "$sock" ] || exit 1
export WAYLAND_DISPLAY="$sock"
rm -f /tmp/grim-unlocked.png
timeout 15 grim /tmp/grim-unlocked.png
test -s /tmp/grim-unlocked.png || { echo "FAIL: grim produced empty unlocked PNG"; exit 1; }
python3 /tmp/wayland-probe.py check-png /tmp/grim-unlocked.png --require-non-black
EOF
    if gssh "test -s /tmp/grim-unlocked.png" 2>/dev/null; then
        "$vm" scp-out /tmp/grim-unlocked.png "$evidence/tty$tag-grim-unlocked.png" || proto_fail=1
    fi

    echo '--- (c) wlr-layer-shell exclusive_zone check ---'
    local layer_ok=0
    if gssh -u tester 'bash -s' <<'EOF'
set -eu
export XDG_RUNTIME_DIR=/run/user/1000
sock=$(cd "$XDG_RUNTIME_DIR" && ls wayland-* 2>/dev/null | grep -v lock | head -n1 || true)
[ -n "$sock" ] || exit 1
export WAYLAND_DISPLAY="$sock"

get_rect() {
    timeout 10 komactl inventory | python3 -c '
import json, sys
data = json.load(sys.stdin)
for ws in data.get("workspaces", []):
    aw = ws.get("active_window")
    if not aw and ws.get("windows"):
        aw = ws["windows"][0]
    if aw and aw.get("rect"):
        r = aw["rect"]
        print(r["x"], r["y"], r["w"], r["h"])
        sys.exit(0)
sys.exit(1)
'
}

read -r x0 y0 w0 h0 < <(get_rect)
echo "exclusive_zone before: x=$x0 y=$y0 w=$w0 h=$h0"
[ "$w0" -gt 0 ] && [ "$h0" -gt 0 ] || { echo "FAIL: invalid initial window rect"; exit 1; }
printf '%s %s %s %s\n' "$x0" "$y0" "$w0" "$h0" > /tmp/layer-before.rect

rm -f /tmp/layer-ready.txt /tmp/layer.pid /tmp/layer-panel.log
setsid -f bash -c 'echo $$ > /tmp/layer.pid; exec python3 /tmp/wayland-probe.py layer-panel --height 64 --anchor top --ready-file /tmp/layer-ready.txt' </dev/null >/tmp/layer-panel.log 2>&1

ready=0
for _ in $(seq 1 20); do
    if [ -s /tmp/layer-ready.txt ]; then
        ready=1
        break
    fi
    sleep 0.5
done
if [ "$ready" -ne 1 ]; then
    echo "FAIL: layer-panel did not map"
    cat /tmp/layer-panel.log 2>/dev/null || true
    exit 1
fi
cat /tmp/layer-ready.txt

shrunk=0
x1=$x0 y1=$y0 w1=$w0 h1=$h0
for _ in $(seq 1 12); do
    if read -r x1 y1 w1 h1 < <(get_rect); then
        if [ "$h1" -le $((h0 - 32)) ] || [ "$y1" -ge $((y0 + 32)) ]; then
            shrunk=1
            break
        fi
    fi
    sleep 0.5
done
echo "exclusive_zone during: x=$x1 y=$y1 w=$w1 h=$h1"
if [ "$shrunk" -ne 1 ]; then
    echo "FAIL: wlr-layer-shell exclusive_zone=64 did not shrink tiled window workarea (before h=$h0 y=$y0, during h=$h1 y=$y1)"
    exit 1
fi
EOF
    then
        layer_ok=1
        "$vm" screendump "$evidence/tty$tag-03-layer-panel.png"
    else
        proto_fail=1
    fi

    gssh -u tester 'bash -s' <<'EOF' || proto_fail=1
set -eu
export XDG_RUNTIME_DIR=/run/user/1000
if [ -s /tmp/layer.pid ]; then
    lpid=$(cat /tmp/layer.pid)
    kill -TERM "$lpid" 2>/dev/null || true
    for _ in $(seq 1 15); do
        kill -0 "$lpid" 2>/dev/null || break
        sleep 0.2
    done
    rm -f /tmp/layer.pid
fi
if [ -s /tmp/layer-before.rect ]; then
    read -r x0 y0 w0 h0 < /tmp/layer-before.rect
    restored=0
    xr=$x0 yr=$y0 wr=$w0 hr=$h0
    for _ in $(seq 1 10); do
        if read -r xr yr wr hr < <(timeout 10 komactl inventory | python3 -c '
import json, sys
data = json.load(sys.stdin)
for ws in data.get("workspaces", []):
    aw = ws.get("active_window") or (ws.get("windows") or [None])[0]
    if aw and aw.get("rect"):
        r = aw["rect"]
        print(r["x"], r["y"], r["w"], r["h"])
        sys.exit(0)
sys.exit(1)
'); then
            if [ "$hr" -ge $((h0 - 2)) ] && [ "$yr" -le $((y0 + 2)) ]; then
                restored=1
                break
            fi
        fi
        sleep 0.5
    done
    echo "exclusive_zone after close: x=$xr y=$yr w=$wr h=$hr"
    [ "$restored" -eq 1 ] || { echo "FAIL: workarea did not restore after layer panel closed"; exit 1; }
fi
EOF
    if [[ "$layer_ok" -eq 1 ]]; then
        echo "PASS: wlr-layer-shell exclusive_zone verified"
    fi

    echo '--- (d) ext-session-lock-v1 lock, screencopy isolation, and clean unlock ---'
    if gssh -u tester 'bash -s' <<'EOF'
set -eu
export XDG_RUNTIME_DIR=/run/user/1000
sock=$(cd "$XDG_RUNTIME_DIR" && ls wayland-* 2>/dev/null | grep -v lock | head -n1 || true)
[ -n "$sock" ] || exit 1
export WAYLAND_DISPLAY="$sock"

rm -f /tmp/lock.pid /tmp/lock-ready.txt /tmp/lock-unlocked.txt /tmp/session-lock.log /tmp/grim-locked.png /tmp/grim-locked.log /tmp/grim-unlocked-after.png
setsid -f bash -c 'echo $$ > /tmp/lock.pid; exec python3 /tmp/wayland-probe.py session-lock --ready-file /tmp/lock-ready.txt --unlocked-file /tmp/lock-unlocked.txt --timeout 120' </dev/null >/tmp/session-lock.log 2>&1

locked=0
for _ in $(seq 1 20); do
    if [ -s /tmp/lock-ready.txt ]; then
        locked=1
        break
    fi
    sleep 0.5
done
if [ "$locked" -ne 1 ]; then
    echo "FAIL: ext-session-lock-v1 client did not reach LOCKED state"
    cat /tmp/session-lock.log 2>/dev/null || true
    exit 1
fi
cat /tmp/lock-ready.txt
EOF
    then
        "$vm" screendump "$evidence/tty$tag-04-session-locked.png"
        if [[ -s "$evidence/tty$tag-02-with-foot.png" ]]; then
            python3 "$probe_local" check-png \
                "$evidence/tty$tag-04-session-locked.png" \
                --compare-unlocked "$evidence/tty$tag-02-with-foot.png" \
                --require-masked || proto_fail=1
        fi

        gssh -u tester 'bash -s' <<'EOF' || proto_fail=1
set -eu
export XDG_RUNTIME_DIR=/run/user/1000
sock=$(cd "$XDG_RUNTIME_DIR" && ls wayland-* 2>/dev/null | grep -v lock | head -n1 || true)
[ -n "$sock" ] || exit 1
export WAYLAND_DISPLAY="$sock"

lpid=$(cat /tmp/lock.pid)
kill -0 "$lpid" 2>/dev/null || { echo "FAIL: session-lock client exited unexpectedly while locked"; cat /tmp/session-lock.log 2>/dev/null || true; exit 1; }

if timeout 10 grim /tmp/grim-locked.png >/tmp/grim-locked.log 2>&1; then
    echo "grim returned exit 0 while locked; verifying locked frame is masked"
    if [ -s /tmp/grim-unlocked.png ]; then
        python3 /tmp/wayland-probe.py check-png /tmp/grim-locked.png --compare-unlocked /tmp/grim-unlocked.png --require-masked
    else
        python3 /tmp/wayland-probe.py check-png /tmp/grim-locked.png
    fi
else
    echo "PASS: grim screencopy rejected while session locked"
    cat /tmp/grim-locked.log 2>/dev/null || true
fi

kill -USR1 "$lpid"
unlocked=0
for _ in $(seq 1 20); do
    if [ -s /tmp/lock-unlocked.txt ]; then
        unlocked=1
        break
    fi
    sleep 0.5
done
if [ "$unlocked" -ne 1 ]; then
    echo "FAIL: ext-session-lock-v1 client did not unlock cleanly"
    cat /tmp/session-lock.log 2>/dev/null || true
    exit 1
fi
cat /tmp/lock-unlocked.txt

sleep 0.5
timeout 15 grim /tmp/grim-unlocked-after.png
test -s /tmp/grim-unlocked-after.png || { echo "FAIL: grim produced empty post-unlock PNG"; exit 1; }
python3 /tmp/wayland-probe.py check-png /tmp/grim-unlocked-after.png --require-non-black

timeout 10 komactl inventory > /tmp/inv-after-unlock.json
grep -qE '"win_id":[[:space:]]*"win_' /tmp/inv-after-unlock.json || { echo "FAIL: mapped window missing after unlock"; cat /tmp/inv-after-unlock.json; exit 1; }
pgrep -u tester -x foot >/dev/null || { echo "FAIL: foot died across lock/unlock cycle"; exit 1; }
EOF
        if gssh "test -s /tmp/grim-locked.png" 2>/dev/null; then
            "$vm" scp-out /tmp/grim-locked.png "$evidence/tty$tag-grim-locked.png" || proto_fail=1
        fi
        if gssh "test -s /tmp/grim-unlocked-after.png" 2>/dev/null; then
            "$vm" scp-out /tmp/grim-unlocked-after.png "$evidence/tty$tag-grim-unlocked-after.png" || proto_fail=1
        fi
        "$vm" screendump "$evidence/tty$tag-05-session-unlocked.png"
    else
        proto_fail=1
        gssh -u tester 'bash -s' <<'EOF' || true
if [ -s /tmp/lock.pid ]; then
    kill -USR1 "$(cat /tmp/lock.pid)" 2>/dev/null || true
fi
EOF
    fi

    return "$proto_fail"
}

step_tty_stop() {
    local stop_fail=0
    gssh "pkill -TERM -u tester -x koma || true; sleep 3; pgrep -a -u tester -x koma && echo 'koma still running' || echo 'koma stopped'
        echo '--- koma-tty.log (tail 300) ---'; tail -n 300 /home/tester/koma-tty.log
        echo '--- journal (koma/seat/drm, this boot) ---'; journalctl -b --no-pager -o short-monotonic -t kernel -t systemd-logind -t seatd | grep -iE 'koma|seat|drm|bochs|virtio|session' | tail -n 80"
    "$vm" scp-out /home/tester/koma-tty.log "$evidence/koma-tty$tag-full.log" || true
    if [[ -f "$evidence/koma-tty$tag-full.log" ]]; then
        grep -q 'koma exited rc=0' "$evidence/koma-tty$tag-full.log" || {
            echo "FAIL: expected graceful shutdown 'koma exited rc=0' in $evidence/koma-tty$tag-full.log"
            stop_fail=1
        }
    else
        echo "FAIL: missing $evidence/koma-tty$tag-full.log"
        stop_fail=1
    fi
    return "$stop_fail"
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
            protocols) run_step "protocols$tag" step_protocols || rc=1 ;;
            tty-stop) run_step "tty-stop$tag" step_tty_stop || rc=1 ;;
            *) die "unknown step: $s (see --help)" ;;
        esac
    done
    return "$rc"
}

main "$@"
