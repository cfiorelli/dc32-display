"""End-to-end latency test: a window changes colour (like a keystroke rendering) and we time how long
until the badge has acknowledged a frame containing the change. Badge scanout adds <=20 ms (avg 10 ms)."""
from __future__ import annotations

import statistics
import threading
import time

from . import config as CFG


def run_latency_test(samples: int = 30):
    import tkinter as tk
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
    r = sorted(state["results"])
    if not r:
        print("no samples (badge connected? window focused?)")
        return
    print(f"screen change -> badge framebuffer: n={len(r)} median {statistics.median(r):.0f} ms, "
          f"p95 {r[int(0.95 * (len(r) - 1))]:.0f} ms, min {r[0]:.0f} ms, max {r[-1]:.0f} ms")
    print(f"add LCD scanout (avg ~10 ms, max ~20 ms) for photons: median ~{statistics.median(r) + 10:.0f} ms")
