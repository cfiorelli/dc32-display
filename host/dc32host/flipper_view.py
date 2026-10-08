"""Badge view of the Flipper Zero's screen: 128x64 at 2x in Flipper orange, plus a status line."""
from __future__ import annotations

import time

import numpy as np
from PIL import Image, ImageDraw

from . import capture as C
from .runner_view import BG, INK, INK2, MUTED, GRID, OK, _ttf

ORANGE = (255, 140, 41)            # Flipper backlight
PIXEL = (10, 10, 10)
X0, Y0, S = 32, 44, 2              # 256x128 screen area


class FlipperView:
    def __init__(self, link):
        self.link = link
        self.f_big, self.f, self.f_s = _ttf(17, bold=True), _ttf(13), _ttf(11)

    def render(self, now=None) -> np.ndarray:
        now = now or time.time()
        img = Image.new("RGB", (C.OUT_W, C.OUT_H), BG)
        d = ImageDraw.Draw(img)
        live = self.link.frame is not None and self.link.status == "connected"
        d.ellipse([8, 9, 20, 21], fill=OK if live else MUTED)
        d.text((28, 4), f"Flipper {self.link.name}" if self.link.name else "Flipper Zero", font=self.f_big, fill=INK)
        d.line([0, 30, C.OUT_W, 30], fill=GRID)
        out = np.asarray(img).copy()
        if self.link.frame is not None:
            px = np.where(self.link.frame[..., None], np.array(PIXEL, np.uint8), np.array(ORANGE, np.uint8))
            out[Y0:Y0 + 64 * S, X0:X0 + 128 * S] = px.repeat(S, 0).repeat(S, 1)
        img = Image.fromarray(out)
        d = ImageDraw.Draw(img)
        if self.link.frame is None:
            d.rectangle([X0, Y0, X0 + 128 * S - 1, Y0 + 64 * S - 1], outline=GRID)
            d.text((X0 + 10, Y0 + 52), self.link.status, font=self.f_s, fill=INK2)
        hint = "D-pad  A = OK  B = Back  (hold = long)" if live else self.link.status
        d.text((8, 186), hint[:52], font=self.f_s, fill=INK2)
        d.text((8, 222), "Ctrl+Alt+F or FN menu to leave", font=self.f_s, fill=MUTED)
        return np.asarray(img).copy()
