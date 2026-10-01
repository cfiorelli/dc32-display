"""Host daemon: capture -> encode -> USB, badge button events -> window management actions."""
from __future__ import annotations

import logging
import queue
import re
import sys
import time

import numpy as np

from . import __version__
from . import capture as C
from . import config as CFG
from . import encoder as E
from . import protocol as P
from .device import Badge, Disconnected

log = logging.getLogger("dc32.daemon")

MODES = ["follow", "pinned", "desktop"]
ZOOM_CYCLE = ["fit", "2x", "1x"]
FAV_ID_BASE = 0x80000000


def make_backend(cfg):
    if sys.platform == "win32":
        from .winapi import Backend
    else:
        from .x11api import Backend
    return Backend(cfg)


class Stats:
    def __init__(self):
        self.reset()

    def reset(self):
        self.t0 = time.time()
        self.frames = 0
        self.bytes = 0
        self.cap_ms = []
        self.enc_ms = []
        self.rtt_ms = []
        self.c2a_ms = []
        self.kinds = {}

    def summary(self):
        dt = max(1e-3, time.time() - self.t0)
        avg = lambda a: (sum(a) / len(a)) if a else 0.0  # noqa: E731
        p = lambda a, q: sorted(a)[int(q * (len(a) - 1))] if a else 0.0  # noqa: E731
        return {
            "fps": self.frames / dt, "kBps": self.bytes / dt / 1000, "cap_ms": avg(self.cap_ms),
            "enc_ms": avg(self.enc_ms), "rtt_ms": avg(self.rtt_ms), "c2a_ms": avg(self.c2a_ms),
            "c2a_p95": p(self.c2a_ms, 0.95), "kinds": dict(self.kinds), "window_s": dt,
        }


class Daemon:
    def __init__(self, cfg: dict, backend=None):
        self.cfg = cfg
        self.be = backend or make_backend(cfg)
        self.badge = Badge()
        self.mode = cfg.get("mode", "follow")
        self.vp = C.Viewport()
        self.vp.zoom = cfg.get("zoom", "fit")
        self.pinned = None            # handle
        self.shown = None             # WindowInfo currently on the badge
        self.mru: list[int] = []
        self.prev = None              # last RGB565 frame sent
        self.frame_id = 0
        self.inflight: dict[int, tuple[float, float]] = {}   # id -> (t_capture, t_sent)
        self.menu_open_until = 0.0
        self.menu_table: dict[int, object] = {}
        self.toast: tuple[float, list[str]] | None = None
        self.info_until = 0.0
        self.last_change = 0.0
        self.cursor_last = (None, 0.0)
        self.brightness = int(cfg.get("brightness", 22))
        self.fn_used_as_modifier = False
        self.pending_fav = None       # (fav, deadline)
        self.stats = Stats()
        self.stats_all = Stats()
        self.last_stats_log = time.time()
        self.last_ping = 0.0
        self.on_frame_sent = None     # test hooks: (frame_id, rgb, t_capture)
        self.on_frame_acked = None    # (frame_id, t_ack)
        self._ex_title = [re.compile(p) for p in cfg.get("exclude_titles", [])]
        self._ex_proc = {p.lower() for p in cfg.get("exclude_processes", [])}

    # ================================================================ helpers
    def excluded(self, wi) -> bool:
        if wi is None:
            return True
        if wi.app and wi.app.lower() in self._ex_proc:
            return True
        return any(r.search(wi.title or "") for r in self._ex_title)

    def say(self, *lines, seconds=None):
        self.toast = (time.time() + (seconds or self.cfg.get("toast_seconds", 1.5)), list(lines))
        log.info("toast: %s", " | ".join(lines))

    def send(self, data: bytes):
        self.badge.write(data)

    def force_refresh(self):
        self.prev = None

    # ================================================================ favorites
    def favorite(self, name):
        for f in self.cfg.get("favorites", []):
            if f.get("name") == name:
                return f
        return None

    def match_favorite(self, fav, windows=None):
        m = fav.get("match") or {}
        windows = windows if windows is not None else self.be.list_windows()
        tr = re.compile(m["title"]) if m.get("title") else None
        pr = re.compile(m["process"], re.I) if m.get("process") else None
        for wi in windows:
            if tr and tr.search(wi.title or ""):
                return wi
            if pr and pr.search(wi.app or ""):
                return wi
        if m.get("cmdline"):
            pids = self._ancestor_pids(re.compile(m["cmdline"]))
            for wi in windows:
                if wi.pid in pids:
                    return wi
        return None

    @staticmethod
    def _ancestor_pids(rx):
        try:
            import psutil
        except ImportError:
            return set()
        out = set()
        for p in psutil.process_iter(["pid", "cmdline"]):
            try:
                if rx.search(" ".join(p.info["cmdline"] or [])):
                    out.add(p.pid)
                    for a in p.parents():
                        out.add(a.pid)
            except Exception:
                continue
        return out

    def open_favorite(self, name):
        fav = self.favorite(name)
        if not fav:
            self.say(f"No favorite '{name}'", "Run: dc32host find-dashboard")
            return
        wi = self.match_favorite(fav)
        if wi:
            self.show_and_focus(wi)
            self.say(fav["name"])
            return
        if fav.get("launch"):
            spec = dict(fav["launch"])
            spec.setdefault("title", fav["name"])
            if self.be.launch(spec):
                self.pending_fav = (fav, time.time() + 15)
                self.say(f"Starting {fav['name']}...")
                return
        self.say(f"{fav['name']}: not running", "no launch command configured")

    def show_and_focus(self, wi):
        ok = self.be.focus(wi.handle)
        if ok and not self.excluded(wi):          # update MRU now so rapid toggles alternate correctly
            if wi.handle in self.mru:
                self.mru.remove(wi.handle)
            self.mru.insert(0, wi.handle)
        if self.mode == "pinned":
            self.pinned = wi.handle
        elif self.mode == "desktop":
            self.mode = "follow"
        self.vp.reset()
        self.force_refresh()
        if not ok:
            log.warning("focus refused for %s", wi.label)
            if self.mode == "follow":       # show it anyway
                self.mode, self.pinned = "pinned", wi.handle
                self.say(wi.label, "(focus refused: pinned instead)")

    # ================================================================ actions
    def action(self, name: str):
        log.debug("action %s", name)
        if name in ("none", "", None):
            return
        if name.startswith("key:"):
            self.be.send_key(name[4:])
        elif name.startswith("favorite:"):
            self.open_favorite(name[9:])
        elif name == "toggle_last_app":
            for h in self.mru[1:]:
                if self.be.alive(h):
                    wi = next((w for w in self.be.list_windows() if w.handle == h), None)
                    if wi:
                        self.show_and_focus(wi)
                        return
            self.say("No previous app")
        elif name == "app_switcher":
            self.open_switcher()
        elif name == "dashboard":
            self.open_favorite(self.cfg.get("dashboard_favorite", "GitHub Runner Dashboard"))
        elif name == "cycle_mode":
            i = (MODES.index(self.mode) + 1) % len(MODES)
            self.set_mode(MODES[i])
        elif name == "pin_current":
            self.set_mode("pinned")
        elif name == "focus_shown":
            if self.shown:
                self.be.focus(self.shown.handle)
        elif name == "zoom_cycle":
            self.vp.zoom = ZOOM_CYCLE[(ZOOM_CYCLE.index(self.vp.zoom) + 1) % len(ZOOM_CYCLE)]
            self.vp.reset()
            self.say(f"Zoom: {self.vp.zoom}")
        elif name == "zoom_fit":
            if self.vp.zoom != "fit":
                self.vp.zoom = "fit"
                self.vp.reset()
                self.say("Zoom: fit")
        elif name.startswith("pan_"):
            d = {"pan_up": (0, -1), "pan_down": (0, 1), "pan_left": (-1, 0), "pan_right": (1, 0)}[name]
            self.vp.pan(*d)
        elif name == "info":
            self.info_until = time.time() + 4
        elif name == "refresh":
            self.force_refresh()
            self.say("Refreshed")
        elif name in ("brightness_up", "brightness_down"):
            self.brightness = max(0, min(31, self.brightness + (3 if name.endswith("up") else -3)))
            self.send(P.set_brightness(self.brightness))
            self.say(f"Brightness {self.brightness}/31")
        else:
            log.warning("unknown action %s", name)

    def set_mode(self, mode):
        self.mode = mode
        if mode == "pinned":
            fg = self.shown or self.be.foreground()
            self.pinned = fg.handle if fg else None
            self.say("Mode: Pinned", fg.label if fg else "")
        else:
            self.say("Mode: " + {"follow": "Follow active window", "desktop": "Whole desktop"}[mode])
        self.vp.reset()
        self.force_refresh()

    # ================================================================ app switcher
    def open_switcher(self):
        wins = [w for w in self.be.list_windows() if not self.excluded(w)]
        order = {h: i for i, h in enumerate(self.mru)}
        wins.sort(key=lambda w: order.get(w.handle, 1000))
        fg = self.be.foreground()
        favs = self.cfg.get("favorites", [])
        fav_handles = {}
        for fi, fav in enumerate(favs):
            wi = self.match_favorite(fav, wins)
            if wi:
                fav_handles[wi.handle] = fav["name"]
        entries, self.menu_table = [], {}
        for i, w in enumerate(wins[:44]):
            flags = 0
            if fg and w.handle == fg.handle:
                flags |= P.MENU_FLAG_ACTIVE
            if w.handle in fav_handles:
                flags |= P.MENU_FLAG_FAVORITE
            if self.mode == "pinned" and w.handle == self.pinned:
                flags |= P.MENU_FLAG_PINNED
            name = fav_handles.get(w.handle) or w.label
            entries.append((i, flags, name))
            self.menu_table[i] = w
        for fi, fav in enumerate(favs):       # favorites that aren't running yet
            if fav["name"] not in fav_handles.values() and fav.get("launch"):
                eid = FAV_ID_BASE | fi
                entries.append((eid, P.MENU_FLAG_FAVORITE, f"{fav['name']} (start)"))
                self.menu_table[eid] = fav
        sel = 1 if len(entries) > 1 and entries[0][2] and (entries[0][1] & P.MENU_FLAG_ACTIVE) else 0
        self.send(P.menu_list("Switch app", entries, sel))
        self.menu_open_until = time.time() + 60

    def on_menu_result(self, r: P.MenuResult):
        self.menu_open_until = 0.0
        self.force_refresh()
        if r.action == P.MENU_CANCEL:
            return
        target = self.menu_table.get(r.entry_id)
        if target is None:
            return
        if isinstance(target, dict):           # favorite to launch
            self.open_favorite(target["name"])
            return
        if r.action == P.MENU_PIN:
            self.mode, self.pinned = "pinned", target.handle
            self.vp.reset()
            self.say("Pinned", target.label)
        else:
            self.show_and_focus(target)

    # ================================================================ events
    def on_button(self, ev: P.ButtonEvent):
        if ev.button == "fn":
            if ev.event == "down":
                self.fn_used_as_modifier = False
            if ev.event == "short" and self.fn_used_as_modifier:
                return
        elif "fn" in ev.held:
            self.fn_used_as_modifier = True
        prefix = "fn+" if ev.fn_held else ""
        key = f"{prefix}{ev.button}.{ev.event}"
        act = self.cfg["buttons"].get(key)
        log.debug("button %s -> %s", key, act)
        if act:
            self.action(act)

    def drain_events(self, timeout, wake_on_input=False):
        """Handle badge events for up to `timeout` s. With wake_on_input, return early ~12 ms after
        local keyboard/mouse input so typed characters are captured immediately."""
        deadline = time.time() + max(0.0, timeout)
        mark = self.be.input_mark() if (wake_on_input and hasattr(self.be, "input_mark")) else None
        while True:
            rem = deadline - time.time()
            try:
                ev = self.badge.events.get(timeout=min(rem, 0.008)) if rem > 0 else self.badge.events.get_nowait()
                self.handle_event(ev)
                continue
            except queue.Empty:
                if rem <= 0:
                    return
            if mark is not None and self.be.input_since(mark):
                time.sleep(0.012)        # give the app a moment to render the keystroke
                return

    def handle_event(self, ev):
        if isinstance(ev, P.Ack):
            t = self.inflight.pop(ev.frame_id, None)
            if self.on_frame_acked:
                self.on_frame_acked(ev.frame_id, time.time())
            if t:
                now = time.time()
                self.stats.rtt_ms.append((now - t[1]) * 1000)
                self.stats.c2a_ms.append((now - t[0]) * 1000)
            # drop anything older (lost ACK)
            for k in [k for k in self.inflight if k < ev.frame_id]:
                self.inflight.pop(k, None)
        elif isinstance(ev, P.ButtonEvent):
            self.on_button(ev)
        elif isinstance(ev, P.MenuResult):
            self.on_menu_result(ev)
        elif isinstance(ev, P.DeviceError):
            log.warning("badge decode error %s/%s -> resync + full refresh", ev.code, ev.detail)
            self.send(P.SYNC_BYTES)
            self.force_refresh()
        elif isinstance(ev, tuple) and ev and ev[0] == "disconnected":
            raise Disconnected(ev[1])

    # ================================================================ frame pipeline
    def track_mru(self):
        fg = self.be.foreground()
        if fg is None or self.excluded(fg):
            return
        if not self.mru or self.mru[0] != fg.handle:
            if fg.handle in self.mru:
                self.mru.remove(fg.handle)
            self.mru.insert(0, fg.handle)
            del self.mru[20:]

    def pick_source(self):
        if self.mode == "desktop":
            return None
        if self.mode == "pinned":
            if self.pinned and self.be.alive(self.pinned):
                wi = next((w for w in self.be.list_windows() if w.handle == self.pinned), None)
                if wi:
                    return wi
            self.mode = "follow"
            self.say("Pinned window closed", "Mode: Follow")
        fg = self.be.foreground()
        if fg is not None and not self.excluded(fg):
            return fg
        return self.shown           # e.g. desktop / start menu focused: keep the last app

    def build_frame(self):
        src_wi = self.pick_source()
        changed_src = (src_wi.handle if src_wi else None) != (self.shown.handle if self.shown else None)
        if changed_src:
            self.vp.reset()
        self.shown = src_wi
        t_cap = time.time()
        src, rect = None, None
        if src_wi is None:
            rect = self.be.desktop_rect()
            src, rect = C.grab_rect(rect)
            key = "desktop"
        else:
            key = src_wi.handle
            if src_wi.minimized:
                return t_cap, C.placeholder([src_wi.label[:40], "(minimized)"]), changed_src
            fg = self.be.foreground()
            occluded = fg is None or fg.handle != src_wi.handle
            if occluded and hasattr(self.be, "capture_window"):
                src = self.be.capture_window(src_wi.handle)
                rect = self.be.rect(src_wi.handle) if src is not None else None
            if src is None:
                r = self.be.rect(src_wi.handle)
                if r:
                    src, rect = C.grab_rect(r)
        if src is None:
            return t_cap, C.placeholder(["No window to show", time.strftime("%H:%M:%S")]), changed_src
        self.stats.cap_ms.append((time.time() - t_cap) * 1000)

        # focus point: caret (Win32), else recently-moved mouse inside the source
        focus_pt = None
        cur = self.be.cursor()
        now = time.time()
        if cur:
            if self.cursor_last[0] != (cur[0], cur[1]):
                self.cursor_last = ((cur[0], cur[1]), now)
        if src_wi is not None and self.vp.zoom != "fit":
            car = self.be.caret(src_wi.handle) if hasattr(self.be, "caret") else None
            if car:
                focus_pt = (car[0] - rect[0], car[1] - rect[1] + car[3])
            elif cur and now - self.cursor_last[1] < 1.0:
                focus_pt = (cur[0] - rect[0], cur[1] - rect[1])
        out, tf = self.vp.compose(src, key, focus_pt, tuple(self.cfg.get("letterbox_color", [0, 0, 0])))

        hide_after = float(self.cfg.get("cursor_only_when_moving_s", 3.0))
        if self.cfg.get("show_cursor", True) and cur and cur[2] and (hide_after <= 0 or now - self.cursor_last[1] < hide_after):
            cx, cy = C.map_point(tf, cur[0] - rect[0], cur[1] - rect[1])
            if 0 <= cx < C.OUT_W and 0 <= cy < C.OUT_H:
                C.draw_cursor(out, cx, cy)
        return t_cap, out, changed_src

    def overlays(self, out):
        now = time.time()
        if now < self.info_until:
            s = self.stats.summary()
            info = self.badge.info
            out = C.draw_lines(out, [
                f"{self.mode.upper()}  zoom {self.vp.zoom}  bri {self.brightness}",
                (self.shown.label if self.shown else "desktop")[:44],
                f"{s['fps']:.1f} fps  {s['kBps']:.0f} kB/s  cap {s['cap_ms']:.0f} ms",
                f"capture->badge {s['c2a_ms']:.0f} ms (p95 {s['c2a_p95']:.0f})  rtt {s['rtt_ms']:.0f}",
                f"fw {info.fw if info else '?'}  host {__version__}",
            ], where="top")
        if self.toast and now < self.toast[0]:
            out = C.draw_lines(out, self.toast[1])
        elif self.toast:
            self.toast = None
        return out

    def tick(self):
        now = time.time()
        if self.menu_open_until and now < self.menu_open_until:
            if now - self.last_ping > 1.0:
                self.send(P.ping(int(now)))
                self.last_ping = now
            self.drain_events(0.05)
            return
        self.menu_open_until = 0.0
        self.track_mru()
        if self.pending_fav:
            fav, deadline = self.pending_fav
            wi = self.match_favorite(fav)
            if wi or now > deadline:
                self.pending_fav = None
                if wi:
                    self.show_and_focus(wi)
                else:
                    self.say(f"{fav['name']} did not appear")

        # flow control: bounded frames in flight keeps latency low
        maxf = int(self.cfg.get("max_frames_in_flight", 2))
        if len(self.inflight) >= maxf:
            oldest = min(t[1] for t in self.inflight.values())
            if now - oldest > 1.5:
                log.warning("ACK timeout; resync")
                self.inflight.clear()
                self.send(P.SYNC_BYTES)
                self.force_refresh()
            else:
                self.drain_events(0.01)
                return

        t_cap, rgb, changed_src = self.build_frame()
        rgb = self.overlays(rgb)
        t_enc = time.time()
        cur = E.rgb_to_565(rgb)
        prev = None if changed_src else self.prev
        st = self.stats.kinds
        msgs = E.encode_frame(cur, prev, st)
        if msgs:
            self.frame_id = (self.frame_id + 1) & 0xFFFFFFFF
            blob = b"".join(msgs) + P.frame_end(self.frame_id)
            self.stats.enc_ms.append((time.time() - t_enc) * 1000)
            self.send(blob)
            self.inflight[self.frame_id] = (t_cap, time.time())
            if self.on_frame_sent:
                self.on_frame_sent(self.frame_id, rgb, t_cap)
            self.prev = cur
            self.stats.frames += 1
            self.stats.bytes += len(blob)
            self.last_change = time.time()
        elif now - self.last_ping > 1.0:
            self.send(P.ping(int(now)))    # keep-alive so the badge doesn't show "disconnected"
            self.last_ping = now

        active = time.time() - self.last_change < 1.0
        fps = self.cfg["fps_active"] if active else self.cfg["fps_idle"]
        remaining = (1.0 / fps) - (time.time() - now)
        self.drain_events(remaining, wake_on_input=True)

        if time.time() - self.last_stats_log > 10:
            s = self.stats.summary()
            log.info("stats: %.1f fps, %.0f kB/s, capture %.1f ms, encode %.1f ms, rtt %.1f ms, capture->badge %.1f ms (p95 %.1f), rects %s",
                     s["fps"], s["kBps"], s["cap_ms"], s["enc_ms"], s["rtt_ms"], s["c2a_ms"], s["c2a_p95"], s["kinds"])
            self.stats.reset()
            self.last_stats_log = time.time()

    # ================================================================ main loop
    def on_connect(self):
        self.inflight.clear()
        self.force_refresh()
        self.menu_open_until = 0.0
        self.send(P.set_brightness(self.brightness) + P.set_timing(int(self.cfg.get("long_press_ms", 600)), 400, 90))
        self.say(f"DC32 Display host {__version__}", f"fw {self.badge.info.fw}")

    def run(self, once_seconds: float | None = None):
        log.info("dc32host %s starting, backend=%s, config=%s", __version__, self.be.name, CFG.config_path())
        t_end = time.time() + once_seconds if once_seconds else None
        waiting_logged = False
        while t_end is None or time.time() < t_end:
            if not self.badge.connected:
                if self.badge.open():
                    waiting_logged = False
                    self.on_connect()
                else:
                    if not waiting_logged:
                        log.info("waiting for badge (USB %04x:%04x)...", P.USB_VID, P.USB_PID)
                        waiting_logged = True
                    time.sleep(1.0)
                    continue
            try:
                self.tick()
            except Disconnected as e:
                log.warning("badge disconnected: %s", e)
                self.badge.close()
            except Exception:
                log.exception("tick failed")
                time.sleep(0.5)
