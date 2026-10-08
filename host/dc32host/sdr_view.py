"""Badge spectrum + waterfall (Ctrl+Alt+W): what the RTL-SDR hears around the tuned frequency."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from . import capture as C
from .runner_view import BG, INK, INK2, MUTED, GRID, OK, _ttf
from .sdr import RATE, NBINS, PRESETS, STEPS

W, H = C.OUT_W, C.OUT_H
SPEC_Y0, SPEC_Y1 = 22, 86          # spectrum trace area
WF_Y0, WF_Y1 = 88, 222             # waterfall area


def _palette():
    """256-entry dark-blue -> cyan -> yellow -> white ramp (readable on the badge LCD)."""
    stops = np.array([[0, 0, 0, 0], [0.25, 20, 30, 120], [0.5, 0, 170, 200], [0.75, 250, 220, 40], [1, 255, 255, 255]])
    t = np.linspace(0, 1, 256)
    return np.stack([np.interp(t, stops[:, 0], stops[:, i]) for i in (1, 2, 3)], -1).astype(np.uint8)


PAL = _palette()


EDGES = np.linspace(0, NBINS, W + 1).astype(int)[:-1]     # each badge column covers 3-4 bins


def to_cols(a):
    """NBINS -> W columns keeping each column's strongest bin, so a narrow carrier never vanishes."""
    return np.maximum.reduceat(a, EDGES, axis=-1)


def mhz(f):
    return f"{f / 1e6:.3f} MHz"


class SdrView:
    def __init__(self, src):
        self.src = src
        self.step_i = 1
        self.preset_i = 0
        self.f, self.f_s, self.f_b = _ttf(12), _ttf(11), _ttf(13, bold=True)

    # ---------------------------------------------------------------- controls
    def key(self, k):
        if k == "left":
            self.src.tune(self.src.freq - STEPS[self.step_i])
        elif k == "right":
            self.src.tune(self.src.freq + STEPS[self.step_i])
        elif k == "up":
            self.step_i = min(len(STEPS) - 1, self.step_i + 1)
        elif k == "down":
            self.step_i = max(0, self.step_i - 1)
        elif k == "preset":
            self.preset_i = (self.preset_i + 1) % len(PRESETS)
            name, f, mode = PRESETS[self.preset_i]
            self.src.tune(f)
            if self.src.audio_mode is not None or mode is None:   # keep listening if already on
                self.src.set_audio(mode)
        elif k == "audio":
            from .sdr_audio import MODES
            self.src.set_audio(MODES[(MODES.index(self.src.audio_mode) + 1) % len(MODES)])

    # ---------------------------------------------------------------- drawing
    def render(self) -> np.ndarray:
        spec, peak, rows = self.src.snapshot()
        out = np.zeros((H, W, 3), np.uint8)
        out[:] = BG
        # waterfall: newest row on top, bins resampled to the badge width, levels auto-ranged
        if rows:
            wf = to_cols(np.array(rows[-(WF_Y1 - WF_Y0):][::-1]))
            lo, hi = np.percentile(wf, 20), np.percentile(wf, 99.7) + 3
            idx = np.clip((wf - lo) / max(hi - lo, 1e-3) * 255, 0, 255).astype(np.uint8)
            out[WF_Y0:WF_Y0 + len(idx)] = PAL[idx]
        img = Image.fromarray(out)
        d = ImageDraw.Draw(img)
        f0, span = self.src.freq, RATE
        d.text((4, 3), mhz(f0), font=self.f_b, fill=INK)
        if getattr(self.src, "audio_mode", None):
            d.text((4 + d.textlength(mhz(f0), font=self.f_b) + 8, 5), "♪ " + self.src.audio_mode, font=self.f_s, fill=OK)
        info = f"step {STEPS[self.step_i] / 1e3:g} kHz   span 2.0 MHz"
        d.text((W - 4 - d.textlength(info, font=self.f_s), 5), info, font=self.f_s, fill=INK2)
        d.line([0, SPEC_Y1 + 1, W, SPEC_Y1 + 1], fill=GRID)
        d.line([W // 2, SPEC_Y0, W // 2, SPEC_Y1], fill=GRID)          # centre marker
        if spec is not None:
            lo, hi = float(np.percentile(spec, 10)) - 3, float(max(peak.max(), spec.max())) + 3
            ys = lambda s: SPEC_Y1 - (np.clip((to_cols(s) - lo) / (hi - lo), 0, 1) * (SPEC_Y1 - SPEC_Y0)).astype(int)
            d.line(list(zip(range(W), ys(peak).tolist())), fill=MUTED)
            d.line(list(zip(range(W), ys(spec).tolist())), fill=OK)
            pb = int(np.argmax(peak))
            pf = f0 + (pb - NBINS / 2) / NBINS * span
            label = f"peak {mhz(pf)}  {peak[pb] - np.median(spec):+.0f} dB"
            d.text((4, 226), label, font=self.f_s, fill=INK2)
        else:
            msg = self.src.error or self.src.status
            d.text((8, 120), f"SDR: {msg}", font=self.f, fill=INK2)
        hint = "◀▶ tune ▲▼ step A band START ♪"
        d.text((W - 4 - d.textlength(hint, font=self.f_s), 226), hint, font=self.f_s, fill=MUTED)
        return np.asarray(img).copy()
