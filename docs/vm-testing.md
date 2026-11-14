# Isolated VM testing

`vm/arch-vm.sh` and `vm/koma-smoke.sh` exercise a built Koma package inside a
disposable QEMU/KVM Arch Linux guest. They implement step 5 of the
[release runbook](release.md) (install, launch, shutdown, removal, reinstall,
rollback) without touching the host desktop session, GPU binding, boot
configuration, system services, or device permissions.

The VM is headless: `-display none`, serial console to a log file, a QMP control
socket, and an optional VNC server bound to `127.0.0.1`. The framebuffer is
captured with QMP `screendump`. Nothing requires root on the host.

## Requirements

- `qemu-system-x86_64` with KVM access (`/dev/kvm` readable/writable by the user)
- OVMF firmware (`edk2-ovmf`: `/usr/share/edk2/x64/OVMF_CODE.4m.fd`, `OVMF_VARS.4m.fd`)
- `qemu-img`, `ssh`, `scp`, `ssh-keygen`, `python3` (QMP client)
- `mkfs.fat` (dosfstools) and `mcopy` (mtools) for the NoCloud seed image
- An Arch Linux installer ISO in the read-only collection named by `ISO_PATH`

The guest display device defaults to `virtio-vga` when QEMU provides it (on Arch
it lives in the optional `qemu-hw-display-virtio-gpu` package) and falls back to
the standard `VGA` device, which the guest drives with the `bochs` DRM/KMS driver.
Override with `VM_GPU`.

## Layout and configuration

All paths are environment-driven (`vm/arch-vm.sh --help` lists every variable).
`KOMA_WORKSPACE` defaults to the parent of this repository's Git common
directory, so the scripts work from the main checkout and from worktrees.

| Item | Default |
| --- | --- |
| Installer ISO | newest `$ISO_PATH/archlinux-*-x86_64.iso` (never written to) |
| Disk, OVMF vars, seed, ephemeral SSH key | `$KOMA_WORKSPACE/recording/vm/arch-koma/` |
| Logs, QEMU command lines, screenshots | `$KOMA_WORKSPACE/recording/<date>/vm-arch-koma/` (`VM_EVIDENCE_DIR`) |
| Guest SSH | `127.0.0.1:2222` forwarded to guest port 22 (`VM_SSH_PORT`) |
| Guest network | QEMU user networking; optional `VM_GUEST_PROXY` (host loopback is `10.0.2.2`) |

`recording/` is ignored by Git. The SSH key is generated per VM, has no
passphrase, and never leaves the ignored VM directory.

## Usage

```bash
export ISO_PATH=/path/to/read-only/isos   # your collection
just vm-help                              # all commands and variables
vm/arch-vm.sh seed                        # ephemeral key + NoCloud CIDATA image
vm/arch-vm.sh install                     # boot ISO, install Arch on a 40G qcow2, power off
vm/arch-vm.sh snapshot create clean-base  # internal qcow2 snapshot (VM must be off)
vm/arch-vm.sh boot                        # boot the installed disk, wait for SSH

KOMA_PKG=/path/koma-0.0.1-1-x86_64.pkg.tar.zst \
KOMA_TARBALL=/path/koma-0.0.1-x86_64-unknown-linux-gnu.tar.gz \
    vm/koma-smoke.sh all                  # or individual steps, see --help

vm/arch-vm.sh screendump out.png          # framebuffer capture via QMP
vm/arch-vm.sh ssh -u tester 'komactl inventory'
vm/arch-vm.sh stop                        # ACPI power-off, hard quit after 90 s
vm/arch-vm.sh snapshot apply clean-base   # roll back to the clean install
```

`install` boots the ISO with the seed attached; archiso's cloud-init installs the
ephemeral key for root. `vm/guest-install.sh` then partitions `/dev/vda` (1 GiB
ESP + ext4), runs `pacstrap` (base, linux, openssh, sudo, foot, wayland-utils,
ttf-dejavu, grim, namcap), installs systemd-boot, enables systemd-networkd,
systemd-resolved and sshd, creates the `tester` user (groups `wheel`, `video`,
`input`) and configures tty1 autologin for it. Koma's own runtime dependencies
are deliberately not preinstalled so that `pacman -U` must resolve them.

The `tty` smoke step launches `koma --tty` from tester's tty1 autologin shell, so
a real logind session owns the active VT and libseat uses the logind backend. It
records the Wayland globals (`wayland-info`), spawns `foot`, queries the admin
IPC (`komactl config-status`, `komactl inventory`), and takes screendumps.
Every QEMU invocation and snapshot operation is appended to
`qemu-commands.log` in the evidence directory; each smoke step writes
`<step>.log` and a line in `results.tsv`.

## What a VM run does and does not establish

A pass shows that the package installs and resolves dependencies on a current
Arch system, that the binaries run, that the files are removed cleanly, and
what the compositor does on an emulated KMS device under a logind seat. It does
not prove physical DRM/KMS hardware support, GPU acceleration, multi-GPU,
physical input devices, hotplug, suspend/resume, timing or latency, BSD support,
or an upgrade between two different package versions.

If the default mirror or a host proxy is slow from inside QEMU user networking,
set `VM_MIRROR` to a nearby mirror (`https://<host>/archlinux/$repo/os/$arch`);
`pacstrap` copies it into the installed system. Run the `tty` step with
`KOMA_LIBSEAT_BACKEND=seatd KOMA_TTY_TAG=seatd` (after enabling `seatd` and adding
`tester` to the `seat` group) to compare libseat backends.

## v0.0.1 results (2026-11-14)

Inputs: `koma-0.0.1-1-x86_64.pkg.tar.zst` (sha256 `7ff392bd…bc3938`, commit
`d4ea6f3`) and `koma-0.0.1-x86_64-unknown-linux-gnu.tar.gz` (sha256
`022e747e…eda8f1`); guest Arch Linux with kernel 7.3.4, systemd 262.2,
mesa 26.2.7, seatd 0.9.3; QEMU 11.1.3 with KVM, OVMF, `-device VGA` (guest
`bochs-drm`, no render node). Installer `archlinux-2026.01.01-x86_64.iso`.

| Test | Result | Notes |
| --- | --- | --- |
| Artifact checksums | Pass | Package and tarball match their published sums and manifest |
| `pacman -U` dependency resolution | Pass | 14 dependencies resolved from the repositories, `pacman -Qkk` clean |
| `namcap` | Pass with warnings | `gcc-libs`, `libdrm`, `libglvnd`, `wayland` reported as possibly unneeded |
| `koma --headless-check`, `komactl validate`/`gen-types`, `koma-mcp --print-tools` | Pass | Example `init.lua` validates |
| `koma --tty` (logind and seatd) | **Fail** | Process and Wayland/IPC sockets come up, but the software-only KMS device is rejected, no `wl_output` is advertised, `foot` reports no monitors, nothing is drawn |
| `komactl config-status` / `inventory` over IPC | Pass | Model state only (virtual 1920×1080 workspace) |
| VT switch from Koma | Pass (seatd) / fail (logind) | logind `change_vt` denied without polkit |
| Release tarball in `/opt` | Pass | `bin/koma --headless-check`, `bin/komactl validate` |
| `pacman -R` | Pass | All package files removed |
| Same-version reinstall | Pass | A version-to-version upgrade needs a newer build |
| qcow2 snapshot rollback to the pre-install state | Pass | Koma and its dependencies absent after rollback |

Packaging change from this run: the template now installs
`koma-portals.conf` to `/usr/share/xdg-desktop-portal/` (as Koma's own
installer does) and `greetd-config.toml` as a documentation example; the
published `0.0.1-1` package predates this. Koma issues found: the TTY backend
continues with zero outputs instead of failing or falling back to software
rendering on a display-only device, unknown command-line flags (including
`--help`) are ignored and start the compositor, and SIGTERM ends the TTY
compositor without a graceful shutdown. This run proves neither physical
DRM/KMS hardware nor GPU support, and no Koma frame was rendered.
