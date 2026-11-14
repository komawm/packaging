#!/usr/bin/env bash
# Runs as root inside the Arch ISO live environment (piped over SSH by arch-vm.sh install).
# Installs a minimal UEFI Arch system on /dev/vda for Koma package tests.
# Inputs (environment): GUEST_PROXY (optional), MIRROR, SSH_PUBKEY.
set -euo pipefail

: "${MIRROR:?MIRROR is required}"
: "${SSH_PUBKEY:?SSH_PUBKEY is required}"
target_disk=/dev/vda
hostname=arch-koma
test_user=tester

step() { printf '\n==> %s\n' "$*"; }

if [[ -n "${GUEST_PROXY:-}" ]]; then
    export http_proxy="$GUEST_PROXY" https_proxy="$GUEST_PROXY"
    export HTTP_PROXY="$GUEST_PROXY" HTTPS_PROXY="$GUEST_PROXY"
fi

step "Live environment"
uname -a
grep -E '^(NAME|BUILD_ID|VERSION_ID)=' /etc/os-release || true
printf 'proxy=%s\n' "${GUEST_PROXY:-none}"

step "Waiting for pacman keyring initialisation"
for _ in $(seq 1 120); do
    state="$(systemctl show -p ActiveState --value pacman-init.service)"
    [[ "$state" == active ]] && break
    sleep 2
done
systemctl show -p ActiveState --value pacman-init.service
systemctl stop reflector.service 2>/dev/null || true

step "Network and mirror reachability"
printf 'Server = %s\n' "$MIRROR" >/etc/pacman.d/mirrorlist
probe="${MIRROR//\$repo/core}"
probe="${probe//\$arch/x86_64}/core.db"
curl -fsS -o /dev/null --max-time 60 -w 'mirror http=%{http_code} time=%{time_total}s\n' "$probe"

step "Partitioning $target_disk (GPT: 1 GiB ESP + ext4 root)"
wipefs -a "$target_disk"
sfdisk "$target_disk" <<'EOF'
label: gpt
size=1GiB, type=uefi, name=esp
type=linux, name=root
EOF
udevadm settle
mkfs.fat -F 32 -n ESP "${target_disk}1"
mkfs.ext4 -q -F -L root "${target_disk}2"
mount "${target_disk}2" /mnt
mount --mkdir "${target_disk}1" /mnt/boot

step "pacstrap"
# Koma's own runtime dependencies (mesa, seatd, libinput, ...) are deliberately left
# out so that `pacman -U` of the package has to resolve them.
pacstrap -K /mnt base linux openssh sudo less foot wayland-utils ttf-dejavu grim namcap
genfstab -U /mnt >>/mnt/etc/fstab

step "System configuration"
root_partuuid="$(blkid -s PARTUUID -o value "${target_disk}2")"
arch-chroot /mnt /bin/bash -euo pipefail -s -- "$hostname" "$test_user" "$root_partuuid" "$SSH_PUBKEY" <<'CHROOT'
hostname="$1" test_user="$2" root_partuuid="$3" pubkey="$4"
printf '%s\n' "$hostname" >/etc/hostname
ln -sf /usr/share/zoneinfo/UTC /etc/localtime
printf 'en_US.UTF-8 UTF-8\n' >/etc/locale.gen
locale-gen >/dev/null
printf 'LANG=en_US.UTF-8\n' >/etc/locale.conf

bootctl install --no-variables 2>/dev/null || bootctl install
cat >/boot/loader/loader.conf <<EOF
default arch.conf
timeout 1
EOF
cat >/boot/loader/entries/arch.conf <<EOF
title Arch Linux (koma test VM)
linux /vmlinuz-linux
initrd /initramfs-linux.img
options root=PARTUUID=$root_partuuid rw console=ttyS0,115200 console=tty0
EOF
# Removable-media fallback path so OVMF boots even without NVRAM entries.
install -Dm644 /usr/lib/systemd/boot/efi/systemd-bootx64.efi /boot/EFI/BOOT/BOOTX64.EFI

cat >/etc/systemd/network/20-wired.network <<'EOF'
[Match]
Name=en*

[Network]
DHCP=yes
EOF
systemctl enable systemd-networkd systemd-resolved sshd >/dev/null

useradd -m -G wheel,video,input -s /bin/bash "$test_user"
printf '%%wheel ALL=(ALL:ALL) NOPASSWD: ALL\n' >/etc/sudoers.d/10-wheel
chmod 0440 /etc/sudoers.d/10-wheel
for home in /root "/home/$test_user"; do
    install -d -m 700 "$home/.ssh"
    printf '%s\n' "$pubkey" >"$home/.ssh/authorized_keys"
    chmod 600 "$home/.ssh/authorized_keys"
done
chown -R "$test_user:$test_user" "/home/$test_user/.ssh"

# Autologin the test user on tty1 so a real logind session owns the active VT.
install -d /etc/systemd/system/getty@tty1.service.d
cat >/etc/systemd/system/getty@tty1.service.d/autologin.conf <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty -o '-p -f -- \\\\u' --noclear --autologin $test_user %I \$TERM
EOF
CHROOT

# arch-chroot bind-mounts the live resolv.conf, so link it after leaving the chroot.
ln -sf ../run/systemd/resolve/stub-resolv.conf /mnt/etc/resolv.conf

step "Installed packages"
arch-chroot /mnt pacman -Q linux systemd mesa seatd foot wayland-utils 2>&1 || true
sync
umount -R /mnt
step "Install finished"
