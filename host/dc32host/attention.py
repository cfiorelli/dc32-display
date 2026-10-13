"""Attention lights + report screen.

A separate process writes ~/.cache/dc32-display/attention.json (this daemon only reads it):
  {"at": "<ISO UTC>", "indicators": [{"id", "label", "lit", "reason", "since", "link"}, ...]}
Each indicator owns one LED (config `attention_leds`: id -> LED index) and one screen slot
(config `attention_slots`: id -> "left_top" ... "right_low"). Lit = needs you, off = nothing for you.
The badge has five front LEDs, placed around the screen as in fio's photo (2026-10-13): left top (beside the
screen's top edge), left middle and right middle (beside its bottom edge), left low (D-pad) and right low (A/B).
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
# Default mapping from the firmware's front LEDs (0,2,4,5,6) and its button zones (4 = D-pad, left low;
# 5 = A/B, right low; 6 = START/SELECT, right middle). 0 and 2 (left top / left middle) are UNCONFIRMED:
# check with `ctl leds_identify` and override `attention_leds` in config if they are swapped.
DEFAULT_LEDS = {"spend": 0, "capacity": 2, "pr_waiting": 4, "priority_blocked": 6, "production": 5}
DEFAULT_SLOTS = {"spend": "left_top", "capacity": "left_middle", "pr_waiting": "left_low",
                 "priority_blocked": "right_middle", "production": "right_low"}
SLOT_ORDER = ["left_top", "left_middle", "left_low", "right_middle", "right_low"]
# Where each slot sits on the 320x240 screen: label y, and the arrow toward its LED (point, direction).
# Top LED is beside the screen's top edge; middle LEDs beside its bottom edge; low LEDs below the screen.
SLOT_Y = {"top": 6, "middle": 166, "low": 204}
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


def _arrow(d, side, row, fill):
    """Arrow at the screen edge pointing at the LED: sideways for top/middle, diagonally down for low."""
    W, H = C.OUT_W, C.OUT_H
    if row == "low":
        if side == "left":
            pts = [(2, H - 2), (14, H - 2), (2, H - 14)]
        else:
            pts = [(W - 3, H - 2), (W - 15, H - 2), (W - 3, H - 14)]
    else:
        ay = {"top": 13, "middle": 190}[row]
        ax, step = (3, 1) if side == "left" else (W - 4, -1)
        pts = [(ax, ay), (ax + step * 9, ay - 6), (ax + step * 9, ay + 6)]
    d.polygon(pts, fill=fill)


def render(state, slots_map=None, now=None):
    """320x240 report: each indicator beside its LED with an arrow pointing at it; one short line each."""
    slots_map = {k: v for k, v in (slots_map or DEFAULT_SLOTS).items() if v in SLOT_ORDER}
    now = now or dt.datetime.now(dt.timezone.utc)
    img = Image.new("RGB", (C.OUT_W, C.OUT_H), BG)
    d = ImageDraw.Draw(img)
    f_t, f_l, f_s = _ttf(15, bold=True), _ttf(13, bold=True), _ttf(11)
    title = "Needs you"                        # top right: there is no LED there
    d.text((C.OUT_W - 8 - d.textlength(title, font=f_t), 6), title, font=f_t, fill=INK)
    if state["stale"]:
        tag = "STALE " + _age(state["age"]) if state["age"] is not None else "STALE no data"
        col = BAD
    else:
        tag, col = "data " + _age(state["age"]), MUTED
    d.text((C.OUT_W - 8 - d.textlength(tag, font=f_s), 25), tag, font=f_s, fill=col)
    hint = "Ctrl+Alt+B runner · FN menu · B mirror"
    d.text(((C.OUT_W - d.textlength(hint, font=f_s)) / 2, 112), hint, font=f_s, fill=GRID)
    by_slot = {}
    for n, i in enumerate(state["indicators"]):
        s = slots_map.get(i["id"]) or (SLOT_ORDER[n] if n < len(SLOT_ORDER) and not slots_map else None)
        if s and s not in by_slot:
            by_slot[s] = i
    colw = 136
    for s, ind in by_slot.items():
        side, row = s.split("_")
        x = 18 if side == "left" else C.OUT_W - 18 - colw
        y = SLOT_Y[row]
        lit = bool(ind.get("lit")) and not state["stale"]
        _arrow(d, side, row, LIT if lit else GRID)
        label = ind.get("label") or ind["id"].replace("_", " ")
        age_s = ""
        if lit:
            since = _parse(ind.get("since"))
            age_s = _age((now - since).total_seconds()) if since else ""
        aw = d.textlength(age_s, font=f_s) + 6 if age_s else 0
        d.ellipse([x, y + 3, x + 9, y + 12], fill=LIT if lit else None, outline=LIT if lit else GRID)
        d.text((x + 14, y), _fit(d, label, f_l, colw - 14 - aw), font=f_l, fill=INK if lit else MUTED)
        if age_s:
            d.text((x + colw - aw + 6, y + 1), age_s, font=f_s, fill=WARN)
        if lit:
            d.text((x + 14, y + 16), _fit(d, ind.get("reason") or "", f_s, colw - 14), font=f_s, fill=INK2)
        else:
            d.text((x + 14, y + 16), "clear" if not state["stale"] else "unknown", font=f_s,
                   fill=GRID if not state["stale"] else MUTED)
    return np.asarray(img).copy()
