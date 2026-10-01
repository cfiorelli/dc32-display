"""dc32host command line.

    python -m dc32host run                 run the daemon (what autostart launches)
    python -m dc32host status              show badge info
    python -m dc32host list-windows        list switchable windows as the badge would see them
    python -m dc32host find-dashboard      locate the GitHub runner cost dashboard (--write to save)
    python -m dc32host bench               measure USB throughput / badge decode speed
    python -m dc32host test-pattern        draw test pattern and echo button events (acceptance test)
    python -m dc32host latency-test        measure screen-change -> badge latency end to end
    python -m dc32host bootsel             reboot the badge into the USB bootloader (for flashing)
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys
import time

from . import __version__
from . import config as CFG


def setup_logging(verbose: bool, to_file: bool = True):
    _, data = CFG.app_dirs()
    os.makedirs(os.path.join(data, "logs"), exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    if to_file:
        fh = logging.handlers.RotatingFileHandler(os.path.join(data, "logs", "host.log"), maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    if sys.stderr is not None:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)


def single_instance():
    _, data = CFG.app_dirs()
    os.makedirs(data, exist_ok=True)
    path = os.path.join(data, "dc32host.lock")
    f = open(path, "a+")
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    return f


def cmd_run(a):
    lock = single_instance()
    if lock is None:
        logging.getLogger("dc32").error("another dc32host instance is already running")
        return 1
    from .daemon import Daemon
    log = logging.getLogger("dc32")
    while True:     # at login the X display / session may not be ready yet: retry instead of dying
        try:
            d = Daemon(CFG.load())
            break
        except Exception as e:
            log.error("startup failed (%s); retrying in 5 s", e)
            time.sleep(5)
    d.run()
    return 0


def _open_badge():
    from .device import Badge
    b = Badge()
    if not b.open():
        print("badge not found / not responding (is the daemon running? stop it first)")
        sys.exit(2)
    return b


def cmd_status(a):
    b = _open_badge()
    print(f"badge fw {b.info.fw}, protocol {b.info.proto}, {b.info.width}x{b.info.height}, {b.info.buttons} buttons")
    b.close()


def cmd_list_windows(a):
    from .daemon import Daemon
    d = Daemon(CFG.load())
    fg = d.be.foreground()
    for w in d.be.list_windows():
        flag = "*" if fg and w.handle == fg.handle else " "
        ex = " (excluded)" if d.excluded(w) else ""
        print(f"{flag} {w.handle:#10x} pid={w.pid:<7} {w.app:<28} {w.title}{ex}")


def cmd_find_dashboard(a):
    from .dashboard import find_candidates, write_favorite
    cands = find_candidates()
    if not cands:
        print("no runner dashboard candidates found")
        return 1
    for i, c in enumerate(cands):
        print(f"[{i}] score={c['score']:3d} {c['kind']:8s} {c['desc']}")
    if a.write:
        write_favorite(cands[a.pick])
        print(f"wrote favorite from candidate [{a.pick}] to {CFG.config_path()}")
    return 0


def cmd_bench(a):
    import numpy as np
    from . import encoder as E
    from . import protocol as P
    b = _open_badge()
    rng = np.random.default_rng(0)
    frame = rng.integers(0, 65536, (240, 320)).astype(np.uint16)
    raw = b"".join(E.encode_rect(frame, 0, y, 320, 16) for y in range(0, 240, 16))
    n, t0, total, dec_us = 0, time.time(), 0, 0
    acks = []
    while time.time() - t0 < a.seconds:
        n += 1
        b.write(raw + P.frame_end(n))
        total += len(raw) + 12
        while True:      # strictly one frame in flight: measures throughput + decode, no queueing
            ev = b.events.get(timeout=2)
            if isinstance(ev, P.Ack) and ev.frame_id == n:
                dec_us += ev.decode_us
                acks.append(time.time())
                break
    dt = time.time() - t0
    print(f"full raw frames: {n / dt:.1f} fps, {total / dt / 1000:.0f} kB/s host->badge, "
          f"badge decode {dec_us / n / 1000:.1f} ms/frame")
    # small-update round trip
    rtts = []
    for i in range(200):
        x = (i * 8) % 312
        msg = E.encode_rect(frame, x, 100, 8, 16)
        t = time.time()
        b.write(msg + P.frame_end(100000 + i))
        while True:
            ev = b.events.get(timeout=2)
            if isinstance(ev, P.Ack) and ev.frame_id == 100000 + i:
                rtts.append((time.time() - t) * 1000)
                break
    rtts.sort()
    print(f"keystroke-sized update round trip: median {rtts[len(rtts) // 2]:.2f} ms, p95 {rtts[int(len(rtts) * .95)]:.2f} ms")
    b.close()


def cmd_test_pattern(a):
    import numpy as np
    from . import capture as C
    from . import encoder as E
    from . import protocol as P
    b = _open_badge()
    img = np.zeros((240, 320, 3), np.uint8)
    bars = [(255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0), (255, 0, 255), (255, 0, 0), (0, 0, 255), (0, 0, 0)]
    for i, c in enumerate(bars):
        img[0:120, i * 40:(i + 1) * 40] = c
    img[120:240] = np.linspace(0, 255, 320, dtype=np.uint8)[None, :, None]
    img = C.draw_lines(img, ["TEST PATTERN: top-left = (0,0)", "press every badge button; Ctrl+C to quit"])
    img[0:4, 0:4] = (255, 0, 0)
    b.write(b"".join(E.encode_frame(E.rgb_to_565(img), None)) + P.frame_end(1))
    seen = set()
    print("press each button (short and long). Ctrl+C to stop.")
    try:
        while True:
            try:
                ev = b.events.get(timeout=1)
            except Exception:
                b.write(P.ping(0))
                continue
            if isinstance(ev, P.ButtonEvent):
                seen.add(ev.button)
                print(f"{ev.event:6s} {ev.button:7s} held={ev.held} t={ev.t_ms} ms dur={ev.held_ms} ms   "
                      f"[{len(seen)}/9 buttons seen: {sorted(seen)}]")
    except KeyboardInterrupt:
        pass
    b.close()


def cmd_latency(a):
    from .latency import run_latency_test
    run_latency_test(a.samples)


def cmd_bootsel(a):
    from . import protocol as P
    b = _open_badge()
    b.write(P.reboot(bootsel=True))
    time.sleep(0.3)
    b.close()
    print("badge rebooting into BOOTSEL (RP2350 USB bootloader)")


def cmd_ctl(a):
    """Send an action to the running daemon, e.g. `dc32host ctl toggle_runner` (for keyboard shortcuts)."""
    import socket
    from .daemon import control_socket_path
    path = control_socket_path()
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        s.sendto(a.action.encode(), path)
    except OSError as e:
        print(f"dc32host is not running ({e})", file=sys.stderr)
        return 1
    return 0


def _supervisor_running():
    try:
        import psutil
    except ImportError:
        return False
    for p in psutil.process_iter(["cmdline"]):
        if any(str(c).endswith("dc32-display/run.sh") for c in (p.info["cmdline"] or [])):
            return True
    return False


def cmd_restart(a):
    """Restart the display service (or start it if it isn't running at all)."""
    if _supervisor_running():
        a.action = "restart"
        cmd_ctl(a)
        print("restarting dc32host (back in a few seconds)")
        return 0
    import subprocess
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    run_sh = os.path.join(data, "dc32-display", "run.sh")
    if not os.path.exists(run_sh):
        print(f"{run_sh} not found: run host/install_linux.sh", file=sys.stderr)
        return 1
    subprocess.Popen([run_sh], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("started dc32host")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dc32host", description=f"DC32 Display host {__version__}")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("run")
    sub.add_parser("status")
    sub.add_parser("list-windows")
    fd = sub.add_parser("find-dashboard")
    fd.add_argument("--write", action="store_true")
    fd.add_argument("--pick", type=int, default=0)
    be = sub.add_parser("bench")
    be.add_argument("--seconds", type=float, default=5)
    sub.add_parser("test-pattern")
    lt = sub.add_parser("latency-test")
    lt.add_argument("--samples", type=int, default=30)
    sub.add_parser("bootsel")
    ct = sub.add_parser("ctl", help="send an action to the running daemon (toggle_runner, home_menu, ...)")
    ct.add_argument("action")
    sub.add_parser("restart", help="restart the display service, or start it if it is not running")
    a = ap.parse_args(argv)
    cmd = a.cmd or "run"
    if cmd == "ctl":
        return cmd_ctl(a)
    if cmd == "restart":
        return cmd_restart(a)
    setup_logging(a.verbose, to_file=(cmd == "run"))
    return {"run": cmd_run, "status": cmd_status, "list-windows": cmd_list_windows, "find-dashboard": cmd_find_dashboard,
            "bench": cmd_bench, "test-pattern": cmd_test_pattern, "latency-test": cmd_latency,
            "bootsel": cmd_bootsel}[cmd](a) or 0


if __name__ == "__main__":
    sys.exit(main())
