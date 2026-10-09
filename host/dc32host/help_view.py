"""Badge-native cheat sheet (Ctrl+Alt+H, or FN > Shortcuts), three pages:
  1/3 views & apps (Ctrl+Alt+<key>); while help is open the number keys 1-9,0 open them
  2/3 badge buttons
  3/3 controls inside each app
Ctrl+Alt+H cycles the pages (wrapping); on the badge left/right turn pages; Esc or B closes.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from . import capture as C
from .runner_view import BG, INK, INK2, MUTED, GRID, HOSTED, OK, _ttf

# Page 1 — views/apps, numbered 1..9,0 (the number key opens it while help is up).
KEYS = [("B", "runner costs", "toggle_runner"), ("F", "Flipper Zero", "flipper"),
        ("W", "spectrum / SDR", "sdr_view"), ("V", "RF bench", "rf_bench"),
        ("E", "IR scope", "ir_scope"), ("O", "level", "level"),
        ("U", "system monitor", "system"), ("C", "clock", "clock"),
        ("M", "command menu", "command_menu"), ("P", "Doom II", "doom2")]
NUMBERED = [a for _, _, a in KEYS]               # number n opens KEYS[n-1]; 0 = the 10th
# Other Ctrl+Alt shortcuts (not numbered), shown as a footer on page 1.
EXTRA = "also: R refresh · G lights · S sleep · Z/X zoom/fit · Y keyboard · H page"
BADGE = [("FN", "menu"), ("B", "back / close"),
         ("A", "zoom"), ("A hold", "runner"),
         ("D-pad", "move view"), ("START", "app list"),
         ("SELECT", "previous app"), ("SEL hold", "pin mode"),
         ("FN + B", "info"), ("FN ▲▼", "brightness")]
# Page 3 — what the controls do inside each view.
APPS = [("Flipper", "D-pad move · A=OK · B=Back · Ctrl+Alt+Y = PC keys"),
        ("Spectrum", "◀▶ tune · ▲▼ step · A=band · START=audio"),
        ("RF bench", "A=arm · START=verify · hold A=self-test · ◀▶ band"),
        ("Level", "hold flat, screen up; tilts like a ball to the low side"),
        ("Command menu", "↑↓ or type a letter · Enter run · Esc close")]
PAGES = 3
ROW_SHADE = (34, 34, 33)

_cache = {}


def _header(d, title, page, f_title, f_small):
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
        _header(d, "Views  Ctrl+Alt+", page, f_title, f_small)
        rh = 17
        for r in range((len(KEYS) + 1) // 2):
            if r % 2:
                d.rectangle([0, 34 + r * rh, C.OUT_W, 34 + (r + 1) * rh - 1], fill=ROW_SHADE)
        for i, (k, v, _) in enumerate(KEYS):
            x, y = 8 + (i % 2) * half, 36 + (i // 2) * rh
            d.text((x, y), str((i + 1) % 10), font=f_key, fill=HOSTED)
            d.text((x + 16, y), k, font=f_key, fill=INK)
            d.text((x + 40, y + 1), v, font=f_desc, fill=INK2)
        d.text((8, 128), EXTRA[:52], font=f_small, fill=MUTED)
        d.text((8, 148), EXTRA[52:], font=f_small, fill=MUTED) if len(EXTRA) > 52 else None
        d.text((8, 224), "press a number to open · Esc or B closes", font=f_small, fill=MUTED)
    elif page == 1:
        _header(d, "Badge buttons", page, f_title, f_small)
        rh = 28
        for r in range((len(BADGE) + 1) // 2):
            if r % 2:
                d.rectangle([0, 34 + r * rh, C.OUT_W, 34 + (r + 1) * rh - 1], fill=ROW_SHADE)
        kw = [max(d.textlength(k, font=f_key) for k, _ in BADGE[c::2]) + 10 for c in (0, 1)]
        for i, (k, v) in enumerate(BADGE):
            x, y = 8 + (i % 2) * half, 40 + (i // 2) * rh
            d.text((x, y), k, font=f_key, fill=INK)
            d.text((x + kw[i % 2], y + 1), v, font=f_desc, fill=INK2)
        d.text((8, 224), "left/right: page · Esc or B closes", font=f_small, fill=MUTED)
    else:
        _header(d, "App controls", page, f_title, f_small)
        y = 35
        for name, desc in APPS:
            d.text((8, y), name, font=f_key, fill=OK)
            y += 15
            # wrap the description to the width
            words, line = desc.split(" "), ""
            for w in words:
                if d.textlength(line + " " + w, font=f_small) > C.OUT_W - 20:
                    d.text((14, y), line, font=f_small, fill=INK2)
                    y += 14
                    line = w
                else:
                    line = (line + " " + w).strip()
            d.text((14, y), line, font=f_small, fill=INK2)
            y += 17
        d.text((8, 226), "left/right: page · Esc or B closes", font=f_small, fill=MUTED)
    _cache[page] = np.asarray(img).copy()
    return _cache[page]
