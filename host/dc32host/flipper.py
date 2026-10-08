"""Flipper Zero over USB (Ctrl+Alt+F): its screen on the badge, the badge's buttons on the Flipper.

Speaks the Flipper's protobuf RPC (the same one qFlipper uses) on its USB serial port, hand-encoded:
only Main.command_id (1), gui_start/stop_screen_stream (20/21), gui_screen_frame (22: data = 1024
bytes, 128x64, SSD1306 page layout), gui_send_input_event (23: key, type) and system_device_info
(32 request, 33 response: key/value strings, e.g. firmware_version) are needed. Messages are
varint-length-delimited. Needs read/write on the port (udev rule: tools/flipper_udev.sh).
"""
from __future__ import annotations

import glob
import logging
import os
import termios
import threading
import time

import numpy as np

log = logging.getLogger("dc32.flipper")

KEYS = {"up": 0, "down": 1, "right": 2, "left": 3, "ok": 4, "back": 5}
PRESS, RELEASE, SHORT, LONG = 0, 1, 2, 3
W, H = 128, 64


# ---------------------------------------------------------------- minimal protobuf
def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def field_varint(num: int, v: int) -> bytes:
    return varint(num << 3) + varint(v)


def field_bytes(num: int, data: bytes) -> bytes:
    return varint(num << 3 | 2) + varint(len(data)) + data


def read_varint(buf, i):
    n = shift = 0
    while True:
        if i >= len(buf):
            raise IndexError
        b = buf[i]
        i += 1
        n |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return n, i


def fields(buf):
    """Yield (field_number, value) of one message; value is int (varint) or bytes (length-delimited)."""
    i = 0
    while i < len(buf):
        key, i = read_varint(buf, i)
        num, wt = key >> 3, key & 7
        if wt == 0:
            v, i = read_varint(buf, i)
        elif wt == 2:
            ln, i = read_varint(buf, i)
            v, i = bytes(buf[i:i + ln]), i + ln
        elif wt == 5:
            v, i = bytes(buf[i:i + 4]), i + 4
        elif wt == 1:
            v, i = bytes(buf[i:i + 8]), i + 8
        else:
            return
        yield num, v


def main_msg(command_id: int, content_field: int, content: bytes = b"") -> bytes:
    body = field_varint(1, command_id) + field_bytes(content_field, content)
    return varint(len(body)) + body


def frame_to_bitmap(data: bytes) -> np.ndarray:
    """1024-byte Flipper frame -> (64, 128) bool array (pixel on). Bytes are 8-pixel vertical columns."""
    a = np.frombuffer(data[:1024].ljust(1024, b"\0"), np.uint8).reshape(8, W)
    bits = np.unpackbits(a[:, None, :], axis=1, bitorder="little")     # (8 pages, 8 rows, 128)
    return bits.reshape(H, W).astype(bool)


# ---------------------------------------------------------------- link
def find_port():
    ports = sorted(glob.glob("/dev/serial/by-id/*Flipper*"))
    return ports[0] if ports else None


class FlipperLink:
    def __init__(self):
        self.frame = None            # latest (64, 128) bool bitmap
        self.frame_t = 0.0
        self.name = None
        self.status = "not connected"
        self.info = {}               # device info (firmware_version, firmware_origin_fork, ...)
        self._fd = None
        self._cid = 0
        self._lock = threading.Lock()
        self._want = False
        threading.Thread(target=self._loop, daemon=True, name="flipper").start()

    def start(self):
        self._want = True

    def stop(self):
        self._want = False

    def _send(self, content_field, content=b""):
        with self._lock:
            if self._fd is None:
                return
            self._cid = self._cid % 0xFFFF + 1
            try:
                os.write(self._fd, main_msg(self._cid, content_field, content))
            except OSError:
                pass

    def firmware(self) -> str:
        """e.g. 'official 1.3.4' or 'momentum mntm-009' (fork name, version)."""
        i = self.info
        fork = i.get("firmware_origin_fork") or "?"
        return f"{fork} {i.get('firmware_version') or i.get('firmware_branch') or '?'}"

    def press(self, key: str, long: bool = False):
        """A full button press on the Flipper, as its own input system would emit it."""
        k = KEYS[key]
        for t in (PRESS, LONG if long else SHORT, RELEASE):
            self._send(23, field_varint(1, k) + field_varint(2, t))

    def _open(self, port):
        fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        a = termios.tcgetattr(fd)
        a[0] = a[1] = a[3] = 0                               # raw: no iflag/oflag/lflag processing
        a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        termios.tcsetattr(fd, termios.TCSANOW, a)
        os.write(fd, b"\r\nstart_rpc_session\r")
        time.sleep(0.4)
        try:
            while os.read(fd, 4096):                         # drop the CLI banner/echo
                pass
        except BlockingIOError:
            pass
        self._fd = fd
        self.info = {}
        self._send(32)                                       # device info (firmware etc.)
        self._send(20)                                       # start screen stream
        self.status = "connected"
        self.name = os.path.basename(port).split("_Flipper_")[-1].split("_flip")[0] if "_Flipper_" in port else "Flipper"
        log.info("flipper connected on %s", port)

    def _close(self):
        with self._lock:
            if self._fd is not None:
                try:
                    os.write(self._fd, main_msg(self._cid + 1, 21))     # stop screen stream
                except OSError:
                    pass
                os.close(self._fd)
            self._fd = None

    def _loop(self):
        buf = bytearray()
        while True:
            if not self._want:
                if self._fd is not None:
                    self._close()
                    self.status = "not connected"
                time.sleep(0.2)
                continue
            if self._fd is None:
                port = find_port()
                if not port:
                    self.status = "plug the Flipper into this PC (USB)"
                    time.sleep(1)
                    continue
                try:
                    self._open(port)
                    buf.clear()
                except PermissionError:
                    self.status = "no access to the Flipper: run tools/flipper_udev.sh"
                    time.sleep(2)
                    continue
                except OSError as e:
                    self.status = f"Flipper error: {e.strerror}"
                    time.sleep(2)
                    continue
            try:
                chunk = os.read(self._fd, 8192)
            except BlockingIOError:
                time.sleep(0.01)
                continue
            except OSError:
                log.info("flipper disconnected")
                self._fd = None
                self.status = "disconnected"
                continue
            if not chunk:
                time.sleep(0.01)
                continue
            buf += chunk
            while True:                                      # varint-length-delimited Main messages
                try:
                    ln, i = read_varint(buf, 0)
                except IndexError:
                    break
                if ln > 65536:                               # garbage (e.g. leftover CLI text): resync
                    del buf[:1]
                    continue
                if len(buf) < i + ln:
                    break
                msg, buf = bytes(buf[i:i + ln]), buf[i + ln:]
                for num, v in fields(msg):
                    if num == 33 and isinstance(v, bytes):     # device info: one key/value per message
                        kv = dict(fields(v))
                        if isinstance(kv.get(1), bytes):
                            self.info[kv[1].decode(errors="replace")] = (kv.get(2) or b"").decode(errors="replace")
                            if kv[1] == b"firmware_version":
                                log.info("flipper firmware %s", self.firmware())
                    if num == 22 and isinstance(v, bytes):
                        for n2, d in fields(v):
                            if n2 == 1 and isinstance(d, bytes) and len(d) >= 1024:
                                self.frame, self.frame_t = frame_to_bitmap(d), time.time()
