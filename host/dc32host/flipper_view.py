"""Badge view of the Flipper Zero's screen: 128x64 at 2x in Flipper orange, plus a status line."""
from __future__ import annotations

import time

import numpy as np
from PIL import Image, ImageDraw

from . import capture as C
from .runner_view import BG, INK, INK2, MUTED, GRID, OK, _ttf

ORANGE = (255, 140, 41)            # Flipper backlight
PIXEL = (10, 10, 10)
X0, Y0, SW, SH = 0, 40, 320, 160   # full badge width: 128x64 at 2.5x


class FlipperView:
    def __init__(self, link):
        self.link = link
        self.f_big, self.f, self.f_s = _ttf(15, bold=True), _ttf(13), _ttf(11)
        self.input_hint = None     # e.g. "keyboard + mouse -> Flipper" while the PC input is grabbed

    def render(self, now=None) -> np.ndarray:
        img = Image.new("RGB", (C.OUT_W, C.OUT_H), BG)
        d = ImageDraw.Draw(img)
        live = self.link.frame is not None and self.link.status == "connected"
        d.ellipse([8, 11, 18, 21], fill=OK if live else MUTED)
        d.text((26, 6), f"Flipper {self.link.name}" if self.link.name else "Flipper Zero", font=self.f_big, fill=INK)
        fw = self.link.firmware() if getattr(self.link, "info", None) else ""
        if fw:
            d.text((C.OUT_W - 8 - d.textlength(fw, font=self.f_s), 10), fw, font=self.f_s, fill=INK2)
        if self.link.frame is not None:
            px = np.where(self.link.frame[..., None], np.array(PIXEL, np.uint8), np.array(ORANGE, np.uint8))
            img.paste(Image.fromarray(px.astype(np.uint8)).resize((SW, SH), Image.Resampling.NEAREST), (X0, Y0))
        else:
            d.rectangle([X0, Y0, X0 + SW - 1, Y0 + SH - 1], outline=GRID)
            d.text((12, Y0 + SH // 2 - 8), self.link.status[:46], font=self.f_s, fill=INK2)
        if live:
            # always say how to drive it: the PC keyboard reaches the Flipper only after Ctrl+Alt+Y,
            # and even then only its 6 buttons (arrows/OK/Back) - no letters, so no typing into apps.
            hint = self.input_hint or "D-pad move  A=OK  B=Back    Ctrl+Alt+Y=keyboard"
            d.text((6, 226), hint, font=self.f_s, fill=MUTED)
        return np.asarray(img).copy()
