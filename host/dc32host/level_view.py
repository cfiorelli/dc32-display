"""Bubble level (Ctrl+Alt+O): a spirit level driven by the badge's accelerometer. The badge streams
raw X/Y/Z; this shows a bubble that floats to the high side, target rings, the tilt angle, and a
LEVEL indicator when it is flat. Hold the badge flat (screen up) to use it."""
from __future__ import annotations

import math
import time

import numpy as np
from PIL import Image, ImageDraw

from . import capture as C
from .runner_view import BG, INK, INK2, MUTED, GRID, OK, WARN, _ttf

W, H = C.OUT_W, C.OUT_H
CX, CY, R = W // 2, 118, 92          # level face
G = 16384.0                          # raw counts per g (±2 g full-scale in a 16-bit left-justified word)
LEVEL_DEG = 1.0                      # within this tilt = "LEVEL"


class LevelView:
    def __init__(self, rotate=90, flip_x=False, flip_y=False):
        self.xyz = (0, 0, G)         # last raw sample (flat default)
        self.ts = 0.0
        self.rotate = rotate % 360   # align the accel axes to the screen (badge mounting is rotated)
        self.flip_x, self.flip_y = flip_x, flip_y
        self.f_big, self.f, self.f_s = _ttf(17, bold=True), _ttf(14), _ttf(11)
        self._sx = self._sy = 0.0    # smoothed g components

    def update(self, x, y, z):
        self.xyz = (x, y, z)
        self.ts = time.time()

    def render(self, now=None) -> np.ndarray:
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        live = time.time() - self.ts < 1.0
        d.ellipse([8, 9, 20, 21], fill=OK if live else MUTED)
        d.text((28, 4), "Bubble level", font=self.f_big, fill=INK)
        d.line([0, 28, W, 28], fill=GRID)

        x, y, z = self.xyz
        gx, gy, gz = x / G, y / G, z / G
        # rotate the in-plane vector to match the screen (the chip is mounted turned vs the display)
        for _ in range((self.rotate // 90) % 4):
            gx, gy = -gy, gx
        if self.flip_x:
            gx = -gx
        if self.flip_y:
            gy = -gy
        self._sx += 0.25 * (gx - self._sx)       # light smoothing
        self._sy += 0.25 * (gy - self._sy)
        # tilt angle from vertical; bubble floats to the high side (opposite the in-plane gravity)
        tilt = math.degrees(math.atan2(math.hypot(self._sx, self._sy), abs(gz) + 1e-6))
        level = tilt <= LEVEL_DEG

        d.ellipse([CX - R, CY - R, CX + R, CY + R], outline=GRID, width=2)
        d.ellipse([CX - R // 2, CY - R // 2, CX + R // 2, CY + R // 2], outline=GRID)
        d.line([CX - R, CY, CX + R, CY], fill=GRID)
        d.line([CX, CY - R, CX, CY + R], fill=GRID)
        d.ellipse([CX - 16, CY - 16, CX + 16, CY + 16], outline=OK if level else GRID, width=2)

        # bubble: offset proportional to in-plane gravity, clamped to the face
        bx = CX - self._sx * R * 1.8
        by = CY + self._sy * R * 1.8
        dist = math.hypot(bx - CX, by - CY)
        if dist > R - 12:
            bx = CX + (bx - CX) / dist * (R - 12)
            by = CY + (by - CY) / dist * (R - 12)
        col = OK if level else WARN if tilt < 6 else (120, 180, 255)
        d.ellipse([bx - 12, by - 12, bx + 12, by + 12], fill=col)

        d.text((8, 214), f"tilt {tilt:4.1f}°", font=self.f, fill=OK if level else INK)
        if level:
            t = "LEVEL"
            d.text((W - 10 - d.textlength(t, font=self.f_big), 210), t, font=self.f_big, fill=OK)
        d.text((8, 2 + 0), "", font=self.f_s, fill=MUTED)  # (header spacer)
        d.text((W - 10 - d.textlength("Ctrl+Alt+O · B close", font=self.f_s), 2 + 6),
               "Ctrl+Alt+O · B close", font=self.f_s, fill=MUTED)
        return np.asarray(img).copy()
