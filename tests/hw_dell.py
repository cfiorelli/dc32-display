"""Hardware acceptance run on the Dell: real X11 desktop (GNOME on Xorg), real gnome-terminal, real badge.

Same checks as test_e2e_x11.py, but every frame must be ACKed by the physical badge. Stop the host
service first (it holds the USB interface). Hands off keyboard/mouse for ~1 minute while it runs.
Run: ~/.local/share/dc32-display/venv/bin/python tests/hw_dell.py
"""
import hashlib
import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "host"))

procs = []
results = []


def sh(*a):
    return subprocess.run(a, capture_output=True, text=True).stdout.strip()


def term(title, extra=""):
    # gnome-terminal ignores --title; a --norc shell keeps the OSC title instead of PS1 overwriting it
    cmd = f"printf '\\033]0;{title}\\007'; {extra} exec env PS1='$ ' bash --norc --noprofile"
    procs.append(subprocess.Popen(["gnome-terminal", "--geometry", "90x26", "--", "bash", "-c", cmd],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))


def wid(title):
    for _ in range(100):
        out = sh("xdotool", "search", "--name", f"^{title}$")
        if out:
            return int(out.split()[0])
        time.sleep(0.1)
    raise RuntimeError(f"no window {title}")


def activate(w):
    sh("xdotool", "windowactivate", "--sync", str(w))
    time.sleep(0.2)


def wait_for(pred, timeout=3.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return time.time() - t0
        time.sleep(0.002)
    return None


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


class Tap:
    """Records frames sent to and ACKed by the real badge."""

    def __init__(self, d):
        self.lock = threading.Lock()
        self.sent = {}       # fid -> (t_capture, digest, shown title)
        self.acks = []       # (fid, t_ack, digest, shown title)
        self.d = d
        d.on_frame_sent, d.on_frame_acked = self.on_sent, self.on_acked

    def on_sent(self, fid, rgb, t_cap):
        with self.lock:
            self.sent[fid] = (t_cap, hashlib.blake2b(rgb.tobytes(), digest_size=8).digest(),
                              self.d.shown.title if self.d.shown else None)

    def on_acked(self, fid, t):
        with self.lock:
            s = self.sent.get(fid)
            if s:
                self.acks.append((fid, t, s[1], s[2]))

    def last(self):
        with self.lock:
            return self.acks[-1] if self.acks else None

    def acked_since(self, t0, title=None, differs_from=None):
        with self.lock:
            for fid, t, dig, ttl in self.acks:
                if t >= t0 and (title is None or ttl == title) and (differs_from is None or dig != differs_from):
                    return t
        return None


def main():
    from dc32host import config as CFG
    from dc32host.daemon import Daemon

    term("dc32 alpha")
    term("dc32 gamma", "cd /tmp;")
    wa, wg = wid("dc32 alpha"), wid("dc32 gamma")
    cfg = CFG.load()
    cfg["mode"], cfg["zoom"] = "follow", "fit"
    cfg["typing_zoom"] = None          # measured separately below (test 23)
    d = Daemon(cfg)
    tap = Tap(d)
    threading.Thread(target=d.run, daemon=True).start()
    ok = wait_for(lambda: d.badge.connected if hasattr(d.badge, "connected") else tap.last() is not None, 8)
    time.sleep(1.5)

    # 4/5
    activate(wa)
    t0 = time.time()
    ok = wait_for(lambda: tap.acked_since(t0, "dc32 alpha"), 3)
    check("4/5 badge detected, foreground app shown (ACKed by badge)", ok is not None,
          f"{round(ok * 1000) if ok else None} ms to first ACKed frame")

    # 6
    seq = []
    for w, t in ((wg, "dc32 gamma"), (wa, "dc32 alpha"), (wg, "dc32 gamma"), (wa, "dc32 alpha")):
        t0 = time.time()
        activate(w)
        seq.append(wait_for(lambda: tap.acked_since(t0, t), 3))
    check("6 badge follows app switches", all(s is not None for s in seq),
          f"{[round(s * 1000) if s else None for s in seq]} ms (after windowactivate)")

    # 7
    for zoom in ("1x", "fit"):
        d.vp.zoom = zoom
        d.vp.reset()
        activate(wa)
        time.sleep(0.8)
        lats = []
        for ch in "hello_badge_typing_test":
            base = tap.last()[2]
            t0 = time.time()
            sh("xdotool", "type", "--delay", "0", ch)
            if wait_for(lambda: tap.acked_since(t0, "dc32 alpha", base), 1.0) is not None:
                lats.append((tap.acked_since(t0, "dc32 alpha", base) - t0) * 1000)
            time.sleep(0.08)
        lats.sort()
        check(f"7 typing appears live (zoom {zoom})", len(lats) >= 20,
              f"{len(lats)}/23 seen; keypress->badge ACK median {lats[len(lats) // 2]:.0f} ms, "
              f"p95 {lats[int(.95 * (len(lats) - 1))]:.0f} ms" if lats else "none")
    d.vp.zoom = "fit"
    d.vp.reset()
    sh("xdotool", "key", "ctrl+u")

    # 8
    sh("xdotool", "type", "--delay", "0", "for i in $(seq 1 400); do echo line $i; sleep 0.01; done\n")
    time.sleep(0.8)
    n0, t0 = len(tap.acks), time.time()
    time.sleep(2.5)
    rate = (len(tap.acks) - n0) / (time.time() - t0)
    check("8 terminal scrolling updates", rate >= 8, f"{rate:.1f} ACKed frames/s")
    time.sleep(2.5)
    sh("xdotool", "type", "--delay", "0", "clear\n")
    time.sleep(0.5)

    # 9
    base = tap.last()[2]
    geo = sh("xdotool", "getwindowgeometry", str(wa))
    t0 = time.time()
    for x, y in ((150, 120), (300, 200), (450, 260)):
        sh("xdotool", "mousemove", "--window", str(wa), str(x), str(y))
        time.sleep(0.15)
    ok = wait_for(lambda: tap.acked_since(t0, "dc32 alpha", base), 2)
    check("9 mouse cursor movement reaches badge", ok is not None, geo.replace("\n", " "))

    # 13
    activate(wg)
    activate(wa)
    time.sleep(0.4)
    d.action("toggle_last_app")
    a1 = wait_for(lambda: sh("xdotool", "getactivewindow") == str(wg), 2)
    d.action("toggle_last_app")
    a2 = wait_for(lambda: sh("xdotool", "getactivewindow") == str(wa), 2)
    check("13 previous-app toggle (SELECT tap action) x2", a1 is not None and a2 is not None)

    # 15
    activate(wa)
    time.sleep(0.4)
    d.set_mode("pinned")
    time.sleep(0.3)
    t0 = time.time()
    activate(wg)
    time.sleep(1.0)
    stayed = d.shown is not None and d.shown.title == "dc32 alpha" and tap.acked_since(t0, "dc32 gamma") is None
    check("15 pinned mode keeps showing pinned app", stayed, f"shown={d.shown and d.shown.title}")

    # 16
    t0 = time.time()
    d.set_mode("desktop")
    ok = wait_for(lambda: tap.acked_since(t0, None) and d.shown is None, 2)
    check("16 whole-desktop mode", ok is not None)
    d.set_mode("follow")
    time.sleep(0.5)

    # 14: dashboard shortcut, start if not running, focus if running
    dash = d.favorite(cfg.get("dashboard_favorite", "GitHub Runner Dashboard"))
    running = d.match_favorite(dash) is not None
    activate(wa)
    t0 = time.time()
    d.action("dashboard")
    ok = wait_for(lambda: d.shown is not None and "Runner Dashboard" in d.shown.title
                  and sh("xdotool", "getactivewindow") == str(d.shown.handle), 15)
    h1 = d.shown.handle if ok else None
    check(f"14 dashboard shortcut ({'was running: focus' if running else 'not running: start'})", ok is not None,
          f"{round(ok, 1) if ok else None} s")
    if ok:
        activate(wa)
        time.sleep(0.5)
        d.action("dashboard")
        ok2 = wait_for(lambda: d.shown is not None and d.shown.handle == h1
                       and sh("xdotool", "getactivewindow") == str(h1), 5)
        check("14 dashboard shortcut when already running: focuses same window", ok2 is not None)

    # 21: badge-native runner view (A-hold)
    from dc32host import protocol as P
    t0 = time.time()
    d.action("toggle_runner")
    ok = wait_for(lambda: d.view == "runner" and tap.acked_since(t0) is not None, 5)
    check("21 runner view on badge (A-hold)", ok is not None)
    d.action("toggle_runner")
    check("21 A-hold again returns to mirroring", d.view == "mirror")

    # 24: home menu -> pick "Runner costs" -> back via menu "Mirror screen"
    d.action("home_menu")
    ids = {v[1]: k for k, v in d.menu_table.items() if isinstance(v, tuple)}
    d.on_menu_result(P.MenuResult(P.MENU_APPS, P.MENU_SELECT, ids["view_runner"]))
    a = d.view == "runner"
    d.action("home_menu")
    ids = {v[1]: k for k, v in d.menu_table.items() if isinstance(v, tuple)}
    d.on_menu_result(P.MenuResult(P.MENU_APPS, P.MENU_SELECT, ids["view_mirror"]))
    check("24 home menu (FN) switches views", a and d.view == "mirror", f"entries={list(ids)}")

    # 25: pause / resume
    t0 = time.time()
    d.action("toggle_pause")
    ok = wait_for(lambda: tap.acked_since(t0) is not None, 3)
    time.sleep(2.0)                    # let the "Display paused" toast expire
    n0 = len(tap.acks)
    activate(wg)
    time.sleep(1.0)
    still = len(tap.acks) == n0
    d.action("toggle_pause")
    check("25 pause shows a static screen and stops mirroring", ok is not None and still)

    # 23: typing auto-zoom: fit -> 1:1 around the caret while typing, back to fit after the hold
    d.cfg["typing_zoom"], d.cfg["typing_zoom_hold_s"] = "1x", 2
    d.zoom_user = "fit"
    d.vp.set_zoom("fit")
    activate(wa)
    time.sleep(0.8)
    sh("xdotool", "type", "--delay", "40", "echo typing zoom")
    z_in = wait_for(lambda: d.vp.zoom == "1x", 1.5)
    z_out = wait_for(lambda: d.vp.zoom == "fit", 5)
    sh("xdotool", "key", "ctrl+u")
    check("23 typing auto-zooms to 1:1, returns to fit when idle", z_in is not None and z_out is not None,
          f"in {round(z_in * 1000) if z_in else None} ms")

    # 22: badge terminal: 320 px wide, shown 1:1
    d.action("badge_terminal")
    ok = wait_for(lambda: d.shown is not None and d.shown.title == "Badge Terminal", 10)
    r = d.be.rect(d.shown.handle) if ok else None
    check("22 badge terminal opens, shown 1:1, fits the badge width", ok is not None and d.vp.zoom == "1x"
          and r is not None and 320 <= r[2] <= 348, f"rect={r} zoom={d.vp.zoom}")
    if ok:
        sh("xdotool", "type", "--delay", "0", "exit\n")

    s = d.stats_all.summary() if hasattr(d.stats_all, "summary") else ""
    print("stats:", s)
    for w in (wa, wg):      # never `xdotool windowclose`: it kills gnome-terminal-server and EVERY terminal
        activate(w)
        sh("xdotool", "type", "--delay", "0", "exit\n")
    fails = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(fails)}/{len(results)} passed")
    os._exit(1 if fails else 0)


if __name__ == "__main__":
    main()
