"""Badge-native cheat sheet (Ctrl+Alt+H, or FN > Shortcuts): every shortcut on one 320x240 screen.
While it is open, the number keys 1-8 run the numbered keyboard shortcuts (the daemon grabs them)."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from . import capture as C
from .runner_view import BG, INK, INK2, MUTED, GRID, HOSTED, _ttf

# (key after Ctrl+Alt+, what it does, daemon action run by its number while help is open)
KEYS = [("B", "runner view", "toggle_runner"), ("R", "refresh data", "refresh_data"),
        ("Z", "zoom", "zoom_cycle"), ("X", "fit", "zoom_fit"),
        ("G", "lights", "lights_next"), ("S", "sleep", "sleep"),
        ("P", "Doom II", "doom2"), ("E", "IR scope", "ir_scope"),
        ("F", "Flipper Zero", "flipper"), ("W", "spectrum (SDR)", "sdr_view"),
        ("I J K L", "move view", None), ("H", "this help (Esc)", None)]
NUMBERED = [a for _, _, a in KEYS if a]          # number key n runs NUMBERED[n - 1]
BADGE = [("FN", "menu"), ("A", "zoom"), ("A hold", "runner view"), ("B", "back / Esc"),
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
    hint = "or just press 1-9, 0"
    d.text((C.OUT_W - 8 - d.textlength(hint, font=f), 7), hint, font=f, fill=INK2)
    n = 0
    for i, (k, v, act) in enumerate(KEYS):
        x, y = 8 + (i % 2) * 156, 24 + (i // 2) * 14
        if act:
            n += 1
            d.text((x, y), str(n % 10), font=fb, fill=HOSTED)
        d.text((x + 14, y), k, font=fb, fill=INK)
        d.text((x + 62, y), v, font=f, fill=INK2)
    d.line([0, 110, C.OUT_W, 110], fill=GRID)
    d.text((8, 112), "Badge buttons", font=h, fill=HOSTED)
    for i, (k, v) in enumerate(BADGE):
        x, y = 8 + (i % 2) * 156, 131 + (i // 2) * 18
        d.text((x, y), k, font=fb, fill=INK)
        d.text((x + 72, y), v, font=f, fill=INK2)
    d.text((8, 222), "Esc, B or Ctrl+Alt+H closes", font=f, fill=MUTED)
    _cache = np.asarray(img).copy()
    return _cache
