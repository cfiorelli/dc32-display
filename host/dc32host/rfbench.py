"""RF bench: capture an unknown Sub-GHz burst with the RTL-SDR, recover its on/off timing, infer the
encoding, decode the bits, re-synthesise a clean waveform as a Flipper RAW .sub, and verify a replay
against the original.

Signal path: IQ (2.048 MS/s, dongle offset-tuned) -> mix to 0 Hz -> 250 kHz channel -> envelope ->
adaptive two-level slicer with hysteresis -> run lengths (us) -> frames (split at long gaps) ->
encoding inference (PWM / pulse-distance / Manchester) -> bits.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
from scipy import signal

from .sdr import OFFSET, RATE

CH_RATE = RATE / 8                    # 256 kHz after the channel filter: 3.9 us timing resolution
US = 1e6 / CH_RATE


def capture(freq, seconds, during=None, gain=30):
    """Record `seconds` of IQ at `freq` (dongle at freq + OFFSET). `during()` runs once streaming has
    started (e.g. tell the Flipper to transmit). Returns complex64 IQ at RATE."""
    from rtlsdr import RtlSdr
    import threading
    for attempt in range(30):                             # another view may still be releasing it
        try:
            dev = RtlSdr()
            break
        except Exception:
            if attempt == 29:
                raise
            time.sleep(0.1)
    try:
        dev.sample_rate = RATE
        dev.center_freq = freq + OFFSET
        dev.gain = gain
        dev.read_samples(32 * 1024)                       # settle
        chunks, n_want = [], int(seconds * RATE)
        started = threading.Event()
        if during:
            threading.Thread(target=lambda: (started.wait(), during()), daemon=True).start()
        got = 0
        while got < n_want:
            x = dev.read_samples(256 * 1024)
            chunks.append(x.astype(np.complex64))
            got += len(x)
            started.set()
        return np.concatenate(chunks)[:n_want]
    finally:
        dev.close()


def envelope(iq):
    """Mix the listening frequency to 0 Hz, keep +-100 kHz, decimate to CH_RATE, magnitude."""
    k = np.arange(len(iq))
    x = iq * np.exp(2j * np.pi * OFFSET * k / RATE).astype(np.complex64)
    b = signal.firwin(129, 100e3, fs=RATE)
    y = signal.lfilter(b, 1.0, x)[::8]
    env = np.abs(y)
    return signal.medfilt(env, 3)                         # kill single-sample spikes


def slice_runs(env, min_us=40):
    """Two-level slicing with hysteresis. Returns [(level, duration_us)], level 1 = carrier on."""
    noise = np.percentile(env, 50)
    hi = np.percentile(env, 99.5)
    if hi < noise * 4:                                    # nothing clearly above the noise
        return []
    on_t, off_t = noise + 0.5 * (hi - noise), noise + 0.3 * (hi - noise)
    state, out, start = 0, [], 0
    for i, v in enumerate(env):
        if state == 0 and v > on_t or state == 1 and v < off_t:
            out.append((state, (i - start) * US))
            state, start = 1 - state, i
    out.append((state, (len(env) - start) * US))
    # merge glitches shorter than min_us into their neighbours
    merged = []
    for lvl, d in out:
        if merged and (d < min_us or merged[-1][0] == lvl):
            merged[-1] = (merged[-1][0], merged[-1][1] + d)
        else:
            merged.append((lvl, d))
    return merged


def frames(runs, gap_us=None):
    """Split into frames at long gaps. Each frame: list of (pulse_us, gap_us); last gap = the split gap."""
    pairs, cur = [], None
    for lvl, d in runs:
        if lvl == 1:
            cur = [d, 0.0]
            pairs.append(cur)
        elif cur is not None:
            cur[1] = d
    if not pairs:
        return []
    gaps = np.array([g for _, g in pairs[:-1]] or [0])
    split = gap_us or max(3000.0, 6 * float(np.median(gaps)) if len(gaps) else 3000.0)
    out, f = [], []
    for p in pairs:
        f.append((p[0], p[1]))
        if p[1] >= split or p is pairs[-1]:
            if len(f) >= 8:
                out.append(f)
            f = []
    return out


def _two_clusters(v):
    """1-D 2-means. Returns (low_centre, high_centre, labels) or None if not clearly bimodal."""
    v = np.asarray(v, float)
    lo, hi = np.percentile(v, 10), np.percentile(v, 90)
    if hi < lo * 1.6:
        return None
    for _ in range(10):
        lab = np.abs(v - hi) < np.abs(v - lo)
        if lab.all() or (~lab).all():
            return None
        lo, hi = v[~lab].mean(), v[lab].mean()
    return lo, hi, lab


@dataclass
class Decoded:
    encoding: str                       # "PWM", "pulse-distance", "Manchester", "unknown"
    te_us: float                        # base timing element
    bits: str                           # "0101..."
    pulses: list = field(default_factory=list)          # [(pulse_us, gap_us)] of the frame used
    repeats: int = 1                    # identical frames seen
    note: str = ""

    @property
    def hex(self):
        if not self.bits:
            return ""
        return f"{int(self.bits, 2):0{(len(self.bits) + 3) // 4}X}"


def decode_frame(f):
    """Infer the encoding of one frame [(pulse, gap)] and decode it. The last pair carries the sync gap."""
    body = f[:-1] if len(f) > 8 else f
    pw = [p for p, _ in body]
    gw = [g for _, g in body]
    cp = _two_clusters(pw + [f[-1][0]])
    cg = _two_clusters(gw)
    if cp and cg and abs(cp[0] - cg[0]) / cg[0] < 0.35 and abs(cp[1] - cg[1]) / cg[1] < 0.35:
        # short/long pulses whose gaps are the complement: PWM (Princeton/EV1527 style), 1 = long pulse
        bits = "".join("1" if p > (cp[0] + cp[1]) / 2 else "0" for p, _ in f)
        note = ""
        if f[-1][1] > 8 * cp[1] and f[-1][0] < (cp[0] + cp[1]) / 2:
            bits, note = bits[:-1], "sync pulse"          # Princeton/EV1527: short pulse + ~31 te gap
        return Decoded("PWM", cp[0], bits, list(f), note=note)
    if not cp and cg:
        # constant pulse, two gap lengths: pulse-distance (NEC-style), 1 = long gap
        bits = "".join("1" if g > (cg[0] + cg[1]) / 2 else "0" for _, g in body)
        return Decoded("pulse-distance", float(np.median(pw)), bits, list(f))
    if cp and cg and cp[1] / cp[0] < 2.4 and cg[1] / cg[0] < 2.4:
        # durations in {T, 2T}: Manchester. Expand to half-bit levels and read pairs (10 = 1, 01 = 0)
        te = min(cp[0], cg[0])
        half = []
        for p, g in body:
            half += [1] * int(round(p / te)) + [0] * int(round(g / te))
        bits = "".join("1" if half[i:i + 2] == [1, 0] else "0" if half[i:i + 2] == [0, 1] else "?"
                       for i in range(0, len(half) - 1, 2))
        return Decoded("Manchester", te, bits, list(f))
    return Decoded("unknown", float(np.median(pw)), "", list(f), note="no two-level timing structure")


def analyse(iq):
    """Full chain on a capture. Returns the best Decoded (most-repeated frame) or None."""
    runs = slice_runs(envelope(iq))
    fs = frames(runs)
    if not fs:
        return None
    decs = [decode_frame(f) for f in fs]
    counts = {}
    for d in decs:
        counts[d.bits] = counts.get(d.bits, 0) + 1
    best = max(decs, key=lambda d: (counts[d.bits], len(d.bits)))
    best.repeats = counts[best.bits]
    return best


def synthesize(d: Decoded, repeats=5):
    """Ideal timings for the decoded bits (re-synthesis, not a copy of the noisy capture). PWM only for
    now: 0 = te on / 3te off, 1 = 3te on / te off, then the sync: te on / 31te off (Princeton/EV1527)."""
    te = d.te_us
    seq = []
    for _ in range(repeats):
        for b in d.bits:
            seq += [3 * te, -te] if b == "1" else [te, -3 * te]
        seq += [te, -31 * te]
    return [int(round(v)) for v in seq]


def sub_file(freq, raw, preset="FuriHalSubGhzPresetOok650Async"):
    """Flipper SubGhz RAW file text (RAW_Data lines of <= 512 values)."""
    lines = ["Filetype: Flipper SubGhz RAW File", "Version: 1", f"Frequency: {int(freq)}",
             f"Preset: {preset}", "Protocol: RAW"]
    for i in range(0, len(raw), 512):
        lines.append("RAW_Data: " + " ".join(str(v) for v in raw[i:i + 512]))
    return "\n".join(lines) + "\n"
