"""Command menu (Ctrl+Alt+M): every badge action in a scrollable list, driven entirely from the PC
keyboard (up/down to move, Enter to run, Esc to close). Host-rendered, so it needs no reaching for
the badge and no Ctrl+Alt+Y - the keys are grabbed while it is open. Actions that change the view
(Flipper, SDR, ...) replace the menu; adjust actions (brightness, lights, zoom) keep it open."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from . import capture as C
from .runner_view import BG, INK, INK2, MUTED, GRID, HOSTED, _ttf

# (label, action). Order: views first, then apps, then adjustments.
COMMANDS = [
    ("Mirror screen", "view_mirror"),
    ("Runner costs", "view_runner"),
    ("Flipper Zero", "flipper"),
    ("Spectrum / SDR", "sdr_view"),
    ("RF bench", "rf_bench"),
    ("IR scope", "ir_scope"),
    ("Doom II", "doom2"),
    ("Badge terminal", "badge_terminal"),
    ("Previous app", "toggle_last_app"),
    ("Pause / resume display", "toggle_pause"),
    ("Lights: next mode", "lights_next"),
    ("Zoom: cycle", "zoom_cycle"),
    ("Zoom: fit whole window", "zoom_fit"),
    ("Brightness up", "brightness_up"),
    ("Brightness down", "brightness_down"),
    ("Refresh runner data", "refresh_data"),
    ("Mode: follow / pin / desktop", "cycle_mode"),
    ("Sleep (screen off)", "sleep"),
    ("Shortcuts / help", "toggle_help"),
    ("System monitor", "system"),
    ("Clock screensaver", "clock"),
]
ROWS = 9                               # visible rows


def clamp(i):
    return max(0, min(len(COMMANDS) - 1, i))


class CommandView:
    def __init__(self):
        self.sel = 0
        self.f_title, self.f, self.f_s = _ttf(15, bold=True), _ttf(14), _ttf(11)

    def move(self, step):
        self.sel = clamp(self.sel + step)

    def action(self):
        return COMMANDS[self.sel][1]

    def jump(self, letter):
        """Move to the next command whose label starts with `letter` (wrapping), for type-to-jump."""
        letter = letter.lower()
        order = list(range(self.sel + 1, len(COMMANDS))) + list(range(0, self.sel + 1))
        for i in order:
            if COMMANDS[i][0].lower().startswith(letter):
                self.sel = i
                return True
        return False

    def render(self, now=None) -> np.ndarray:
        img = Image.new("RGB", (C.OUT_W, C.OUT_H), BG)
        d = ImageDraw.Draw(img)
        d.text((8, 6), "Commands", font=self.f_title, fill=HOSTED)
        tag = f"{self.sel + 1}/{len(COMMANDS)}"
        d.text((C.OUT_W - 8 - d.textlength(tag, font=self.f_s), 9), tag, font=self.f_s, fill=INK2)
        d.line([0, 28, C.OUT_W, 28], fill=GRID)
        top = clamp(self.sel - ROWS // 2)
        top = min(top, max(0, len(COMMANDS) - ROWS))
        rh = 20
        for r in range(min(ROWS, len(COMMANDS) - top)):
            i = top + r
            y = 32 + r * rh
            if i == self.sel:
                d.rectangle([4, y - 1, C.OUT_W - 4, y + rh - 3], fill=HOSTED)
            d.text((12, y), COMMANDS[i][0], font=self.f, fill=(0, 0, 0) if i == self.sel else INK)
        d.text((8, 224), "↑↓ or type a letter · Enter run · Esc close", font=self.f_s, fill=MUTED)
        return np.asarray(img).copy()
