"""Badge-native cheat sheet (Ctrl+Alt+H, or FN > Shortcuts): every shortcut on one 320x240 screen."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from . import capture as C
from .runner_view import BG, INK, INK2, MUTED, GRID, HOSTED, _ttf

KEYS = [("B", "runner view"), ("H", "this help"), ("Z", "zoom"), ("X", "fit"),
        ("I J K L", "move view"), ("", "")]
BADGE = [("FN", "menu"), ("A", "zoom"), ("A hold", "runner view"), ("B", "fit / back"),
         ("SELECT", "prev app"), ("START", "app list"), ("D-pad", "move view"), ("FN+B", "info"),
         ("FN+Up/Dn", "bright"), ("SEL hold", "pin mode")]

_cache = None


def render() -> np.ndarray:
    global _cache
    if _cache is not None:
        return _cache
    img = Image.new("RGB", (C.OUT_W, C.OUT_H), BG)
    d = ImageDraw.Draw(img)
    h, f, fb = _ttf(15, bold=True), _ttf(12), _ttf(12, bold=True)
    d.text((8, 4), "Keyboard: Ctrl+Alt+", font=h, fill=HOSTED)
    for i, (k, v) in enumerate(KEYS):
        x, y = 8 + (i % 2) * 156, 26 + (i // 2) * 17
        d.text((x, y), k, font=fb, fill=INK)
        d.text((x + 52, y), v, font=f, fill=INK2)
    d.line([0, 80, C.OUT_W, 80], fill=GRID)
    d.text((8, 84), "Badge buttons", font=h, fill=HOSTED)
    for i, (k, v) in enumerate(BADGE):
        x, y = 8 + (i % 2) * 156, 106 + (i // 2) * 21
        d.text((x, y), k, font=fb, fill=INK)
        d.text((x + 72, y), v, font=f, fill=INK2)
    d.text((8, 222), "Ctrl+Alt+H or B to close", font=f, fill=MUTED)
    _cache = np.asarray(img).copy()
    return _cache
