# Isolated VM testing

`vm/arch-vm.sh` and `vm/koma-smoke.sh` exercise a built Koma package inside a
disposable QEMU/KVM Arch Linux guest, while `vm/freebsd-vm.sh` manages a
separate FreeBSD 15.1 QEMU/KVM guest for cross-platform compilation and unit-test
verification (`cargo check` / `cargo test`). Together they support step 5 of the
[release runbook](release.md) (install, launch, shutdown, removal, reinstall,
rollback, and Wayland protocol checks) without touching the host desktop
session, GPU binding, boot configuration, system services, or device
permissions.

The VM defaults to headless (`VM_DISPLAY=none`): serial console to a log file, a QMP
control socket, and an optional VNC server bound to `127.0.0.1` (`VM_VNC`). Set
`VM_DISPLAY` (for example `gtk,gl=on`, `sdl,gl=on`, or
`egl-headless,rendernode=/dev/dri/renderD128`) and optionally `VM_GPU=virtio-vga-gl`
to view locally or accelerate guest rendering with host Virgl 3D. When
`VM_DISPLAY=none`, the framebuffer is captured with QMP `screendump`. Nothing
requires root on the host.

## Requirements

- `qemu-system-x86_64` with KVM access (`/dev/kvm` readable/writable by the user)
- OVMF firmware (`edk2-ovmf`: `/usr/share/edk2/x64/OVMF_CODE.4m.fd`, `OVMF_VARS.4m.fd`)
- `qemu-img`, `ssh`, `scp`, `ssh-keygen`, `python3` (QMP client)
- `mkfs.fat` (dosfstools) and `mcopy` (mtools) for the NoCloud seed image
- An Arch Linux installer ISO (`archlinux-*-x86_64.iso`) and/or a FreeBSD
  installer ISO (`FreeBSD-*-amd64-disc1.iso`) in the read-only collection named
  by `ISO_PATH` (the scripts never write inside `ISO_PATH`)
- Optional host display / VNC / Virgl packages on Arch Linux:
  - `qemu-ui-gtk`, `qemu-ui-sdl`, `qemu-ui-opengl` (local QEMU window with `VM_DISPLAY=gtk,gl=on` or `sdl,gl=on`)
  - `qemu-ui-egl-headless` + `tigervnc` (`vncviewer`): headless host GPU Virgl (`VM_GPU=virtio-vga-gl VM_DISPLAY=egl-headless VM_VNC=1`) viewed via `vncviewer 127.0.0.1:5901`

The guest display device defaults to `virtio-vga` when QEMU provides it (on Arch
it lives in the `qemu-hw-display-virtio-gpu` / `qemu-hw-display-virtio-vga`
packages) and falls back to the standard `VGA` device, which the guest drives
with the `bochs` DRM/KMS driver. Override with `VM_GPU` (`virtio-vga`,
`virtio-vga-gl`, `VGA`). For local non-headless `VM_DISPLAY` backends (`gtk*` /
`sdl*`), `vm/arch-vm.sh` runs QEMU in the background without `-daemonize` and
waits for its pidfile before polling SSH.

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
ttf-dejavu, grim, python, namcap), installs systemd-boot, enables systemd-networkd,
systemd-resolved and sshd, creates the `tester` user (groups `wheel`, `video`,
`input`) and configures tty1 autologin for it. Koma's own runtime dependencies
are deliberately not preinstalled so that `pacman -U` must resolve them.

The `tty` smoke step launches `koma --tty` from tester's tty1 autologin shell, so
a real logind session owns the active VT and libseat uses the logind backend. It
records the Wayland globals (`wayland-info`), spawns `foot`, queries the admin
IPC (`komactl config-status`, `komactl inventory`), captures `komactl capture`,
and runs the protocol completeness suite (also runnable standalone via
`vm/koma-smoke.sh protocols`):

1. **Client log and globals check**: asserts `zwlr_layer_shell_v1`,
   `zwlr_screencopy_manager_v1`, and `ext_session_lock_manager_v1` are
   advertised and fails if `/tmp/foot.log` reports missing Wayland protocols or
   fatal client errors.
2. **Unlocked `wlr-screencopy` (`grim`)**: captures a PNG via `grim`, verifies
   non-black pixel content with `vm/wayland-probe.py check-png`, and saves it to
   `$VM_EVIDENCE_DIR`.
3. **`wlr-layer-shell` `exclusive_zone`**: maps a top bar (`height=64`,
   `exclusive_zone=64`) using `vm/wayland-probe.py layer-panel`, verifies that
   `komactl inventory` tiled window geometry shrinks vertically, captures a
   screendump, closes the bar, and verifies the workarea restores.
4. **`ext-session-lock-v1` isolation**: acquires a session lock via
   `vm/wayland-probe.py session-lock`, captures QEMU and `grim` outputs while
   locked, verifies normal windows stay hidden and `grim` either fails or
   returns a masked frame, unlocks cleanly via `SIGUSR1` (`unlock_and_destroy`),
   and re-verifies `grim` and `komactl inventory` restoration.

Every QEMU invocation and snapshot operation is appended to
`qemu-commands.log` in the evidence directory; each smoke step writes
`<step>.log` and a line in `results.tsv`.

## FreeBSD 15.1 harness (`vm/freebsd-vm.sh`)

`vm/freebsd-vm.sh` mirrors `vm/arch-vm.sh` with separate defaults so Arch and
FreeBSD guests can coexist side by side:

| Item | Default |
| --- | --- |
| Installer ISO | `$ISO_PATH/FreeBSD-15.1-RELEASE-amd64-disc1.iso` or newest `$ISO_PATH/FreeBSD-*-amd64-disc1.iso` |
| Disk, OVMF vars, FAT16 seed | `$KOMA_WORKSPACE/recording/vm/freebsd-koma/` (`VM_DISK_SIZE=32G`, `VM_FS=zfs` or `ufs`) |
| Guest resources | `4` vCPUs (`VM_CPUS`), `6G` RAM (`VM_MEM`), auto KVM/TCG detection (`VM_ACCEL=auto`) |
| Logs and evidence | `$KOMA_WORKSPACE/recording/<date>/vm-freebsd-koma/` (`VM_EVIDENCE_DIR`) |
| Guest SSH | `127.0.0.1:2223` forwarded to guest port 22 (`VM_SSH_PORT`) |
| Guest network | QEMU user networking; optional `VM_GUEST_PROXY` forwarded into guest SSH runs |

`install` builds a 16 MiB FAT16 `bsdinstall` seed disk (`mkfs.fat` + `mcopy`,
no host `xorriso` or Tcl `expect` required), drives FreeBSD `loader.efi` into
single-user serial mode (`boot -s`) over a local Unix serial socket, runs
non-interactive `bsdinstall script` from the ISO distribution sets, boots once to
run `provision` (`pkgconf`, `git`, `curl`, `ca_root_nss`, `sudo`, `dbus`,
`libdrm`, `fontconfig`, `libinput`, `seatd`, `libudev-devd`, `wayland`,
`libxkbcommon`, `mesa-libs`, `rust`), and powers off cleanly.

```bash
export ISO_PATH=/path/to/read-only/isos
vm/freebsd-vm.sh seed                      # build FAT16 seed image only
vm/freebsd-vm.sh install                   # unattended ZFS (or VM_FS=ufs) install + pkg provision
vm/freebsd-vm.sh snapshot save clean-base  # offline qcow2 snapshot
vm/freebsd-vm.sh boot && vm/freebsd-vm.sh wait-ssh
vm/freebsd-vm.sh sync-repo                 # copy clean Koma tree to guest:/root/koma-src
vm/freebsd-vm.sh koma-check                # run cargo check --workspace --all-targets --locked & cargo test
vm/freebsd-vm.sh stop
```

If FreeBSD `pkg`'s `rust` package is older than `koma/rust-toolchain.toml`,
`koma-check` provisions the required toolchain with `rustup` before running the
locked workspace checks.

## What a VM run does and does not establish

A pass shows that the package installs and resolves dependencies on a current
Arch system, that the binaries run, that the files are removed cleanly, and
what the compositor does on an emulated KMS device under a logind seat (and, for
FreeBSD, that Koma compiles and passes its locked workspace test suite on
FreeBSD 15.1 amd64). It does not prove physical DRM/KMS hardware support, GPU
acceleration, multi-GPU, physical input devices, hotplug, suspend/resume, timing
or latency, physical FreeBSD `drm-kmod` display output, or an upgrade between
two different package versions.

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

## Post-v0.0.1 KOM-0011 verification (2026-11-15)

Follow-up verification tested the unreleased `KOM-0011` fix series (`carryctx/kom-0011`) inside the same Arch Linux 7.3.4 QEMU/KVM guest under both `-device virtio-vga` (`1af4:1050`, VNC `127.0.0.1:5901`) and `-device VGA` (`bochs-drm` `1234:1111`), recorded under `recording/2026-11-15/vm-arch-koma-kom-0011/`:

| Test | Result | Notes |
| --- | --- | --- |
| `smoke-cli` (`koma --help`, `--version`, `--bogus`, `--headless-check`, `komactl validate`/`gen-types`, `koma-mcp --print-tools`) | Pass | Unknown flags exit `2`; headless checks pass |
| `koma --tty` (`seatd`, `virtio-vga` & `VGA`) | Pass | `llvmpipe` software KMS fallback advertises `wl_output` `Virtual-1` (`1280x800`); `foot` maps as `win_1_g1` inside `#58a6ff` active border |
| `komactl capture ws_01` (`grid_225` over `SCM_RIGHTS` `memfd`) | Pass | Saved `4096000` RGBA8888 bytes (`1280x800`, `grid_cells: 225`) |
| `logind` VT switch (`ctrl-alt-f2` via QMP) | Diagnostic pass | Logged `Permission denied (os error 13)` with explicit `polkit` (`org.freedesktop.login1.chvt`) and `seatd` guidance |
| `SIGTERM` shutdown | Pass | `koma exited rc=0` cleanly on `virtio-vga` (`seatd`/`logind`) and `VGA` (`seatd`) |

Boundary unchanged: software KMS (`llvmpipe`) in QEMU establishes compositor protocol, client mapping, IPC frame capture, CLI flag handling, and signal shutdown on emulated display devices; it still does not prove physical GPU DRM/KMS scanout or Virgl 3D hardware acceleration.

Both VM runs above predate the upstream removal of the standalone `koma-mcp`
binary: those packages shipped `koma-mcp --print-tools`, which is now the
`koma mcp --print-tools` subcommand. The result rows record what each build
actually executed.
