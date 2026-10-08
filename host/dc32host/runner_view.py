"""Native 320x240 runner status, seven local days, month share and billed budget.

The configured dashboard module owns collection and accounting. GitHub refreshes every
5 minutes by default and not at all during quiet hours (23:00-03:00 local; Ctrl+Alt+R forces one);
the local systemd/journald probe runs every 10 seconds.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
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
STATE_FILE = Path.home() / ".cache" / "dc32-display" / "runner-state.json"   # last good snapshot (see _restore)

W, H = C.OUT_W, C.OUT_H
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

    def __init__(self, script: str, refresh_s: int = 300, local_s: int = 10, quiet_hours=(23, 3)):
        self.script, self.refresh_s, self.local_s = script, refresh_s, local_s
        self.quiet_hours = tuple(quiet_hours) if quiet_hours else None   # (start, end) local hours
        self.fetching = False
        self._forced = False
        self._synced = False           # backfill complete: scheduled refreshes may wait for quiet hours
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
        self._forced = True
        self._kick.set()

    def quiet_now(self, t=None):
        if not self.quiet_hours:
            return False
        start, end = self.quiet_hours
        h = dt.datetime.fromtimestamp(t if t is not None else time.time()).hour
        return (start <= h < end) if start <= end else (h >= start or h < end)

    def quiet_end(self, t=None):
        """Epoch seconds of the next end of quiet hours."""
        now = dt.datetime.fromtimestamp(t if t is not None else time.time())
        end = now.replace(hour=self.quiet_hours[1], minute=0, second=0, microsecond=0)
        return (end if end > now else end + dt.timedelta(days=1)).timestamp()

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
        local_now = now.astimezone()
        self.st = m.State(local_now.year, local_now.month, m.SELF_HOSTED_SINCE, cache)

    def _restore(self):
        """Last good snapshot from STATE_FILE: the view shows it at once, and a restart within the
        refresh interval waits for the schedule instead of refetching everything."""
        m, st = self.mod, self.st
        try:
            s = json.loads(STATE_FILE.read_text())
        except (OSError, ValueError):
            return
        if s.get("version") != 1 or (s.get("year"), s.get("month")) != (st.year, st.month):
            return
        st.billing_items, st.budget, st.runners = s.get("billing_items"), s.get("budget"), s.get("runners")
        st.billing_at = _iso(s.get("billing_at"))
        st.listed_at.update({tuple(k.split("|", 1)): _iso(t) for k, t in (s.get("listed_at") or {}).items()})
        updated = _iso(s.get("updated"))
        v = _compute(m, st)
        done, total = v.get("backfill", (0, 0))
        with self.lock:
            self.v, self.updated = v, updated
        if updated and done >= total:
            self._synced = True
            self.next_at = max(self.next_at, updated.timestamp() + self.refresh_s)
        log.info("runner data restored (last update %s, next fetch in %d min)", updated,
                 max(0, self.next_at - time.time()) // 60)

    def _save(self):
        st = self.st
        with st.lock, self.lock:
            s = {"version": 1, "year": st.year, "month": st.month, "updated": self.updated,
                 "billing_items": st.billing_items, "billing_at": st.billing_at, "budget": st.budget,
                 "runners": st.runners, "listed_at": {"|".join(k): t for k, t in st.listed_at.items()}}
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(s, default=lambda o: o.isoformat() if hasattr(o, "isoformat") else str(o)))
            tmp.replace(STATE_FILE)
        except OSError as e:
            log.debug("runner state not saved: %s", e)

    def _gh_loop(self):
        if self.st is None:
            try:
                self._setup()
                self._restore()
            except Exception as e:          # the fetch below retries setup and reports the error
                log.debug("runner restore skipped: %s", e)
        while True:
            if time.time() >= self.next_at and not self._forced and self._synced and self.quiet_now():
                self.next_at = self.quiet_end()          # nobody's looking: skip the night's refreshes
            if time.time() >= self.next_at:
                self._forced = False
                self.fetching = True
                try:
                    if self.st is None or self.api is None:
                        self._setup()
                    m, st = self.mod, self.st
                    now = dt.datetime.now(m.UTC)
                    local_now = now.astimezone()
                    if (local_now.year, local_now.month) != (st.year, st.month):
                        self._setup()
                        st = self.st
                    m.fetch_billing(st, self.api)
                    m.fetch_runners(st, self.api)
                    m.fetch_runs(st, self.api, now, max_job_calls=80)
                    v = _compute(m, st)
                    done, total = v.get("backfill", (0, 0))
                    self._publish(v, st, dt.datetime.now(m.UTC))
                    # backfill quickly at first, then the configured cadence; a pass that hit an
                    # error (timeout, no access) retries in 15 min rather than leaving STALE for 2 h
                    err = self.error
                    self.next_at = time.time() + (20 if done < total else min(900, self.refresh_s) if err
                                                  else self.refresh_s)
                    self._synced = done >= total
                    self._save()
                    log.info("runner data: hosted MTD $%.2f, backfill %s/%s, api calls %s%s",
                             v.get("actions_net", 0), done, total, self.api.calls,
                             f", error: {err}" if err else "")
                except Exception as e:
                    log.warning("runner data fetch failed: %s", e)
                    with self.lock:
                        self.error = str(e)[:80]
                    self.next_at = time.time() + 300
                finally:
                    self.fetching = False
            self._kick.wait(5)
            self._kick.clear()

    def _publish(self, v, st, now):
        """Publish a snapshot without letting a failed/partial fetch reset its age."""
        done, total = v.get("backfill", (0, 0))
        with self.lock:
            runner = v.get("runner_api") or {}
            previous = (self.v or {}).get("runner_api") or {}
            if runner.get("status") == "offline":
                same = previous.get("status") == "offline" and previous.get("name") == runner.get("name")
                runner["offline_since"] = (previous.get("offline_since") if same else None) or now
            self.v = v
            self.error = st.billing_err or st.jobs_err or st.runners_err or (
                st.budget_err if st.budget_err != "no org Actions budget found" else None)
            if not self.error and done == total:
                self.updated = now

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


def saved_30d(m, st, now, rates):
    """List-price cost of the self-hosted jobs that started in the last 30 days, i.e. what they
    would have cost on GitHub-hosted runners. Rates come from the billing data (Linux $0.006/min)."""
    since = now - dt.timedelta(days=30)
    with st.lock:
        jobs = dict(st.jobs)
    seen, total = set(), 0.0
    for key, entry in jobs.items():
        repo = key.split(":", 1)[0]
        for j in entry.get("jobs", []):
            if (repo, j.get("id")) in seen:
                continue
            seen.add((repo, j.get("id")))
            prov, typ = m.job_where(j)
            if (prov != "self" or not m.job_executed(j) or j.get("status") != "completed"
                    or j.get("conclusion") == "skipped"):
                continue
            ts = m.parse_ts(j.get("started_at"))
            if ts and ts >= since:
                total += m.billed_minutes(m.job_seconds(j, now) or 0) * rates.get(typ, m.FALLBACK_RATE.get(typ, 0.0))
    return total


def _compute(m, st):
    now = dt.datetime.now(m.UTC)
    v = m.compute(st, now)
    try:
        v["saved30"] = saved_30d(m, st, now, v.get("rates") or {})
    except Exception as e:              # an older dashboard module without the helpers
        log.debug("30-day savings unavailable: %s", e)
    return v


def _iso(s):
    return dt.datetime.fromisoformat(s) if s else None


def _money(x):
    return f"${x:,.2f}" if x < 1000 else f"${x:,.0f}"


def _ago(seconds):
    m = max(0, int(seconds // 60))
    return f"{m}m" if m < 60 else f"{m // 60}h{m % 60:02d}"


def _utc(value):
    # Old RunnerData snapshots used naive local datetimes.
    return value.astimezone(dt.timezone.utc) if value else None


class RunnerView:
    def __init__(self, data: RunnerData):
        self.data = data
        self.f_big = _ttf(17, bold=True)
        self.f = _ttf(14)
        self.f_b = _ttf(14, bold=True)
        self.f_s = _ttf(12)
        self.f_sb = _ttf(12, bold=True)
        self.f_mid = _ttf(18, bold=True)
        self._offline_since = None

    def status(self, v, loc, err, updated, now):
        """One status, highest priority first. Offline age is observed age, not outage onset."""
        units = loc.get("units") or []
        api = (v or {}).get("runner_api") or {}
        if (units and units[0].get("active") != "active") or (loc and not units):
            return BAD, "SERVICE DOWN"
        if api.get("status") == "offline":
            self._offline_since = _utc(api.get("offline_since")) or self._offline_since or now
            return BAD, "OFFLINE " + _ago((now - self._offline_since).total_seconds())
        self._offline_since = None
        age = max(0, (now - _utc(updated)).total_seconds()) if updated else None
        backfill = (v or {}).get("backfill", (0, 0))
        month = now.astimezone().strftime("%B %Y")
        wrong_month = (v or {}).get("month_label", month) != month
        if v and not err and not wrong_month and backfill[0] < backfill[1]:
            # first fetch / widened history: say how far along, not just "stale" (numbers still partial)
            return WARN, f"SYNC {100 * backfill[0] // backfill[1]}%"
        if err and err.startswith("no access"):
            return BAD, "NO ACCESS"        # token can't read a configured repo/billing: fix the token, not wait
        expected = getattr(self.data, "fetching", False) or (
            hasattr(self.data, "quiet_now") and self.data.quiet_now(now.timestamp()))
        too_old = age is not None and age > max(2 * self.data.refresh_s, 1800) and not expected
        if (err or wrong_month or backfill[0] < backfill[1] or age is None or too_old or
                not v or "days7" not in v or "month_min" not in v or not units):
            return BAD, "STALE " + (_ago(age) if age is not None else "?")
        q = v.get("queue") or {}
        waiting = q.get("self_over_10m", 0)
        if waiting:
            return WARN, f"{waiting} WAITING"
        cur = loc.get("current")
        if cur or api.get("busy"):
            since = _utc((cur or {}).get("since"))
            if since is None:
                since = next((_utc(r.get("ts")) for r in v.get("recent", [])
                              if r.get("status") == "in_progress" and r.get("prov") == "self"
                              and r.get("runner") == api.get("name")), None)
            return WARN, "BUSY " + (_ago((now - since).total_seconds()) if since else "?")
        return OK, "IDLE"

    def render(self, now=None) -> np.ndarray:
        v, loc, err, updated = self.data.snapshot()
        now = _utc(now) or dt.datetime.now(dt.timezone.utc)
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        units = loc.get("units") or []
        api = (v or {}).get("runner_api") or {}
        name = api.get("name") or (units[0]["unit"].split(".")[-2]
               if units and units[0].get("unit", "").count(".") >= 3 else "runner")
        color, word = self.status(v, loc, err, updated, now)

        # Header: status, plus how old the GitHub numbers are (a long runner name gets truncated).
        age = _ago((now - _utc(updated)).total_seconds()) + " ago" if updated else "—"
        room = W - 8 - 28 - d.textlength(word, font=self.f_b) - 16 - d.textlength(name, font=self.f_big)
        right = "Last update " + age
        if d.textlength(right, font=self.f_s) > room:
            right = age                  # keep the runner name whole; drop the prefix
        mx = W - 8 - d.textlength(right, font=self.f_s)
        name = _fit(d, name, self.f_big, mx - 28 - d.textlength(word, font=self.f_b) - 16)
        d.ellipse([8, 9, 20, 21], fill=color)
        d.text((28, 4), name, font=self.f_big, fill=INK)
        nx = 28 + d.textlength(name, font=self.f_big) + 8
        d.text((nx, 6), word, font=self.f_b, fill=color)
        d.text((mx, 8), right, font=self.f_s, fill=age_color(updated, now))
        d.line([0, 30, W, 30], fill=GRID)

        # Week: two adjacent bars per day on one shared minute scale.
        d.text((8, 38), "week", font=self.f_s, fill=MUTED)
        by_date = {r["date"]: r for r in (v or {}).get("days7", [])}
        today = now.astimezone().date()
        days = []
        for offset in range(6, -1, -1):
            day = today - dt.timedelta(days=offset)
            days.append({"weekday_letter": "MTWTFSS"[day.weekday()],
                         **by_date.get(day.isoformat(), {"self_min": 0, "gh_min": 0})})
        peak = max([r.get(k, 0) for r in days for k in ("self_min", "gh_min")] + [1])
        x0, x1, base, height = 52, W - 8, 104, 62
        stride = (x1 - x0) / 7
        d.line([x0, base, x1, base], fill=GRID)
        for i, row in enumerate(days):
            center = x0 + (i + .5) * stride
            for key, col, offset in (("self_min", SELF, -12), ("gh_min", HOSTED, 1)):
                val = row.get(key, 0)
                if val > 0:
                    h = max(2, round(height * val / peak))
                    x = round(center + offset)
                    d.rounded_rectangle([x, base-h, x+11, base-1], radius=2, fill=col)
            letter = row["weekday_letter"]
            font = self.f_sb if i == len(days)-1 else self.f_s
            d.text((center-d.textlength(letter, font=font)/2, 108), letter,
                   font=font, fill=INK if i == len(days)-1 else MUTED)
        d.line([0, 128, W, 128], fill=GRID)

        # Month: minute share, including an honest empty track for zero usage.
        d.text((8, 140), "month", font=self.f_s, fill=MUTED)
        same_month = (v or {}).get("month_label", now.astimezone().strftime("%B %Y")) == now.astimezone().strftime("%B %Y")
        mins = ((v or {}).get("month_min") or {}) if same_month else {}
        own, gh = max(0, mins.get("self_min", 0)), max(0, mins.get("gh_min", 0))
        total = own + gh
        pct = round(100 * own / total) if total else 0
        gpct = 100 - pct if total else 0
        d.rounded_rectangle([x0, 140, x1, 154], radius=3, fill=GRID)
        split = x0 + round((x1-x0) * own/total) if total else x0
        if split > x0:
            d.rounded_rectangle([x0, 140, split-1, 154], radius=3, fill=SELF)
        if total and split < x1:
            d.rounded_rectangle([split, 140, x1, 154], radius=3, fill=HOSTED)
        d.text((x0, 159), f"self {pct}%", font=self.f_s, fill=INK2)
        right = f"GitHub {gpct}%"
        d.text((x1-d.textlength(right, font=self.f_s), 159), right, font=self.f_s, fill=INK2)
        d.line([0, 180, W, 180], fill=GRID)

        # Footer, two compact lines:
        #   $12.34 saved, rolling 30d              what self-hosted minutes would have cost on GitHub
        #   $0.00 of $250, resets 24d  next 28m    billed Actions spend this cycle (GitHub blue)
        saved = (v or {}).get("saved30")
        x = 8
        if saved is not None:
            amt = _money(saved)
            d.text((x, 186), amt, font=self.f_mid, fill=SELF)
            d.text((x + d.textlength(amt, font=self.f_mid) + 6, 191), "saved, rolling 30d", font=self.f_s, fill=INK2)
        amount = _money(v.get("actions_net", 0)) if v and same_month and v.get("billing_ok", True) else "$—"
        budget = (v or {}).get("budget")
        label = (f"of ${budget['amount']:,.0f}, resets {cycle_days_left(now)}d" if budget
                 else f"spent, resets {cycle_days_left(now)}d")
        y = 212 if saved is not None else 196
        d.text((x, y), amount, font=self.f_mid, fill=HOSTED)
        lx = x + d.textlength(amount, font=self.f_mid) + 6
        d.text((lx, y + 4), label, font=self.f_s, fill=INK2)
        sched = self.schedule_text(v, now)
        if sched:
            sx = W - 8 - d.textlength(sched, font=self.f_s)
            if sx > lx + d.textlength(label, font=self.f_s) + 8:
                d.text((sx, y + 4), sched, font=self.f_s, fill=MUTED)
        return np.asarray(img).copy()

    def schedule_text(self, v, now):
        """When the GitHub numbers next change: 'updating...' or 'next 28m' (nothing overnight)."""
        data = self.data
        if not v or not hasattr(data, "next_at"):
            return None
        done, total = v.get("backfill", (0, 0))
        if data.fetching:
            return "updating..."
        if done < total:
            return None                  # header already says SYNC n%
        t = now.timestamp()
        if data.quiet_hours and data.next_at > t and (data.quiet_now(t) or data.quiet_now(data.next_at)):
            return None                  # overnight pause: "Last update" in the header says enough
        return "next " + _ago(max(0.0, data.next_at - t))


def age_color(updated, now):
    """One glance: green = fresh (< 15 min), amber = aging (< 2 h), red = old, grey = never."""
    if not updated:
        return MUTED
    age = (now - _utc(updated)).total_seconds()
    return OK if age < 900 else WARN if age < 7200 else BAD


def cycle_days_left(now):
    """Days until GitHub's metered billing cycle (calendar month, UTC) and its budget reset."""
    u = _utc(now) or dt.datetime.now(dt.timezone.utc)
    nxt = dt.datetime(u.year + (u.month == 12), u.month % 12 + 1, 1, tzinfo=dt.timezone.utc)
    return max(1, -(-int((nxt - u).total_seconds()) // 86400))


def _fit(d, text, font, width):
    if d.textlength(text, font=font) <= width:
        return text
    while text and d.textlength(text + "...", font=font) > width:
        text = text[:-1]
    return text + "..."
