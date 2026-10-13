"""Attention lights + report screen.

A separate process writes ~/.cache/dc32-display/attention.json (this daemon only reads it):
  {"at": "<ISO UTC>", "indicators": [{"id", "label", "lit", "level", "reason", "tag", "since", "link"}, ...]}
reason is the descriptor under the label (the writer keeps it to 12 characters; an unlit indicator may still carry one);
tag (DWT, Ops, API, Eng) is drawn before it in the Agent Ops dashboard's colour. since is not shown.
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
LIT = (255, 80, 0)                    # lit with no level given (older writers)
# red = act now, yellow = heads-up. LED values are tuned for the WS2812s; screen values for the LCD.
LEVEL_LED = {"red": (255, 0, 0), "yellow": (255, 150, 0)}
LEVEL_SCREEN = {"red": (255, 56, 56), "yellow": (255, 196, 0)}
LEVEL_GLOW = {"red": (90, 0, 0), "yellow": (88, 60, 0)}
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
SLOT_Y = {"top": 8, "middle": 140, "low": 204}
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


# Badge theme (DEF CON 32 "HUMAN" badge: violet PCB, neon cyan screen frame, magenta traces).
TH_BG = (16, 7, 26)
TH_PCB = (40, 18, 58)          # faint background traces
TH_TRACE = (78, 44, 108)       # an indicator's trace when clear
TH_CYAN = (92, 214, 255)
TH_MAGENTA = (255, 64, 196)
TH_VIOLET = (164, 132, 196)    # clear labels
TH_DIM = (98, 74, 128)
TH_GLOW = (96, 30, 0)          # under a lit trace
TH_SOFT = (128, 100, 160)      # descriptor of a clear signal that still has one (CI spend)
# Agent Ops tag colours: pr-watch.py GOAL_STYLE ("API Check" shows as API)
TAG_RGB = {"DWT": (0x5f, 0xd7, 0xff), "Ops": (0xff, 0xaf, 0x5f), "API": (0xff, 0x87, 0xd7), "Eng": (0xaf, 0xff, 0x5f)}
COLW = 130                     # hard cap on a column's text width; left starts at x=22, right ends at x=298, so they never meet

# Faint PCB traces behind everything: (polyline, via at end?)
_PCB = [
    ([(60, 44), (60, 60), (76, 76), (118, 76)], True), ([(320, 52), (292, 52), (276, 68), (276, 96)], True),
    ([(0, 108), (30, 108), (44, 94), (88, 94)], True), ([(320, 124), (270, 124), (256, 110), (232, 110)], True),
    ([(150, 240), (150, 222), (166, 206), (166, 186)], True), ([(110, 186), (124, 172), (196, 172), (210, 186)], False),
    ([(40, 240), (40, 232), (58, 214)], False), ([(280, 240), (280, 232), (262, 214)], False),
]

# Each slot: the trace from its pad to the screen edge nearest the LED (left side; mirrored for right).
_TRACES = {
    "top": [(16, 15), (4, 15)],
    "middle": [(16, 147), (9, 147), (9, 180), (3, 186)],
    "low": [(16, 211), (9, 211), (9, 228), (3, 234)],
}


def _mirror(pts):
    return [(C.OUT_W - 1 - x, y) for x, y in pts]


def _via(d, xy, r, col, fill=None):
    x, y = xy
    d.ellipse([x - r, y - r, x + r, y + r], outline=col, fill=fill, width=1)


def _background(d):
    for pts, via in _PCB:
        d.line(pts, fill=TH_PCB, width=2, joint="curve")
        if via:
            _via(d, pts[-1], 3, TH_PCB)
    # screen-frame corner brackets, like the badge's own screen art
    for (x, y, dx, dy) in ((4, 4, 1, 1), (C.OUT_W - 5, 4, -1, 1), (4, C.OUT_H - 5, 1, -1),
                           (C.OUT_W - 5, C.OUT_H - 5, -1, -1)):
        d.line([(x, y + dy * 10), (x, y), (x + dx * 10, y)], fill=(48, 70, 110), width=1)


def _trace(d, side, row, lit, stale, col=LIT, glow=TH_GLOW):
    pts = _TRACES[row] if side == "left" else _mirror(_TRACES[row])
    if lit:
        d.line(pts, fill=glow, width=6, joint="curve")
        d.line(pts, fill=col, width=2, joint="curve")
        end = pts[-1]
        d.ellipse([end[0] - 3, end[1] - 3, end[0] + 3, end[1] + 3], fill=col)
    else:
        d.line(pts, fill=(120, 120, 130) if stale else TH_TRACE, width=1, joint="curve")
        _via(d, pts[-1], 2, (120, 120, 130) if stale else TH_TRACE)


def render(state, slots_map=None, now=None, boxes=None):
    """320x240 report in the badge's style: each signal beside its LED, a PCB trace running to it.
    `boxes`, when a list, receives (side, x0, y0, x1, y1) for every text run drawn in a column."""
    slots_map = {k: v for k, v in (slots_map or DEFAULT_SLOTS).items() if v in SLOT_ORDER}
    img = Image.new("RGB", (C.OUT_W, C.OUT_H), TH_BG)
    d = ImageDraw.Draw(img)
    _background(d)
    f_t, f_l, f_s, f_big = _ttf(13, bold=True), _ttf(13, bold=True), _ttf(11), _ttf(44, bold=True)
    f_d, f_tag = _ttf(13), _ttf(13, bold=True)     # descriptor line: tag and reason share one size and one baseline
    stale = state["stale"]
    by_slot = {}
    for n, i in enumerate(state["indicators"]):
        s = slots_map.get(i["id"]) or (SLOT_ORDER[n] if n < len(SLOT_ORDER) and not slots_map else None)
        if s and s not in by_slot:
            by_slot[s] = i
    lits = [i for i in by_slot.values() if i.get("lit")] if not stale else []
    n_lit = len(lits)
    worst = "red" if any(i.get("level") == "red" for i in lits) else ("yellow" if lits else None)
    # title, top right (no LED there)
    title = "N E E D S   Y O U"
    tw = d.textlength(title, font=f_t)
    d.text((C.OUT_W - 12 - tw, 9), title, font=f_t, fill=TH_CYAN)
    d.line([(C.OUT_W - 12 - tw, 27), (C.OUT_W - 12, 27)], fill=TH_MAGENTA, width=1)
    if stale:
        tag, col = ("STALE " + _age(state["age"])) if state["age"] is not None else "STALE  no data", BAD
    else:
        tag, col = "data " + _age(state["age"]), TH_DIM
    d.text((C.OUT_W - 12 - d.textlength(tag, font=f_s), 31), tag, font=f_s, fill=col)
    # centre: how many need you
    big = "?" if stale else str(n_lit)
    bw = d.textlength(big, font=f_big)
    bc = (200, 200, 210) if stale else (LEVEL_SCREEN.get(worst, LIT) if n_lit else TH_CYAN)
    d.text(((C.OUT_W - bw) / 2, 58), big, font=f_big, fill=bc)
    if stale:
        sub = "data is old"
        d.text(((C.OUT_W - d.textlength(sub, font=f_s)) / 2, 110), sub, font=f_s, fill=TH_VIOLET)

    def put(side, xy, text, font, fill, anchor="la"):
        d.text(xy, text, font=font, fill=fill, anchor=anchor)
        if boxes is not None and text.strip():
            boxes.append((side,) + tuple(d.textbbox(xy, text, font=font, anchor=anchor)))

    for s, ind in by_slot.items():
        side, row = s.split("_")
        y = SLOT_Y[row]
        lit = bool(ind.get("lit")) and not stale
        col = LEVEL_SCREEN.get(ind.get("level"), LIT)
        _trace(d, side, row, lit, stale, col, LEVEL_GLOW.get(ind.get("level"), TH_GLOW))
        pad = (16, y + 7) if side == "left" else (C.OUT_W - 17, y + 7)
        if lit:
            d.ellipse([pad[0] - 4, pad[1] - 4, pad[0] + 4, pad[1] + 4], fill=col)
        else:
            _via(d, pad, 3, TH_VIOLET if not stale else (150, 150, 160))
        label = _fit(d, ind.get("label") or ind["id"].replace("_", " "), f_l, COLW)
        # descriptor: [tag] reason, one line, never wider than the column
        tag = ind.get("tag") if lit and ind.get("tag") in TAG_RGB else ""
        tagw = d.textlength(tag + " ", font=f_tag) if tag else 0
        reason = "" if stale or not ind.get("reason") else _fit(d, str(ind["reason"]), f_d, COLW - tagw)
        total = tagw + d.textlength(reason, font=f_d)
        # left column reads from its LED inward; the right column is right-aligned against its LED
        if side == "left":
            lx = dx = 22
        else:
            lx, dx = C.OUT_W - 22 - d.textlength(label, font=f_l), C.OUT_W - 22 - total
        put(side, (lx, y), label, f_l, INK if lit else TH_VIOLET)
        if tag:
            put(side, (dx, y + 31), tag, f_tag, TAG_RGB[tag], "ls")
        if reason:
            put(side, (dx + tagw, y + 31), reason, f_d, col if lit else TH_SOFT, "ls")
    return np.asarray(img).copy()
