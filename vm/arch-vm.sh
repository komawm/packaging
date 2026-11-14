#!/usr/bin/env bash
# Headless QEMU/KVM Arch Linux test VM harness for Koma packaging validation.
# Licensed under the MIT license of the packaging repository.
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

usage() {
    cat <<'EOF'
Usage: vm/arch-vm.sh <command> [args...]

QEMU/KVM Arch Linux VM for package install/launch/removal and Koma TTY tests.
Defaults to headless (`VM_DISPLAY=none`) with serial-to-file, a QMP socket,
and an optional VNC server bound to 127.0.0.1. Set `VM_DISPLAY` (e.g. `gtk,gl=on`
or `egl-headless,rendernode=/dev/dri/renderD128`) to view locally or use host
Virgl 3D rendering.

Commands:
  seed                 Generate an ephemeral SSH key and a NoCloud CIDATA seed
  install              Boot the Arch ISO, install Arch onto the VM disk, power off
  boot                 Boot the installed disk in the background
  wait-ssh             Wait until guest sshd answers
  ssh [-u USER] [CMD]  SSH into the guest (default user: root)
  scp-in SRC... DEST   Copy host files into the guest (as root)
  scp-out SRC DEST     Copy a guest file to the host (as root)
  screendump [OUT.png] Save the guest framebuffer as PNG via QMP
  sendkey KEYS...      Send QMP key combos, e.g. ctrl-alt-f2
  qmp JSON             Send one raw QMP command and print the reply
  stop                 ACPI power-off, then hard quit after a timeout
  snapshot ACTION [N]  qemu-img internal snapshot: create|apply|delete|list
  status               Show whether the VM is running and its paths
  -h, --help           Show this help

Environment (defaults in brackets):
  KOMA_WORKSPACE   Workspace root [derived from the packaging Git common dir]
  ISO_PATH         Read-only installer collection (required for seed/install)
  VM_ISO           Installer image [$ISO_PATH/archlinux-*-x86_64.iso, newest]
  VM_NAME          VM name [arch-koma]
  VM_DIR           Disk, OVMF vars, seed, key [$KOMA_WORKSPACE/recording/vm/$VM_NAME]
  VM_EVIDENCE_DIR  Logs and screenshots [$KOMA_WORKSPACE/recording/<date>/vm-$VM_NAME]
  VM_DISK_SIZE     qcow2 virtual size [40G]
  VM_CPUS / VM_MEM vCPUs / memory [4 / 6G]
  VM_SSH_PORT      Host loopback port forwarded to guest :22 [2222]
  VM_DISPLAY       QEMU `-display` backend [none]; e.g.:
                     none (headless, QMP screendump only)
                     egl-headless,rendernode=/dev/dri/renderD128 (host GPU Virgl + VNC)
                     gtk,gl=on | sdl,gl=on (local window; requires qemu-ui-{gtk,sdl,opengl})
  VM_VNC           VNC display number on 127.0.0.1 (1 => 127.0.0.1:5901),
                   empty disables [empty]; use with `vncviewer 127.0.0.1:5901`
  VM_GPU           QEMU display device [virtio-vga if the module exists, else VGA]
                   Use `virtio-vga-gl` with `gl=on` / `egl-headless` for 3D Virgl
  VM_OVMF_CODE     UEFI firmware [/usr/share/edk2/x64/OVMF_CODE.4m.fd]
  VM_OVMF_VARS     UEFI vars template [/usr/share/edk2/x64/OVMF_VARS.4m.fd]
  VM_GUEST_PROXY   HTTP proxy for guest downloads [none]; a host proxy on
                   127.0.0.1:<port> is reachable as http://10.0.2.2:<port>
  VM_MIRROR        Arch mirror [https://geo.mirror.pkgbuild.com/$repo/os/$arch]
EOF
}

die() {
    printf 'arch-vm: %s\n' "$*" >&2
    exit 1
}

log() {
    printf 'arch-vm: %s\n' "$*" >&2
}

derive_workspace() {
    local common
    if common="$(git -C "$script_dir" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"; then
        dirname -- "$(dirname -- "$common")"
    else
        dirname -- "$(dirname -- "$script_dir")"
    fi
}

KOMA_WORKSPACE="${KOMA_WORKSPACE:-$(derive_workspace)}"
VM_NAME="${VM_NAME:-arch-koma}"
VM_DIR="${VM_DIR:-$KOMA_WORKSPACE/recording/vm/$VM_NAME}"
VM_EVIDENCE_DIR="${VM_EVIDENCE_DIR:-$KOMA_WORKSPACE/recording/$(date +%F)/vm-$VM_NAME}"
VM_DISK_SIZE="${VM_DISK_SIZE:-40G}"
VM_CPUS="${VM_CPUS:-4}"
VM_MEM="${VM_MEM:-6G}"
VM_SSH_PORT="${VM_SSH_PORT:-2222}"
VM_DISPLAY="${VM_DISPLAY:-none}"
VM_VNC="${VM_VNC:-}"
default_gpu() {
    # virtio-vga lives in the optional qemu-hw-display-virtio-gpu module on Arch;
    # fall back to the standard VGA device (guest bochs-drm KMS driver) without it.
    if qemu-system-x86_64 -device help 2>/dev/null | grep -q '"virtio-vga"'; then
        printf 'virtio-vga'
    else
        printf 'VGA'
    fi
}
VM_GPU="${VM_GPU:-$(default_gpu)}"
VM_OVMF_CODE="${VM_OVMF_CODE:-/usr/share/edk2/x64/OVMF_CODE.4m.fd}"
VM_OVMF_VARS="${VM_OVMF_VARS:-/usr/share/edk2/x64/OVMF_VARS.4m.fd}"
# shellcheck disable=SC2016 # pacman expands $repo/$arch itself.
VM_MIRROR="${VM_MIRROR:-https://geo.mirror.pkgbuild.com/\$repo/os/\$arch}"

disk="$VM_DIR/disk.qcow2"
vars="$VM_DIR/OVMF_VARS.4m.fd"
seed="$VM_DIR/seed-cidata.img"
key="$VM_DIR/id_ed25519"
qmp_sock="$VM_DIR/qmp.sock"
pidfile="$VM_DIR/qemu.pid"

# Direct slirp NAT is the default; a host proxy on loopback is reachable at 10.0.2.2.
guest_proxy="${VM_GUEST_PROXY:-}"

find_iso() {
    if [[ -n "${VM_ISO:-}" ]]; then
        [[ -r "$VM_ISO" ]] || die "VM_ISO is not readable: $VM_ISO"
        printf '%s' "$VM_ISO"
        return
    fi
    [[ -n "${ISO_PATH:-}" ]] || die "set ISO_PATH (read-only installer collection) or VM_ISO"
    local candidates=()
    local f
    for f in "$ISO_PATH"/archlinux-*-x86_64.iso; do
        [[ -r "$f" ]] && candidates+=("$f")
    done
    ((${#candidates[@]} > 0)) || die "no archlinux-*-x86_64.iso under \$ISO_PATH"
    printf '%s\n' "${candidates[@]}" | sort -V | tail -n 1
}

ssh_opts() {
    printf '%s\n' -i "$key" -p "$VM_SSH_PORT" \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o LogLevel=ERROR -o ConnectTimeout=5 -o BatchMode=yes \
        -o ServerAliveInterval=15
}

is_running() {
    [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null
}

record() {
    mkdir -p "$VM_EVIDENCE_DIR"
    printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >>"$VM_EVIDENCE_DIR/qemu-commands.log"
}

qemu_start() {
    # $1 = mode (install|boot)
    local mode="$1"
    is_running && die "VM already running (pid $(cat "$pidfile"))"
    [[ -r /dev/kvm && -w /dev/kvm ]] || die "/dev/kvm is not accessible to this user"
    [[ -f "$disk" ]] || die "missing disk $disk (run install)"
    [[ -f "$vars" ]] || cp -- "$VM_OVMF_VARS" "$vars"
    mkdir -p "$VM_EVIDENCE_DIR"
    rm -f -- "$qmp_sock"
    local serial_log
    serial_log="$VM_EVIDENCE_DIR/serial-$mode-$(date -u +%Y%m%dT%H%M%SZ).log"
    local args=(
        qemu-system-x86_64
        -name "$VM_NAME"
        -machine 'q35,accel=kvm'
        -cpu host
        -smp "$VM_CPUS"
        -m "$VM_MEM"
        -drive "if=pflash,format=raw,unit=0,readonly=on,file=$VM_OVMF_CODE"
        -drive "if=pflash,format=raw,unit=1,file=$vars"
        -drive "file=$disk,if=virtio,format=qcow2,cache=writeback"
        -netdev "user,id=n0,hostfwd=tcp:127.0.0.1:$VM_SSH_PORT-:22"
        -device 'virtio-net-pci,netdev=n0'
        -device "$VM_GPU"
        -device qemu-xhci -device usb-kbd -device usb-tablet
        -object 'rng-random,id=rng0,filename=/dev/urandom'
        -device 'virtio-rng-pci,rng=rng0'
        -display "$VM_DISPLAY"
        -monitor none
        -serial "file:$serial_log"
        -qmp "unix:$qmp_sock,server=on,wait=off"
        -pidfile "$pidfile"
    )
    if [[ "$mode" == install ]]; then
        local iso
        iso="$(find_iso)"
        [[ -f "$seed" ]] || die "missing seed $seed (run seed)"
        args+=(
            -drive "file=$iso,media=cdrom,readonly=on"
            -drive "file=$seed,if=virtio,format=raw,readonly=on"
            -boot order=d
        )
    fi
    if [[ -n "$VM_VNC" ]]; then
        args+=(-vnc "127.0.0.1:$VM_VNC")
    fi
    if [[ "$VM_DISPLAY" == none || "$VM_DISPLAY" == egl-headless* ]]; then
        args+=(-daemonize)
        record "${args[*]}"
        "${args[@]}"
    else
        record "${args[*]} (background, display=$VM_DISPLAY)"
        "${args[@]}" </dev/null &
        local bg_pid=$! i
        for (( i = 0; i < 30; i++ )); do
            [[ -f "$pidfile" ]] && break
            kill -0 "$bg_pid" 2>/dev/null || die "qemu exited before writing pidfile"
            sleep 0.1
        done
        [[ -f "$pidfile" ]] || die "timed out waiting for qemu pidfile: $pidfile"
    fi
    log "started ($mode), serial log: $serial_log"
}

wait_ssh() {
    local timeout="${1:-600}"
    local deadline=$((SECONDS + timeout))
    local opts
    mapfile -t opts < <(ssh_opts)
    until ssh "${opts[@]}" root@127.0.0.1 true 2>/dev/null; do
        is_running || die "VM exited while waiting for SSH"
        ((SECONDS < deadline)) || die "timed out waiting for SSH on 127.0.0.1:$VM_SSH_PORT"
        sleep 3
    done
    log "ssh is up on 127.0.0.1:$VM_SSH_PORT"
}

guest_ssh() {
    local user=root
    if [[ "${1:-}" == -u ]]; then
        user="$2"
        shift 2
    fi
    local opts
    mapfile -t opts < <(ssh_opts)
    # shellcheck disable=SC2029 # the command intentionally runs on the guest.
    ssh "${opts[@]}" "$user@127.0.0.1" "$@"
}

guest_scp() {
    local opts=(-i "$key" -P "$VM_SSH_PORT" -o StrictHostKeyChecking=no
        -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o BatchMode=yes)
    scp "${opts[@]}" "$@"
}

qmp() {
    # Sends QMP commands (one JSON object per argument) and prints each reply.
    [[ -S "$qmp_sock" ]] || die "QMP socket missing: $qmp_sock (VM not running?)"
    python3 - "$qmp_sock" "$@" <<'PY'
import json, socket, sys

sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.connect(sys.argv[1])
reader = sock.makefile("r")


def recv():
    while True:
        msg = json.loads(reader.readline())
        if "event" not in msg:
            return msg


recv()  # greeting
sock.sendall(b'{"execute": "qmp_capabilities"}\n')
recv()
status = 0
for raw in sys.argv[2:]:
    sock.sendall(raw.encode() + b"\n")
    reply = recv()
    print(json.dumps(reply))
    if "error" in reply:
        status = 1
sys.exit(status)
PY
}

cmd_seed() {
    mkdir -p "$VM_DIR"
    command -v mkfs.fat >/dev/null || die "mkfs.fat (dosfstools) is required"
    command -v mcopy >/dev/null || die "mcopy (mtools) is required"
    if [[ ! -f "$key" ]]; then
        ssh-keygen -q -t ed25519 -N '' -C "arch-vm-ephemeral-$VM_NAME" -f "$key"
    fi
    local work
    work="$(mktemp -d "${TMPDIR:-/tmp}/arch-vm-seed.XXXXXX")"
    printf 'instance-id: %s-%s\nlocal-hostname: archiso-%s\n' "$VM_NAME" "$(date +%s)" "$VM_NAME" >"$work/meta-data"
    {
        printf '#cloud-config\n'
        printf 'disable_root: false\n'
        printf 'users:\n  - name: root\n    ssh_authorized_keys:\n      - %s\n' "$(cat "$key.pub")"
    } >"$work/user-data"
    rm -f -- "$seed"
    mkfs.fat -n CIDATA -C "$seed" 2048 >/dev/null
    mcopy -i "$seed" "$work/user-data" "$work/meta-data" ::
    rm -rf -- "$work"
    log "seed written: $seed (key: $key)"
}

cmd_install() {
    [[ -f "$seed" ]] || cmd_seed
    if [[ ! -f "$disk" ]]; then
        qemu-img create -f qcow2 "$disk" "$VM_DISK_SIZE" >/dev/null
        record "qemu-img create -f qcow2 $disk $VM_DISK_SIZE"
    fi
    local iso
    iso="$(find_iso)"
    log "installer: $(basename -- "$iso")"
    qemu_start install
    wait_ssh 600
    local opts
    mapfile -t opts < <(ssh_opts)
    mkdir -p "$VM_EVIDENCE_DIR"
    # shellcheck disable=SC2029 # values are expanded on the host on purpose.
    ssh "${opts[@]}" root@127.0.0.1 \
        "GUEST_PROXY='$guest_proxy' MIRROR='$VM_MIRROR' SSH_PUBKEY='$(cat "$key.pub")' bash -s" \
        <"$script_dir/guest-install.sh" 2>&1 | tee "$VM_EVIDENCE_DIR/os-install.log"
    local rc="${PIPESTATUS[0]}"
    ((rc == 0)) || die "guest install failed (rc=$rc); VM left running for inspection"
    cmd_stop
    log "install complete: $disk"
}

cmd_boot() {
    qemu_start boot
    wait_ssh 300
}

cmd_stop() {
    is_running || {
        log "VM not running"
        return 0
    }
    qmp '{"execute": "system_powerdown"}' >/dev/null || true
    local deadline=$((SECONDS + 90))
    while is_running && ((SECONDS < deadline)); do
        sleep 2
    done
    if is_running; then
        log "ACPI power-off timed out; sending quit"
        qmp '{"execute": "quit"}' >/dev/null || true
        sleep 2
    fi
    rm -f -- "$pidfile" "$qmp_sock"
    log "stopped"
}

cmd_screendump() {
    local out="${1:-$VM_EVIDENCE_DIR/screen-$(date -u +%Y%m%dT%H%M%SZ).png}"
    mkdir -p "$(dirname -- "$out")"
    out="$(cd -- "$(dirname -- "$out")" && pwd)/$(basename -- "$out")"
    qmp "{\"execute\": \"screendump\", \"arguments\": {\"filename\": \"$out\", \"format\": \"png\"}}" >/dev/null
    printf '%s\n' "$out"
}

cmd_sendkey() {
    local combo keys json
    for combo in "$@"; do
        keys=""
        IFS=- read -ra parts <<<"$combo"
        local k
        for k in "${parts[@]}"; do
            keys+="${keys:+, }{\"type\": \"qcode\", \"data\": \"$k\"}"
        done
        json="{\"execute\": \"send-key\", \"arguments\": {\"keys\": [$keys]}}"
        qmp "$json" >/dev/null
        sleep 0.3
    done
}

cmd_snapshot() {
    local action="${1:-list}" name="${2:-clean-base}"
    is_running && die "stop the VM before changing internal snapshots"
    case "$action" in
        create) qemu-img snapshot -c "$name" "$disk" ;;
        apply) qemu-img snapshot -a "$name" "$disk" ;;
        delete) qemu-img snapshot -d "$name" "$disk" ;;
        list) qemu-img snapshot -l "$disk" ;;
        *) die "snapshot action must be create|apply|delete|list" ;;
    esac
    [[ "$action" == list ]] || record "qemu-img snapshot $action $name $disk"
}

cmd_status() {
    if is_running; then
        printf 'running pid=%s ssh=127.0.0.1:%s\n' "$(cat "$pidfile")" "$VM_SSH_PORT"
    else
        printf 'stopped\n'
    fi
    local vnc_desc=none
    [[ -n "$VM_VNC" ]] && vnc_desc="127.0.0.1:$VM_VNC"
    printf 'disk=%s\nevidence=%s\nguest_proxy=%s\ngpu=%s\ndisplay=%s\nvnc=%s\n' \
        "$disk" "$VM_EVIDENCE_DIR" "${guest_proxy:-none}" "$VM_GPU" "$VM_DISPLAY" "$vnc_desc"
}

main() {
    local cmd="${1:-}"
    [[ $# -gt 0 ]] && shift
    case "$cmd" in
        seed) cmd_seed ;;
        install) cmd_install ;;
        boot) cmd_boot ;;
        wait-ssh) wait_ssh "${1:-300}" ;;
        ssh) guest_ssh "$@" ;;
        scp-in)
            (($# >= 2)) || die "usage: scp-in SRC... DEST"
            local dest="${*: -1}"
            guest_scp "${@:1:$#-1}" "root@127.0.0.1:$dest"
            ;;
        scp-out)
            (($# == 2)) || die "usage: scp-out SRC DEST"
            guest_scp "root@127.0.0.1:$1" "$2"
            ;;
        screendump) cmd_screendump "$@" ;;
        sendkey) cmd_sendkey "$@" ;;
        qmp) qmp "$@" ;;
        stop) cmd_stop ;;
        snapshot) cmd_snapshot "$@" ;;
        status) cmd_status ;;
        -h | --help | help) usage ;;
        "") usage >&2; exit 2 ;;
        *) die "unknown command: $cmd (see --help)" ;;
    esac
}

main "$@"
