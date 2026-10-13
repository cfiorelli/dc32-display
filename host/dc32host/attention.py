"""Attention lights + report screen.

A separate process writes ~/.cache/dc32-display/attention.json (this daemon only reads it):
  {"at": "<ISO UTC>", "indicators": [{"id", "label", "lit", "reason", "since", "link"}, ...]}
Each indicator owns one LED (config `attention_leds`: id -> LED index) and one screen slot
(config `attention_slots`: id -> "right_top" ... "left_low"). Lit = needs you, off = nothing for you.
If the file is missing, unreadable or older than `attention_stale_s` (default 10 min), every mapped
LED blinks slowly white, so a dead writer never looks like "all fine".
`dc32host ctl leds_identify` lights LED 0..8 in turn and shows the index, to confirm the mapping.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from . import capture as C
from .runner_view import BG, INK, INK2, MUTED, GRID, BAD, WARN, OK, _ttf

PATH = Path.home() / ".cache" / "dc32-display" / "attention.json"
STALE_S = 600
LIT = (255, 80, 0)                    # one steady colour for "needs you"
STALE_RGB = (255, 255, 255)
# Default mapping from the firmware's front LEDs (0,2,4,5,6) and its button zones (4 = D-pad, left;
# 5 = A/B, right; 6 = START/SELECT, low). UNCONFIRMED until checked with `ctl leds_identify`.
DEFAULT_LEDS = {"priority_blocked": 2, "production": 5, "test_machines": 6,
                "spend": 0, "capacity": 4, "pr_waiting": 3}
DEFAULT_SLOTS = {"priority_blocked": "right_top", "production": "right_middle", "test_machines": "right_low",
                 "spend": "left_top", "capacity": "left_middle", "pr_waiting": "left_low"}
SLOT_ORDER = ["right_top", "right_middle", "right_low", "left_top", "left_middle", "left_low"]
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
    for idx in lit_leds(state, leds_map):
        if 0 <= idx < N_LEDS:
            out[idx] = LIT
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


def _age(sec):
    if sec is None:
        return "?"
    sec = max(0, int(sec))
    if sec < 90:
        return f"{sec}s"
    if sec < 5400:
        return f"{round(sec / 60)}m"
    if sec < 172800:
        return f"{round(sec / 3600)}h"
    return f"{round(sec / 86400)}d"


def _fit(d, text, font, width):
    if d.textlength(text, font=font) <= width:
        return text
    while text and d.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


def _wrap(d, text, font, width, lines):
    out, cur = [], ""
    for w in str(text).split():
        trial = (cur + " " + w).strip()
        if d.textlength(trial, font=font) <= width:
            cur = trial
        else:
            if cur:
                out.append(cur)
            cur = w
    if cur:
        out.append(cur)
    if len(out) > lines:
        out = out[:lines]
        out[-1] = _fit(d, out[-1] + "…", font, width)
    return [_fit(d, l, font, width) for l in out]


def render(state, slots_map=None, now=None):
    """320x240 report: each indicator on the side and at the height of its LED, arrow pointing to it."""
    slots_map = slots_map or DEFAULT_SLOTS
    now = now or dt.datetime.now(dt.timezone.utc)
    img = Image.new("RGB", (C.OUT_W, C.OUT_H), BG)
    d = ImageDraw.Draw(img)
    f_t, f_l, f_s = _ttf(15, bold=True), _ttf(13, bold=True), _ttf(11)
    d.text((8, 6), "Needs you", font=f_t, fill=INK)
    if state["stale"]:
        tag = "STALE " + _age(state["age"]) if state["age"] is not None else "STALE no data"
        col = BAD
    else:
        tag, col = "data " + _age(state["age"]), MUTED
    d.text((C.OUT_W - 8 - d.textlength(tag, font=f_t), 6), tag, font=f_t, fill=col)
    d.line([0, 28, C.OUT_W, 28], fill=GRID)
    by_slot = {}
    for n, i in enumerate(state["indicators"]):
        by_slot.setdefault(slots_map.get(i["id"]) or (SLOT_ORDER[n] if n < 6 else None), i)
    colw, rowh, y0 = 132, 62, 34
    for s, ind in by_slot.items():
        if s not in SLOT_ORDER:
            continue
        side, row = s.split("_")
        r = ("top", "middle", "low").index(row)
        x = 18 if side == "left" else C.OUT_W - 18 - colw
        y = y0 + r * rowh
        lit = bool(ind.get("lit")) and not state["stale"]
        ax = 4 if side == "left" else C.OUT_W - 5                      # arrow toward the LED, at the screen edge
        ay = y + 8
        step = -1 if side == "left" else 1
        d.polygon([(ax, ay - 6), (ax, ay + 6), (ax + step * 9, ay)], fill=LIT if lit else GRID)
        label = ind.get("label") or ind["id"].replace("_", " ")
        d.ellipse([x, y + 3, x + 9, y + 12], fill=LIT if lit else None, outline=LIT if lit else GRID)
        d.text((x + 14, y), _fit(d, label, f_l, colw - 14), font=f_l, fill=INK if lit else MUTED)
        if lit:
            since = _parse(ind.get("since"))
            lines = _wrap(d, ind.get("reason") or "", f_s, colw, 2)
            for k, l in enumerate(lines):
                d.text((x, y + 18 + k * 13), l, font=f_s, fill=INK2)
            if since:
                d.text((x, y + 18 + len(lines) * 13), _age((now - since).total_seconds()), font=f_s, fill=WARN)
        else:
            d.text((x, y + 18), "clear" if not state["stale"] else "unknown", font=f_s, fill=GRID if not state["stale"] else MUTED)
    d.line([0, 222, C.OUT_W, 222], fill=GRID)
    d.text((8, 226), "Ctrl+Alt+B runner · FN menu · B mirror", font=f_s, fill=MUTED)
    return np.asarray(img).copy()
