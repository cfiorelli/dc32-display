"""Badge-native cheat sheet (Ctrl+Alt+H, or FN > Shortcuts), two pages:
  1/2 keyboard shortcuts (Ctrl+Alt+<key>); while help is open, the number keys run them
  2/2 badge buttons
Ctrl+Alt+H again turns the page, then closes; on the badge left/right turn pages, B closes; Esc closes.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from . import capture as C
from .runner_view import BG, INK, INK2, MUTED, GRID, HOSTED, _ttf

# (key after Ctrl+Alt+, what it does, daemon action run by its number while help is open)
KEYS = [("B", "runner costs", "toggle_runner"), ("R", "refresh data", "refresh_data"),
        ("Z", "zoom", "zoom_cycle"), ("X", "fit", "zoom_fit"),
        ("G", "lights", "lights_next"), ("S", "sleep", "sleep"),
        ("P", "Doom II", "doom2"), ("E", "IR scope", "ir_scope"),
        ("F", "Flipper Zero", "flipper"), ("W", "spectrum", "sdr_view"),
        ("V", "RF bench", None), ("Y", "keys to badge", None),
        ("I J K L", "move view", None), ("H", "next page", None)]
NUMBERED = [a for _, _, a in KEYS if a]          # number key n runs NUMBERED[n - 1]; 0 = the 10th
BADGE = [("FN", "menu"), ("B", "back / Esc"),
         ("A", "zoom"), ("A hold", "runner"),
         ("D-pad", "move view"), ("START", "app list"),
         ("SELECT", "previous app"), ("SEL hold", "pin mode"),
         ("FN + B", "info"), ("FN ▲▼", "brightness")]
PAGES = 2
ROW_SHADE = (34, 34, 33)

_cache = {}


def _page(d, title, page, f_title, f_small):
    d.text((10, 6), title, font=f_title, fill=HOSTED)
    tag = f"{page + 1}/{PAGES}"
    d.text((C.OUT_W - 10 - d.textlength(tag, font=f_title), 6), tag, font=f_title, fill=INK2)
    d.line([0, 30, C.OUT_W, 30], fill=GRID)


def render(page: int = 0) -> np.ndarray:
    page %= PAGES
    if page in _cache:
        return _cache[page]
    img = Image.new("RGB", (C.OUT_W, C.OUT_H), BG)
    d = ImageDraw.Draw(img)
    f_title, f_key, f_desc, f_small = _ttf(16, bold=True), _ttf(14, bold=True), _ttf(13), _ttf(11)
    half = C.OUT_W // 2
    if page == 0:
        _page(d, "Keyboard  Ctrl+Alt+", page, f_title, f_small)
        n = 0
        rows = (len(KEYS) + 1) // 2
        rh = 24
        for r in range(rows):
            if r % 2:
                d.rectangle([0, 34 + r * rh, C.OUT_W, 34 + (r + 1) * rh - 1], fill=ROW_SHADE)
        for i, (k, v, act) in enumerate(KEYS):
            x, y = 8 + (i % 2) * half, 38 + (i // 2) * rh
            if act:
                n += 1
                d.text((x, y), str(n % 10), font=f_key, fill=HOSTED)
            d.text((x + 16, y), k, font=f_key, fill=INK)
            d.text((x + 16 + max(18, d.textlength(k, font=f_key) + 8), y + 1), v, font=f_desc, fill=INK2)
        d.text((10, 222), "press a number to run it   Esc closes", font=f_small, fill=MUTED)
    else:
        _page(d, "Badge buttons", page, f_title, f_small)
        rh = 28
        for r in range((len(BADGE) + 1) // 2):
            if r % 2:
                d.rectangle([0, 34 + r * rh, C.OUT_W, 34 + (r + 1) * rh - 1], fill=ROW_SHADE)
        kw = [max(d.textlength(k, font=f_key) for k, _ in BADGE[c::2]) + 10 for c in (0, 1)]
        for i, (k, v) in enumerate(BADGE):
            x, y = 8 + (i % 2) * half, 40 + (i // 2) * rh
            d.text((x, y), k, font=f_key, fill=INK)
            d.text((x + kw[i % 2], y + 1), v, font=f_desc, fill=INK2)
        d.text((10, 184), "Flipper, spectrum and RF bench screens use", font=f_small, fill=MUTED)
        d.text((10, 198), "the D-pad, A, START for their own controls.", font=f_small, fill=MUTED)
        d.text((10, 222), "left/right: page   B or Esc closes", font=f_small, fill=MUTED)
    _cache[page] = np.asarray(img).copy()
    return _cache[page]
