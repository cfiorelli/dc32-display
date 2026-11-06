"""Attention lights.

A separate process writes ~/.cache/dc32-display/attention.json (this daemon only reads it):
  {"at": "<ISO UTC>", "indicators": [{"id", "label", "lit", "level", "reason", "tag", "since", "link"}, ...]}
reason is the descriptor under the label (an optional `segments` [{t, level}] draws it in one colour per piece) (the writer keeps it to 12 characters; an unlit indicator may still carry one);
tag (Lab, Ops, API, Eng) is drawn before it in a category colour. since is not shown.
Each indicator owns one LED (config `attention_leds`: id -> LED index). Lit = needs you, off = nothing for you.
The badge has five front LEDs, placed around the screen: left top (beside the
screen's top edge), left middle and right middle (beside its bottom edge), left low (D-pad) and right low (A/B).
A lit LED pulses (slowly, off to the configured level and back) while an item behind it needs you and is solid when every item
is being handled (`pulse` false); the count on screen does not drive the LED. If the file is missing, unreadable or older than `attention_stale_s` (default 10 min), every mapped
LED blinks slowly white, so a dead writer never looks like "all fine".
`dc32host ctl leds_identify` lights LED 0..8 in turn and shows the index, to confirm the mapping.
"""
from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from . import capture as C
from .runner_view import BG, INK, INK2, MUTED, _ttf

PATH = Path.home() / ".cache" / "dc32-display" / "attention.json"
STALE_S = 600
LIT = (255, 80, 0)                    # lit with no level given (older writers)
# red = act now, yellow = heads-up, blue = a plan sits idle with allowance to spend (give it work). LED values are tuned for
# the WS2812s (pure blue: at the 1% level it still scales to (0,0,2), the same peak as red). Blue is
# well away from the white stale blink.
LEVEL_LED = {"red": (255, 0, 0), "yellow": (255, 150, 0), "blue": (0, 0, 255)}
LEVEL_ORDER = ("red", "yellow", "blue")
STALE_RGB = (255, 255, 255)
# Default mapping from the firmware's front LEDs (0,2,4,5,6) and its button zones (4 = D-pad, left low;
# 5 = A/B, right low; 6 = START/SELECT, right middle). 0 and 2 (left top / left middle) are UNCONFIRMED:
# check with `ctl leds_identify` and override `attention_leds` in config if they are swapped.
DEFAULT_LEDS = {"spend": 0, "capacity": 2, "pr_waiting": 4, "priority_blocked": 6, "production": 5}
IDENTIFY_S = 3.0
N_LEDS = 9


def _parse(ts):
    try:
        t = dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def load(path=None, now=None, stale_s=STALE_S):
    """Read the file. Returns {"stale": bool, "age": seconds|None, "indicators": [...]}; never raises."""
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        with open(path or PATH, encoding="utf-8") as f:
            raw = json.load(f)
        at = _parse(raw.get("at"))
        inds = [i for i in raw.get("indicators", []) if isinstance(i, dict) and i.get("id")]
    except (OSError, ValueError, AttributeError):
        return {"stale": True, "age": None, "indicators": []}
    age = (now - at).total_seconds() if at else None
    return {"stale": age is None or age > stale_s or age < -120, "age": age, "indicators": inds}


def lit_leds(state, leds_map):
    """LED indices that are lit (empty when stale: the blink is drawn instead)."""
    if state["stale"]:
        return set()
    return {leds_map[i["id"]] for i in state["indicators"] if i.get("lit") and i["id"] in leds_map}


def frame(t, state, leds_map):
    """9 RGB tuples. Fresh: lit LEDs steady, rest off. Stale: all mapped LEDs blink slowly white."""
    out = [(0, 0, 0)] * N_LEDS
    if state["stale"]:
        if int(t / 1.5) % 2 == 0:
            for idx in leds_map.values():
                if 0 <= idx < N_LEDS:
                    out[idx] = STALE_RGB
        return out
    for i in state["indicators"]:
        idx = leds_map.get(i["id"])
        if i.get("lit") and idx is not None and 0 <= idx < N_LEDS:
            out[idx] = LEVEL_LED.get(i.get("level"), LIT)
    return out


PULSE_S = 2.0          # one pulse: off -> the configured level -> off
PULSE_OFF_BELOW = 0.12


def pulse_leds(state, leds_map):
    """LED indices that pulse: lit lights with at least one item that needs attention (`pulse` true; missing = needs attention).
    Empty when the data is stale (those blink white instead, see frame())."""
    if state.get("stale", True):
        return set()
    return {leds_map[i["id"]] for i in state.get("indicators", []) if i.get("lit") and i.get("pulse", True) and i["id"] in leds_map}


def pulse_env(t):
    """0..1 raised cosine, PULSE_S long, starting and ending at 0 (fully off)."""
    return (1 - math.cos(2 * math.pi * (t % PULSE_S) / PULSE_S)) / 2


def apply_pulse(cols, idxs, t):
    """Scale the pulsing LEDs of an already level-scaled frame by the envelope. The peak is exactly the configured
    level, the trough is off; in between a lit channel stays at least 1 so a 1% level (peak value 2) still ramps."""
    if not idxs:
        return list(cols)
    e = pulse_env(t)
    out = list(cols)
    for i in idxs:
        if 0 <= i < len(out):
            out[i] = (0, 0, 0) if e < PULSE_OFF_BELOW else tuple(max(1, int(v * e + 0.5)) if v else 0 for v in out[i])
    return out


def identify_frame(t0, now):
    """(frame, index or None). Lights LED 0..8 one at a time, IDENTIFY_S each."""
    k = int((now - t0) / IDENTIFY_S)
    if k >= N_LEDS:
        return [(0, 0, 0)] * N_LEDS, None
    out = [(0, 0, 0)] * N_LEDS
    out[k] = (255, 255, 255)
    return out, k


def render_identify(index):
    img = Image.new("RGB", (C.OUT_W, C.OUT_H), BG)
    d = ImageDraw.Draw(img)
    d.text((10, 8), "LED identify", font=_ttf(16, bold=True), fill=INK2)
    big = _ttf(96, bold=True)
    s = "done" if index is None else str(index)
    if index is None:
        big = _ttf(48, bold=True)
    w = d.textlength(s, font=big)
    d.text(((C.OUT_W - w) / 2, 70), s, font=big, fill=INK)
    d.text((10, 214), "LED index lit now (0-8). Say where it is." if index is not None
           else "Sequence finished.", font=_ttf(12), fill=MUTED)
    return np.asarray(img).copy()




