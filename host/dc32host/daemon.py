"""Host daemon: capture -> encode -> USB, badge button events -> window management actions."""
from __future__ import annotations

import logging
import os
import queue
import re
import sys
import time


from . import __version__
from . import capture as C
from . import config as CFG
from . import encoder as E
from . import protocol as P
from .device import Badge, Disconnected

log = logging.getLogger("dc32.daemon")

MODES = ["follow", "pinned", "desktop"]
def E_black_frame(d):
    """Full black frame (sleep): encoded like any other frame so the badge clears to it."""
    import numpy as np
    cur = E.rgb_to_565(np.zeros((C.OUT_H, C.OUT_W, 3), np.uint8))
    d.frame_id = (d.frame_id + 1) & 0xFFFFFFFF
    d.prev = cur
    return b"".join(E.encode_frame(cur, None, d.stats.kinds)) + P.frame_end(d.frame_id)


TAP_BURST_GAP = 0.2   # events closer than this are one knock ringing (measured: <= 0.1 s apart)
KNOCK_WINDOW = (0.3, 1.0)   # knock-knock: the second knock starts this long after the first
RESYNC_GAP_S = 2.5    # firmware HOST_TIMEOUT_MS is 3000: past this the badge may have blanked
ZOOM_CYCLE = ["fit", "2x", "1x"]
FAV_ID_BASE = 0x80000000
HOME_ID_BASE = 0x40000000
BASHRC = __import__("os").path.join(__import__("os").path.dirname(__file__), "badge_bashrc")
DOOM2_SH = __import__("os").path.expanduser("~/.local/share/dc32-display/doom/doom2.sh")


def control_socket_path():
    if os.name == "nt":
        return None
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/dc32host-{os.getuid()}"
    os.makedirs(base, mode=0o700, exist_ok=True)
    return os.path.join(base, "dc32host.sock")


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
        self.view = "mirror"          # mirror | runner | paused | help
        self.prev_view = "mirror"
        self.runner = None            # lazily created runner_view.RunnerView
        self._runner_img = (0.0, None)
        self.zoom_user = self.vp.zoom # what the user picked; per-app and typing zoom override it
        self.typing_until = 0.0
        self.last_key_t = 0.0
        self._in_mark = None
        self.ctl_q: queue.Queue = queue.Queue()
        from .lights import Lights
        self.lights = Lights(cfg.get("lights_mode", "off"))
        self._leds_last = None
        self._leds_t = 0.0
        self.last_badge_input = time.time()
        self.dimmed = False
        self.sleeping = False
        self.sleep_t = 0.0
        self._tap_last = 0.0
        self._knock_t = 0.0
        self._dim_check = 0.0

    # ================================================================ local control (keyboard shortcuts)
    def start_control(self):
        """`dc32host ctl <action>` -> same actions as badge buttons (bind it to a desktop shortcut)."""
        path = control_socket_path()
        if not path:
            return
        import socket
        import threading
        try:
            if os.path.exists(path):
                os.unlink(path)
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            sock.bind(path)
            os.chmod(path, 0o600)
        except OSError as e:
            log.warning("control socket unavailable: %s", e)
            return

        def loop():
            while True:
                try:
                    data = sock.recv(256).decode("utf-8", "replace").strip()
                except OSError:
                    return
                if re.fullmatch(r"[a-z0-9_:+.-]{1,64}", data):
                    self.ctl_q.put(data)

        threading.Thread(target=loop, daemon=True, name="ctl").start()

    def sleep(self):
        """Backlight off, LEDs off, no capture; ping only. Wakes on PC keyboard/mouse or any badge button."""
        if self.sleeping:
            return
        self.sleeping, self.sleep_t = True, time.time()
        self.send(P.set_leds([(0, 0, 0)] * 9) + P.set_brightness(0))
        self._leds_last = None
        self.force_refresh()             # next frame is the black sleep frame
        log.info("sleep")

    def wake(self, why):
        if not self.sleeping:
            return
        self.sleeping = False
        self.dimmed = False
        self.last_badge_input = time.time()
        self.send(P.set_brightness(self.brightness))
        self._leds_last = None
        self.force_refresh()
        log.info("wake (%s) after %d min", why, (time.time() - self.sleep_t) // 60)

    def on_tap(self, ev):
        """Knock-knock toggles sleep: two knocks on the badge 0.3-1 s apart. A single knock is ignored,
        because a knock on the PC case reaches the badge almost as hard as a tap on it (measured
        2026-10-08: case 1.6-2.9 g vs badge 1.9-3.9 g peak-to-peak), but bumps come one at a time.
        Each knock rings as a burst of tap events; events < TAP_BURST_GAP apart are the same knock."""
        now = time.time()
        new_knock = now - self._tap_last > TAP_BURST_GAP
        self._tap_last = now
        log.info("tap x%d%s", ev.count, " (knock)" if new_knock else "")
        if not self.cfg.get("tap_sleep", True) or not new_knock:
            return
        prev, self._knock_t = self._knock_t, now
        if not KNOCK_WINDOW[0] <= now - prev <= KNOCK_WINDOW[1]:
            return
        self._knock_t = 0.0                             # consumed: a third knock starts a new pair
        if self.sleeping:
            self.wake("knock-knock")
        else:
            self.sleep()

    def check_wake(self):
        """PC input after the sleep command (1 s grace: the shortcut's own keys) wakes the badge."""
        idle_ms = self.be._idle_ms() if hasattr(self.be, "_idle_ms") else None
        if idle_ms is not None and time.time() - idle_ms / 1000.0 > self.sleep_t + 1.0:
            self.wake("PC input")

    def idle_dim(self):
        """LCD backlight saver: dim after `dim_after_s` without PC or badge input, wake on either.
        (The panel is an LCD, so there is no burn-in; this saves the backlight and the room's darkness.)"""
        now = time.time()
        if now - self._dim_check < 1.0:
            return
        self._dim_check = now
        after = float(self.cfg.get("dim_after_s", 600))
        idle_ms = self.be._idle_ms() if hasattr(self.be, "_idle_ms") else None
        pc_idle = (idle_ms / 1000.0) if idle_ms is not None else 0.0
        idle = min(pc_idle, now - self.last_badge_input)
        if after > 0 and idle > after and not self.dimmed:
            self.dimmed = True
            self.send(P.set_brightness(int(self.cfg.get("dim_brightness", 2))))
        elif self.dimmed and idle < after:
            self.dimmed = False
            self.send(P.set_brightness(self.brightness))

    def _save_pref(self, key, value):
        """Persist a user preference (e.g. lights mode) into config.json without touching the rest."""
        try:
            import json
            path = CFG.config_path()
            with open(path, encoding="utf-8") as f:
                user = json.load(f)              # the raw file, so defaults don't get frozen into it
            user[key] = value
            CFG.save(user, path)
        except Exception as e:
            log.warning("could not save %s: %s", key, e)

    def update_leds(self):
        now = time.time()
        if now - self._leds_t < 1 / 30:
            return
        self._leds_t = now
        loc = None
        if self.lights.mode == "runner":
            try:
                loc = self._runner().data.snapshot()[1]
            except Exception:
                loc = {}
        cols = self.lights.frame(now, loc)
        if self.dimmed:
            cols = [tuple(v // 4 for v in c) for c in cols]
        if cols != self._leds_last:
            self._leds_last = cols
            self.send(P.set_leds(cols))

    def run_control(self):
        while True:
            try:
                act = self.ctl_q.get_nowait()
            except queue.Empty:
                return
            log.info("ctl: %s", act)
            self._ctl_quiet = time.time() + 0.6   # the shortcut's own keypress is not "typing"
            self.action(act)

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
        self._last_tx = time.time()

    def force_refresh(self):
        self.prev = None

    # ================================================================ favorites
    def favorite(self, name):
        for f in self.cfg.get("favorites", []) + CFG.BUILTIN_FAVORITES:
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
            # A terminal server (gnome-terminal-server, konsole) owns every terminal window, so an
            # ancestor pid only identifies the dashboard when it owns exactly one window.
            per_pid = {}
            for wi in windows:
                per_pid[wi.pid] = per_pid.get(wi.pid, 0) + 1
            for wi in windows:
                if wi.pid in pids and per_pid[wi.pid] == 1:
                    return wi
        return None

    # processes that own many unrelated windows: an ancestor match on them says nothing
    SHARED_WINDOW_OWNERS = re.compile(r"(?i)^(gnome-terminal-server|konsole|xfce4-terminal|tilix|terminator|"
                                      r"mate-terminal|ptyxis|kgx|gnome-shell|explorer\.exe|windowsterminal\.exe)$")

    @classmethod
    def _ancestor_pids(cls, rx):
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
                        if not cls.SHARED_WINDOW_OWNERS.match(a.name() or ""):
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
            spec["cmd"] = [BASHRC if a == "{badge_bashrc}" else DOOM2_SH if a == "{doom2}" else a
                           for a in spec.get("cmd") or []]
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
        elif name in ("dashboard", "open_dashboard"):     # full terminal dashboard on the PC
            self.view = "mirror"
            self.open_favorite(self.cfg.get("dashboard_favorite", "GitHub Runner Dashboard"))
        elif name == "home_menu":
            self.open_home()
        elif name in ("view_runner", "toggle_runner"):
            self.set_view("mirror" if (name == "toggle_runner" and self.view == "runner") else "runner")
        elif name in ("view_mirror", "resume"):
            self.set_view("mirror")
        elif name.startswith("helpkey:"):  # a number (or Esc) pressed while the cheat sheet is up
            if self.view != "help":
                return
            from .help_view import NUMBERED
            key = name[8:]
            n = 10 if key == "0" else int(key) if key.isdigit() else 0      # 0 = the 10th entry
            act = NUMBERED[n - 1] if 0 < n <= len(NUMBERED) else None
            if key == "Escape" or act:
                self.set_view(self.prev_view if self.prev_view != "help" else "mirror")
            if act and act != "toggle_help":
                self.action(act)
        elif name.startswith("fkey:"):     # PC key / mouse button while the Flipper view is up
            if self.view != "flipper":
                return
            ev = name[5:]
            long = ev.startswith("Shift+")
            ev = ev[6:] if long else ev
            key = (self.FLIPPER_BUTTONS.get(int(ev[6:])) if ev.startswith("button") and ev[6:].isdigit()
                   else self.FLIPPER_KEYS.get(ev))
            if key:
                self._flipper()[0].press(key, long=long)
        elif name in ("keyboard", "toggle_keyboard"):   # Ctrl+Alt+Y: keyboard -> badge view and back
            if self.view not in self.KEYBOARD_VIEWS:
                self.say("No keyboard controls here")
                return
            self.set_keyboard(not getattr(self, "kb_focus", None))
            self.say("Keyboard -> badge (Ctrl+Alt+Y: back to PC)" if self.kb_focus else "Keyboard -> PC")
        elif name in ("command_menu", "toggle_cmd"):   # Ctrl+Alt+M: every action, keyboard-driven
            if self.view == "cmd":
                self.set_view(self.prev_view if self.prev_view != "cmd" else "mirror")
            else:
                self._cmd().sel = 0
                self.prev_view = self.view
                self.set_view("cmd")
        elif name.startswith("cmdkey:"):
            if self.view != "cmd":
                return
            key = name[7:]
            if key == "Up":
                self._cmd().move(-1)
            elif key == "Down":
                self._cmd().move(1)
            elif key == "Home":
                self._cmd().move(-9999)
            elif key == "End":
                self._cmd().move(9999)
            elif key == "Escape":
                self.set_view(self.prev_view if self.prev_view != "cmd" else "mirror")
            elif key == "Return":
                act = self._cmd().action()
                self.action(act)
                if self.view == "cmd":           # an adjust action (brightness/lights/zoom): stay open
                    self.force_refresh()
        elif name in ("rf_bench", "toggle_bench"):   # Ctrl+Alt+V: capture / decode / replay / verify
            self.set_view("mirror" if self.view == "bench" else "bench")
        elif name.startswith("bkey:"):
            if self.view == "bench":
                b = self._bench()[0]
                {"Return": b.arm, "space": b.replay_verify, "Shift+Return": b.selftest,
                 "Left": lambda: b.band(-1), "Right": lambda: b.band(1)}.get(name[5:], lambda: None)()
        elif name in ("sdr_view", "toggle_sdr"):     # Ctrl+Alt+W: spectrum + waterfall
            self.set_view("mirror" if self.view == "sdr" else "sdr")
        elif name.startswith("skey:"):     # PC arrows / Enter while the spectrum is up
            if self.view == "sdr":
                k = {"Up": "up", "Down": "down", "Left": "left", "Right": "right", "Return": "preset",
                     "Shift+Return": "audio"}.get(name[5:])
                if k:
                    self._sdr()[1].key(k)
        elif name in ("flipper", "toggle_flipper"):   # Ctrl+Alt+F
            self.set_view("mirror" if self.view == "flipper" else "flipper")
        elif name in ("ir_scope", "toggle_ir"):   # Ctrl+Alt+E
            self.set_view("mirror" if self.view == "ir" else "ir")
        elif name == "refresh_data":       # Ctrl+Alt+R: fetch GitHub runner data now
            self._runner().data.refresh_now()
            self.say("Updating runner data")
        elif name in ("help", "toggle_help"):     # Ctrl+Alt+H: page 1 -> page 2 -> close
            from .help_view import PAGES
            if self.view != "help":
                self.help_page = 0
                self.set_view("help")
            elif getattr(self, "help_page", 0) + 1 < PAGES:
                self.help_page += 1
                self.force_refresh()
            else:
                self.set_view(self.prev_view if self.prev_view != "help" else "mirror")
        elif name in ("lights_next", "lights_cycle"):
            from .lights import LABELS
            self.lights.next_mode()
            self._leds_last = None
            self.say(LABELS[self.lights.mode])
            self.cfg["lights_mode"] = self.lights.mode
            self._save_pref("lights_mode", self.lights.mode)
        elif name.startswith("lights:"):
            from .lights import MODES, LABELS
            if name[7:] in MODES:
                self.lights.mode = name[7:]
                self._leds_last = None
                self.say(LABELS[self.lights.mode])
                self._save_pref("lights_mode", self.lights.mode)
        elif name in ("pause", "toggle_pause"):
            self.set_view("mirror" if self.view == "paused" else "paused")
        elif name == "doom2":              # Ctrl+Alt+P: Doom II on the PC, mirrored 1:1 on the badge
            if not os.path.exists(DOOM2_SH):
                self.say("Doom II not installed", "run tools/install_doom.sh")
                return
            self.view = "mirror"
            self.open_favorite("Doom II")
        elif name == "badge_terminal":
            self.view = "mirror"
            self.open_favorite("Badge Terminal")
        elif name == "cycle_mode":
            i = (MODES.index(self.mode) + 1) % len(MODES)
            self.set_mode(MODES[i])
        elif name == "pin_current":
            self.set_mode("pinned")
        elif name == "focus_shown":
            if self.shown:
                self.be.focus(self.shown.handle)
        elif name == "zoom_cycle":
            self.typing_until = 0.0
            self.zoom_user = ZOOM_CYCLE[(ZOOM_CYCLE.index(self.vp.zoom) + 1) % len(ZOOM_CYCLE)]
            self.vp.set_zoom(self.zoom_user)
            self.say(f"Zoom: {self.zoom_user}")
        elif name == "zoom_fit":
            self.typing_until = 0.0
            if self.vp.zoom != "fit" or self.zoom_user != "fit":
                self.zoom_user = "fit"
                self.vp.set_zoom("fit")
                self.say("Zoom: fit")
        elif name == "back":               # badge B: undo one thing, so B always gets you unstuck
            fg = self.be.foreground()
            if self.view != "mirror":
                self.set_view(self.prev_view if self.view == "help" and self.prev_view != "help" else "mirror")
            elif self.vp.zoom != "fit" or self.zoom_user != "fit":
                self.action("zoom_fit")
            elif self.shown and fg and fg.handle == self.shown.handle:
                self.be.send_key("esc")    # closes an overlay/popup in the shown app (e.g. one bumped open)
                self.say("Esc")
        elif name.startswith("pan_"):
            d = {"pan_up": (0, -1), "pan_down": (0, 1), "pan_left": (-1, 0), "pan_right": (1, 0)}[name]
            self.vp.pan(*d)
        elif name == "restart":            # the autostart loop (run.sh) starts a fresh daemon in ~3 s
            log.info("restart requested")
            raise SystemExit(0)
        elif name == "info":
            self.info_until = time.time() + 4
        elif name == "refresh":
            self.force_refresh()
            if self.view == "runner" and self.runner:
                self.runner.data.refresh_now()   # B-hold on the runner view: fetch GitHub now
                self.say("Updating runner data")
            else:
                self.say("Refreshed")
        elif name in ("sleep", "toggle_sleep"):
            if self.sleeping and name == "toggle_sleep":
                self.wake("command")
            else:
                self.sleep()
        elif name in ("sd_rw", "sd_ro"):   # microSD over USB; remount on the PC to pick it up
            self.cfg["sd_writable"] = name == "sd_rw"
            self.send(P.set_sd_write(self.cfg["sd_writable"]))
            self._save_pref("sd_writable", self.cfg["sd_writable"])
            self.say("microSD: " + ("read/write" if self.cfg["sd_writable"] else "read-only"))
        elif name == "wake":
            self.wake("command")
        elif name == "bootsel":            # for tools/badgetool.py flash while the daemon owns USB
            log.info("rebooting the badge into BOOTSEL")
            self.send(P.reboot(bootsel=True))
        elif name in ("brightness_up", "brightness_down"):
            # 0 is "off" (sleep) on fw >= 0.2.1, so manual brightness stops at 1
            self.brightness = max(1, min(31, self.brightness + (3 if name.endswith("up") else -3)))
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

    # ================================================================ views + home menu
    def set_view(self, view):
        if view == "help" and self.view != "help":
            self.prev_view = self.view
        if view != self.view:
            self.set_keyboard(False)               # leaving a view always gives the keyboard back
        if view == "sdr":
            self._sdr()[0].start()
        elif self.view == "sdr":
            self._sdr()[0].stop()
        if view == "flipper":
            self._flipper()[0].start()
        elif self.view == "flipper":
            self._flipper()[0].stop()
        if (view == "ir") != (self.view == "ir"):
            self.send(P.set_ir(view == "ir"))     # the IR receiver only runs while the scope is open
        self.view = view
        if view == "help" and hasattr(self.be, "grab_help_keys"):
            self.be.grab_help_keys(lambda k: self.ctl_q.put("helpkey:" + k))
        elif hasattr(self.be, "release_help_keys"):
            self.be.release_help_keys()
        if hasattr(self.be, "grab_input"):
            if view == "cmd":
                self.be.grab_input("cmd", ["Up", "Down", "Return", "Escape", "Home", "End"],
                                   lambda e: self.ctl_q.put("cmdkey:" + e))
            else:
                self.be.release_input("cmd")
        if view == "runner":
            self._runner()                 # starts the background fetch on first use
        if view not in ("help", "cmd"):
            self.say({"mirror": "Mirroring screen", "runner": "Runner costs", "paused": "Display paused",
                      "ir": "IR scope", "flipper": "Flipper", "sdr": "Spectrum (SDR)",
                      "bench": "RF bench"}[view],
                     "Ctrl+Alt+Y: keyboard -> badge" if view in self.KEYBOARD_VIEWS
                     else "FN: menu" if view != "mirror" else "")
        self.vp.reset()
        self.force_refresh()

    def _flipper(self):
        if getattr(self, "flipper", None) is None:
            from .flipper import FlipperLink
            from .flipper_view import FlipperView
            link = FlipperLink()
            self.flipper = (link, FlipperView(link))
        return self.flipper

    FLIPPER_KEYS = {"Up": "up", "Down": "down", "Left": "left", "Right": "right", "Return": "ok",
                    "KP_Enter": "ok", "space": "ok", "BackSpace": "back", "Escape": "back"}
    FLIPPER_BUTTONS = {1: "ok", 3: "back", 4: "up", 5: "down", 6: "left", 7: "right"}

    # view -> (keys grabbed while the keyboard is handed to the badge, event prefix, use mouse buttons)
    KEYBOARD_VIEWS = {
        "flipper": (list(FLIPPER_KEYS), "fkey:", True),
        "sdr": (["Up", "Down", "Left", "Right", "Return"], "skey:", False),
        "bench": (["Return", "space", "Left", "Right"], "bkey:", False),
    }

    def set_keyboard(self, on):
        """Hand the PC keyboard (and for the Flipper, the mouse) to the current badge view, or give it
        back. Off unless asked for (Ctrl+Alt+Y), off again on every view change, so typing elsewhere is
        never swallowed. Ctrl+Alt shortcuts are never grabbed."""
        on = bool(on) and self.view in self.KEYBOARD_VIEWS and hasattr(self.be, "grab_input")
        if getattr(self, "kb_focus", None) and hasattr(self.be, "release_input"):
            self.be.release_input("badge-keys")
        self.kb_focus = self.view if on else None
        if on:
            keys, prefix, mouse = self.KEYBOARD_VIEWS[self.view]
            buttons = list(self.FLIPPER_BUTTONS) if mouse and self.cfg.get("flipper_mouse", True) else []
            self.be.grab_input("badge-keys", keys, lambda e: self.ctl_q.put(prefix + e),
                               buttons=buttons, shift=True)

    def _bench(self):
        if getattr(self, "bench", None) is None:
            from .bench_view import Bench, BenchView
            b = Bench()
            self.bench = (b, BenchView(b))
        return self.bench

    def _sdr(self):
        if getattr(self, "sdr", None) is None:
            from .sdr import SdrSource
            from .sdr_view import SdrView
            src = SdrSource(freq=float(self.cfg.get("sdr_freq", 433.92e6)))
            self.sdr = (src, SdrView(src))
        return self.sdr

    def _cmd(self):
        if getattr(self, "cmd", None) is None:
            from .command_view import CommandView
            self.cmd = CommandView()
        return self.cmd

    def _ir(self):
        if getattr(self, "ir", None) is None:
            from .ir_view import IrScope
            self.ir = IrScope()
        return self.ir

    def _runner(self):
        if self.runner is None:
            from .runner_view import RunnerData, RunnerView
            rv = self.cfg.get("runner_view") or {}
            script = rv.get("script") or self._dashboard_script()
            data = RunnerData(script, int(rv.get("refresh_s", 300)),
                              quiet_hours=rv.get("quiet_hours", [23, 3]))
            data.start()
            self.runner = RunnerView(data)
        return self.runner

    def _dashboard_script(self):
        fav = self.favorite(self.cfg.get("dashboard_favorite", "GitHub Runner Dashboard")) or {}
        for a in reversed((fav.get("launch") or {}).get("cmd") or []):
            if str(a).endswith(".py"):
                return a
        raise RuntimeError("no dashboard script: run dc32host find-dashboard --write")

    def runner_frame(self):
        t, img = self._runner_img
        if img is None or time.time() - t > 1.0:
            try:
                img = self._runner().render()
            except Exception as e:
                log.warning("runner view failed: %s", e)
                img = C.placeholder(["Runner view unavailable", str(e)[:44]])
            self._runner_img = (time.time(), img)
        return img

    def is_dashboard_window(self, wi) -> bool:
        if wi is None or not self.cfg.get("runner_view_for_dashboard", True):
            return False
        fav = self.favorite(self.cfg.get("dashboard_favorite", "GitHub Runner Dashboard")) or {}
        t = (fav.get("match") or {}).get("title")
        return bool(t and re.search(t, wi.title or ""))

    def open_home(self):
        items = [
            ("Mirror screen", "view_mirror", self.view == "mirror"),
            ("Runner costs", "view_runner", self.view == "runner"),
            ("Badge terminal", "badge_terminal", bool(self.shown and self.shown.title == "Badge Terminal")),
            ("Switch app...", "app_switcher", False),
            (f"Zoom: {self.vp.zoom} (A cycles)", "zoom_cycle", False),
            ("Open dashboard on PC", "open_dashboard", False),
            ("Resume display" if self.view == "paused" else "Pause display", "toggle_pause", self.view == "paused"),
            (f"Lights: {self.lights.mode} (Ctrl+Alt+G)", "lights_next", self.lights.mode != "off"),
            ("Shortcuts (Ctrl+Alt+H)", "toggle_help", self.view == "help"),
            ("IR scope (Ctrl+Alt+E)", "ir_scope", self.view == "ir"),
            ("Flipper Zero (Ctrl+Alt+F)", "flipper", self.view == "flipper"),
            ("Spectrum / SDR (Ctrl+Alt+W)", "sdr_view", self.view == "sdr"),
            ("RF bench (Ctrl+Alt+V)", "rf_bench", self.view == "bench"),
            ("Sleep: screen off (Ctrl+Alt+S)", "sleep", False),
            ("Status info", "info", False),
        ]
        entries, self.menu_table = [], {}
        for i, (label, act, on) in enumerate(items):
            eid = HOME_ID_BASE | i
            entries.append((eid, P.MENU_FLAG_ACTIVE if on else 0, label))
            self.menu_table[eid] = ("action", act)
        self.send(P.menu_list("DC32 Display", entries, 0))
        self.menu_open_until = time.time() + 60

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
        if isinstance(target, tuple) and target[0] == "action":   # home menu entry
            self.action(target[1])
            return
        if isinstance(target, dict):           # favorite to launch
            self.open_favorite(target["name"])
            return
        self.view = "mirror"
        if r.action == P.MENU_PIN:
            self.mode, self.pinned = "pinned", target.handle
            self.vp.reset()
            self.say("Pinned", target.label)
        else:
            self.show_and_focus(target)

    # ================================================================ events
    def on_button(self, ev: P.ButtonEvent):
        self.last_badge_input = time.time()
        if self.sleeping:                    # any press wakes; it does nothing else
            self.wake("badge button")
            self._swallow = ev.button
        elif self.dimmed:                    # a press on a dimmed badge only wakes the backlight
            self.dimmed = False
            self.send(P.set_brightness(self.brightness))
            self._swallow = ev.button
        if getattr(self, "_swallow", None) == ev.button:
            if ev.event in ("short", "long"):
                self._swallow = None
            return
        if ev.button == "fn":
            if ev.event == "down":
                self.fn_used_as_modifier = False
            if ev.event == "short" and self.fn_used_as_modifier:
                return
        elif "fn" in ev.held:
            self.fn_used_as_modifier = True
        if self.view == "bench" and ev.button in ("a", "start", "left", "right") and "fn" not in ev.held:
            b = self._bench()[0]
            if ev.event == "short":
                {"a": b.arm, "start": b.replay_verify, "left": lambda: b.band(-1),
                 "right": lambda: b.band(1)}[ev.button]()
            elif ev.event == "long" and ev.button == "a":
                b.selftest()
            return
        if self.view == "sdr" and ev.button in ("up", "down", "left", "right", "a", "start") and "fn" not in ev.held:
            if ev.event in ("down", "repeat") and ev.button not in ("a", "start"):
                self._sdr()[1].key(ev.button)
            elif ev.event == "short" and ev.button == "a":
                self._sdr()[1].key("preset")
            elif ev.event == "short" and ev.button == "start":
                self._sdr()[1].key("audio")
            return
        if self.view == "flipper" and ev.button != "fn" and "fn" not in ev.held:
            key = {"up": "up", "down": "down", "left": "left", "right": "right", "a": "ok", "b": "back",
                   "select": "back", "start": "ok"}.get(ev.button)
            if key and ev.event in ("short", "long"):
                self._flipper()[0].press(key, long=ev.event == "long")
            elif key and ev.event == "repeat" and key in ("up", "down", "left", "right"):
                self._flipper()[0].press(key)
            return
        if self.view == "help" and ev.button == "b" and ev.event == "short":
            self.set_view(self.prev_view)
            return
        if self.view == "help" and ev.button in ("left", "right") and ev.event == "short":
            from .help_view import PAGES
            self.help_page = (getattr(self, "help_page", 0) + (1 if ev.button == "right" else -1)) % PAGES
            self.force_refresh()
            return
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
        elif isinstance(ev, P.IrFrame):
            self._ir().add(ev.pairs)
            log.info("IR frame: %d pulses, %s  [%s ...]", len(ev.pairs), self._ir().frames[-1][2] or "unknown",
                     " ".join(f"{m}/{s}" for m, s in ev.pairs[:6]))
        elif isinstance(ev, P.TapEvent):
            self.on_tap(ev)
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
        if getattr(self.be, "wayland", False):
            return time.time(), C.placeholder(["Wayland session: capture blocked", "Log out, click the gear icon,",
                                               "choose 'Ubuntu on Xorg', log in", "(or: install_linux.sh --xorg)"]), False
        if self.view == "paused":
            return time.time(), C.placeholder(["Display paused", "FN: menu  |  FN > Resume"]), False
        if self.view == "runner":
            return time.time(), self.runner_frame(), False
        if self.view == "help":
            from . import help_view
            return time.time(), help_view.render(getattr(self, "help_page", 0)), False
        if self.view == "ir":
            return time.time(), self._ir().render(), False
        if self.view == "cmd":
            return time.time(), self._cmd().render(), False
        if self.view == "flipper":
            self._flipper()[1].kb_here = getattr(self, "kb_focus", None) == "flipper"
            return time.time(), self._flipper()[1].render(), False
        if self.view == "sdr":
            self._sdr()[1].kb_here = getattr(self, "kb_focus", None) == "sdr"
            return time.time(), self._sdr()[1].render(), False
        if self.view == "bench":
            self._bench()[1].kb_here = getattr(self, "kb_focus", None) == "bench"
            return time.time(), self._bench()[1].render(), False
        src_wi = self.pick_source()
        changed_src = (src_wi.handle if src_wi else None) != (self.shown.handle if self.shown else None)
        if changed_src:
            self.vp.reset()
        self.shown = src_wi
        if self.is_dashboard_window(src_wi):  # the terminal dashboard is unreadable at 320x240
            return time.time(), self.runner_frame(), changed_src
        self.apply_zoom(src_wi)
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
        focus_pt, by_mouse = None, False
        cur = self.be.cursor()
        now = time.time()
        if cur:
            if self.cursor_last[0] != (cur[0], cur[1]):
                if self.cursor_last[0] is not None:
                    self.mouse_takes_over(now)
                self.cursor_last = ((cur[0], cur[1]), now)
        typing = now < self.typing_until
        key_recent = now - self.last_key_t < 0.5    # changes this soon after a key are its echo
        if src_wi is not None and self.vp.zoom != "fit":
            car = self.be.caret(src_wi.handle) if hasattr(self.be, "caret") else None
            if car:
                focus_pt = (car[0] - rect[0], car[1] - rect[1] + car[3])
            elif (cur and now - self.cursor_last[1] < 1.0 and not typing     # typing beats the mouse
                  and self.cfg.get("mouse_moves_view", True)):
                focus_pt, by_mouse = (cur[0] - rect[0], cur[1] - rect[1]), True
        out, tf = self.vp.compose(src, key, focus_pt, tuple(self.cfg.get("letterbox_color", [0, 0, 0])), key_recent,
                                  mouse=by_mouse)

        hide_after = float(self.cfg.get("cursor_only_when_moving_s", 3.0))
        if self.cfg.get("show_cursor", True) and cur and cur[2] and (hide_after <= 0 or now - self.cursor_last[1] < hide_after):
            cx, cy = C.map_point(tf, cur[0] - rect[0], cur[1] - rect[1])
            if 0 <= cx < C.OUT_W and 0 <= cy < C.OUT_H:
                C.draw_cursor(out, cx, cy)
            else:
                C.draw_edge_marker(out, cx, cy)
        return t_cap, out, changed_src

    def mouse_takes_over(self, now):
        """Latest input wins: moving the mouse ends a keyboard/D-pad pan hold (was 15 s) and the
        after-typing hold (3 s), so the view follows the pointer again right away."""
        self.vp.manual_until = 0.0
        self.typing_until = min(self.typing_until, now)

    def apply_zoom(self, src_wi):
        """Effective zoom = per-app zoom (e.g. Badge Terminal 1:1) > typing zoom > the user's choice."""
        now = time.time()
        mark, self._in_mark = self._in_mark, (self.be.input_mark() if hasattr(self.be, "input_mark") else None)
        cur = self.be.cursor()
        mouse_still = cur is None or self.cursor_last[0] == (cur[0], cur[1])
        quiet = now < getattr(self, "_ctl_quiet", 0.0)
        if mark is not None and mouse_still and not quiet and self.be.input_since(mark):
            self.last_key_t = now
            hold = float(self.cfg.get("typing_zoom_hold_s", 0))
            self.typing_until = now + (hold if hold > 0 else 3.0)
            if hold <= 0 and self.zoom_user == "fit" and self.cfg.get("typing_zoom"):
                # no timeout: typing zooms in and it stays until B (zoom_fit), user's call
                self.zoom_user = self.cfg["typing_zoom"]
                self.say(f"Zoom {self.zoom_user} (B: fit)")
        want = self.zoom_user
        fav = self.app_favorite(src_wi)
        if fav and fav.get("zoom"):
            want = fav["zoom"]
        elif now < self.typing_until and self.zoom_user == "fit" and self.cfg.get("typing_zoom"):
            want = self.cfg["typing_zoom"]
        if want != self.vp.zoom:
            self.vp.set_zoom(want)

    def app_favorite(self, wi):
        if wi is None:
            return None
        for f in self.cfg.get("favorites", []):
            t = (f.get("match") or {}).get("title")
            if f.get("zoom") and t and re.search(t, wi.title or ""):
                return f
        return None

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
        # the keyboard-capture pill is drawn by each keyboard view (flipper/sdr/bench) via kb_pill()
        if self.toast and now < self.toast[0]:
            out = C.draw_lines(out, self.toast[1])
        elif self.toast:
            self.toast = None
        return out

    def tick(self):
        self.run_control()
        now = time.time()
        if self.sleeping:
            self.check_wake()
        if self.sleeping:
            if self.prev is None:        # one black frame, then only keep-alives
                self.send(E_black_frame(self))
            elif now - self.last_ping > 1.0:
                self.send(P.ping(int(now)))
                self.last_ping = now
            self.drain_events(0.1)
            return
        self.idle_dim()
        self.update_leds()
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

        # Silent >3 s (a stalled tick) and the badge dropped to its 'disconnected' screen; on the next
        # byte it clears to black and expects a redraw, so a diff against self.prev would leave holes.
        if time.time() - getattr(self, "_last_tx", time.time()) > RESYNC_GAP_S:
            log.info("host was silent %.1f s: full redraw", time.time() - self._last_tx)
            self.inflight.clear()
            self.force_refresh()
        t_b = time.time()
        t_cap, rgb, changed_src = self.build_frame()
        self._t_build = time.time() - t_b
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
            t_s = time.time()
            self.send(blob)
            self._t_send = time.time() - t_s
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
        if time.time() - self.last_key_t < 0.35:
            fps = max(fps, 100)              # apps echo a key 10-80 ms later: poll fast until it shows
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
        self.dimmed = False
        self.last_badge_input = time.time()
        self._leds_last = None
        self.send(P.set_brightness(self.brightness) + P.set_timing(int(self.cfg.get("long_press_ms", 600)), 400, 90)
                  + P.set_tap(int(self.cfg.get("tap_threshold", 12)) if self.cfg.get("tap_sleep", True) else 0)
                  + P.set_sd_write(bool(self.cfg.get("sd_writable", False)))
                  + P.set_ir(self.view == "ir"))
        self.say(f"DC32 Display host {__version__}", f"fw {self.badge.info.fw}")

    def run(self, once_seconds: float | None = None):
        log.info("dc32host %s starting, backend=%s, config=%s", __version__, self.be.name, CFG.config_path())
        t_end = time.time() + once_seconds if once_seconds else None
        if once_seconds is None:
            self.start_control()
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
                t0 = time.time()
                self._t_build = self._t_send = 0.0
                self.tick()
                dt = time.time() - t0
                if dt > 1.5:      # the badge shows "disconnected" after 3 s of silence; find what stalls
                    log.warning("slow tick: %.1f s (build %.1f s, send %.1f s, view=%s, shown=%s)", dt,
                                self._t_build, self._t_send, self.view,
                                self.shown.title[:40] if self.shown else None)
            except Disconnected as e:
                log.warning("badge disconnected: %s", e)
                self.badge.close()
            except Exception:
                log.exception("tick failed")
                time.sleep(0.5)
