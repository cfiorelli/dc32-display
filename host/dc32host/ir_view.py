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


def _bits_by_period(periods, zero, one, n):
    """Pulse-distance bits: start-to-start periods near `zero` / `one` us. None unless all n match."""
    out = []
    for p in periods[:n]:
        if _near(p, zero, 0.25):
            out.append(0)
        elif _near(p, one, 0.25):
            out.append(1)
        else:
            return None
    return out if len(out) == n else None


def decode(pairs):
    """Name the remote protocol and code for a frame of (mark_us, space_us) pairs, or None.

    Decodes from start-to-start periods (mark + space), not mark widths: the badge's IrDA receiver
    may report a long burst as a short blip at its start (or as carrier pulses), but the rhythm of
    burst starts is exact, and NEC / Samsung / Sony all carry their bits in it."""
    if not pairs:
        return None
    periods = [m + s for m, s in pairs[:-1]]
    total = sum(periods) + pairs[-1][0]
    # NEC repeat: 9 ms burst + 2.25 ms gap + stop burst (11.8 ms, 2 bursts; the leader may split)
    if len(pairs) <= 3 and _near(total, 11800, 0.15):
        return "NEC repeat"
    # NEC / Samsung: 32 bits, 0 = 1120 us, 1 = 2250 us start-to-start, after a 13.5 / 9 ms leader
    for k in range(0, max(0, len(periods) - 31)):
        bits = _bits_by_period(periods[k:], 1120, 2250, 32)
        if bits is None:
            continue
        lead = sum(periods[:k])
        v = sum(b << i for i, b in enumerate(bits))
        # a long lead-in: NEC's 13.5 ms leader (the receiver may only catch its tail), or Samsung's 9 ms
        name = "Samsung" if _near(lead, 9000, 0.08) else "NEC" if lead > 3000 else "NEC-like"
        a, na, c, nc = v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF, v >> 24
        if c ^ nc != 0xFF:
            return f"{name} 0x{v:08X}"
        addr = f"0x{a:02X}" if a ^ na == 0xFF else f"0x{a | na << 8:04X}"     # extended NEC: 16-bit address
        return f"{name}  addr {addr}  cmd 0x{c:02X}"
    # Sony SIRC: 3 ms leader period, then 1 = 1800 us, 0 = 1200 us; 12/15/20 bits, LSB first
    if periods and _near(periods[0], 3000, 0.2):
        n = len(pairs) - 1
        bits = _bits_by_period(periods[1:] + [1200 if pairs[-1][0] < 900 else 1800], 1200, 1800, n) \
            if n in (12, 15, 20) else None
        if bits:
            cmd = sum(b << i for i, b in enumerate(bits[:7]))
            dev = sum(b << i for i, b in enumerate(bits[7:]))
            return f"Sony {n}-bit  dev 0x{dev:02X}  cmd 0x{cmd:02X}"
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
