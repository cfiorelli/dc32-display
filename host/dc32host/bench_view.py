"""RF bench on the badge (Ctrl+Alt+V): arm -> capture -> decode -> re-synthesise -> Flipper replay ->
recapture -> verify. A background thread runs each job; the view draws the original and replay frames
and the verdict. While the view is up the SDR runs live (spectrum + waterfall of the band, so you see
the noise floor and any transmission); a job takes the dongle over and keeps feeding the same waterfall.

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
from .sdr import NBINS, RATE, SdrSource
from .sdr_view import PAL
from .runner_view import BG, INK, INK2, MUTED, GRID, OK, WARN, BAD, HOSTED, SELF, _ttf, kb_pill

log = logging.getLogger("dc32.bench")

BANDS = [433.92e6, 315.0e6, 868.35e6, 915.0e6]
SUB_PATH = "/ext/subghz/dc32_bench.sub"
ZOOM_BINS = 100                # live view: +-100 bins = +-200 kHz around the band (the DC spike sits at +250)


class Bench:
    def __init__(self):
        self.freq = BANDS[0]
        self.state = "idle"            # idle | armed | captured | replaying | pass | fail | error
        self.msg = "idle — arm to capture a Sub-GHz signal"
        self.orig = None               # rfbench.Decoded
        self.replay = None
        self.expect = None             # self-test key (hex)
        self._busy = threading.Lock()
        self.live = SdrSource(freq=self.freq, gain=30)    # same gain as the captures: levels don't jump
        self.visible = False

    def show(self, on):
        """The view opened/closed: run the live spectrum only while it is on screen."""
        self.visible = on
        self._live_update()

    def _live_update(self):
        if self.visible and not self._busy.locked():
            self.live.start()
        else:
            self.live.stop()

    def band(self, step):
        if not self._busy.locked():
            self.freq = BANDS[(BANDS.index(self.freq) + step) % len(BANDS)]
            self.live.tune(self.freq)
            with self.live._lock:
                self.live.rows.clear()                    # the old band's waterfall would mislead

    def _capture(self, seconds, during=None):
        return B.capture(self.freq, seconds, during=during, on_chunk=self.live.push)

    def _job(self, fn):
        if not self._busy.acquire(blocking=False):
            self.msg = "busy — wait for the current step to finish"   # feedback, not a silent drop
            return
        def run():
            try:
                self.live.release()                      # the capture needs the dongle to itself
                fn()
            except Exception as e:
                self.state, self.msg = "error", f"{type(e).__name__}: {e}"[:60]
                traceback.print_exc()
            finally:
                self._busy.release()
                self._live_update()
        threading.Thread(target=run, daemon=True, name="bench").start()

    # ---------------------------------------------------------------- jobs
    def arm(self, seconds=20):
        def go():
            self.state, self.msg, self.replay, self.expect = "armed", "listening... transmit now", None, None
            end = time.time() + seconds
            while time.time() < end:
                d = B.analyse(self._capture(2.5))
                if d and d.bits and d.repeats >= 2:
                    self.orig, self.state = d, "captured"
                    log.info("bench captured %s 0x%s (%d bits, te %.0f us, x%d) at %.2f MHz", d.encoding, d.hex,
                             len(d.bits), d.te_us, d.repeats, self.freq / 1e6)
                    self.msg = "captured: START to replay + verify"
                    return
            self.state, self.msg = "idle", "nothing decodable heard — arm and transmit, or self-test"
            log.info("bench arm: nothing decodable at %.2f MHz", self.freq / 1e6)
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
                iq = self._capture(4.0, during=lambda: (time.sleep(0.4), fz.tx_file(SUB_PATH, 1)))
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
                iq = self._capture(4.0, during=lambda: (
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

    SPEC_Y0, SPEC_Y1, WF_Y0, WF_Y1 = 29, 52, 54, 92

    def _live(self, out):
        """Spectrum trace + waterfall of the band (+-200 kHz) into the pixel array; returns the overlay
        drawing to do once the image exists (trace, markers, level)."""
        spec, peak, rows = self.b.live.snapshot()
        W = C.OUT_W - 16
        cols = (NBINS // 2 - ZOOM_BINS + np.arange(W) * (2 * ZOOM_BINS) / W).astype(int)
        h = self.WF_Y1 - self.WF_Y0
        if rows:
            wf = np.array(rows[-h:][::-1])[:, cols]
            lo = np.percentile(wf, 5)
            hi = max(np.percentile(wf, 99.7) + 3, lo + 10)   # noise shows as speckle, not full scale
            out[self.WF_Y0:self.WF_Y0 + len(wf), 8:8 + W] = PAL[np.clip((wf - lo) / (hi - lo) * 255, 0, 255).astype(np.uint8)]

        def overlay(d):
            y0, y1 = self.SPEC_Y0, self.SPEC_Y1
            band = int(100e3 / RATE * NBINS * W / (2 * ZOOM_BINS))   # the bench's +-100 kHz channel
            for x in (8 + W // 2 - band, 8 + W // 2 + band):
                d.line([x, y0, x, self.WF_Y1], fill=GRID)
            if spec is None:
                busy = self.b._busy.locked()
                msg = "starting capture..." if busy else f"SDR: {self.b.live.error or self.b.live.status}"
                d.text((8, y0 + 12), msg, font=self.f_s, fill=INK2)
                return
            s, p = spec[cols], (peak if peak is not None else spec)[cols]
            floor = float(np.median(spec))
            lo, hi = floor - 4, max(float(p.max()) + 3, floor + 30)
            ys = lambda a: (y1 - np.clip((a - lo) / (hi - lo), 0, 1) * (y1 - y0)).astype(int).tolist()
            d.line(list(zip(range(8, 8 + W), ys(p))), fill=MUTED)
            d.line(list(zip(range(8, 8 + W), ys(s))), fill=OK)
            inband = float(spec[NBINS // 2 - 50:NBINS // 2 + 50].max()) - floor
            lvl = f"{inband:+.0f} dB"
            d.text((C.OUT_W - 8 - d.textlength(lvl, font=self.f_s), y0 - 1), lvl, font=self.f_s,
                   fill=WARN if inband > 15 else MUTED)
        return overlay

    def render(self) -> np.ndarray:
        b = self.b
        out = np.zeros((C.OUT_H, C.OUT_W, 3), np.uint8)
        out[:] = BG
        overlay = self._live(out)
        img = Image.fromarray(out)
        d = ImageDraw.Draw(img)
        overlay(d)
        colour = {"pass": OK, "fail": BAD, "error": BAD, "armed": WARN, "replaying": WARN}.get(b.state, INK2)
        pill_left = kb_pill(d, getattr(self, "kb_here", False))
        live = b.state == "idle" and b.live.spec is not None and b.live.status == "running"
        st = "LIVE" if live else {"pass": "PASS", "fail": "FAIL"}.get(b.state, b.state.upper())
        st_x = pill_left - 8 - d.textlength(st, font=self.f_b)
        d.text((st_x, 6), st, font=self.f_b, fill=OK if live else colour)
        title = f"RF bench  {b.freq / 1e6:.2f} MHz"
        for t in (title, f"Bench {b.freq / 1e6:.2f}", f"{b.freq / 1e6:.2f}"):   # make room for long states
            if 8 + d.textlength(t, font=self.f_big) <= st_x - 6:
                break
        d.text((8, 4), t, font=self.f_big, fill=INK)
        d.line([0, 26, C.OUT_W, 26], fill=GRID)
        if b.orig:
            self._frame(d, b.orig.pulses, 98, 16, SELF)
        self._info(d, b.orig, 116, "captured", INK)
        if b.replay:
            self._frame(d, b.replay.pulses, 134, 16, HOSTED)
        self._info(d, b.replay, 152, "replay", INK)
        if b.orig and b.orig.bits:
            bits = b.orig.bits
            for i in range(0, min(len(bits), 72), 36):
                d.text((8, 168 + 13 * (i // 36)), " ".join(bits[j:j + 4] for j in range(i, min(i + 36, len(bits)), 4)),
                       font=self.f_s, fill=INK2)
        d.text((8, 199), b.msg[:52], font=self.f, fill=colour)
        # control hint: badge buttons always work; the PC keyboard works only after Ctrl+Alt+Y
        if getattr(self, "kb_here", False):
            hint = "Enter=arm  Space=verify  Sh+Enter=test  <>=band"
        else:
            hint = "badge: A arm  START verify  A-hold test   Ctrl+Alt+Y"
        d.text((8, 222), hint, font=self.f_s, fill=MUTED)
        return np.asarray(img).copy()
