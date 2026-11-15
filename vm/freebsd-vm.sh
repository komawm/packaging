#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Manage the local FreeBSD 15.1 QEMU/KVM VM for Koma cross-platform build/test verification.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

resolve_workspace() {
    if [[ -n "${KOMA_WORKSPACE:-}" ]]; then
        printf '%s\n' "$KOMA_WORKSPACE"
        return 0
    fi
    local dir
    dir="$(cd "$SCRIPT_DIR/.." && pwd)"
    while [[ "$dir" != "/" ]]; do
        if [[ -d "$dir/koma" || -f "$dir/workspace.toml" ]]; then
            printf '%s\n' "$dir"
            return 0
        fi
        dir="$(dirname "$dir")"
    done
    (cd "$SCRIPT_DIR/../.." && pwd)
}

KOMA_WORKSPACE="$(resolve_workspace)"
VM_NAME="${VM_NAME:-freebsd-koma}"
VM_DIR="${VM_DIR:-$KOMA_WORKSPACE/recording/vm/$VM_NAME}"
VM_EVIDENCE_DIR="${VM_EVIDENCE_DIR:-$KOMA_WORKSPACE/recording/$(date +%F)/vm-$VM_NAME}"
VM_SSH_PORT="${VM_SSH_PORT:-2223}"
VM_DISK_SIZE="${VM_DISK_SIZE:-32G}"
VM_CPUS="${VM_CPUS:-4}"
VM_MEM="${VM_MEM:-6G}"
VM_FS="${VM_FS:-zfs}"
VM_GUEST_PROXY="${VM_GUEST_PROXY:-}"

DISK="$VM_DIR/disk.qcow2"
SEED="$VM_DIR/seed.img"
OVMF_VARS="$VM_DIR/OVMF_VARS.4m.fd"
QMP_SOCK="$VM_DIR/qmp.sock"
PID_FILE="$VM_DIR/qemu.pid"
SERIAL_SOCK="$VM_DIR/serial.sock"

die() {
    printf 'freebsd-vm: %s\n' "$*" >&2
    exit 1
}

log() {
    printf '[freebsd-vm] %s\n' "$*"
}

require_tools() {
    local t
    for t in "$@"; do
        command -v "$t" >/dev/null 2>&1 || die "missing host tool: $t"
    done
}

resolve_ovmf_code() {
    if [[ -n "${VM_OVMF_CODE:-}" ]]; then
        [[ -r "$VM_OVMF_CODE" ]] || die "VM_OVMF_CODE not readable: $VM_OVMF_CODE"
        printf '%s\n' "$VM_OVMF_CODE"
        return 0
    fi
    local candidate
    for candidate in \
        /usr/share/edk2/x64/OVMF_CODE.4m.fd \
        /usr/share/edk2-ovmf/x64/OVMF_CODE.4m.fd \
        /usr/share/OVMF/OVMF_CODE_4M.fd \
        /usr/share/OVMF/OVMF_CODE.fd; do
        if [[ -r "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    die "OVMF code firmware not found (set VM_OVMF_CODE)"
}

resolve_ovmf_vars_template() {
    if [[ -n "${VM_OVMF_VARS:-}" ]]; then
        [[ -r "$VM_OVMF_VARS" ]] || die "VM_OVMF_VARS not readable: $VM_OVMF_VARS"
        printf '%s\n' "$VM_OVMF_VARS"
        return 0
    fi
    local candidate
    for candidate in \
        /usr/share/edk2/x64/OVMF_VARS.4m.fd \
        /usr/share/edk2-ovmf/x64/OVMF_VARS.4m.fd \
        /usr/share/OVMF/OVMF_VARS_4M.fd \
        /usr/share/OVMF/OVMF_VARS.fd; do
        if [[ -r "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    die "OVMF vars template not found (set VM_OVMF_VARS)"
}

resolve_iso() {
    if [[ -n "${VM_ISO:-}" ]]; then
        [[ -r "$VM_ISO" ]] || die "VM_ISO not readable: $VM_ISO"
        printf '%s\n' "$VM_ISO"
        return 0
    fi
    [[ -n "${ISO_PATH:-}" ]] || die "ISO_PATH is not set and VM_ISO was not provided"
    [[ -d "$ISO_PATH" ]] || die "ISO_PATH does not exist: $ISO_PATH"
    local preferred="$ISO_PATH/FreeBSD-15.1-RELEASE-amd64-disc1.iso"
    if [[ -r "$preferred" ]]; then
        printf '%s\n' "$preferred"
        return 0
    fi
    local candidate=""
    while IFS= read -r path; do
        if [[ -r "$path" ]]; then
            candidate="$path"
            break
        fi
    done < <(find "$ISO_PATH" -maxdepth 1 -type f -name 'FreeBSD-*-amd64-disc1.iso' -print 2>/dev/null | sort -r)
    [[ -n "$candidate" ]] || die "no FreeBSD-*-amd64-disc1.iso found in ISO_PATH=$ISO_PATH"
    printf '%s\n' "$candidate"
}

resolve_ssh_pubkey() {
    if [[ -n "${VM_SSH_KEY:-}" ]]; then
        [[ -r "$VM_SSH_KEY" ]] || die "VM_SSH_KEY not readable: $VM_SSH_KEY"
        printf '%s\n' "$VM_SSH_KEY"
        return 0
    fi
    local candidate
    for candidate in \
        "$HOME/.ssh/id_ed25519.pub" \
        "$HOME/.ssh/id_ecdsa.pub" \
        "$HOME/.ssh/id_rsa.pub"; do
        if [[ -r "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    die "no SSH public key found (checked ~/.ssh/id_{ed25519,ecdsa,rsa}.pub; set VM_SSH_KEY)"
}

resolve_ssh_identity_args() {
    local pub=""
    if pub="$(resolve_ssh_pubkey 2>/dev/null)"; then
        local priv="${pub%.pub}"
        if [[ -r "$priv" && "$priv" != "$pub" ]]; then
            printf '%s\n' "$priv"
            return 0
        fi
    fi
    return 1
}

validate_fs_mode() {
    case "$VM_FS" in
        zfs | ufs) ;;
        *) die "unsupported VM_FS='$VM_FS' (expected 'zfs' or 'ufs')" ;;
    esac
}

accel_args() {
    local mode="${VM_ACCEL:-auto}"
    case "$mode" in
        auto)
            if [[ -r /dev/kvm && -w /dev/kvm ]]; then
                printf '%s\n' "-enable-kvm" "-cpu" "host"
            else
                log "KVM unavailable in this environment; using TCG emulation (-cpu max)"
                printf '%s\n' "-machine" "accel=tcg" "-cpu" "max"
            fi
            ;;
        kvm)
            [[ -r /dev/kvm && -w /dev/kvm ]] || die "VM_ACCEL=kvm requested but /dev/kvm is unavailable"
            printf '%s\n' "-enable-kvm" "-cpu" "host"
            ;;
        tcg)
            printf '%s\n' "-machine" "accel=tcg" "-cpu" "max"
            ;;
        *) die "unsupported VM_ACCEL='$mode' (expected auto, kvm, or tcg)" ;;
    esac
}

is_running() {
    [[ -f "$PID_FILE" ]] || return 1
    local pid
    pid="$(<"$PID_FILE")"
    [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

ensure_evidence_dir() {
    mkdir -p "$VM_EVIDENCE_DIR"
}

proxy_env_prefix() {
    if [[ -n "$VM_GUEST_PROXY" ]]; then
        printf 'export http_proxy=%q https_proxy=%q HTTP_PROXY=%q HTTPS_PROXY=%q no_proxy=%q NO_PROXY=%q' \
            "$VM_GUEST_PROXY" "$VM_GUEST_PROXY" "$VM_GUEST_PROXY" "$VM_GUEST_PROXY" \
            "127.0.0.1,localhost,10.0.2.2" "127.0.0.1,localhost,10.0.2.2"
    fi
}

ssh_base_args() {
    local args=(
        -p "$VM_SSH_PORT"
        -o BatchMode=yes
        -o ConnectTimeout=5
        -o StrictHostKeyChecking=no
        -o UserKnownHostsFile=/dev/null
        -o LogLevel=ERROR
    )
    local priv=""
    if priv="$(resolve_ssh_identity_args)"; then
        args+=(-i "$priv" -o IdentitiesOnly=yes)
    fi
    printf '%s\n' "${args[@]}"
}

run_ssh() {
    local user="root"
    if [[ "${1:-}" == "-u" ]]; then
        user="${2:?missing username after -u}"
        shift 2
    fi
    local opts=()
    mapfile -t opts < <(ssh_base_args)
    if (($# == 0)); then
        exec ssh "${opts[@]}" "${user}@127.0.0.1"
    fi
    local env_prefix
    env_prefix="$(proxy_env_prefix)"
    # shellcheck disable=SC2029 # Intentional client-side proxy env and remote command string.
    if [[ -n "$env_prefix" ]]; then
        ssh "${opts[@]}" "${user}@127.0.0.1" "${env_prefix}; $*"
    else
        ssh "${opts[@]}" "${user}@127.0.0.1" "$*"
    fi
}

build_seed_image() {
    require_tools mkfs.fat mcopy python3
    validate_fs_mode
    local pubkey_file pubkey_line
    pubkey_file="$(resolve_ssh_pubkey)"
    pubkey_line="$(tr -d '\r\n' <"$pubkey_file")"
    [[ -n "$pubkey_line" ]] || die "empty SSH public key: $pubkey_file"

    mkdir -p "$VM_DIR"
    local workdir
    workdir="$(mktemp -d)"
    trap 'rm -rf "${workdir:-}"' RETURN

    if [[ "$VM_FS" == "zfs" ]]; then
        cat >"$workdir/inst.cfg" <<EOF
export nonInteractive="YES"
export ZFSBOOT_DISKS="vtbd0"
export ZFSBOOT_VDEV_TYPE="stripe"
export ZFSBOOT_BOOT_TYPE="UEFI"
export ZFSBOOT_SWAP_SIZE="2g"
export ZFSBOOT_CONFIRM_LAYOUT=""
#!/bin/sh
set -eux
EOF
    else
        cat >"$workdir/inst.cfg" <<EOF
export nonInteractive="YES"
export PARTITIONS="vtbd0 GPT { 260M efi , auto freebsd-ufs /, 2G freebsd-swap }"
#!/bin/sh
set -eux
EOF
    fi

    {
        cat <<EOF
sysrc hostname="${VM_NAME}"
sysrc ifconfig_DEFAULT="DHCP"
sysrc sshd_enable="YES"
sysrc sendmail_enable="NONE"
sysrc dbus_enable="YES"
sysrc seatd_enable="YES"
EOF
        if [[ "$VM_FS" == "zfs" ]]; then
            printf 'sysrc zfs_enable="YES"\n'
        fi
        cat <<'EOF'
if ! grep -q '^boot_multicons=' /boot/loader.conf 2>/dev/null; then
    printf 'boot_multicons="YES"\nboot_serial="YES"\nautoboot_delay="2"\n' >> /boot/loader.conf
fi
mkdir -p /root/.ssh
chmod 700 /root/.ssh
cat <<'__KOMA_SSH_PUBKEY__' > /root/.ssh/authorized_keys
EOF
        printf '%s\n' "$pubkey_line"
        cat <<'EOF'
__KOMA_SSH_PUBKEY__
chmod 600 /root/.ssh/authorized_keys

if ! pw usershow tester >/dev/null 2>&1; then
    pw useradd tester -m -G wheel,video -s /bin/sh
fi
mkdir -p /home/tester/.ssh
chown tester:tester /home/tester/.ssh
chmod 700 /home/tester/.ssh
cp /root/.ssh/authorized_keys /home/tester/.ssh/authorized_keys
chown tester:tester /home/tester/.ssh/authorized_keys
chmod 600 /home/tester/.ssh/authorized_keys

cat <<'__KOMA_SSHD__' >> /etc/ssh/sshd_config
PermitRootLogin prohibit-password
PubkeyAuthentication yes
PasswordAuthentication no
UseDNS no
__KOMA_SSHD__

mkdir -p /usr/local/etc/pkg/repos
rm -f /usr/local/etc/pkg/repos/FreeBSD.conf
cat <<'__KOMA_PKG_REPO__' > /usr/local/etc/pkg/repos/99-koma-vm.conf
FreeBSD-ports: { enabled: no }
FreeBSD-ports-kmods: { enabled: no }
FreeBSD-base: { enabled: no }
FreeBSD: {
  url: "https://pkg.FreeBSD.org/${ABI}/latest",
  signature_type: "fingerprints",
  fingerprints: "/usr/share/keys/pkg",
  enabled: yes
}
__KOMA_PKG_REPO__

ssh-keygen -A
EOF
    } >>"$workdir/inst.cfg"

    cat >"$workdir/install.sh" <<'EOF'
#!/bin/sh
trap 'echo "===FREEBSD_INSTALL_FAILED==="' EXIT
set -eux

kldload -nq zfs 2>/dev/null || true
mount -t tmpfs tmpfs /var
mkdir -p /var/db /var/run /var/tmp /var/log /var/empty

for p in $(zpool list -H -o name 2>/dev/null || true); do
    zpool destroy -f "$p" 2>/dev/null || true
done
gpart destroy -F vtbd0 2>/dev/null || true

export TERM="vt100"
stty rows 25 cols 80 2>/dev/null || true
export nonInteractive="YES"
export BSDINSTALL_CHROOT="/mnt"
export BSDINSTALL_TMPETC="/tmp/bsdinstall_etc"
export BSDINSTALL_TMPBOOT="/tmp/bsdinstall_boot"
export PATH_FSTAB="/tmp/bsdinstall_etc/fstab"
if [ -f /usr/freebsd-dist/base.txz ] && [ -f /usr/freebsd-dist/kernel.txz ]; then
    export BSDINSTALL_DISTDIR="/usr/freebsd-dist"
    export DISTRIBUTIONS="kernel.txz base.txz"
elif [ -f /usr/freebsd-packages/repos/FreeBSD-base-offline.conf ]; then
    export DISTRIBUTIONS=""
    export COMPONENTS="base"
    export BSDINSTALL_PKG_REPOS_DIR="/usr/freebsd-packages/repos"
else
    export DISTRIBUTIONS=""
    export COMPONENTS="base"
fi
mkdir -p /tmp/bsdinstall_etc /tmp/bsdinstall_boot

bsdinstall script /tmp/seed/inst.cfg </dev/null

sync
if [ -x /sbin/zpool ]; then
    zpool export zroot 2>/dev/null || zpool export -f zroot 2>/dev/null || true
fi
sync

trap - EXIT
echo "===FREEBSD_INSTALL_DONE==="
EOF

    chmod 0644 "$workdir/inst.cfg"
    chmod 0755 "$workdir/install.sh"

    rm -f "$SEED"
    mkfs.fat -F 16 -n FBSDSEED -C "$SEED" 16384 >/dev/null
    mcopy -i "$SEED" "$workdir/inst.cfg" ::inst.cfg
    mcopy -i "$SEED" "$workdir/install.sh" ::install.sh
    rm -rf "$workdir"
    trap - RETURN
    log "built seed image ($VM_FS): $SEED"
}

cmd_seed() {
    build_seed_image
}

cmd_install() {
    require_tools qemu-system-x86_64 qemu-img mkfs.fat mcopy python3 ssh scp
    is_running && die "VM is already running (pid $(<"$PID_FILE")); stop it first"
    validate_fs_mode

    local iso ovmf_code ovmf_vars_tpl
    iso="$(resolve_iso)"
    ovmf_code="$(resolve_ovmf_code)"
    ovmf_vars_tpl="$(resolve_ovmf_vars_template)"

    mkdir -p "$VM_DIR"
    ensure_evidence_dir
    build_seed_image

    log "creating $VM_DISK_SIZE disk image: $DISK"
    rm -f "$DISK" "$OVMF_VARS" "$SERIAL_SOCK" "$QMP_SOCK" "$PID_FILE"
    qemu-img create -f qcow2 "$DISK" "$VM_DISK_SIZE" >/dev/null
    cp "$ovmf_vars_tpl" "$OVMF_VARS"

    local accel=()
    mapfile -t accel < <(accel_args)
    local serial_log="$VM_EVIDENCE_DIR/install-serial.log"
    local qemu_cmd=(
        qemu-system-x86_64
        -name "$VM_NAME-install"
        -machine q35
        "${accel[@]}"
        -m "$VM_MEM"
        -smp "$VM_CPUS"
        -drive "if=pflash,format=raw,readonly=on,file=$ovmf_code"
        -drive "if=pflash,format=raw,file=$OVMF_VARS"
        -drive "file=$DISK,if=virtio,format=qcow2"
        -drive "file=$SEED,if=virtio,format=raw,readonly=on"
        -drive "file=$iso,media=cdrom,readonly=on,index=0"
        -netdev "user,id=net0,hostfwd=tcp:127.0.0.1:${VM_SSH_PORT}-:22"
        -device "virtio-net-pci,netdev=net0"
        -device virtio-vga
        -device qemu-xhci
        -device usb-kbd
        -device usb-tablet
        -display none
        -serial "unix:$SERIAL_SOCK"
        -monitor none
        -no-reboot
    )

    log "installing FreeBSD from $iso (fs=$VM_FS, log=$serial_log)"
    python3 - "$SERIAL_SOCK" "$serial_log" "${qemu_cmd[@]}" <<'PY'
import os
import re
import socket
import subprocess
import sys
import time

serial_sock = sys.argv[1]
serial_log = sys.argv[2]
qemu_cmd = sys.argv[3:]

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-Z\\-_78]|\r")

if os.path.exists(serial_sock):
    os.unlink(serial_sock)

srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(serial_sock)
srv.listen(1)
srv.settimeout(0.5)

proc = subprocess.Popen(qemu_cmd)
conn = None
deadline_accept = time.monotonic() + 20.0
while conn is None:
    if proc.poll() is not None:
        srv.close()
        raise SystemExit(f"QEMU exited prematurely with code {proc.returncode}")
    if time.monotonic() >= deadline_accept:
        proc.terminate()
        srv.close()
        raise SystemExit("Timed out waiting for QEMU serial socket connection")
    try:
        conn, _ = srv.accept()
    except TimeoutError:
        continue

conn.settimeout(0.5)
raw_log = open(serial_log, "wb")
clean_buf = ""


def pump(timeout: float) -> bytes:
    global clean_buf
    end = time.monotonic() + timeout
    got = bytearray()
    while time.monotonic() < end:
        try:
            chunk = conn.recv(4096)
        except TimeoutError:
            if proc.poll() is not None:
                break
            continue
        if not chunk:
            break
        raw_log.write(chunk)
        raw_log.flush()
        got.extend(chunk)
        text = chunk.decode("utf-8", errors="replace")
        clean_buf = (clean_buf + ANSI_RE.sub("", text))[-32768:]
        break
    return bytes(got)


def wait_for(patterns: list[str], timeout: float, label: str, idle_poke: bytes | None = None) -> str:
    compiled = [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in patterns]
    deadline = time.monotonic() + timeout
    last_rx = time.monotonic()
    poked = False
    while time.monotonic() < deadline:
        for pat in compiled:
            m = pat.search(clean_buf)
            if m:
                return pat.pattern
        if re.search(r"^===FREEBSD_INSTALL_FAILED===\s*$", clean_buf, re.MULTILINE):
            raise RuntimeError("guest reported ===FREEBSD_INSTALL_FAILED===")
        rx = pump(0.5)
        if rx:
            last_rx = time.monotonic()
            poked = False
        elif idle_poke and not poked and (time.monotonic() - last_rx) > 5.0:
            conn.sendall(idle_poke)
            poked = True
        if proc.poll() is not None:
            for pat in compiled:
                if pat.search(clean_buf):
                    return pat.pattern
            raise RuntimeError(f"QEMU exited ({proc.returncode}) while waiting for {label}")
    raise TimeoutError(f"timed out after {timeout}s waiting for {label}\n--- tail ---\n{clean_buf[-1500:]}")


try:
    matched = wait_for(
        [r"Autoboot in \d+ seconds", r"Escape to loader prompt", r"Type '\?' for a list", r"\nOK "],
        timeout=90.0,
        label="FreeBSD loader menu",
    )
    if "Type" not in matched and r"\nOK" not in matched:
        clean_buf = ""
        conn.sendall(b"3")
        try:
            wait_for([r"Type '\?' for a list", r"(?:^|\n)OK\s*$"], timeout=6.0, label="loader OK prompt")
        except TimeoutError:
            conn.sendall(b"\x1b")
            wait_for([r"Type '\?' for a list", r"(?:^|\n)OK\s*$"], timeout=10.0, label="loader OK prompt (Esc)")

    for line in ("set boot_multicons=YES\r", "set boot_serial=YES\r", "boot -s\r"):
        clean_buf = ""
        conn.sendall(line.encode("ascii"))
        if line != "boot -s\r":
            wait_for([r"(?:^|\n)OK\s*$"], timeout=10.0, label=f"OK after {line.strip()}")

    wait_for([r"RETURN for /bin/sh:"], timeout=180.0, label="single-user shell prompt")
    clean_buf = ""
    conn.sendall(b"\r")
    wait_for([r"(?:^|\n)(?:root@[^\n#]*#\s*|#\s*)$"], timeout=60.0, label="single-user # prompt", idle_poke=b"\r")

    clean_buf = ""
    bootstrap = (
        "kldload -nq tmpfs msdosfs 2>/dev/null || true; "
        "mount -t tmpfs tmpfs /tmp && mkdir -p /tmp/seed && "
        "for d in vtbd1 vtbd2 da1 da0; do "
        "mount -t msdosfs -o ro /dev/$d /tmp/seed 2>/dev/null && "
        "[ -f /tmp/seed/install.sh ] && break; done && "
        "/bin/sh /tmp/seed/install.sh\r"
    )
    conn.sendall(bootstrap.encode("ascii"))
    wait_for([r"^===FREEBSD_INSTALL_DONE===\s*$"], timeout=1800.0, label="===FREEBSD_INSTALL_DONE===")
    wait_for(
        [r"(?:^|\n)(?:root@[^\n#]*#\s*|#\s*)$"],
        timeout=15.0,
        label="post-install # prompt",
        idle_poke=b"\r",
    )

    conn.sendall(b"sync; halt -p -q\r")
    shutdown_start = time.monotonic()
    sent_fallback = False
    while proc.poll() is None:
        if time.monotonic() - shutdown_start >= 90.0:
            raise TimeoutError(
                f"QEMU did not power off after halt -p -q\n--- tail ---\n{clean_buf[-1500:]}"
            )
        if not sent_fallback and (time.monotonic() - shutdown_start) >= 15.0:
            conn.sendall(b"\rpoweroff -q\r")
            sent_fallback = True
        pump(0.5)

    proc.wait(timeout=5)
    if proc.returncode != 0:
        raise RuntimeError(f"QEMU exited with code {proc.returncode} after halt")
except Exception:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    raise
finally:
    raw_log.close()
    conn.close()
    srv.close()
    if os.path.exists(serial_sock):
        os.unlink(serial_sock)
PY

    log "base OS installation finished; booting once to provision packages"
    cmd_boot
    cmd_wait_ssh 240
    cmd_provision
    cmd_stop
    log "FreeBSD 15.1 VM installed and provisioned cleanly ($DISK)"
}

cmd_boot() {
    require_tools qemu-system-x86_64
    is_running && die "VM is already running (pid $(<"$PID_FILE"))"
    [[ -f "$DISK" ]] || die "disk image missing: $DISK (run 'install' first)"

    local ovmf_code
    ovmf_code="$(resolve_ovmf_code)"
    if [[ ! -f "$OVMF_VARS" ]]; then
        cp "$(resolve_ovmf_vars_template)" "$OVMF_VARS"
    fi

    ensure_evidence_dir
    rm -f "$QMP_SOCK" "$PID_FILE"
    local accel=()
    mapfile -t accel < <(accel_args)
    local serial_log="$VM_EVIDENCE_DIR/boot-serial.log"

    qemu-system-x86_64 \
        -name "$VM_NAME" \
        -machine q35 \
        "${accel[@]}" \
        -m "$VM_MEM" \
        -smp "$VM_CPUS" \
        -drive "if=pflash,format=raw,readonly=on,file=$ovmf_code" \
        -drive "if=pflash,format=raw,file=$OVMF_VARS" \
        -drive "file=$DISK,if=virtio,format=qcow2" \
        -netdev "user,id=net0,hostfwd=tcp:127.0.0.1:${VM_SSH_PORT}-:22" \
        -device "virtio-net-pci,netdev=net0" \
        -device virtio-vga \
        -device qemu-xhci \
        -device usb-kbd \
        -device usb-tablet \
        -display none \
        -serial "file:$serial_log" \
        -qmp "unix:$QMP_SOCK,server,nowait" \
        -pidfile "$PID_FILE" \
        -daemonize

    log "booted $VM_NAME (pid $(<"$PID_FILE"), ssh=127.0.0.1:$VM_SSH_PORT, serial=$serial_log)"
}

cmd_wait_ssh() {
    require_tools ssh
    local timeout="${1:-180}"
    local deadline=$((SECONDS + timeout))
    while ((SECONDS < deadline)); do
        if run_ssh "true" >/dev/null 2>&1; then
            log "SSH ready on 127.0.0.1:$VM_SSH_PORT"
            return 0
        fi
        if ! is_running; then
            die "VM stopped while waiting for SSH (check $VM_EVIDENCE_DIR/boot-serial.log)"
        fi
        sleep 2
    done
    die "timed out after ${timeout}s waiting for SSH on 127.0.0.1:$VM_SSH_PORT"
}

cmd_ssh() {
    require_tools ssh
    run_ssh "$@"
}

cmd_scp_in() {
    require_tools scp
    (($# == 2)) || die "usage: scp-in <local-path> <remote-path>"
    local opts=()
    mapfile -t opts < <(ssh_base_args)
    local scp_opts=()
    local i=0
    while ((i < ${#opts[@]})); do
        if [[ "${opts[$i]}" == "-p" ]]; then
            scp_opts+=(-P "${opts[$((i + 1))]}")
            i=$((i + 2))
        else
            scp_opts+=("${opts[$i]}")
            i=$((i + 1))
        fi
    done
    scp -r "${scp_opts[@]}" "$1" "root@127.0.0.1:$2"
}

cmd_scp_out() {
    require_tools scp
    (($# == 2)) || die "usage: scp-out <remote-path> <local-path>"
    local opts=()
    mapfile -t opts < <(ssh_base_args)
    local scp_opts=()
    local i=0
    while ((i < ${#opts[@]})); do
        if [[ "${opts[$i]}" == "-p" ]]; then
            scp_opts+=(-P "${opts[$((i + 1))]}")
            i=$((i + 2))
        else
            scp_opts+=("${opts[$i]}")
            i=$((i + 1))
        fi
    done
    scp -r "${scp_opts[@]}" "root@127.0.0.1:$1" "$2"
}

cmd_provision() {
    require_tools ssh
    ensure_evidence_dir
    log "provisioning FreeBSD packages and sudo configuration"
    run_ssh "set -eu
        export ASSUME_ALWAYS_YES=yes
        export BATCH=yes
        export FETCH_TIMEOUT=30
        export FETCH_RETRY=3
        retry_pkg() {
            attempt=1
            while [ \"\$attempt\" -le 5 ]; do
                if \"\$@\"; then
                    return 0
                fi
                if [ \"\$attempt\" -eq 5 ]; then
                    return 1
                fi
                attempt=\$((attempt + 1))
                sleep 3
            done
        }
        mkdir -p /usr/local/etc/pkg/repos
        rm -f /usr/local/etc/pkg/repos/FreeBSD.conf
        cat <<'__KOMA_PKG_REPO__' > /usr/local/etc/pkg/repos/99-koma-vm.conf
FreeBSD-ports: { enabled: no }
FreeBSD-ports-kmods: { enabled: no }
FreeBSD-base: { enabled: no }
FreeBSD: {
  url: \"https://pkg.FreeBSD.org/\${ABI}/latest\",
  signature_type: \"fingerprints\",
  fingerprints: \"/usr/share/keys/pkg\",
  enabled: yes
}
__KOMA_PKG_REPO__
        pkg -N >/dev/null 2>&1 || retry_pkg pkg bootstrap -f
        retry_pkg pkg update
        retry_pkg pkg install -y \
            pkgconf git curl ca_root_nss sudo python3 \
            dbus libdrm fontconfig libinput \
            seatd libudev-devd wayland libxkbcommon mesa-libs mesa-dri pixman sqlite3
        retry_pkg pkg install -y rustup-init || true
        mkdir -p /usr/local/etc/sudoers.d
        printf '%s\n' '%wheel ALL=(ALL:ALL) NOPASSWD: ALL' > /usr/local/etc/sudoers.d/wheel
        chmod 440 /usr/local/etc/sudoers.d/wheel
        uname -a
        pkg info -q | sort
    " | tee "$VM_EVIDENCE_DIR/provision.log"
}

cmd_sync_repo() {
    require_tools ssh tar
    local src="${1:-$KOMA_WORKSPACE/koma}"
    [[ -d "$src" ]] || die "koma repository directory not found: $src"
    log "syncing $src -> guest:/root/koma-src (excluding .git, target, .worktrees, recording)"
    tar -C "$src" \
        --exclude='.git' \
        --exclude='target' \
        --exclude='.worktrees' \
        --exclude='.carryctx' \
        --exclude='recording' \
        -cf - . | run_ssh "rm -rf /root/koma-src && mkdir -p /root/koma-src && tar -xf - -C /root/koma-src"
}

cmd_koma_check() {
    require_tools ssh
    ensure_evidence_dir
    local guest_dir="${1:-/root/koma-src}"
    local log_file="$VM_EVIDENCE_DIR/koma-check.log"
    local guest_dir_q
    printf -v guest_dir_q '%q' "$guest_dir"
    log "running FreeBSD cargo check/test in guest:$guest_dir (log=$log_file)"
    run_ssh "set -eu
        export PATH=\"\$HOME/.cargo/bin:/usr/local/bin:\$PATH\"
        export PKG_CONFIG_PATH=\"/usr/local/libdata/pkgconfig:/usr/local/lib/pkgconfig:\${PKG_CONFIG_PATH:-}\"
        cd $guest_dir_q
        req_channel=''
        if [ -f rust-toolchain.toml ]; then
            req_channel=\$(sed -n 's/^[[:space:]]*channel[[:space:]]*=[[:space:]]*\"\([^\"]*\)\".*/\1/p' rust-toolchain.toml | head -n1)
        fi
        need_rustup=0
        if ! command -v rustc >/dev/null 2>&1 || ! command -v cargo >/dev/null 2>&1; then
            need_rustup=1
        elif [ -n \"\$req_channel\" ]; then
            cur_ver=\$(rustc --version | awk '{print \$2}')
            case \"\$req_channel\" in
                [0-9]*)
                    if ! awk -v cur=\"\$cur_ver\" -v req=\"\$req_channel\" 'BEGIN {
                        split(cur, c, /[^0-9]+/); split(req, r, /[^0-9]+/);
                        for (i = 1; i <= 3; i++) {
                            cv = c[i] + 0; rv = r[i] + 0;
                            if (cv < rv) exit 1;
                            if (cv > rv) exit 0;
                        }
                        exit 0;
                    }'; then
                        need_rustup=1
                    fi
                    ;;
                *)
                    case \"\$(rustc --version)\" in
                        *\"\$req_channel\"*) ;;
                        *) need_rustup=1 ;;
                    esac
                    ;;
            esac
        fi
        if [ \"\$need_rustup\" -eq 1 ]; then
            target_tc=\"\${req_channel:-stable}\"
            rustup_ok=1
            if ! command -v rustup >/dev/null 2>&1; then
                if command -v rustup-init >/dev/null 2>&1; then
                    rustup-init -y --no-modify-path --profile minimal --default-toolchain \"\$target_tc\" || rustup_ok=0
                else
                    curl -fsSL https://sh.rustup.rs | sh -s -- -y --no-modify-path --profile minimal --default-toolchain \"\$target_tc\" || rustup_ok=0
                fi
                export PATH=\"\$HOME/.cargo/bin:\$PATH\"
            else
                rustup toolchain install \"\$target_tc\" --profile minimal || rustup_ok=0
                rustup override set \"\$target_tc\" || rustup_ok=0
            fi
            if [ \"\$rustup_ok\" -eq 0 ] || ! command -v cargo >/dev/null 2>&1; then
                attempt=1
                while [ \"\$attempt\" -le 5 ]; do
                    if env ASSUME_ALWAYS_YES=yes BATCH=yes FETCH_TIMEOUT=30 FETCH_RETRY=3 pkg install -y rust; then
                        break
                    fi
                    if [ \"\$attempt\" -eq 5 ]; then
                        exit 1
                    fi
                    attempt=\$((attempt + 1))
                    sleep 3
                done
            fi
        fi
        uname -a
        rustc --version
        cargo --version
        cargo check --workspace --all-targets --locked
        cargo test --workspace --locked
    " 2>&1 | tee "$log_file"
}

qmp_call() {
    require_tools python3
    [[ -S "$QMP_SOCK" ]] || die "QMP socket not found: $QMP_SOCK"
    python3 - "$QMP_SOCK" "$@" <<'PY'
import json
import socket
import sys

sock_path = sys.argv[1]
cmds = [json.loads(arg) for arg in sys.argv[2:]]

s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(15.0)
s.connect(sock_path)
f = s.makefile("rw", encoding="utf-8", newline="\n")
greeting = json.loads(f.readline())
assert "QMP" in greeting, greeting
f.write(json.dumps({"execute": "qmp_capabilities"}) + "\n")
f.flush()
cap_res = json.loads(f.readline())
if "error" in cap_res:
    raise SystemExit(f"QMP qmp_capabilities error: {cap_res}")

for cmd in cmds:
    f.write(json.dumps(cmd) + "\n")
    f.flush()
    while True:
        line = f.readline()
        if not line:
            raise SystemExit("EOF from QMP socket")
        msg = json.loads(line)
        if "event" in msg:
            continue
        if "error" in msg:
            raise SystemExit(f"QMP error: {msg}")
        print(json.dumps(msg))
        break
s.close()
PY
}

cmd_qmp() {
    (($# >= 1)) || die "usage: qmp <json-command>"
    qmp_call "$@"
}

cmd_sendkey() {
    (($# >= 1)) || die "usage: sendkey <qcode...>"
    local keys_json
    keys_json="$(python3 -c 'import json, sys; print(json.dumps([{"type": "qcode", "data": k} for k in sys.argv[1:]]))' "$@")"
    qmp_call "{\"execute\":\"send-key\",\"arguments\":{\"keys\":$keys_json}}"
}

cmd_screendump() {
    local out="${1:?usage: screendump <out.png>}"
    mkdir -p "$(dirname "$out")"
    local ppm
    ppm="$(mktemp --suffix=.ppm)"
    trap 'rm -f "${ppm:-}"' RETURN
    qmp_call "{\"execute\":\"screendump\",\"arguments\":{\"filename\":\"$ppm\"}}" >/dev/null
    if command -v ffmpeg >/dev/null 2>&1; then
        ffmpeg -y -loglevel error -i "$ppm" "$out"
    elif command -v magick >/dev/null 2>&1; then
        magick "$ppm" "$out"
    else
        python3 - "$ppm" "$out" <<'PY'
import struct
import sys
import zlib

with open(sys.argv[1], "rb") as fh:
    raw = fh.read()
if not raw.startswith(b"P6"):
    raise SystemExit("unsupported PPM format (expected P6)")
idx = 2
tokens = []
while len(tokens) < 3:
    while idx < len(raw) and raw[idx:idx + 1] in b" \t\r\n":
        idx += 1
    if raw[idx:idx + 1] == b"#":
        while idx < len(raw) and raw[idx:idx + 1] != b"\n":
            idx += 1
        continue
    start = idx
    while idx < len(raw) and raw[idx:idx + 1] not in b" \t\r\n":
        idx += 1
    tokens.append(int(raw[start:idx]))
idx += 1
w, h, _maxv = tokens
pixels = raw[idx:idx + w * h * 3]
scanlines = b"".join(b"\x00" + pixels[y * w * 3:(y + 1) * w * 3] for y in range(h))

def chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

png = (
    b"\x89PNG\r\n\x1a\n"
    + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    + chunk(b"IDAT", zlib.compress(scanlines, 6))
    + chunk(b"IEND", b"")
)
with open(sys.argv[2], "wb") as out:
    out.write(png)
PY
    fi
    rm -f "$ppm"
    trap - RETURN
    log "screendump written: $out"
}

cmd_stop() {
    if ! is_running; then
        rm -f "$PID_FILE" "$QMP_SOCK"
        log "VM is not running"
        return 0
    fi
    local pid
    pid="$(<"$PID_FILE")"
    if [[ -S "$QMP_SOCK" ]]; then
        qmp_call '{"execute":"system_powerdown"}' >/dev/null 2>&1 || true
        local i
        for i in $(seq 1 30); do
            if ! kill -0 "$pid" 2>/dev/null; then
                rm -f "$PID_FILE" "$QMP_SOCK"
                log "VM powered down cleanly"
                return 0
            fi
            sleep 1
        done
        qmp_call '{"execute":"quit"}' >/dev/null 2>&1 || true
        sleep 2
    fi
    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        sleep 2
    fi
    if kill -0 "$pid" 2>/dev/null; then
        kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE" "$QMP_SOCK"
    log "VM stopped"
}

cmd_snapshot() {
    require_tools qemu-img
    local sub="${1:?usage: snapshot <save|load|list|delete> [name]}"
    local name="${2:-}"
    [[ -f "$DISK" ]] || die "disk image missing: $DISK"
    case "$sub" in
        list)
            qemu-img snapshot -l "$DISK"
            ;;
        save | load | delete)
            [[ -n "$name" ]] || die "snapshot name required for '$sub'"
            is_running && die "stop VM before 'snapshot $sub' (offline qcow2 operation)"
            case "$sub" in
                save) qemu-img snapshot -c "$name" "$DISK" ;;
                load) qemu-img snapshot -a "$name" "$DISK" ;;
                delete) qemu-img snapshot -d "$name" "$DISK" ;;
            esac
            log "snapshot $sub '$name' complete"
            ;;
        *) die "unknown snapshot action '$sub' (expected save|load|list|delete)" ;;
    esac
}

cmd_status() {
    local state="stopped"
    if is_running; then
        state="running (pid $(<"$PID_FILE"))"
    fi
    local iso_info
    iso_info="$(resolve_iso 2>/dev/null || echo 'not resolved (set ISO_PATH or VM_ISO)')"
    printf 'VM_NAME=%s\n' "$VM_NAME"
    printf 'STATE=%s\n' "$state"
    printf 'SSH=127.0.0.1:%s\n' "$VM_SSH_PORT"
    printf 'VM_FS=%s\n' "$VM_FS"
    printf 'VM_DIR=%s\n' "$VM_DIR"
    printf 'DISK=%s (%s)\n' "$DISK" "$([[ -f "$DISK" ]] && du -h "$DISK" | awk '{print $1}' || echo 'missing')"
    printf 'ISO=%s\n' "$iso_info"
    printf 'EVIDENCE=%s\n' "$VM_EVIDENCE_DIR"
    if [[ -f "$DISK" ]] && command -v qemu-img >/dev/null 2>&1; then
        qemu-img snapshot -l "$DISK" || true
    fi
}

usage() {
    cat <<'EOF'
Usage: freebsd-vm.sh <command> [args...]

Manage the local FreeBSD 15.1 QEMU/KVM VM for Koma FreeBSD build/test checks.

Commands:
  seed                        Build FAT16 bsdinstall seed image only
  install                     Unattended install from $ISO_PATH/FreeBSD-*-amd64-disc1.iso + pkg provision
  boot                        Start the installed FreeBSD VM in the background
  wait-ssh [timeout]          Wait for root SSH readiness on VM_SSH_PORT (default 180s)
  ssh [-u user] [cmd...]      Open SSH shell or run command in guest
  scp-in <local> <remote>     Copy files into guest
  scp-out <remote> <local>    Copy files out of guest
  provision                   Bootstrap pkg and install Koma FreeBSD build/runtime dependencies
  sync-repo [koma-dir]        Copy clean Koma source tree into guest:/root/koma-src
  koma-check [guest-dir]      Run cargo check --workspace --all-targets --locked and cargo test --workspace --locked
  screendump <out.png>        Capture guest framebuffer to PNG via QMP
  sendkey <qcode...>          Send QEMU key chord via QMP
  qmp <json-command>          Execute raw QMP JSON command
  stop                        Shut down the running VM gracefully via QMP (fallback SIGTERM)
  snapshot <save|load|list|delete> [name]
                              Manage offline qcow2 snapshots
  status                      Print VM configuration, running state, ISO path, and snapshots

Environment defaults:
  VM_NAME=freebsd-koma        VM_SSH_PORT=2223            VM_FS=zfs (zfs|ufs)
  VM_DISK_SIZE=32G            VM_CPUS=4                   VM_MEM=6G
  VM_DIR=$KOMA_WORKSPACE/recording/vm/$VM_NAME
  VM_EVIDENCE_DIR=$KOMA_WORKSPACE/recording/$(date +%F)/vm-$VM_NAME
  ISO_PATH=<read-only ISO dir>  VM_GUEST_PROXY=<optional http://10.0.2.2:PORT>
EOF
}

main() {
    (($# > 0)) || {
        usage >&2
        exit 2
    }
    local cmd="$1"
    shift
    case "$cmd" in
        -h | --help | help) usage ;;
        seed) cmd_seed "$@" ;;
        install) cmd_install "$@" ;;
        boot) cmd_boot "$@" ;;
        wait-ssh) cmd_wait_ssh "$@" ;;
        ssh) cmd_ssh "$@" ;;
        scp-in) cmd_scp_in "$@" ;;
        scp-out) cmd_scp_out "$@" ;;
        provision) cmd_provision "$@" ;;
        sync-repo) cmd_sync_repo "$@" ;;
        koma-check) cmd_koma_check "$@" ;;
        screendump) cmd_screendump "$@" ;;
        sendkey) cmd_sendkey "$@" ;;
        qmp) cmd_qmp "$@" ;;
        stop) cmd_stop "$@" ;;
        snapshot) cmd_snapshot "$@" ;;
        status) cmd_status "$@" ;;
        *) die "unknown command: $cmd (see --help)" ;;
    esac
}

main "$@"