"""RTL-SDR source for the badge (spectrum/waterfall now; RF bench and satellite station build on it).

A background thread owns the dongle only while someone wants it (start()/stop()), reads IQ blocks,
and publishes an averaged power spectrum (dB, NBINS bins across the sample rate) plus peak hold.
Retuning is a request the thread applies between reads. Needs pyrtlsdr < 0.3 (Ubuntu's librtlsdr 0.6).
"""
from __future__ import annotations

import logging
import threading
import time

import numpy as np

log = logging.getLogger("dc32.sdr")

NBINS = 1024
RATE = 2.048e6
PRESETS = [("433.92 ISM", 433.92e6), ("315 ISM", 315.0e6), ("868 ISM", 868.3e6), ("915 ISM", 915.0e6),
           ("137 wx sats", 137.5e6), ("145.8 ISS", 145.8e6), ("1090 ADS-B", 1090e6), ("FM radio", 98.1e6)]
STEPS = [10e3, 100e3, 1e6, 10e6]


def spectrum_db(x: np.ndarray, nbins: int = NBINS) -> np.ndarray:
    """Averaged power spectrum in dB (Welch, Hann window), DC in the middle."""
    n = len(x) // nbins
    if n == 0:
        return np.full(nbins, -120.0)
    seg = x[:n * nbins].reshape(n, nbins) * np.hanning(nbins)
    p = (np.abs(np.fft.fftshift(np.fft.fft(seg, axis=1), axes=1)) ** 2).mean(axis=0)
    return 10 * np.log10(p / nbins + 1e-12)


class SdrSource:
    def __init__(self, freq=433.92e6, gain="auto"):
        self.freq, self.gain = freq, gain
        self.spec = None             # latest spectrum (dB)
        self.peak = None             # peak hold (decays)
        self.rows = []               # waterfall history, newest last
        self.status = "idle"
        self.error = None
        self._want = False
        self._retune = True
        self._lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True, name="sdr").start()

    def start(self):
        self._want = True

    def stop(self):
        self._want = False

    def tune(self, freq):
        self.freq = float(min(max(freq, 24e6), 1766e6))   # R820T range
        self._retune = True
        with self._lock:
            self.peak = None

    def _loop(self):
        dev = None
        while True:
            if not self._want:
                if dev is not None:
                    dev.close()
                    dev = None
                    self.status = "idle"
                time.sleep(0.2)
                continue
            if dev is None:
                try:
                    from rtlsdr import RtlSdr
                    dev = RtlSdr()
                    dev.sample_rate = RATE
                    dev.gain = self.gain
                    self._retune = True
                    self.status, self.error = "running", None
                except Exception as e:                       # no dongle / busy / library
                    self.status, self.error = "no SDR", str(e)[:60]
                    time.sleep(2)
                    continue
            try:
                if self._retune:
                    self._retune = False
                    dev.center_freq = self.freq
                    dev.read_samples(16 * 1024)              # settle after the PLL retunes
                x = dev.read_samples(64 * 1024)
            except Exception as e:
                log.warning("sdr read failed: %s", e)
                try:
                    dev.close()
                except Exception:
                    pass
                dev = None
                self.status, self.error = "SDR lost", str(e)[:60]
                time.sleep(1)
                continue
            s = spectrum_db(x)
            with self._lock:
                self.spec = s
                self.peak = s if self.peak is None else np.maximum(s, self.peak - 0.3)
                self.rows.append(s)
                del self.rows[:-200]

    def snapshot(self):
        with self._lock:
            return self.spec, self.peak, list(self.rows)
