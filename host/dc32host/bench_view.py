"""RF bench on the badge (Ctrl+Alt+V): arm -> capture -> decode -> re-synthesise -> Flipper replay ->
recapture -> verify. A background thread runs each job; the view draws the original and replay frames
and the verdict. Uses the RTL-SDR and the Flipper CLI exclusively while a job runs.

Badge: A arm (capture the next transmission), START replay + verify, hold A self-test (the Flipper
sends a random key and the whole loop must recover it), left/right band. PC (while the view is up):
Enter arm, Space replay, Shift+Enter self-test, Left/Right band.
"""
from __future__ import annotations

import logging
import random
import threading
import time
import traceback

import numpy as np
from PIL import Image, ImageDraw

from . import capture as C
from . import rfbench as B
from .runner_view import BG, INK, INK2, MUTED, GRID, OK, WARN, BAD, HOSTED, SELF, _ttf

log = logging.getLogger("dc32.bench")

BANDS = [433.92e6, 315.0e6, 868.35e6, 915.0e6]
SUB_PATH = "/ext/subghz/dc32_bench.sub"


class Bench:
    def __init__(self):
        self.freq = BANDS[0]
        self.state = "idle"            # idle | armed | captured | replaying | pass | fail | error
        self.msg = "idle — arm to capture a Sub-GHz signal"
        self.orig = None               # rfbench.Decoded
        self.replay = None
        self.expect = None             # self-test key (hex)
        self._busy = threading.Lock()

    def band(self, step):
        if not self._busy.locked():
            self.freq = BANDS[(BANDS.index(self.freq) + step) % len(BANDS)]

    def _job(self, fn):
        if not self._busy.acquire(blocking=False):
            return
        def run():
            try:
                fn()
            except Exception as e:
                self.state, self.msg = "error", f"{type(e).__name__}: {e}"[:60]
                traceback.print_exc()
            finally:
                self._busy.release()
        threading.Thread(target=run, daemon=True, name="bench").start()

    # ---------------------------------------------------------------- jobs
    def arm(self, seconds=20):
        def go():
            self.state, self.msg, self.replay, self.expect = "armed", "listening... transmit now", None, None
            end = time.time() + seconds
            while time.time() < end:
                d = B.analyse(B.capture(self.freq, 2.5))
                if d and d.bits and d.repeats >= 2:
                    self.orig, self.state = d, "captured"
                    log.info("bench captured %s 0x%s (%d bits, te %.0f us, x%d) at %.2f MHz", d.encoding, d.hex,
                             len(d.bits), d.te_us, d.repeats, self.freq / 1e6)
                    self.msg = "captured: START to replay + verify"
                    return
            self.state, self.msg = "idle", "nothing decodable heard (fixed-code OOK only)"
        self._job(go)

    def replay_verify(self):
        def go():
            if not self.orig or self.orig.encoding != "PWM":
                self.state, self.msg = "error", "capture a PWM signal first"
                return
            from .flipper_cli import FlipperCli
            self.state, self.msg = "replaying", "re-synthesising + uploading to the Flipper"
            sub = B.sub_file(self.freq, B.synthesize(self.orig, 8)).encode()
            with FlipperCli() as fz:
                fz.run("storage mkdir /ext/subghz")
                fz.write_file(SUB_PATH, sub)
                self.msg = "Flipper transmitting, SDR verifying"
                iq = B.capture(self.freq, 4.0, during=lambda: (time.sleep(0.4), fz.tx_file(SUB_PATH, 1)))
            self.replay = B.analyse(iq)
            ok = bool(self.replay and self.replay.bits == self.orig.bits)
            if self.expect:
                ok = ok and self.orig.hex == self.expect
            err = abs(self.replay.te_us - self.orig.te_us) / self.orig.te_us * 100 if self.replay else 0
            self.state = "pass" if ok else "fail"
            self.msg = (f"bits match, timing {err:.1f}%" if ok else
                        "replay not decoded" if not self.replay else f"MISMATCH ({len(self.replay.bits)} bits)")
            log.info("bench %s: original %s 0x%s te %.0f us, replay %s; %s", self.state.upper(),
                     self.orig.encoding, self.orig.hex, self.orig.te_us,
                     f"0x{self.replay.hex} te {self.replay.te_us:.0f} us" if self.replay else "none", self.msg)
        self._job(go)

    def selftest(self):
        def go():
            from .flipper_cli import FlipperCli
            key = f"{random.randrange(1 << 24):06X}"
            self.expect, self.replay, self.state = key, None, "armed"
            self.msg = f"self-test: Flipper sends {key}"
            with FlipperCli() as fz:
                iq = B.capture(self.freq, 4.0, during=lambda: (
                    time.sleep(0.4), fz.run(f"subghz tx {key} {int(self.freq)} 350 10 0", timeout=15)))
            self.orig = B.analyse(iq)
            log.info("bench self-test: sent %s, decoded %s", key, self.orig.hex if self.orig else None)
            if not self.orig or self.orig.hex != key:
                self.state = "fail"
                self.msg = f"sent {key}, decoded {self.orig.hex if self.orig else 'nothing'}"
                return
            self.state, self.msg = "captured", f"recovered {key}: replaying..."
        self._job(go)
        # chain the replay once the capture job has finished
        def chain():
            while self._busy.locked():
                time.sleep(0.1)
            if self.state == "captured" and self.expect:
                self.replay_verify()
        threading.Thread(target=chain, daemon=True).start()


class BenchView:
    def __init__(self, bench: Bench):
        self.b = bench
        self.kb_here = False      # set by the daemon each frame: is the PC keyboard grabbed for the bench?
        self.f, self.f_s, self.f_b, self.f_big = _ttf(12), _ttf(11), _ttf(12, bold=True), _ttf(15, bold=True)

    def _frame(self, d, pulses, y, h, col):
        total = sum(p + g for p, g in pulses) or 1
        x, W = 8.0, C.OUT_W - 16
        d.line([8, y + h, 8 + W, y + h], fill=GRID)
        for p, g in pulses:
            x1 = x + W * p / total
            d.rectangle([int(x), y, max(int(x), int(x1) - 1), y + h], fill=col)
            x = x1 + W * g / total

    def _info(self, d, dec, y, label, col):
        if not dec:
            d.text((8, y), f"{label}: -", font=self.f_s, fill=MUTED)
            return
        txt = f"{label}: {dec.encoding}  te {dec.te_us:.0f} us  {len(dec.bits)} bits  0x{dec.hex}  x{dec.repeats}"
        d.text((8, y), txt, font=self.f_s, fill=col)

    def render(self) -> np.ndarray:
        b = self.b
        img = Image.new("RGB", (C.OUT_W, C.OUT_H), BG)
        d = ImageDraw.Draw(img)
        colour = {"pass": OK, "fail": BAD, "error": BAD, "armed": WARN, "replaying": WARN}.get(b.state, INK2)
        d.text((8, 4), f"RF bench  {b.freq / 1e6:.2f} MHz", font=self.f_big, fill=INK)
        st = {"pass": "PASS", "fail": "FAIL"}.get(b.state, b.state.upper())
        d.text((C.OUT_W - 8 - d.textlength(st, font=self.f_b), 6), st, font=self.f_b, fill=colour)
        d.line([0, 26, C.OUT_W, 26], fill=GRID)
        if b.orig:
            self._frame(d, b.orig.pulses, 34, 26, SELF)
        self._info(d, b.orig, 64, "captured", INK)
        if b.replay:
            self._frame(d, b.replay.pulses, 92, 26, HOSTED)
        self._info(d, b.replay, 122, "replay", INK)
        if b.orig and b.orig.bits:
            bits = b.orig.bits
            for i in range(0, min(len(bits), 72), 36):
                d.text((8, 146 + 14 * (i // 36)), " ".join(bits[j:j + 4] for j in range(i, min(i + 36, len(bits)), 4)),
                       font=self.f_s, fill=INK2)
        d.text((8, 190), b.msg[:52], font=self.f, fill=colour)
        # control hint: badge buttons always work; the PC keyboard works only after Ctrl+Alt+Y
        if getattr(self, "kb_here", False):
            hint = "Enter=arm  Space=verify  Sh+Enter=test  <>=band"
        else:
            hint = "badge: A arm  START verify  A-hold test   Ctrl+Alt+Y"
        d.text((8, 222), hint, font=self.f_s, fill=MUTED)
        return np.asarray(img).copy()
