"""Drifting clock screensaver (Ctrl+Alt+C): a big clock that slowly wanders the screen so a static
image can't be retained on the LCD, with the date and a small system line. Host-rendered.

It can also show automatically when the PC has been idle for `idle_clock_s` seconds (config, 0 = off),
reverting to the mirror on any input - before the backlight dims."""
from __future__ import annotations

import math
import time

import numpy as np
from PIL import Image, ImageDraw

from . import capture as C
from .runner_view import BG, INK, INK2, MUTED, OK, WARN, BAD, _ttf

W, H = C.OUT_W, C.OUT_H


class ClockView:
    def __init__(self):
        self.f_time = _ttf(54, bold=True)
        self.f_date = _ttf(18)
        self.f_s = _ttf(12)

    def render(self, now=None) -> np.ndarray:
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        t = time.localtime()
        clock = time.strftime("%H:%M", t)
        secs = time.strftime(":%S", t)
        date = time.strftime("%A %d %B", t)

        # slow Lissajous drift (period ~2-3 min) to avoid LCD image retention
        tt = time.time()
        tw = d.textlength(clock, font=self.f_time)
        margin = 24
        cx = (W - tw) / 2 + (W - tw - 2 * margin) / 2 * math.sin(tt / 97.0) * 0.8
        cy = 70 + 36 * math.sin(tt / 61.0)
        d.text((cx, cy), clock, font=self.f_time, fill=INK)
        d.text((cx + tw + 2, cy + 24), secs, font=self.f_date, fill=MUTED)
        d.text(((W - d.textlength(date, font=self.f_date)) / 2, cy + 64), date, font=self.f_date, fill=INK2)

        # small system line (best-effort; never the point of failure)
        line = ""
        try:
            import psutil
            up = int(tt - psutil.boot_time())
            parts = [f"up {up // 86400}d {up % 86400 // 3600}h"]
            temp = None
            temps = psutil.sensors_temperatures()
            for k in ("coretemp", "k10temp", "cpu_thermal", "acpitz"):
                if temps.get(k):
                    temp = temps[k][0].current
                    break
            if temp is not None:
                parts.append(f"{temp:.0f}°C")
            line = "   ".join(parts)
        except Exception:
            pass
        if line:
            d.text(((W - d.textlength(line, font=self.f_s)) / 2, 222), line, font=self.f_s, fill=MUTED)
        return np.asarray(img).copy()
