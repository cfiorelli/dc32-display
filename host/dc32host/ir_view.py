"""IR scope (Ctrl+Alt+E): what the badge's IR receiver sees, as pulse trains, plus remote-code decoding.

The badge sends completed frames of (mark_us, space_us) pairs; a mark is one burst of IR light (a
remote's 38 kHz carrier merged), a space the darkness after it. The latest frame is drawn full width,
the four before it as smaller traces underneath.
"""
from __future__ import annotations

import collections
import time

import numpy as np
from PIL import Image, ImageDraw

from . import capture as C
from .runner_view import BG, INK, INK2, MUTED, GRID, HOSTED, SELF, BAD, OK, _ttf, _ago


def _near(v, want, tol=0.3):
    return abs(v - want) <= want * tol


def decode(pairs):
    """Name the remote protocol and code for a frame of (mark_us, space_us) pairs, or None."""
    if not pairs:
        return None
    m0, s0 = pairs[0]
    # NEC: 9 ms / 4.5 ms leader, 32 bits as 560 us marks + 560 (0) / 1690 (1) us spaces; repeat 9 / 2.25 ms.
    # Samsung: same bits after a 4.5 / 4.5 ms leader.
    if _near(m0, 9000) and _near(s0, 2250) and len(pairs) <= 2:
        return "NEC repeat"
    if (_near(m0, 9000) or _near(m0, 4500)) and _near(s0, 4500) and len(pairs) >= 33:
        bits = 0
        for i, (m, s) in enumerate(pairs[1:33]):
            if not _near(m, 560, 0.5):
                return None
            bits |= (1 if s > 1120 else 0) << i
        a, na, c, nc = bits & 0xFF, (bits >> 8) & 0xFF, (bits >> 16) & 0xFF, bits >> 24
        name = "NEC" if _near(m0, 9000) else "Samsung"
        if c ^ nc != 0xFF:
            return f"{name} 0x{bits:08X}"
        addr = f"0x{a:02X}" if a ^ na == 0xFF else f"0x{a | na << 8:04X}"     # extended NEC: 16-bit address
        return f"{name}  addr {addr}  cmd 0x{c:02X}"
    # Sony SIRC: 2.4 ms leader, 600 us spaces, bits in the mark: 1200 us = 1, 600 us = 0, LSB first
    if _near(m0, 2400, 0.25) and len(pairs) - 1 in (12, 15, 20):
        bits = [1 if m > 900 else 0 for m, _ in pairs[1:]]
        cmd = sum(b << i for i, b in enumerate(bits[:7]))
        dev = sum(b << i for i, b in enumerate(bits[7:]))
        return f"Sony {len(bits)}-bit  dev 0x{dev:02X}  cmd 0x{cmd:02X}"
    return None


class IrScope:
    def __init__(self):
        self.frames = collections.deque(maxlen=5)    # (t, pairs, label)
        self.total = 0
        self.f_big, self.f, self.f_b, self.f_s = _ttf(17, bold=True), _ttf(13), _ttf(13, bold=True), _ttf(11)

    def add(self, pairs):
        self.frames.append((time.time(), list(pairs), decode(pairs)))
        self.total += 1

    def _trace(self, d, pairs, x0, x1, y, h, col):
        total = sum(m + s for m, s in pairs) or 1
        d.line([x0, y + h, x1, y + h], fill=GRID)
        t = 0
        for m, s in pairs:
            a = x0 + (x1 - x0) * t / total
            b = x0 + (x1 - x0) * (t + m) / total
            d.rectangle([int(a), y, max(int(a), int(b) - 1), y + h], fill=col)
            t += m + s
        return total

    def render(self, now=None) -> np.ndarray:
        now = now or time.time()
        img = Image.new("RGB", (C.OUT_W, C.OUT_H), BG)
        d = ImageDraw.Draw(img)
        W = C.OUT_W
        last = self.frames[-1] if self.frames else None
        fresh = last and now - last[0] < 0.6
        d.ellipse([8, 9, 20, 21], fill=SELF if fresh else (OK if self.frames else MUTED))
        d.text((28, 4), "IR scope", font=self.f_big, fill=INK)
        right = f"{self.total} signals" if self.total else "point a remote here"
        d.text((W - 8 - d.textlength(right, font=self.f_s), 9), right, font=self.f_s, fill=INK2)
        d.line([0, 30, W, 30], fill=GRID)
        if not last:
            for i, line in enumerate(["Listening on the badge's IR receiver.",
                                      "Press any button on a TV / AC remote",
                                      "aimed at the badge."]):
                d.text((12, 80 + 20 * i), line, font=self.f, fill=INK2)
            d.text((8, 222), "Ctrl+Alt+E or B to close", font=self.f_s, fill=MUTED)
            return np.asarray(img).copy()
        t, pairs, label = last
        total = self._trace(d, pairs, 8, W - 8, 42, 34, HOSTED)
        d.text((8, 82), label or "unknown protocol", font=self.f_b, fill=INK if label else INK2)
        d.text((8, 100), f"{len(pairs)} pulses, {total / 1000:.1f} ms, {_ago(now - t)} ago"
                         if now - t >= 60 else f"{len(pairs)} pulses, {total / 1000:.1f} ms",
               font=self.f_s, fill=MUTED)
        d.line([0, 118, W, 118], fill=GRID)
        for i, (t2, p2, l2) in enumerate(reversed(list(self.frames)[:-1])):
            y = 124 + 24 * i
            self._trace(d, p2, 8, 150, y, 12, MUTED)
            d.text((158, y), (l2 or f"{len(p2)} pulses")[:24], font=self.f_s, fill=INK2)
        return np.asarray(img).copy()
