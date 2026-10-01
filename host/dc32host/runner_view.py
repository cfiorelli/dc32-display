"""Badge-native GitHub runner view: drawn at the badge's own 320x240, so nothing is scaled.

Data comes from the user's terminal dashboard script (the runner-cost dashboard favorite), imported as a
module so the token handling, org/repo list and cost rules stay in one place. GitHub is polled every
`refresh_s` (default 30 min); the runner's local state (systemd/journald) every 10 s.

    +--------------------------------------------+
    | * my-runner    BUSY 4m            14:05    |  runner state (status colour + word)
    |   build / test-linux                       |  current job, or the last one
    | GitHub-hosted          $0.42 / 12h         |  strip 1: est. hosted cost per 30 min
    | ||  |   ||||    |                          |
    | Self-hosted (saved)    $1.10 / 12h         |  strip 2: what the Dell saved per 30 min
    |  |||| ||   |||||||  |                      |
    | -12h            -6h                   now  |
    | Month $12.34  saved $5.67     upd 13:30    |
    +--------------------------------------------+
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import logging
import os
import shutil
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import capture as C

log = logging.getLogger("dc32.runner")

W, H = C.OUT_W, C.OUT_H
BUCKET_MIN = 30
BUCKETS = 24                         # 12 hours
# dark chart surface + categorical slots 1/2 (validated pair), text inks, reserved status colours
BG = (26, 26, 25)
INK = (255, 255, 255)
INK2 = (195, 194, 183)
MUTED = (130, 129, 122)
GRID = (60, 60, 58)
HOSTED = (57, 135, 229)
SELF = (217, 89, 38)
OK, WARN, BAD = (46, 160, 67), (201, 133, 0), (210, 60, 60)


def _ttf(size, bold=False):
    for p in (f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
              "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return C._font(size)


def load_dashboard_module(path: str):
    spec = importlib.util.spec_from_file_location("dc32_runner_dashboard", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RunnerData:
    """Background fetcher. `snapshot()` never blocks on the network."""

    def __init__(self, script: str, refresh_s: int = 1800, local_s: int = 10):
        self.script, self.refresh_s, self.local_s = script, refresh_s, local_s
        self.lock = threading.Lock()
        self.v = None                  # dashboard compute() result
        self.local = {}
        self.error = None
        self.updated = None            # datetime of last GitHub fetch
        self.next_at = 0.0
        self.mod = None
        self.st = None
        self.api = None
        self._started = False
        self._kick = threading.Event()

    def start(self):
        if self._started:
            return
        self._started = True
        threading.Thread(target=self._gh_loop, daemon=True, name="runner-gh").start()
        threading.Thread(target=self._local_loop, daemon=True, name="runner-local").start()

    def refresh_now(self):
        self.next_at = 0.0
        self._kick.set()

    def _setup(self):
        m = load_dashboard_module(self.script)
        tok = m.load_token(m.TOKEN_FILE)
        if not tok:
            raise RuntimeError("no GitHub token (run the dashboard with --save-token)")
        now = dt.datetime.now(m.UTC)
        # own cache file: the terminal dashboard may be running and writing its own concurrently
        cache = Path.home() / ".cache" / "dc32-display" / "runner-dashboard.json"
        if not cache.exists() and m.CACHE_FILE.exists():
            cache.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(m.CACHE_FILE, cache)
        self.mod, self.api = m, m.Api("https://api.github.com", tok)
        self.st = m.State(now.year, now.month, m.SELF_HOSTED_SINCE, cache)

    def _gh_loop(self):
        while True:
            if time.time() >= self.next_at:
                try:
                    if self.mod is None:
                        self._setup()
                    m, st = self.mod, self.st
                    now = dt.datetime.now(m.UTC)
                    if (now.year, now.month) != (st.year, st.month):
                        self._setup()
                        st = self.st
                    m.fetch_billing(st, self.api)
                    m.fetch_runners(st, self.api)
                    m.fetch_runs(st, self.api, now, max_job_calls=80)
                    v = m.compute(st, dt.datetime.now(m.UTC))
                    done, total = v.get("backfill", (0, 0))
                    with self.lock:
                        self.v, self.updated = v, dt.datetime.now()
                        self.error = st.billing_err or st.jobs_err
                    # backfill quickly at first, then the configured cadence
                    self.next_at = time.time() + (20 if done < total else self.refresh_s)
                    log.info("runner data: hosted MTD $%.2f, backfill %s/%s, api calls %s",
                             v.get("actions_net", 0), done, total, self.api.calls)
                except Exception as e:
                    log.warning("runner data fetch failed: %s", e)
                    with self.lock:
                        self.error = str(e)[:80]
                    self.next_at = time.time() + 300
            self._kick.wait(5)
            self._kick.clear()

    def _local_loop(self):
        while True:
            try:
                if self.mod is None:
                    self.mod = load_dashboard_module(self.script)
                loc = self.mod.fetch_local()
                with self.lock:
                    self.local = loc
            except Exception as e:
                log.debug("runner local probe failed: %s", e)
            time.sleep(self.local_s)

    def snapshot(self):
        with self.lock:
            return self.v, dict(self.local), self.error, self.updated


def buckets(v, now: dt.datetime):
    """-> (hosted[24], self[24]) estimated dollars per 30-minute bucket, oldest first."""
    hosted, saved = [0.0] * BUCKETS, [0.0] * BUCKETS
    if not v:
        return hosted, saved
    rates = v.get("rates", {})
    span = dt.timedelta(minutes=BUCKET_MIN)
    start = now - span * BUCKETS
    for r in v.get("recent", []):
        ts = r.get("ts")
        if not ts or ts < start or r.get("status") != "completed" or r.get("conclusion") == "skipped":
            continue
        i = min(BUCKETS - 1, int((ts - start) / span))
        sec = r.get("sec") or 0
        mins = -(-int(sec) // 60) if sec > 0 else 0        # GitHub bills whole minutes, rounded up
        usd = mins * rates.get(r.get("type"), 0.006)
        if r.get("prov") == "self":
            saved[i] += usd
        else:
            hosted[i] += r.get("cost") if r.get("cost") is not None else usd
    return hosted, saved


def _money(x):
    return f"${x:,.2f}" if x < 1000 else f"${x:,.0f}"


def _ago(seconds):
    m = int(seconds // 60)
    return f"{m}m" if m < 60 else f"{m // 60}h{m % 60:02d}"


class RunnerView:
    def __init__(self, data: RunnerData):
        self.data = data
        self.f_big = _ttf(17, bold=True)
        self.f = _ttf(14)
        self.f_b = _ttf(14, bold=True)
        self.f_s = _ttf(12)

    def render(self) -> np.ndarray:
        v, loc, err, updated = self.data.snapshot()
        now_utc = dt.datetime.now(dt.timezone.utc)
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)

        # --- runner state (local systemd/journald is authoritative and live) ---
        units = loc.get("units") or []
        active = units[0]["active"] if units else None
        cur, last = loc.get("current"), loc.get("last")
        api_r = (v or {}).get("runner_api") or {}
        name = api_r.get("name") or (units[0]["unit"].split(".")[-2] if units and units[0]["unit"].count(".") >= 3 else "runner")
        if active is None:
            col, word = MUTED, "checking"
        elif active != "active":
            col, word = BAD, "SERVICE " + active.upper()
        elif cur:
            col, word = WARN, f"BUSY {_ago((now_utc - cur['since']).total_seconds())}"
        else:
            col, word = OK, "IDLE"
        d.ellipse([8, 8, 20, 20], fill=col)
        d.text((28, 4), name[:16], font=self.f_big, fill=INK)
        nx = 28 + d.textlength(name[:16], font=self.f_big) + 8
        d.text((nx, 6), word, font=self.f_b, fill=col if col != MUTED else INK2)
        clock = time.strftime("%H:%M")
        d.text((W - 6 - d.textlength(clock, font=self.f), 6), clock, font=self.f, fill=INK2)
        if cur:
            line2 = cur["job"]
        elif last:
            mark = "ok" if last["result"] == "Succeeded" else last["result"].lower()
            line2 = f"last: {last['job']} ({mark}, {_ago((now_utc - last['at']).total_seconds())} ago)"
        else:
            line2 = "no jobs seen yet"
        q = (v or {}).get("queue") or {}
        if q.get("self"):
            line2 = f"{q['self']} queued | " + line2
        d.text((8, 28), _fit(d, line2, self.f, W - 16), font=self.f, fill=INK2)

        # --- two strips, one shared $ scale so they compare honestly ---
        hosted, saved = buckets(v, now_utc)
        top = max(max(hosted), max(saved), 0.01)
        self._strip(d, 50, "GitHub-hosted", sum(hosted), hosted, top, HOSTED)
        self._strip(d, 122, "Self-hosted (saved)", sum(saved), saved, top, SELF)
        y = 192
        for i, lab in ((0, "-12h"), (BUCKETS // 2, "-6h"), (BUCKETS, "now")):
            x = 8 + i * (W - 16) / BUCKETS
            tw = d.textlength(lab, font=self.f_s)
            d.text((min(max(8, x - tw / 2), W - 8 - tw), y), lab, font=self.f_s, fill=MUTED)

        # --- footer: month to date from the billing API ---
        if v:
            saved_mtd = sum(r.get("saved", 0) for r in v.get("self_rows", {}).values())
            foot = f"Month billed {_money(v.get('actions_net', 0))}  saved {_money(saved_mtd)}"
        else:
            foot = "waiting for GitHub data..."
        d.line([0, 210, W, 210], fill=GRID)
        d.text((8, 216), foot, font=self.f, fill=INK)
        if err:
            msg = "! " + err
            d.text((W - 8 - min(150, d.textlength(msg, font=self.f_s)), 218), _fit(d, msg, self.f_s, 150),
                   font=self.f_s, fill=BAD)
        elif updated:
            u = "upd " + updated.strftime("%H:%M")
            d.text((W - 8 - d.textlength(u, font=self.f_s), 218), u, font=self.f_s, fill=MUTED)
        return np.asarray(img).copy()

    def _strip(self, d, y, label, total, vals, top, color):
        d.rectangle([8, y + 4, 18, y + 14], fill=color)          # legend key next to the label
        d.text((24, y), label, font=self.f_b, fill=INK)
        t = f"{_money(total)} / 12h"
        d.text((W - 8 - d.textlength(t, font=self.f), y), t, font=self.f, fill=INK)
        base, hmax = y + 66, 44
        d.line([8, base, W - 8, base], fill=GRID)
        bw = (W - 16) / BUCKETS
        for i, val in enumerate(vals):
            if val <= 0:
                continue
            h = max(2, round(hmax * val / top))
            x0 = 8 + i * bw + 1
            x1 = 8 + (i + 1) * bw - 1                              # 2 px gap between bars
            d.rounded_rectangle([x0, base - h, x1, base - 1], radius=min(3, (x1 - x0) / 2), fill=color)


def _fit(d, text, font, width):
    if d.textlength(text, font=font) <= width:
        return text
    while text and d.textlength(text + "...", font=font) > width:
        text = text[:-1]
    return text + "..."
