"""Badge LED modes (9 WS2812s; fw >= 0.2). Animated on the host at ~30 fps, 27 bytes per frame.

Ctrl+Alt+G / FN > Lights cycles: off -> bright -> wave -> rainbow -> rave -> runner -> off.
`runner` shows the self-hosted runner: soft green breathing when idle, amber pulse while a job runs,
a green flash when a job just succeeded, slow red blink for 10 minutes after a failure.
"""
from __future__ import annotations

import colorsys
import datetime as dt
import math
import random

N = 9
FRONT, REAR = (0, 2, 4, 5, 6), (1, 3, 7, 8)
MODES = ["off", "bright", "wave", "rainbow", "rave", "runner"]
LABELS = {"off": "Lights off", "bright": "Lights: bright", "wave": "Lights: slow wave",
          "rainbow": "Lights: rainbow", "rave": "Lights: rave", "runner": "Lights: runner status"}


def _hsv(h, s, v):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, max(0.0, min(1.0, v)))
    return int(r * 255), int(g * 255), int(b * 255)


def _all(c):
    return [c] * N


class Lights:
    def __init__(self, mode="off"):
        self.mode = mode if mode in MODES else "off"
        self._rave = [(0, 0, 0)] * N
        self._rave_t = 0.0

    def next_mode(self):
        self.mode = MODES[(MODES.index(self.mode) + 1) % len(MODES)]
        return self.mode

    @property
    def animated(self):
        return self.mode not in ("off", "bright")

    def frame(self, t: float, runner_local: dict | None = None) -> list:
        m = self.mode
        if m == "off":
            return _all((0, 0, 0))
        if m == "bright":
            return _all((255, 214, 170))                      # warm white; the badge caps the power
        if m == "wave":                                       # slow breathing, hue drifting along the LEDs
            out = []
            for i in range(N):
                v = 0.15 + 0.55 * (0.5 + 0.5 * math.sin(2 * math.pi * (t / 6.0 - i / N)))
                out.append(_hsv(0.55 + 0.08 * math.sin(t / 20.0) + i * 0.02, 0.8, v))
            return out
        if m == "rainbow":
            return [_hsv(t / 8.0 + i / N, 1.0, 0.8) for i in range(N)]
        if m == "rave":                                       # fast colour chase; no full-badge strobing
            if t - self._rave_t > 0.12:
                self._rave_t = t
                k = int(t * 8) % N
                self._rave = [_hsv(random.random(), 1.0, 1.0 if i == k or random.random() < 0.25 else 0.15)
                              for i in range(N)]
            return self._rave
        if m == "runner":
            return self._runner(t, runner_local or {})
        return _all((0, 0, 0))

    def _runner(self, t, loc):
        now = dt.datetime.now(dt.timezone.utc)
        units = loc.get("units") or []
        if units and units[0].get("active") != "active":
            return _all((200, 0, 0) if int(t * 2) % 2 else (40, 0, 0))   # service down: fast red blink
        cur, last = loc.get("current"), loc.get("last")
        if cur:
            v = 0.25 + 0.75 * (0.5 + 0.5 * math.sin(2 * math.pi * t))     # 1 Hz amber pulse
            return _all(_hsv(0.09, 1.0, v))
        if last and last.get("at"):
            age = (now - last["at"]).total_seconds()
            if last.get("result") != "Succeeded" and age < 600:
                return _all((180, 0, 0) if int(t) % 2 else (25, 0, 0))     # failed recently
            if last.get("result") == "Succeeded" and age < 60:
                return _all((0, 200, 40) if int(t * 3) % 2 else (0, 40, 8))
        v = 0.06 + 0.12 * (0.5 + 0.5 * math.sin(2 * math.pi * t / 5.0))     # idle: dim green breathing
        return _all(_hsv(0.33, 0.9, v))
