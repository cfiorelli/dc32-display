"""Badge view of the Flipper Zero's screen: 128x64 at 2.5x in Flipper orange.

A keyboard pill in the header always says where the PC keyboard goes: grey "keys: PC" (typing stays on
the computer) or orange "keys: HERE" (the badge has grabbed it). The badge buttons always drive the
Flipper regardless; Ctrl+Alt+Y toggles the keyboard, which can send only the 6 Flipper buttons.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from . import capture as C
from .runner_view import BG, INK, INK2, MUTED, GRID, OK, _ttf

ORANGE = (255, 140, 41)            # Flipper backlight + "keys here" highlight
PIXEL = (10, 10, 10)
X0, Y0, SW, SH = 0, 40, 320, 160   # the Flipper screen, full badge width


def _pill(d, right, y, text, font, bg, fg):
    w = d.textlength(text, font=font)
    x0 = right - w - 12
    d.rounded_rectangle([x0, y, right, y + 18], radius=4, fill=bg)
    d.text((x0 + 6, y + 2), text, font=font, fill=fg)
    return x0


class FlipperView:
    def __init__(self, link):
        self.link = link
        self.kb_here = False       # set by the daemon each frame: is the PC keyboard grabbed for us?
        self.f_big, self.f_s, self.f_pill = _ttf(15, bold=True), _ttf(11), _ttf(12, bold=True)

    def render(self, now=None) -> np.ndarray:
        img = Image.new("RGB", (C.OUT_W, C.OUT_H), BG)
        d = ImageDraw.Draw(img)
        live = self.link.frame is not None and self.link.status == "connected"
        d.ellipse([8, 11, 18, 21], fill=OK if live else MUTED)
        name = f"Flipper {self.link.name}" if self.link.name else "Flipper Zero"
        d.text((26, 6), name, font=self.f_big, fill=INK)
        # keyboard pill, top-right: the glanceable "where does my typing go" indicator
        if self.kb_here:
            _pill(d, C.OUT_W - 6, 6, "keys: HERE", self.f_pill, ORANGE, (0, 0, 0))
        else:
            _pill(d, C.OUT_W - 6, 6, "keys: PC", self.f_pill, (60, 60, 58), INK2)
        d.line([0, 30, C.OUT_W, 30], fill=GRID)

        if self.link.frame is not None:
            px = np.where(self.link.frame[..., None], np.array(PIXEL, np.uint8), np.array(ORANGE, np.uint8))
            img.paste(Image.fromarray(px.astype(np.uint8)).resize((SW, SH), Image.Resampling.NEAREST), (X0, Y0))
        else:
            d.rectangle([X0, Y0, X0 + SW - 1, Y0 + SH - 1], outline=GRID)
            d.text((12, Y0 + SH // 2 - 8), self.link.status[:46], font=self.f_s, fill=INK2)

        if live:
            if self.kb_here:
                hint = "arrows/OK/Back only · Ctrl+Alt+Y releases keyboard"
            else:
                hint = "D-pad  A=OK  B=Back    Ctrl+Alt+Y: keyboard here"
            d.text((6, 226), hint, font=self.f_s, fill=MUTED)
        return np.asarray(img).copy()
