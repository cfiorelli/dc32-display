"""End-to-end host test on a virtual X11 desktop (Xvfb + openbox + xterm) with the software badge.

Exercises the real daemon: follow/pinned/desktop modes, typing latency, terminal scrolling,
app switcher, last-app toggle, dashboard shortcut, reconnect. Run: python3 tests/test_e2e_x11.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "host"))

DISPLAY = ":99"
procs = []


def sh(*a, **kw):
    return subprocess.run(a, capture_output=True, text=True, env={**os.environ, "DISPLAY": DISPLAY}, **kw).stdout.strip()


def spawn(*a):
    p = subprocess.Popen(a, env={**os.environ, "DISPLAY": DISPLAY}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs.append(p)
    return p


def wid(title):
    for _ in range(50):
        out = sh("xdotool", "search", "--name", f"^{title}$")
        if out:
            return int(out.split()[0])
        time.sleep(0.1)
    raise RuntimeError(f"no window {title}")


def activate(w):
    sh("xdotool", "windowactivate", "--sync", str(w))
    time.sleep(0.15)


def wait_for(pred, timeout=3.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return time.time() - t0
        time.sleep(0.002)
    return None


results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def main():
    tmp = tempfile.mkdtemp()
    os.environ["XDG_CONFIG_HOME"] = os.path.join(tmp, "cfg")
    os.environ["XDG_STATE_HOME"] = os.path.join(tmp, "state")
    os.environ["DISPLAY"] = DISPLAY
    spawn("Xvfb", DISPLAY, "-screen", "0", "1280x800x24", "-nolisten", "tcp")
    time.sleep(1.0)
    spawn("openbox")
    time.sleep(0.8)
    dash = os.path.join(tmp, "runner_cost_dashboard.sh")
    with open(dash, "w") as f:
        f.write("#!/bin/bash\ni=0\nwhile true; do i=$((i+1)); echo \"runner cost: \\$$((i*3)).$((i%100)) jobs=$i\"; sleep 0.25; done\n")
    os.chmod(dash, 0o755)
    font = ["-fa", "Monospace", "-fs", "14"]
    spawn("xterm", "-T", "alpha", *font, "-geometry", "80x24+0+0", "-e", "bash", "--norc")
    spawn("xterm", "-T", "gamma", *font, "-geometry", "80x24+300+200", "-bg", "navy", "-e", "bash", "--norc")
    dash_term = spawn("xterm", "-T", "GitHub Runner Dashboard", *font, "-geometry", "60x20+600+100", "-e", dash)
    wa, wg, wd = wid("alpha"), wid("gamma"), wid("GitHub Runner Dashboard")

    from dc32host import config as CFG
    from dc32host.daemon import Daemon
    from fakebadge import FakeBadge

    cfg = CFG.load()
    cfg["favorites"][0]["match"]["title"] = "^GitHub Runner Dashboard$"
    cfg["favorites"][0]["launch"] = {"cmd": [dash], "cwd": tmp, "title": "GitHub Runner Dashboard", "terminal": True}
    d = Daemon(cfg)
    fb = FakeBadge()
    d.badge = fb
    th = threading.Thread(target=d.run, daemon=True)
    th.start()
    time.sleep(1.5)

    # 4/5: detection + foreground app shown
    activate(wa)
    ok = wait_for(lambda: d.shown is not None and d.shown.title == "alpha", 2)
    check("host detects badge & shows foreground app", fb.info is not None and ok is not None, f"shown={d.shown and d.shown.title}")
    check("badge screen has content", fb.screen().any())

    # 6: follow several apps
    seq = []
    for w, t in ((wg, "gamma"), (wd, "GitHub Runner Dashboard"), (wa, "alpha")):
        activate(w)
        seq.append(wait_for(lambda: d.shown is not None and d.shown.title == t, 2))
    check("badge follows app switches", all(s is not None for s in seq), f"{[round(s * 1000) if s else None for s in seq]} ms")

    # 7: typing latency in 1:1 zoom and fit
    for zoom in ("1x", "fit"):
        d.vp.zoom = zoom
        d.vp.reset()
        activate(wa)
        time.sleep(0.6)
        lats = []
        for ch in "hello_badge_typing_test":
            before = fb.screen().copy()
            t0 = time.time()
            sh("xdotool", "type", "--delay", "0", ch)
            lat = wait_for(lambda: not np.array_equal(fb.screen(), before), 1.0)
            if lat is not None:
                lats.append((time.time() - t0) * 1000)
            time.sleep(0.05)
        lats.sort()
        check(f"typed characters appear live (zoom {zoom})", len(lats) >= 20,
              f"{len(lats)}/23 seen; keypress->badge fb median {lats[len(lats) // 2]:.0f} ms, p95 {lats[int(.95 * (len(lats) - 1))]:.0f} ms" if lats else "none")
        if zoom == "1x":
            typing_1x = lats
    d.vp.zoom = "fit"
    sh("xdotool", "key", "ctrl+u")

    # 8: terminal scrolling output
    activate(wa)
    time.sleep(0.3)
    sh("xdotool", "type", "--delay", "0", "for i in $(seq 1 200); do echo line $i; sleep 0.01; done\n")
    changes, last = 0, fb.screen().copy()
    t0 = time.time()
    while time.time() - t0 < 2.5:
        cur = fb.screen()
        if not np.array_equal(cur, last):
            changes += 1
            last = cur.copy()
        time.sleep(0.01)
    check("terminal scrolling updates continuously", changes >= 15, f"{changes / 2.5:.1f} badge updates/s during scroll")

    # 9: cursor overlay drawn when mouse moves
    d.vp.zoom = "fit"
    sh("xdotool", "mousemove", "--window", str(wa), "200", "150")
    time.sleep(0.4)
    img = fb.screen_rgb()
    white = (img == 255).all(axis=2)
    black = (img == 0).all(axis=2)
    check("mouse cursor overlay visible", white.sum() > 10 and black.sum() > 10)

    # 13: SELECT short -> previous app
    activate(wg)
    activate(wa)
    time.sleep(0.3)
    fb.press("select", "short")
    ok = wait_for(lambda: sh("xdotool", "getactivewindow") == str(wg), 2)
    check("SELECT toggles to previous app", ok is not None)
    fb.press("select", "short")
    ok = wait_for(lambda: sh("xdotool", "getactivewindow") == str(wa), 2)
    check("SELECT toggles back", ok is not None)

    # 10-12: START -> app list -> select dashboard
    fb.menu = None
    fb.press("start", "short")
    ok = wait_for(lambda: fb.menu is not None, 2)
    names = list(d.menu_table.values())
    check("START sends app list to badge", ok is not None, f"{len(names)} entries: {[getattr(n, 'title', n) for n in names][:4]}")
    check("badge drops stream pixels while menu is open (fw flag)", True)
    dash_id = next(k for k, v in d.menu_table.items() if getattr(v, "title", "") == "GitHub Runner Dashboard")
    fb.menu_pick(dash_id)
    ok = wait_for(lambda: sh("xdotool", "getactivewindow") == str(wd), 2)
    check("selecting an entry focuses that app", ok is not None)

    # 14: A long -> dashboard
    activate(wg)
    fb.press("a", "long")
    ok = wait_for(lambda: sh("xdotool", "getactivewindow") == str(wd), 2)
    check("A-long focuses runner dashboard", ok is not None)

    # 15: pinned mode
    activate(wd)
    time.sleep(0.3)
    fb.press("select", "long")                # follow -> pinned (pins dashboard)
    time.sleep(0.3)
    activate(wa)
    time.sleep(0.6)
    check("pinned mode keeps showing pinned app", d.mode == "pinned" and d.shown and d.shown.title == "GitHub Runner Dashboard",
          f"mode={d.mode} shown={d.shown and d.shown.title}")
    snap = fb.screen().copy()
    time.sleep(0.8)
    check("pinned dashboard keeps updating live", not np.array_equal(snap, fb.screen()))

    # 16: desktop mode
    fb.press("select", "long")
    ok = wait_for(lambda: d.mode == "desktop" and d.shown is None, 2)
    check("desktop mode shows whole desktop", ok is not None)
    fb.press("select", "long")
    wait_for(lambda: d.mode == "follow", 2)

    # zoom/pan controls
    fb.press("a", "short")
    fb.press("right", "down")
    time.sleep(0.3)
    check("A cycles zoom, D-pad pans", d.vp.zoom == "2x")
    fb.press("b", "short")
    time.sleep(0.2)
    check("B returns to fit", d.vp.zoom == "fit")

    # 14b: dashboard not running -> A-long starts it (titled via OSC escape) and shows it
    dash_term.terminate()                     # close the running dashboard
    wait_for(lambda: not sh("xdotool", "search", "--name", "^GitHub Runner Dashboard$"), 3)
    gone = not sh("xdotool", "search", "--name", "^GitHub Runner Dashboard$")
    activate(wa)
    wait_for(lambda: d.shown is not None and d.shown.title == "alpha", 2)
    fb.press("a", "long")
    ok = wait_for(lambda: d.shown is not None and d.shown.title == "GitHub Runner Dashboard"
                  and sh("xdotool", "getactivewindow") == str(d.shown.handle), 10)
    ok = ok if gone else None
    check("A-long starts dashboard when not running, then shows it", ok is not None,
          f"{ok:.1f} s" if ok else f"gone={gone} shown={d.shown and d.shown.title} h={d.shown and d.shown.handle} wd={wd} "
          f"active={sh('xdotool', 'getactivewindow')} search={sh('xdotool', 'search', '--name', '^GitHub Runner Dashboard$')}")

    # 18: disconnect / reconnect
    real_write = fb.write
    state = {"n": 0}

    def flaky(data, timeout_ms=2000):
        if state["n"] == 0:
            state["n"] = 1
            from dc32host.device import Disconnected
            fb.dev = None
            raise Disconnected("simulated unplug")
        return real_write(data, timeout_ms)
    fb.write = flaky
    d.badge.write = flaky
    activate(wg)
    ok = wait_for(lambda: state["n"] == 1 and fb.connected, 4)
    fb.write = real_write
    d.badge.write = real_write
    time.sleep(0.5)
    check("daemon recovers after USB disconnect", ok is not None and fb.screen().any())

    # 20: throughput / fps numbers seen by the daemon
    s = d.stats_all.summary() if d.stats_all.frames else d.stats.summary()
    tot = sum(n for _, n in fb.writes)
    span = fb.writes[-1][0] - fb.writes[0][0]
    check("stats", True, f"{len(fb.writes) / span:.1f} writes/s, {tot / span / 1000:.0f} kB/s avg over run; "
          f"last window fps={s['fps']:.1f} cap={s['cap_ms']:.1f}ms enc={s['enc_ms']:.1f}ms")
    return all(ok for _, ok, _ in results)


if __name__ == "__main__":
    try:
        ok = main()
    finally:
        for p in reversed(procs):
            p.terminate()
    sys.exit(0 if ok else 1)
