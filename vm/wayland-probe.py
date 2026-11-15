#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Pure-stdlib Wayland protocol and PNG probe for Koma VM smoke tests."""

from __future__ import annotations

import argparse
import array
import json
import math
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


_FONT_5X7: dict[str, tuple[int, ...]] = {
    "A": (0b01110, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001),
    "B": (0b11110, 0b10001, 0b10001, 0b11110, 0b10001, 0b10001, 0b11110),
    "C": (0b01110, 0b10001, 0b10000, 0b10000, 0b10000, 0b10001, 0b01110),
    "D": (0b11110, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b11110),
    "E": (0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b11111),
    "F": (0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b10000),
    "G": (0b01110, 0b10001, 0b10000, 0b10111, 0b10001, 0b10001, 0b01110),
    "H": (0b10001, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001),
    "I": (0b01110, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110),
    "K": (0b10001, 0b10010, 0b10100, 0b11000, 0b10100, 0b10010, 0b10001),
    "L": (0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b11111),
    "M": (0b10001, 0b11011, 0b10101, 0b10101, 0b10001, 0b10001, 0b10001),
    "N": (0b10001, 0b11001, 0b10101, 0b10011, 0b10001, 0b10001, 0b10001),
    "O": (0b01110, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110),
    "P": (0b11110, 0b10001, 0b10001, 0b11110, 0b10000, 0b10000, 0b10000),
    "R": (0b11110, 0b10001, 0b10001, 0b11110, 0b10100, 0b10010, 0b10001),
    "S": (0b01111, 0b10000, 0b10000, 0b01110, 0b00001, 0b00001, 0b11110),
    "T": (0b11111, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100),
    "U": (0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110),
    "V": (0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01010, 0b00100),
    "W": (0b10001, 0b10001, 0b10001, 0b10101, 0b10101, 0b11011, 0b10001),
    "X": (0b10001, 0b10001, 0b01010, 0b00100, 0b01010, 0b10001, 0b10001),
    "Y": (0b10001, 0b10001, 0b01010, 0b00100, 0b00100, 0b00100, 0b00100),
    "0": (0b01110, 0b10001, 0b10011, 0b10101, 0b11001, 0b10001, 0b01110),
    "1": (0b00100, 0b01100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110),
    "2": (0b01110, 0b10001, 0b00001, 0b00110, 0b01000, 0b10000, 0b11111),
    "3": (0b11110, 0b00001, 0b00001, 0b01110, 0b00001, 0b00001, 0b11110),
    "4": (0b00010, 0b00110, 0b01010, 0b10010, 0b11111, 0b00010, 0b00010),
    "5": (0b11111, 0b10000, 0b11110, 0b00001, 0b00001, 0b10001, 0b01110),
    "6": (0b01110, 0b10000, 0b10000, 0b11110, 0b10001, 0b10001, 0b01110),
    "7": (0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b01000, 0b01000),
    "8": (0b01110, 0b10001, 0b10001, 0b01110, 0b10001, 0b10001, 0b01110),
    "9": (0b01110, 0b10001, 0b10001, 0b01111, 0b00001, 0b00001, 0b01110),
}

_PAINT_CANVAS_BG_RGB = (0xFA, 0xF8, 0xF2)
_PAINT_TOOLBAR_BG_RGB = (0x24, 0x27, 0x33)
_PAINT_SLOTS: tuple[dict[str, Any], ...] = (
    {"kind": "color", "name": "RED", "rgb": (0xE0, 0x3E, 0x36)},
    {"kind": "color", "name": "ORG", "rgb": (0xF0, 0x8C, 0x00)},
    {"kind": "color", "name": "YLW", "rgb": (0xF5, 0xC4, 0x00)},
    {"kind": "color", "name": "GRN", "rgb": (0x2F, 0x9E, 0x44)},
    {"kind": "color", "name": "CYN", "rgb": (0x15, 0xAA, 0xBF)},
    {"kind": "color", "name": "BLU", "rgb": (0x1C, 0x7E, 0xD6)},
    {"kind": "color", "name": "PRP", "rgb": (0x79, 0x50, 0xF2)},
    {"kind": "color", "name": "BRN", "rgb": (0x8D, 0x5B, 0x4C)},
    {"kind": "color", "name": "BLK", "rgb": (0x1E, 0x1E, 0x24)},
    {"kind": "color", "name": "ERS", "rgb": _PAINT_CANVAS_BG_RGB},
    {"kind": "brush", "name": "S3", "radius": 3, "rgb": (0x3D, 0x43, 0x56)},
    {"kind": "brush", "name": "M7", "radius": 7, "rgb": (0x3D, 0x43, 0x56)},
    {"kind": "brush", "name": "L13", "radius": 13, "rgb": (0x3D, 0x43, 0x56)},
    {"kind": "action", "name": "CLR", "rgb": (0x54, 0x38, 0x3E)},
    {"kind": "action", "name": "SAV", "rgb": (0x2E, 0x4F, 0x44)},
)


def _argb_pixel(rgb: tuple[int, int, int]) -> bytes:
    r, g, b = rgb
    return struct.pack("<BBBB", b, g, r, 0xFF)


def _write_atomic_bytes(path: str, payload: bytes) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "wb") as fh:
        fh.write(payload)
    os.replace(tmp, path)


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(tag + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


def _encode_bgra_to_png(width: int, height: int, bgra: bytearray) -> bytes:
    stride = width * 4
    raw = bytearray((stride + 1) * height)
    bgra_view = memoryview(bgra)
    raw_view = memoryview(raw)
    for y in range(height):
        src_off = y * stride
        dst_off = y * (stride + 1)
        raw[dst_off] = 0
        row_view = raw_view[dst_off + 1 : dst_off + 1 + stride]
        src_row = bgra_view[src_off : src_off + stride]
        row_view[:] = src_row
        # Convert little-endian ARGB8888 (B, G, R, A in memory) to RGBA PNG scanlines.
        row_view[0::4] = src_row[2::4]
        row_view[2::4] = src_row[0::4]
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    idat = zlib.compress(bytes(raw), level=4)
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")


def _fill_rect(
    buf: bytearray,
    width: int,
    height: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    pixel: bytes,
) -> None:
    cx0 = max(0, x0)
    cy0 = max(0, y0)
    cx1 = min(width, x1)
    cy1 = min(height, y1)
    if cx1 <= cx0 or cy1 <= cy0:
        return
    span_bytes = pixel * (cx1 - cx0)
    stride = width * 4
    for y in range(cy0, cy1):
        off = y * stride + cx0 * 4
        buf[off : off + len(span_bytes)] = span_bytes


def _draw_text_5x7(
    buf: bytearray,
    width: int,
    height: int,
    x: int,
    y: int,
    text: str,
    pixel: bytes,
    scale: int = 2,
) -> None:
    cursor_x = x
    for ch in text.upper():
        glyph = _FONT_5X7.get(ch)
        if glyph is not None:
            for row_idx, bits in enumerate(glyph):
                for col_idx in range(5):
                    if (bits >> (4 - col_idx)) & 1:
                        px = cursor_x + col_idx * scale
                        py = y + row_idx * scale
                        _fill_rect(buf, width, height, px, py, px + scale, py + scale, pixel)
        cursor_x += 6 * scale


def _stamp_disk(
    buf: bytearray,
    width: int,
    height: int,
    min_y: int,
    cx: int,
    cy: int,
    radius: int,
    pixel: bytes,
) -> None:
    stride = width * 4
    y_start = max(min_y, cy - radius)
    y_end = min(height - 1, cy + radius)
    r2 = radius * radius
    for py in range(y_start, y_end + 1):
        dy = py - cy
        dx = math.isqrt(max(0, r2 - dy * dy))
        x0 = max(0, cx - dx)
        x1 = min(width, cx + dx + 1)
        if x1 > x0:
            off = py * stride + x0 * 4
            buf[off : off + (x1 - x0) * 4] = pixel * (x1 - x0)


def _draw_capsule(
    buf: bytearray,
    width: int,
    height: int,
    min_y: int,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    radius: int,
    pixel: bytes,
) -> None:
    dist = math.hypot(x1 - x0, y1 - y0)
    step_len = max(1.0, radius * 0.45)
    steps = max(1, int(math.ceil(dist / step_len)))
    for idx in range(steps + 1):
        t = idx / steps
        ix = round(x0 + (x1 - x0) * t)
        iy = round(y0 + (y1 - y0) * t)
        _stamp_disk(buf, width, height, min_y, ix, iy, radius, pixel)


class _PaintSurfaceApp:
    """Pure-stdlib Wayland SHM + xdg-shell + wl_pointer interactive canvas."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.width = max(300, int(args.width))
        self.height = max(240, int(args.height))
        self.pending_width = self.width
        self.pending_height = self.height
        self.bar_h = self._compute_bar_height(self.height)
        self.canvas = bytearray()
        self._reset_canvas(clear_only=False)

        self.active_color_idx = 0
        self.brush_radius = 7
        self.colors_used: list[str] = [_PAINT_SLOTS[0]["name"]]
        self.enter_events = 0
        self.motion_events = 0
        self.button_events = 0
        self.toolbar_clicks = 0
        self.clears = 0
        self.saves = 0
        self.strokes: list[dict[str, Any]] = []
        self._active_stroke: dict[str, Any] | None = None

        self.configured = False
        self.running = True
        self.dirty = False
        self.pointer_inside = False
        self.pointer_x = 0.0
        self.pointer_y = 0.0
        self.left_down = False
        self._toolbar_hold = False
        self._frame_moved = False
        self._frame_button_state: int | None = None

        self.conn = WaylandConnection(args.display)
        self.shm_id = 0
        self.surface_id = 0
        self.xdg_surface_id = 0
        self.xdg_toplevel_id = 0
        self.pointer_id = 0
        # Each slot: {"pool_id": int, "buf_id": int, "fd": int, "mm": mmap.mmap, "w": int, "h": int, "busy": bool}
        self.slots: list[dict[str, Any]] = [{}, {}]
        self._render_toolbar()

    @staticmethod
    def _compute_bar_height(height: int) -> int:
        return max(48, round(height / 15))

    def _reset_canvas(self, clear_only: bool) -> None:
        self.bar_h = self._compute_bar_height(self.height)
        bg_row = _argb_pixel(_PAINT_CANVAS_BG_RGB) * self.width
        if not clear_only or len(self.canvas) != self.width * self.height * 4:
            self.canvas = bytearray(bg_row * self.height)
        else:
            start = self.bar_h * self.width * 4
            self.canvas[start:] = bg_row * (self.height - self.bar_h)
        self._render_toolbar()
        self.dirty = True

    def _render_toolbar(self) -> None:
        self.bar_h = self._compute_bar_height(self.height)
        _fill_rect(
            self.canvas,
            self.width,
            self.height,
            0,
            0,
            self.width,
            self.bar_h,
            _argb_pixel(_PAINT_TOOLBAR_BG_RGB),
        )
        col_w = self.width / len(_PAINT_SLOTS)
        for idx, slot in enumerate(_PAINT_SLOTS):
            sx0 = round(idx * col_w) + 2
            sx1 = round((idx + 1) * col_w) - 2
            sy0 = 3
            sy1 = self.bar_h - 4
            _fill_rect(
                self.canvas,
                self.width,
                self.height,
                sx0,
                sy0,
                sx1,
                sy1,
                _argb_pixel(slot["rgb"]),
            )
            selected = (slot["kind"] == "color" and idx == self.active_color_idx) or (
                slot["kind"] == "brush" and slot["radius"] == self.brush_radius
            )
            border_px = _argb_pixel((0xFF, 0xFF, 0xFF) if selected else (0x12, 0x13, 0x18))
            border_t = 3 if selected else 1
            _fill_rect(self.canvas, self.width, self.height, sx0, sy0, sx1, sy0 + border_t, border_px)
            _fill_rect(self.canvas, self.width, self.height, sx0, sy1 - border_t, sx1, sy1, border_px)
            _fill_rect(self.canvas, self.width, self.height, sx0, sy0, sx0 + border_t, sy1, border_px)
            _fill_rect(self.canvas, self.width, self.height, sx1 - border_t, sy0, sx1, sy1, border_px)

            r, g, b = slot["rgb"]
            luma = 0.299 * r + 0.587 * g + 0.114 * b
            fg = _argb_pixel((0x10, 0x12, 0x18) if luma > 145 else (0xFA, 0xFA, 0xFA))
            label = slot["name"]
            scale = 2 if (sx1 - sx0) >= 48 else 1
            text_w = len(label) * 6 * scale - scale
            tx = max(sx0 + 3, (sx0 + sx1 - text_w) // 2)
            ty = max(sy0 + 4, (sy0 + sy1 - 7 * scale) // 2)
            _draw_text_5x7(self.canvas, self.width, self.height, tx, ty, label, fg, scale=scale)

        _fill_rect(
            self.canvas,
            self.width,
            self.height,
            0,
            self.bar_h - 2,
            self.width,
            self.bar_h,
            _argb_pixel((0xC8, 0xC2, 0xB4)),
        )
        self.dirty = True

    def _destroy_slot(self, slot_idx: int) -> None:
        slot = self.slots[slot_idx]
        if not slot:
            return
        try:
            self.conn.handlers.pop(slot["buf_id"], None)
            self.conn.send(slot["buf_id"], 0)
            self.conn.send(slot["pool_id"], 1)
        except OSError:
            pass
        try:
            slot["mm"].close()
        except Exception:
            pass
        try:
            os.close(slot["fd"])
        except OSError:
            pass
        self.slots[slot_idx] = {}

    def _ensure_slot(self, slot_idx: int) -> dict[str, Any]:
        slot = self.slots[slot_idx]
        if slot and (slot["w"] != self.width or slot["h"] != self.height) and not slot["busy"]:
            self._destroy_slot(slot_idx)
            slot = {}
        if slot:
            return slot
        stride = self.width * 4
        size = stride * self.height
        fd = _create_shm_fd(size)
        mm = mmap.mmap(fd, size, mmap.MAP_SHARED, mmap.PROT_WRITE | mmap.PROT_READ)
        pool_id = self.conn.alloc_id()
        self.conn.send(self.shm_id, 0, struct.pack("<Ii", pool_id, size), fds=[fd])
        buf_id = self.conn.alloc_id()
        self.conn.send(
            pool_id,
            0,
            struct.pack("<IiiiiI", buf_id, 0, self.width, self.height, stride, WL_SHM_FORMAT_ARGB8888),
        )

        def _on_buf_event(opcode: int, _payload: bytes, idx: int = slot_idx) -> None:
            if opcode == 0 and self.slots[idx]:
                self.slots[idx]["busy"] = False
                if self.slots[idx]["w"] != self.width or self.slots[idx]["h"] != self.height:
                    self._destroy_slot(idx)

        self.conn.handlers[buf_id] = _on_buf_event
        slot = {
            "pool_id": pool_id,
            "buf_id": buf_id,
            "fd": fd,
            "mm": mm,
            "w": self.width,
            "h": self.height,
            "busy": False,
        }
        self.slots[slot_idx] = slot
        return slot

    def _present_if_dirty(self) -> None:
        if not self.configured or not self.dirty:
            return
        for idx in (0, 1):
            slot = self.slots[idx]
            if slot and slot.get("busy"):
                continue
            slot = self._ensure_slot(idx)
            if slot["w"] != self.width or slot["h"] != self.height:
                continue
            size = self.width * self.height * 4
            slot["mm"][:size] = self.canvas
            self.conn.send(self.surface_id, 1, struct.pack("<Iii", slot["buf_id"], 0, 0))
            self.conn.send(
                self.surface_id,
                2,
                struct.pack("<iiii", 0, 0, self.width, self.height),
            )
            self.conn.send(self.surface_id, 6)
            slot["busy"] = True
            self.dirty = False
            return

    def _handle_toolbar_click(self, x: float) -> None:
        idx = max(0, min(len(_PAINT_SLOTS) - 1, int(x * len(_PAINT_SLOTS) / max(1, self.width))))
        slot = _PAINT_SLOTS[idx]
        self.toolbar_clicks += 1
        if slot["kind"] == "color":
            self.active_color_idx = idx
            if slot["name"] not in self.colors_used:
                self.colors_used.append(slot["name"])
            self._render_toolbar()
        elif slot["kind"] == "brush":
            self.brush_radius = int(slot["radius"])
            self._render_toolbar()
        elif slot["name"] == "CLR":
            self.clears += 1
            self._reset_canvas(clear_only=True)
        elif slot["name"] == "SAV":
            self.saves += 1
            self._flush_png()
        self._flush_telemetry()

    def _begin_stroke(self, x: float, y: float) -> None:
        slot = _PAINT_SLOTS[self.active_color_idx]
        pixel = _argb_pixel(slot["rgb"])
        cy = max(float(self.bar_h + self.brush_radius), y)
        _stamp_disk(
            self.canvas,
            self.width,
            self.height,
            self.bar_h,
            round(x),
            round(cy),
            self.brush_radius,
            pixel,
        )
        self.dirty = True
        self._active_stroke = {
            "id": len(self.strokes) + 1,
            "color": slot["name"],
            "hex": "#{:02X}{:02X}{:02X}".format(*slot["rgb"]),
            "radius": self.brush_radius,
            "points": 1,
            "start": [round(x, 1), round(y, 1)],
            "end": [round(x, 1), round(y, 1)],
            "last": [x, cy],
        }

    def _continue_stroke(self, x: float, y: float) -> None:
        if self._active_stroke is None:
            return
        lx, ly = self._active_stroke["last"]
        cy = max(float(self.bar_h + self.brush_radius), y)
        slot = _PAINT_SLOTS[self.active_color_idx]
        _draw_capsule(
            self.canvas,
            self.width,
            self.height,
            self.bar_h,
            lx,
            ly,
            x,
            cy,
            self.brush_radius,
            _argb_pixel(slot["rgb"]),
        )
        self._active_stroke["last"] = [x, cy]
        self._active_stroke["end"] = [round(x, 1), round(y, 1)]
        self._active_stroke["points"] += 1
        self.dirty = True

    def _finish_stroke(self) -> None:
        if self._active_stroke is None:
            return
        entry = dict(self._active_stroke)
        entry.pop("last", None)
        self.strokes.append(entry)
        self._active_stroke = None
        self._flush_png()
        self._flush_telemetry()

    def _apply_pointer_frame(self) -> None:
        btn_state = self._frame_button_state
        moved = self._frame_moved
        self._frame_button_state = None
        self._frame_moved = False

        if btn_state == 1 and not self.left_down:
            self.left_down = True
            if self.pointer_y < self.bar_h:
                self._toolbar_hold = True
                self._handle_toolbar_click(self.pointer_x)
            else:
                self._toolbar_hold = False
                self._begin_stroke(self.pointer_x, self.pointer_y)
        elif moved and self.left_down and not self._toolbar_hold:
            if self._active_stroke is None and self.pointer_y >= self.bar_h:
                self._begin_stroke(self.pointer_x, self.pointer_y)
            else:
                self._continue_stroke(self.pointer_x, self.pointer_y)

        if btn_state == 0 and self.left_down:
            self.left_down = False
            self._toolbar_hold = False
            self._finish_stroke()

    def _flush_png(self) -> None:
        if not self.args.png_out:
            return
        _write_atomic_bytes(
            self.args.png_out,
            _encode_bgra_to_png(self.width, self.height, self.canvas),
        )

    def _flush_telemetry(self) -> None:
        if not self.args.telemetry_file:
            return
        slot = _PAINT_SLOTS[self.active_color_idx]
        payload = {
            "width": self.width,
            "height": self.height,
            "bar_h": self.bar_h,
            "configured": self.configured,
            "enter_events": self.enter_events,
            "motion_events": self.motion_events,
            "button_events": self.button_events,
            "toolbar_clicks": self.toolbar_clicks,
            "clears": self.clears,
            "saves": self.saves,
            "strokes_count": len(self.strokes),
            "active_color_name": slot["name"],
            "active_color_hex": "#{:02X}{:02X}{:02X}".format(*slot["rgb"]),
            "brush_radius": self.brush_radius,
            "colors_used": self.colors_used,
            "last_pointer": {"x": round(self.pointer_x, 2), "y": round(self.pointer_y, 2)},
            "strokes": self.strokes[-64:],
        }
        _write_atomic_bytes(
            self.args.telemetry_file,
            (json.dumps(payload, indent=2) + "\n").encode("utf-8"),
        )

    def setup(self) -> None:
        globals_by_iface = self.conn.discover_globals()
        for req_iface in ("wl_compositor", "wl_shm", "xdg_wm_base", "wl_seat"):
            if req_iface not in globals_by_iface:
                raise WaylandProtocolError(f"missing required Wayland global: {req_iface}")

        comp_id = self.conn.bind(globals_by_iface["wl_compositor"][0], version=4)
        self.shm_id = self.conn.bind(globals_by_iface["wl_shm"][0], version=1)
        xdg_wm_base_id = self.conn.bind(globals_by_iface["xdg_wm_base"][0], version=5)
        seat_id = self.conn.bind(globals_by_iface["wl_seat"][0], version=7)

        self.surface_id = self.conn.alloc_id()
        self.conn.send(comp_id, 0, struct.pack("<I", self.surface_id))

        self.xdg_surface_id = self.conn.alloc_id()
        self.conn.send(xdg_wm_base_id, 2, struct.pack("<II", self.xdg_surface_id, self.surface_id))

        self.xdg_toplevel_id = self.conn.alloc_id()
        self.conn.send(self.xdg_surface_id, 1, struct.pack("<I", self.xdg_toplevel_id))
        self.conn.send(self.xdg_toplevel_id, 2, _pack_string(self.args.title))
        self.conn.send(self.xdg_toplevel_id, 3, _pack_string(self.args.app_id))

        def _on_wm_base(opcode: int, payload: bytes) -> None:
            if opcode == 0 and len(payload) >= 4:
                (serial,) = struct.unpack_from("<I", payload, 0)
                self.conn.send(xdg_wm_base_id, 3, struct.pack("<I", serial))

        def _on_xdg_toplevel(opcode: int, payload: bytes) -> None:
            if opcode == 0 and len(payload) >= 8:
                w, h = struct.unpack_from("<ii", payload, 0)
                if w >= 300 and h >= 240:
                    self.pending_width = w
                    self.pending_height = h
            elif opcode == 1:
                self.running = False

        def _on_xdg_surface(opcode: int, payload: bytes) -> None:
            if opcode == 0 and len(payload) >= 4:
                (serial,) = struct.unpack_from("<I", payload, 0)
                self.conn.send(self.xdg_surface_id, 4, struct.pack("<I", serial))
                resized = (self.pending_width, self.pending_height) != (self.width, self.height)
                if resized:
                    old_canvas = self.canvas
                    old_w, old_h = self.width, self.height
                    self.width, self.height = self.pending_width, self.pending_height
                    self._reset_canvas(clear_only=False)
                    copy_w = min(old_w, self.width)
                    copy_h = min(old_h, self.height)
                    old_bar_h = self._compute_bar_height(old_h)
                    for y in range(max(self.bar_h, old_bar_h), copy_h):
                        src = y * old_w * 4
                        dst = y * self.width * 4
                        self.canvas[dst : dst + copy_w * 4] = old_canvas[src : src + copy_w * 4]
                    self._render_toolbar()
                self.configured = True
                self.dirty = True
                self._present_if_dirty()
                self._flush_telemetry()

        def _on_pointer(opcode: int, payload: bytes) -> None:
            if opcode == 0 and len(payload) >= 16:
                _serial, surf, sx_fx, sy_fx = struct.unpack_from("<IIii", payload, 0)
                if surf == self.surface_id:
                    self.pointer_inside = True
                    self.enter_events += 1
                    self.pointer_x = sx_fx / 256.0
                    self.pointer_y = sy_fx / 256.0
                    self._flush_telemetry()
            elif opcode == 1 and len(payload) >= 8:
                _serial, surf = struct.unpack_from("<II", payload, 0)
                if surf == self.surface_id:
                    self.pointer_inside = False
                    if self.left_down:
                        self.left_down = False
                        self._finish_stroke()
            elif opcode == 2 and len(payload) >= 12:
                _t, sx_fx, sy_fx = struct.unpack_from("<Iii", payload, 0)
                self.motion_events += 1
                self.pointer_x = sx_fx / 256.0
                self.pointer_y = sy_fx / 256.0
                self._frame_moved = True
            elif opcode == 3 and len(payload) >= 16:
                _serial, _t, button, state = struct.unpack_from("<IIII", payload, 0)
                if button == 0x110:
                    self.button_events += 1
                    self._frame_button_state = state
            elif opcode == 5:
                self._apply_pointer_frame()
                self._present_if_dirty()

        def _on_seat(opcode: int, payload: bytes) -> None:
            if opcode == 0 and len(payload) >= 4:
                (caps,) = struct.unpack_from("<I", payload, 0)
                if (caps & 1) != 0 and self.pointer_id == 0:
                    self.pointer_id = self.conn.alloc_id()
                    self.conn.send(seat_id, 0, struct.pack("<I", self.pointer_id))
                    self.conn.handlers[self.pointer_id] = _on_pointer

        self.conn.handlers[xdg_wm_base_id] = _on_wm_base
        self.conn.handlers[self.xdg_surface_id] = _on_xdg_surface
        self.conn.handlers[self.xdg_toplevel_id] = _on_xdg_toplevel
        self.conn.handlers[seat_id] = _on_seat

        self.conn.send(self.surface_id, 6)
        self.conn.roundtrip()
        deadline = time.monotonic() + 10.0
        while not self.configured and time.monotonic() < deadline:
            self.conn.dispatch(timeout=0.05)
        if not self.configured:
            raise WaylandProtocolError("timed out waiting for xdg_surface.configure")

        self._present_if_dirty()
        self.conn.roundtrip()
        self._flush_png()
        self._flush_telemetry()
        _touch_marker(
            self.args.ready_file,
            json.dumps(
                {
                    "app_id": self.args.app_id,
                    "title": self.args.title,
                    "width": self.width,
                    "height": self.height,
                    "bar_h": self.bar_h,
                }
            )
            + "\n",
        )

    def run(self) -> int:
        sig_fd = _install_signal_pipe()
        deadline = time.monotonic() + self.args.timeout
        try:
            self.setup()
            while self.running and time.monotonic() < deadline:
                self._present_if_dirty()
                timeout = min(0.05, max(0.0, deadline - time.monotonic()))
                signaled = self.conn.dispatch(timeout=timeout, extra_fds=[sig_fd])
                if signaled:
                    break
                if self._frame_moved or self._frame_button_state is not None:
                    self._apply_pointer_frame()
                self._present_if_dirty()
            self._finish_stroke()
            self._flush_png()
            self._flush_telemetry()
            return 0
        finally:
            os.close(sig_fd)
            for idx in (0, 1):
                self._destroy_slot(idx)
            self.conn.close()


def cmd_paint_canvas(args: argparse.Namespace) -> int:
    app = _PaintSurfaceApp(args)
    return app.run()


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

    pc = sub.add_parser("paint-canvas", help="Run interactive xdg-shell Koma Paint SHM canvas")
    pc.add_argument("--display", default=None)
    pc.add_argument("--title", default="Koma Paint")
    pc.add_argument("--app-id", default="koma-paint-probe")
    pc.add_argument("--width", type=int, default=1256)
    pc.add_argument("--height", type=int, default=776)
    pc.add_argument("--ready-file", default=None)
    pc.add_argument("--telemetry-file", default=None)
    pc.add_argument("--png-out", default=None)
    pc.add_argument("--timeout", type=float, default=600.0)
    pc.set_defaults(func=cmd_paint_canvas)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
