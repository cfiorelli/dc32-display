"""A software badge: the real firmware decoder (compiled natively) behind the host's Badge interface.
Lets the full host daemon run without hardware (tests/test_e2e_x11.py)."""
import ctypes
import queue
import struct
import time

import numpy as np

import test_protocol as T
from dc32host import protocol as P


class FakeBadge:
    def __init__(self):
        self.L = T.lib()
        self.L.h_ring_payload.restype = ctypes.POINTER(ctypes.c_uint8)
        self.L.h_ring_len.restype = ctypes.c_uint32
        self.events = queue.Queue()
        self.info = None
        self.dev = None
        self.bytes_out = 0
        self.menu = None
        self.writes = []

    @property
    def connected(self):
        return self.dev is not None

    def open(self, timeout=1):
        self.L.h_init()
        self.dev = object()
        self.write(P.SYNC_BYTES + P.hello())
        while True:
            ev = self.events.get(timeout=1)
            if isinstance(ev, P.Info):
                self.info = ev
                return True

    def close(self):
        self.dev = None

    def write(self, data, timeout_ms=2000):
        self.L.h_feed(data, len(data))
        self.bytes_out += len(data)
        self.writes.append((time.time(), len(data)))
        for i in range(self.L.h_ring_count()):
            t = self.L.h_ring_type(i)
            n = self.L.h_ring_len(i)
            p = bytes(self.L.h_ring_payload(i)[:min(n, 64)])
            if t == P.HELLO:
                self.events.put(P.Info(1, 320, 240, "sim+0", 9))
            elif t == P.FRAME_END:
                fid = struct.unpack_from("<I", p)[0]
                self.events.put(P.Ack(fid, 0, 0, 0))
            elif t == P.MENU_LIST:
                self.menu = p
        self.L.h_ring_clear()
        return len(data)

    def press(self, button, event="short", held=()):
        ev = P.ButtonEvent(event, button, list(held), int(time.time() * 1000) & 0xFFFFFFFF, 80)
        self.events.put(ev)

    def menu_pick(self, entry_id, action=P.MENU_SELECT):
        self.events.put(P.MenuResult(0, action, entry_id))

    def screen(self):
        return T.logical_fb()

    def screen_rgb(self):
        f = self.screen().astype(np.uint32)
        r = ((f >> 11) & 0x1F) * 255 // 31
        g = ((f >> 5) & 0x3F) * 255 // 63
        b = (f & 0x1F) * 255 // 31
        return np.dstack([r, g, b]).astype(np.uint8)
