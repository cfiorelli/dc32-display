"""End-to-end latency test: a window changes colour (like a keystroke rendering) and we time how long
until the badge has acknowledged a frame containing the change. Badge scanout adds <=20 ms (avg 10 ms)."""
from __future__ import annotations

import statistics
import threading
import time

from . import config as CFG


def run_latency_test(samples: int = 30):
    from .daemon import Daemon

    cfg = CFG.load()
    cfg["mode"], cfg["zoom"] = "follow", "fit"
    d = Daemon(cfg)
    state = {"t0": None, "color": None, "frame": None, "results": []}
    colors = [(255, 0, 0), (0, 0, 255)]

    def sent(fid, rgb, t_cap):
        if state["t0"] is None or state["frame"] is not None:
            return
        c = tuple(int(v) for v in rgb[120, 160])
        if c == state["color"]:
            state["frame"] = fid

    def acked(fid, t):
        if state["frame"] is not None and fid == state["frame"]:
            state["results"].append((t - state["t0"]) * 1000)
            state["t0"], state["frame"] = None, None

    d.on_frame_sent, d.on_frame_acked = sent, acked
    th = threading.Thread(target=d.run, daemon=True)
    th.start()

    try:
        import tkinter as tk
    except ImportError:          # Ubuntu without python3-tk: drive a plain X11 window instead
        _xlib_window(state, colors, samples)
        _report(state["results"])
        return

    root = tk.Tk()
    root.title("dc32 latency test")
    root.geometry("800x600+100+100")
    cv = tk.Canvas(root, highlightthickness=0, bg="black")
    cv.pack(fill="both", expand=True)
    root.lift()
    root.focus_force()
    i = {"n": 0}

    def step():
        if len(state["results"]) >= samples:
            root.destroy()
            return
        if state["t0"] is None or time.time() - state["t0"] > 2:
            col = colors[i["n"] % 2]
            i["n"] += 1
            cv.configure(bg="#%02x%02x%02x" % col)
            root.update_idletasks()
            state["color"], state["frame"], state["t0"] = col, None, time.time()
        root.after(400 + (i["n"] * 37) % 150, step)   # jitter so we don't phase-lock with polling

    root.after(3000, step)    # let the badge connect first
    root.mainloop()
    _report(state["results"])


def _xlib_window(state, colors, samples):
    from Xlib import X, display as xdisplay
    dpy = xdisplay.Display()
    scr = dpy.screen()
    pix = [scr.default_colormap.alloc_color(r * 257, g * 257, b * 257).pixel for r, g, b in colors]
    win = scr.root.create_window(100, 100, 800, 600, 0, scr.root_depth, X.InputOutput, X.CopyFromParent,
                                 background_pixel=scr.black_pixel, event_mask=X.ExposureMask)
    win.set_wm_name("dc32 latency test")
    win.map()
    dpy.flush()
    time.sleep(0.5)
    win.set_input_focus(X.RevertToParent, X.CurrentTime)
    win.configure(stack_mode=X.Above)
    dpy.flush()
    time.sleep(3)                # let the badge connect first
    n = 0
    deadline = time.time() + samples * 3 + 30
    while len(state["results"]) < samples and time.time() < deadline:
        if state["t0"] is None or time.time() - state["t0"] > 2:
            win.change_attributes(background_pixel=pix[n % 2])
            win.clear_area()
            dpy.sync()
            state["color"], state["frame"], state["t0"] = colors[n % 2], None, time.time()
            n += 1
        time.sleep((400 + (n * 37) % 150) / 1000)   # jitter so we don't phase-lock with polling
    win.destroy()
    dpy.flush()


def _report(results):
    r = sorted(results)
    if not r:
        print("no samples (badge connected? window focused?)")
        return
    print(f"screen change -> badge framebuffer: n={len(r)} median {statistics.median(r):.0f} ms, "
          f"p95 {r[int(0.95 * (len(r) - 1))]:.0f} ms, min {r[0]:.0f} ms, max {r[-1]:.0f} ms")
    print(f"add LCD scanout (avg ~10 ms, max ~20 ms) for photons: median ~{statistics.median(r) + 10:.0f} ms")
