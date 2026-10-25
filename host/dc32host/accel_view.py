"""Tap tuner: live accelerometer impulse, the peak impulse seen, and the current tap threshold.
Up/Down set the threshold live (1..127, units of 16 mg); A clears the peak; B closes. A tap fires
when the high-passed acceleration exceeds the threshold, so set it above the stray-knock peak and
below a deliberate tap. The badge streams raw X/Y/Z while this view is open (like the level)."""
from __future__ import annotations

import math
import time

import numpy as np
from PIL import Image, ImageDraw

from . import capture as C
from .runner_view import BG, INK, INK2, MUTED, GRID, OK, WARN, _ttf

W, H = C.OUT_W, C.OUT_H
G = 16384.0            # raw counts per g (±2 g in a 16-bit left-justified word)
UNIT_MG = 16.0         # one threshold unit = 16 mg


class AccelTunerView:
    def __init__(self, threshold: int):
        self.threshold = max(1, min(127, int(threshold)))
        self.cur_units = 0.0      # current impulse, in threshold-units
        self.max_units = 0.0      # peak impulse seen since last clear
        self.ts = 0.0
        self.f_big, self.f, self.f_s, self.f_huge = _ttf(17, bold=True), _ttf(14), _ttf(11), _ttf(54, bold=True)

    def update(self, x, y, z):
        mag = math.sqrt(x * x + y * y + z * z) / G       # total acceleration, in g
        units = abs(mag - 1.0) * 1000.0 / UNIT_MG        # deviation from gravity, in threshold units
        self.cur_units = units
        if units > self.max_units:
            self.max_units = units
        self.ts = time.time()

    def clear(self):
        self.max_units = 0.0

    def _bar(self, d, y, label, units, col):
        d.text((16, y), label, font=self.f_s, fill=MUTED)
        frac = max(0.0, min(1.0, units / 127.0))
        d.rectangle([16, y + 14, W - 16, y + 26], outline=GRID)
        if frac > 0:
            d.rectangle([16, y + 14, 16 + int((W - 32) * frac), y + 26], fill=col)
        d.text((W - 16 - d.textlength(f"{units:.0f}", font=self.f), y - 2), f"{units:.0f}", font=self.f, fill=INK)
        tx = 16 + int((W - 32) * (self.threshold / 127.0))     # threshold marker
        d.line([tx, y + 11, tx, y + 29], fill=WARN, width=2)

    def render(self, now=None) -> np.ndarray:
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        live = time.time() - self.ts < 1.0
        d.ellipse([8, 9, 20, 21], fill=OK if live else MUTED)
        d.text((28, 4), "Tap tuner", font=self.f_big, fill=INK)
        d.line([0, 28, W, 28], fill=GRID)

        d.text((16, 40), "THRESHOLD (Up/Down)", font=self.f_s, fill=MUTED)
        d.text((16, 52), f"{self.threshold}", font=self.f_huge, fill=INK)
        d.text((150, 92), f"= {self.threshold * UNIT_MG / 1000:.2f} g", font=self.f, fill=MUTED)

        self._bar(d, 140, "PEAK  (A clears)", self.max_units, OK if self.max_units < self.threshold else WARN)
        self._bar(d, 184, "live", self.cur_units, INK2)

        tip = "A clear · B close"
        d.text((W - 10 - d.textlength(tip, font=self.f_s), 8), tip, font=self.f_s, fill=MUTED)
        return np.asarray(img).copy()
