"""RTL-SDR source for the badge (spectrum/waterfall now; RF bench and satellite station build on it).

A background thread owns the dongle only while someone wants it (start()/stop()), reads IQ blocks,
and publishes an averaged power spectrum (dB, NBINS bins across the sample rate) plus peak hold.
Retuning is a request the thread applies between reads. Needs pyrtlsdr < 0.3 (Ubuntu's librtlsdr 0.6).
"""
from __future__ import annotations

import logging
import queue
import threading
import time

import numpy as np

log = logging.getLogger("dc32.sdr")

NBINS = 1024
RATE = 2.048e6
# (name, frequency, audio mode) - see sdr_audio.MODES
PRESETS = [("433.92 ISM", 433.92e6, None), ("315 ISM", 315.0e6, None), ("868 ISM", 868.3e6, None),
           ("915 ISM", 915.0e6, None), ("137 wx sats", 137.5e6, "NFM"), ("145.8 ISS", 145.8e6, "NFM"),
           ("120.5 airband", 120.5e6, "AM"), ("1090 ADS-B", 1090e6, None), ("FM radio", 98.1e6, "WFM")]
OFFSET = 250e3                 # the dongle tunes this far above the listening frequency (DC spike off-signal)
OFFSET_BINS = int(round(OFFSET / RATE * 1024))
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
        self.audio_mode = None       # None | WFM | NFM | AM (sdr_audio)
        self._demod = self._player = None
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

    def set_audio(self, mode):
        self.audio_mode = mode
        self._audio_changed = True

    def _audio(self, x):
        if getattr(self, "_audio_changed", False) or (self.audio_mode is None) != (self._demod is None):
            self._audio_changed = False
            if self._player:
                self._player.close()
            self._demod = self._player = None
            if self.audio_mode:
                from .sdr_audio import Demod, Player
                self._demod, self._player = Demod(self.audio_mode, RATE, OFFSET), Player()
        if self._demod:
            self._player.write(self._demod.process(x))

    def _open(self):
        """Open the dongle and start async streaming into self._q (a reader thread in librtlsdr).
        Async matters: blocking reads with work in between dropped ~1/3 of the samples (audio skips)."""
        from rtlsdr import RtlSdr
        dev = RtlSdr()
        dev.sample_rate = RATE
        dev.gain = self.gain
        dev.center_freq = self.freq + OFFSET
        self._q = queue.Queue(maxsize=32)

        def cb(x, ctx):
            try:
                self._q.put_nowait(x)
            except queue.Full:                               # consumer stalled: drop, never block USB
                self.dropped += 1

        def reader():
            try:
                dev.read_samples_async(cb, 64 * 1024)
            except Exception as e:
                log.warning("sdr stream ended: %s", e)
            self._q.put(None)                               # wake the consumer
        threading.Thread(target=reader, daemon=True, name="sdr-usb").start()
        return dev

    def _close(self, dev):
        try:
            dev.cancel_read_async()
            time.sleep(0.1)
            dev.close()
        except Exception:
            pass

    def _loop(self):
        dev = None
        self.dropped = 0
        while True:
            if not self._want:
                if self._player:
                    self._player.close()
                    self._demod = self._player = None
                if dev is not None:
                    self._close(dev)
                    dev = None
                    self.status = "idle"
                time.sleep(0.2)
                continue
            if dev is None:
                try:
                    dev = self._open()
                    self._retune = False
                    self.status, self.error = "running", None
                except Exception as e:                       # no dongle / busy / library
                    self.status, self.error = "no SDR", str(e)[:60]
                    time.sleep(2)
                    continue
            try:
                if self._retune:
                    self._retune = False
                    dev.center_freq = self.freq + OFFSET
                    for _ in range(2):                       # let the PLL settle: skip ~64 ms of samples
                        self._q.get(timeout=2)
                x = self._q.get(timeout=2)
                if x is None:
                    raise IOError("stream stopped")
            except Exception as e:
                log.warning("sdr read failed: %s", e)
                self._close(dev)
                dev = None
                self.status, self.error = "SDR lost", str(e)[:60]
                time.sleep(1)
                continue
            self._audio(x)
            # the dongle sits OFFSET above self.freq: shift so the display is centred on self.freq
            s = np.roll(spectrum_db(x), OFFSET_BINS)
            s[:OFFSET_BINS] = np.median(s)
            with self._lock:
                self.spec = s
                self.peak = s if self.peak is None else np.maximum(s, self.peak - 0.3)
                self.rows.append(s)
                del self.rows[:-200]

    def snapshot(self):
        with self._lock:
            return self.spec, self.peak, list(self.rows)
