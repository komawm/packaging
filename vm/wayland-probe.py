#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Pure-stdlib Wayland protocol and PNG probe for Koma VM smoke tests."""

from __future__ import annotations

import argparse
import array
import json
import mmap
import os
import select
import signal
import socket
import struct
import sys
import tempfile
import time
import zlib
from dataclasses import dataclass
from typing import Any

WL_DISPLAY_ID = 1
WL_SHM_FORMAT_ARGB8888 = 0

# zwlr_layer_shell_v1 layer constants
WLR_LAYER_TOP = 2
WLR_LAYER_ANCHOR_TOP = 1
WLR_LAYER_ANCHOR_BOTTOM = 2
WLR_LAYER_ANCHOR_LEFT = 4
WLR_LAYER_ANCHOR_RIGHT = 8


def _pack_string(text: str) -> bytes:
    raw = text.encode("utf-8") + b"\x00"
    pad = (-len(raw)) % 4
    return struct.pack("<I", len(raw)) + raw + (b"\x00" * pad)


def _unpack_string(buf: bytes, offset: int) -> tuple[str, int]:
    (length,) = struct.unpack_from("<I", buf, offset)
    offset += 4
    padded = (length + 3) & ~3
    raw = buf[offset : offset + length]
    if raw.endswith(b"\x00"):
        raw = raw[:-1]
    return raw.decode("utf-8", errors="replace"), offset + padded


def _make_message(object_id: int, opcode: int, payload: bytes = b"") -> bytes:
    total = 8 + len(payload)
    return struct.pack("<II", object_id, (total << 16) | opcode) + payload


def _create_shm_fd(size: int) -> int:
    if hasattr(os, "memfd_create"):
        fd = os.memfd_create("koma-wayland-probe")
    else:
        tmp = tempfile.TemporaryFile()
        fd = os.dup(tmp.fileno())
        tmp.close()
    os.ftruncate(fd, size)
    return fd


class WaylandProtocolError(RuntimeError):
    """Raised when the compositor emits wl_display.error or disconnects."""


@dataclass
class GlobalInfo:
    name: int
    interface: str
    version: int


class WaylandConnection:
    """Minimal synchronous/event-driven Wayland wire client using stdlib sockets."""

    def __init__(self, display_name: str | None = None) -> None:
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
        if not runtime_dir:
            raise WaylandProtocolError("XDG_RUNTIME_DIR is not set")
        name = display_name or os.environ.get("WAYLAND_DISPLAY") or "wayland-0"
        sock_path = name if os.path.isabs(name) else os.path.join(runtime_dir, name)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(sock_path)
        self.sock.setblocking(False)
        self._next_id = 2
        self._recv_buf = bytearray()
        self.globals_by_iface: dict[str, list[GlobalInfo]] = {}
        self.registry_id = 0
        self.handlers: dict[int, Any] = {}
        self._sync_callbacks: set[int] = set()
        self._sync_done: set[int] = set()
        self.protocol_errors: list[str] = []

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def alloc_id(self) -> int:
        oid = self._next_id
        self._next_id += 1
        return oid

    def send(
        self, object_id: int, opcode: int, payload: bytes = b"", fds: list[int] | None = None
    ) -> None:
        msg = _make_message(object_id, opcode, payload)
        if fds:
            cmsg = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", fds))]
            sent = self.sock.sendmsg([msg], cmsg)
            if sent != len(msg):
                raise WaylandProtocolError("partial sendmsg on Wayland socket")
            return
        view = memoryview(msg)
        while view:
            try:
                sent = self.sock.send(view)
                view = view[sent:]
            except BlockingIOError:
                select.select([], [self.sock], [], 5.0)

    def bind(self, info: GlobalInfo, version: int | None = None) -> int:
        oid = self.alloc_id()
        use_ver = min(info.version, version) if version is not None else info.version
        payload = (
            struct.pack("<I", info.name)
            + _pack_string(info.interface)
            + struct.pack("<II", use_ver, oid)
        )
        self.send(self.registry_id, 0, payload)
        return oid

    def sync(self) -> int:
        cb = self.alloc_id()
        self._sync_callbacks.add(cb)
        self.send(WL_DISPLAY_ID, 0, struct.pack("<I", cb))
        return cb

    def roundtrip(self, timeout: float = 10.0) -> None:
        cb = self.sync()
        deadline = time.monotonic() + timeout
        while cb not in self._sync_done:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WaylandProtocolError("timed out waiting for wl_display.sync")
            self.dispatch(timeout=remaining)

    def discover_globals(self) -> dict[str, list[GlobalInfo]]:
        self.registry_id = self.alloc_id()
        self.send(WL_DISPLAY_ID, 1, struct.pack("<I", self.registry_id))
        self.roundtrip()
        return self.globals_by_iface

    def dispatch(self, timeout: float = 1.0, extra_fds: list[int] | None = None) -> bool:
        watch = [self.sock] + (extra_fds or [])
        readable, _, _ = select.select(watch, [], [], timeout)
        if self.sock in readable:
            while True:
                try:
                    chunk = self.sock.recv(65536)
                except BlockingIOError:
                    break
                if not chunk:
                    raise WaylandProtocolError("Wayland compositor closed the connection")
                self._recv_buf.extend(chunk)
            self._process_messages()
        return any(fd in readable for fd in (extra_fds or []))

    def _process_messages(self) -> None:
        while len(self._recv_buf) >= 8:
            obj_id, size_op = struct.unpack_from("<II", self._recv_buf, 0)
            size = size_op >> 16
            opcode = size_op & 0xFFFF
            if size < 8:
                raise WaylandProtocolError(f"invalid Wayland event size {size}")
            if len(self._recv_buf) < size:
                break
            payload = bytes(self._recv_buf[8:size])
            del self._recv_buf[:size]
            self._handle_event(obj_id, opcode, payload)

    def _handle_event(self, obj_id: int, opcode: int, payload: bytes) -> None:
        if obj_id == WL_DISPLAY_ID:
            if opcode == 0:
                err_obj, code = struct.unpack_from("<II", payload, 0)
                msg, _ = _unpack_string(payload, 8)
                err_text = f"wl_display.error(object={err_obj}, code={code}): {msg}"
                self.protocol_errors.append(err_text)
                raise WaylandProtocolError(err_text)
            return
        if obj_id == self.registry_id:
            if opcode == 0:
                (name,) = struct.unpack_from("<I", payload, 0)
                iface, off = _unpack_string(payload, 4)
                (ver,) = struct.unpack_from("<I", payload, off)
                self.globals_by_iface.setdefault(iface, []).append(
                    GlobalInfo(name=name, interface=iface, version=ver)
                )
            return
        handler = self.handlers.get(obj_id)
        if handler is not None:
            handler(opcode, payload)
        elif obj_id in self._sync_callbacks and opcode == 0 and len(payload) == 4:
            self._sync_done.add(obj_id)


def _create_solid_shm_buffer(
    conn: WaylandConnection, shm_id: int, width: int, height: int, argb: int
) -> tuple[int, int]:
    stride = width * 4
    size = stride * height
    fd = _create_shm_fd(size)
    try:
        pixel = struct.pack("<I", argb)
        row = pixel * width
        data = row * height
        with mmap.mmap(fd, size, mmap.MAP_SHARED, mmap.PROT_WRITE) as mm:
            mm[:size] = data
        pool_id = conn.alloc_id()
        conn.send(shm_id, 0, struct.pack("<Ii", pool_id, size), fds=[fd])
    finally:
        os.close(fd)
    buf_id = conn.alloc_id()
    conn.send(
        pool_id,
        0,
        struct.pack("<IiiiiI", buf_id, 0, width, height, stride, WL_SHM_FORMAT_ARGB8888),
    )
    return pool_id, buf_id


def _install_signal_pipe() -> int:
    r_fd, w_fd = os.pipe()
    os.set_blocking(r_fd, False)
    os.set_blocking(w_fd, False)

    def _on_signal(_signum: int, _frame: Any) -> None:
        try:
            os.write(w_fd, b"x")
        except OSError:
            pass

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGUSR1):
        signal.signal(sig, _on_signal)
    return r_fd


def _touch_marker(path: str | None, text: str) -> None:
    if not path:
        return
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def cmd_layer_panel(args: argparse.Namespace) -> int:
    conn = WaylandConnection(args.display)
    sig_r = _install_signal_pipe()
    pool_id = 0
    buf_id = 0
    layer_id = 0
    surf_id = 0
    layer_shell_id = 0
    layer_shell_ver = 1
    try:
        globals_map = conn.discover_globals()
        for req in ("wl_compositor", "wl_shm", "zwlr_layer_shell_v1"):
            if req not in globals_map:
                print(f"FAIL: missing Wayland global {req}", file=sys.stderr)
                return 1
        comp_id = conn.bind(globals_map["wl_compositor"][0], version=4)
        shm_id = conn.bind(globals_map["wl_shm"][0], version=1)
        ls_info = globals_map["zwlr_layer_shell_v1"][0]
        layer_shell_ver = min(ls_info.version, 4)
        layer_shell_id = conn.bind(ls_info, version=layer_shell_ver)

        surf_id = conn.alloc_id()
        conn.send(comp_id, 0, struct.pack("<I", surf_id))

        layer_id = conn.alloc_id()
        conn.send(
            layer_shell_id,
            0,
            struct.pack("<IIII", layer_id, surf_id, 0, WLR_LAYER_TOP)
            + _pack_string(args.namespace),
        )

        anchors = {
            "top": WLR_LAYER_ANCHOR_TOP | WLR_LAYER_ANCHOR_LEFT | WLR_LAYER_ANCHOR_RIGHT,
            "bottom": WLR_LAYER_ANCHOR_BOTTOM | WLR_LAYER_ANCHOR_LEFT | WLR_LAYER_ANCHOR_RIGHT,
        }[args.anchor]
        conn.send(layer_id, 0, struct.pack("<II", 0, args.height))
        conn.send(layer_id, 1, struct.pack("<I", anchors))
        conn.send(layer_id, 2, struct.pack("<i", args.height))
        conn.send(layer_id, 4, struct.pack("<I", 0))
        conn.send(surf_id, 6)  # commit

        configured: dict[str, int | bool] = {"done": False, "closed": False, "w": 0, "h": 0, "serial": 0}

        def on_layer_event(opcode: int, payload: bytes) -> None:
            if opcode == 0:
                serial, w, h = struct.unpack_from("<III", payload, 0)
                configured["serial"] = serial
                configured["w"] = w
                configured["h"] = h
                configured["done"] = True
            elif opcode == 1:
                configured["closed"] = True

        conn.handlers[layer_id] = on_layer_event
        deadline = time.monotonic() + 10.0
        while not configured["done"] and not configured["closed"]:
            if time.monotonic() >= deadline:
                print("FAIL: timed out waiting for zwlr_layer_surface_v1.configure", file=sys.stderr)
                return 1
            if conn.dispatch(timeout=0.5, extra_fds=[sig_r]):
                print("FAIL: interrupted before zwlr_layer_surface_v1.configure", file=sys.stderr)
                return 1
        if configured["closed"]:
            print("FAIL: compositor closed zwlr_layer_surface_v1 before configure", file=sys.stderr)
            return 1

        width = max(int(configured["w"]), 1)
        height = max(int(configured["h"]), args.height)
        pool_id, buf_id = _create_solid_shm_buffer(conn, shm_id, width, height, 0xFF2E3440)
        conn.send(layer_id, 6, struct.pack("<I", int(configured["serial"])))
        conn.send(surf_id, 1, struct.pack("<Iii", buf_id, 0, 0))
        conn.send(surf_id, 2, struct.pack("<iiii", 0, 0, width, height))
        conn.send(surf_id, 6)
        conn.roundtrip()

        status = f"MAPPED {width}x{height}\n"
        sys.stdout.write(status)
        sys.stdout.flush()
        _touch_marker(args.ready_file, status)

        end_at = time.monotonic() + args.timeout
        while not configured["closed"] and time.monotonic() < end_at:
            if conn.dispatch(timeout=0.5, extra_fds=[sig_r]):
                break
        return 0
    finally:
        try:
            if layer_id:
                conn.send(layer_id, 7)
            if surf_id:
                conn.send(surf_id, 0)
            if buf_id:
                conn.send(buf_id, 0)
            if pool_id:
                conn.send(pool_id, 1)
            if layer_shell_id and layer_shell_ver >= 3:
                conn.send(layer_shell_id, 1)
            conn.roundtrip(timeout=2.0)
        except Exception:
            pass
        conn.close()
        os.close(sig_r)


def cmd_session_lock(args: argparse.Namespace) -> int:
    conn = WaylandConnection(args.display)
    sig_r = _install_signal_pipe()
    lock_id = 0
    lock_mgr_id = 0
    state = {"locked": False, "finished": False, "unlocked": False}
    surfaces: list[dict[str, int]] = []
    pools: list[int] = []
    buffers: list[int] = []

    try:
        globals_map = conn.discover_globals()
        for req in ("wl_compositor", "wl_shm", "ext_session_lock_manager_v1", "wl_output"):
            if req not in globals_map:
                print(f"FAIL: missing Wayland global {req}", file=sys.stderr)
                return 1

        comp_id = conn.bind(globals_map["wl_compositor"][0], version=4)
        shm_id = conn.bind(globals_map["wl_shm"][0], version=1)
        lock_mgr_id = conn.bind(globals_map["ext_session_lock_manager_v1"][0], version=1)
        output_ids = [conn.bind(info, version=2) for info in globals_map["wl_output"]]
        conn.roundtrip()

        lock_id = conn.alloc_id()
        conn.send(lock_mgr_id, 1, struct.pack("<I", lock_id))

        def on_lock_event(opcode: int, _payload: bytes) -> None:
            if opcode == 0:
                state["locked"] = True
            elif opcode == 1:
                state["finished"] = True

        conn.handlers[lock_id] = on_lock_event

        for out_id in output_ids:
            surf_id = conn.alloc_id()
            conn.send(comp_id, 0, struct.pack("<I", surf_id))
            ls_id = conn.alloc_id()
            conn.send(lock_id, 1, struct.pack("<III", ls_id, surf_id, out_id))
            rec = {"ls_id": ls_id, "surf_id": surf_id, "configured": 0}
            surfaces.append(rec)

            def make_ls_handler(record: dict[str, int]) -> Any:
                def on_ls_event(opcode: int, payload: bytes) -> None:
                    if opcode == 0:
                        serial, w, h = struct.unpack_from("<III", payload, 0)
                        width = max(w, 1)
                        height = max(h, 1)
                        pool_id, buf_id = _create_solid_shm_buffer(
                            conn, shm_id, width, height, 0xFF123456
                        )
                        pools.append(pool_id)
                        buffers.append(buf_id)
                        conn.send(record["ls_id"], 1, struct.pack("<I", serial))
                        conn.send(record["surf_id"], 1, struct.pack("<Iii", buf_id, 0, 0))
                        conn.send(record["surf_id"], 2, struct.pack("<iiii", 0, 0, width, height))
                        conn.send(record["surf_id"], 6)
                        record["configured"] = 1

                return on_ls_event

            conn.handlers[ls_id] = make_ls_handler(rec)

        deadline = time.monotonic() + 10.0
        while not state["locked"] and not state["finished"]:
            if time.monotonic() >= deadline:
                print("FAIL: timed out waiting for ext_session_lock_v1.locked", file=sys.stderr)
                return 1
            if conn.dispatch(timeout=0.5, extra_fds=[sig_r]):
                break

        if state["finished"] and not state["locked"]:
            print("FAIL: compositor sent ext_session_lock_v1.finished (lock rejected)", file=sys.stderr)
            return 1

        if state["locked"]:
            # Flush pending configure events and subsequent lock-surface buffer commits.
            conn.roundtrip(timeout=5.0)
            conn.roundtrip(timeout=5.0)
            sys.stdout.write("LOCKED\n")
            sys.stdout.flush()
            _touch_marker(args.ready_file, "LOCKED\n")

            end_at = time.monotonic() + args.timeout
            while not state["finished"] and time.monotonic() < end_at:
                if conn.dispatch(timeout=0.5, extra_fds=[sig_r]):
                    try:
                        os.read(sig_r, 64)
                    except OSError:
                        pass
                    break

        if lock_id and not state["finished"]:
            conn.send(lock_id, 2)  # unlock_and_destroy
            state["unlocked"] = True
        elif lock_id:
            conn.send(lock_id, 0)  # destroy

        for rec in surfaces:
            conn.send(rec["ls_id"], 0)
            conn.send(rec["surf_id"], 0)
        for buf_id in buffers:
            conn.send(buf_id, 0)
        for pool_id in pools:
            conn.send(pool_id, 1)
        if lock_mgr_id:
            conn.send(lock_mgr_id, 0)
        conn.roundtrip(timeout=5.0)
        sys.stdout.write("UNLOCKED\n")
        sys.stdout.flush()
        _touch_marker(args.unlocked_file, "UNLOCKED\n")
        return 0 if state["locked"] else 1
    finally:
        conn.close()
        os.close(sig_r)


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def decode_png_rgb_samples(path: str, max_samples: int = 16384) -> dict[str, Any]:
    """Parse and unfilter a PNG file using stdlib zlib, returning pixel stats."""
    with open(path, "rb") as fh:
        data = fh.read()
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path}: invalid PNG signature")

    pos = 8
    ihdr: tuple[int, int, int, int, int, int, int] | None = None
    idat_parts: list[bytes] = []
    palette: bytes = b""
    saw_iend = False

    while pos + 12 <= len(data):
        (length,) = struct.unpack_from(">I", data, pos)
        ctype = data[pos + 4 : pos + 8]
        chunk_start = pos + 8
        chunk_end = chunk_start + length
        if chunk_end + 4 > len(data):
            raise ValueError(f"{path}: truncated PNG chunk {ctype!r}")
        payload = data[chunk_start:chunk_end]
        pos = chunk_end + 4
        if ctype == b"IHDR":
            if length != 13:
                raise ValueError(f"{path}: invalid IHDR length {length}")
            ihdr = struct.unpack(">IIBBBBB", payload)
        elif ctype == b"PLTE":
            palette = payload
        elif ctype == b"IDAT":
            idat_parts.append(payload)
        elif ctype == b"IEND":
            saw_iend = True
            break

    if ihdr is None or not saw_iend or not idat_parts:
        raise ValueError(f"{path}: missing IHDR, IDAT, or IEND")

    width, height, bit_depth, color_type, comp, filt, interlace = ihdr
    if width <= 0 or height <= 0 or comp != 0 or filt != 0:
        raise ValueError(f"{path}: invalid IHDR fields {ihdr}")
    if interlace != 0:
        raise ValueError(f"{path}: interlaced PNG not supported")

    channels_by_type = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
    if color_type not in channels_by_type or bit_depth not in (8, 16):
        raise ValueError(f"{path}: unsupported color_type={color_type} bit_depth={bit_depth}")

    channels = channels_by_type[color_type]
    bpp = (channels * bit_depth) // 8
    raw = zlib.decompress(b"".join(idat_parts))
    stride = width * bpp
    expected = (stride + 1) * height
    if len(raw) != expected:
        raise ValueError(f"{path}: decompressed length {len(raw)} != expected {expected}")

    prev = bytearray(stride)
    rows: list[bytes] = []
    idx = 0
    for _ in range(height):
        ftype = raw[idx]
        idx += 1
        scan = raw[idx : idx + stride]
        idx += stride
        recon = bytearray(stride)
        if ftype == 0:
            recon[:] = scan
        elif ftype == 1:
            for i in range(stride):
                left = recon[i - bpp] if i >= bpp else 0
                recon[i] = (scan[i] + left) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                recon[i] = (scan[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                left = recon[i - bpp] if i >= bpp else 0
                recon[i] = (scan[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                left = recon[i - bpp] if i >= bpp else 0
                up = prev[i]
                up_left = prev[i - bpp] if i >= bpp else 0
                recon[i] = (scan[i] + _paeth(left, up, up_left)) & 0xFF
        else:
            raise ValueError(f"{path}: unknown PNG filter type {ftype}")
        prev = recon
        rows.append(bytes(recon))

    total_pixels = width * height
    step = max(1, int((total_pixels / max_samples) ** 0.5))
    samples: list[tuple[int, int, int]] = []
    non_black = 0
    step_bytes = bpp // channels

    for y in range(0, height, step):
        row = rows[y]
        for x in range(0, width, step):
            off = x * bpp
            if color_type == 0:
                g = row[off]
                rgb = (g, g, g)
            elif color_type == 4:
                g = row[off]
                rgb = (g, g, g)
            elif color_type == 3:
                pi = row[off] * 3
                rgb = (
                    (palette[pi], palette[pi + 1], palette[pi + 2])
                    if pi + 2 < len(palette)
                    else (0, 0, 0)
                )
            else:
                rgb = (
                    row[off],
                    row[off + step_bytes],
                    row[off + 2 * step_bytes],
                )
            samples.append(rgb)
            if rgb != (0, 0, 0):
                non_black += 1

    unique = len(set(samples))
    return {
        "path": path,
        "width": width,
        "height": height,
        "bit_depth": bit_depth,
        "color_type": color_type,
        "sample_count": len(samples),
        "non_black_ratio": round(non_black / max(len(samples), 1), 4),
        "unique_sampled_colors": unique,
        "samples": samples,
    }


def cmd_check_png(args: argparse.Namespace) -> int:
    info = decode_png_rgb_samples(args.png)
    samples = info.pop("samples")
    if args.require_non_black and info["non_black_ratio"] <= 0.0:
        print(f"FAIL: {args.png} is completely black", file=sys.stderr)
        return 1

    if args.compare_unlocked:
        base = decode_png_rgb_samples(args.compare_unlocked)
        base_samples = base.pop("samples")
        n = min(len(samples), len(base_samples))
        diffs = 0
        abs_sum = 0
        for (r1, g1, b1), (r2, g2, b2) in zip(samples[:n], base_samples[:n]):
            delta = abs(r1 - r2) + abs(g1 - g2) + abs(b1 - b2)
            abs_sum += delta
            if max(abs(r1 - r2), abs(g1 - g2), abs(b1 - b2)) > 2:
                diffs += 1
        info["diff_ratio"] = round(diffs / max(n, 1), 4)
        info["mean_channel_diff"] = round(abs_sum / max(3 * n, 1), 2)
        if args.require_masked:
            # Valid lock capture is either nearly uniform (black/mask) or visibly altered.
            if info["unique_sampled_colors"] > 4 and info["diff_ratio"] < 0.05:
                print(
                    f"FAIL: locked PNG appears unmasked (unique={info['unique_sampled_colors']}, diff_ratio={info['diff_ratio']})",
                    file=sys.stderr,
                )
                return 1

    if args.json:
        print(json.dumps(info))
    else:
        summary = (
            f"PNG {info['width']}x{info['height']} color={info['color_type']} "
            f"depth={info['bit_depth']} non_black={info['non_black_ratio']} "
            f"unique_sampled={info['unique_sampled_colors']}"
        )
        if "diff_ratio" in info:
            summary += f" diff_ratio={info['diff_ratio']} mean_diff={info['mean_channel_diff']}"
        print(summary)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Wayland protocol and PNG probe for Koma VM tests")
    sub = parser.add_subparsers(dest="command", required=True)

    lp = sub.add_parser("layer-panel", help="Map a wlr-layer-shell surface with exclusive_zone")
    lp.add_argument("--display", default=None)
    lp.add_argument("--height", type=int, default=64)
    lp.add_argument("--anchor", choices=("top", "bottom"), default="top")
    lp.add_argument("--namespace", default="koma-smoke-panel")
    lp.add_argument("--ready-file", default=None)
    lp.add_argument("--timeout", type=float, default=120.0)
    lp.set_defaults(func=cmd_layer_panel)

    sl = sub.add_parser("session-lock", help="Acquire ext-session-lock-v1 until SIGUSR1/SIGTERM")
    sl.add_argument("--display", default=None)
    sl.add_argument("--ready-file", default=None)
    sl.add_argument("--unlocked-file", default=None)
    sl.add_argument("--timeout", type=float, default=120.0)
    sl.set_defaults(func=cmd_session_lock)

    cp = sub.add_parser("check-png", help="Validate and inspect PNG pixels using stdlib zlib")
    cp.add_argument("png", help="Path to PNG file")
    cp.add_argument("--compare-unlocked", default=None, help="Compare against unlocked PNG")
    cp.add_argument("--require-non-black", action="store_true")
    cp.add_argument("--require-masked", action="store_true")
    cp.add_argument("--json", action="store_true")
    cp.set_defaults(func=cmd_check_png)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
