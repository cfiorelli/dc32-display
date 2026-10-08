"""Audio demodulators for the RTL-SDR view: wideband FM (broadcast), narrowband FM (voice, satellites,
ISS) and AM (airband). Stateful filters, so consecutive IQ blocks join without clicks; 32 kHz mono
int16 out, played through the PC (pacat -> PulseAudio/PipeWire default output).

The dongle is tuned OFFSET Hz away from the listening frequency (its DC/LO-leak spike would sit right
on the signal otherwise); the first stage mixes the wanted signal back to 0 Hz.
"""
from __future__ import annotations

import shutil
import subprocess

import numpy as np
from scipy import signal

MODES = [None, "WFM", "NFM", "AM"]
AUDIO_RATE = 32000


class _Fir:
    """Low-pass FIR + decimation by `m`, filter state kept across blocks."""

    def __init__(self, fs, cutoff, m, taps=96):
        self.b = signal.firwin(taps, cutoff, fs=fs)
        self.zi = np.zeros(taps - 1, complex)
        self.m = m

    def __call__(self, x):
        y, self.zi = signal.lfilter(self.b, 1.0, x, zi=self.zi.astype(x.dtype))
        return y[::self.m]


class Demod:
    def __init__(self, mode, fs=2.048e6, offset=250e3):
        self.mode, self.fs, self.offset = mode, fs, offset
        self.n = 0                                           # sample counter for a continuous mixer
        if mode == "WFM":
            self.ch = _Fir(fs, 110e3, 8)                     # 256 kHz, the full +-75 kHz broadcast channel
            self.audio = _Fir(fs / 8, 15e3, 8)               # -> 32 kHz
            a = np.exp(-1 / (fs / 8 * 75e-6))                # 75 us de-emphasis (US broadcast)
            self.de_b, self.de_a, self.de_zi = [1 - a], [1, -a], np.zeros(1)
        else:
            self.ch = _Fir(fs, 100e3, 8)
            self.ch2 = _Fir(fs / 8, 8e3 if mode == "NFM" else 5e3, 8)   # 32 kHz channel
        self.prev = 0j
        self.peak = 1e-3
        self.dc_zi = np.zeros(1)

    def process(self, x: np.ndarray) -> bytes:
        k = np.arange(self.n, self.n + len(x))
        self.n += len(x)
        x = x * np.exp(2j * np.pi * self.offset * k / self.fs)      # wanted signal -> 0 Hz
        y = self.ch(x)
        if self.mode == "WFM":
            d = np.angle(y * np.conj(np.concatenate(([self.prev], y[:-1]))))
            self.prev = y[-1]
            d, self.de_zi = signal.lfilter(self.de_b, self.de_a, d, zi=self.de_zi)
            a = self.audio(d.astype(complex)).real
        else:
            y = self.ch2(y)
            if self.mode == "NFM":
                a = np.angle(y * np.conj(np.concatenate(([self.prev], y[:-1]))))
                self.prev = y[-1]
            else:                                            # AM envelope through a DC blocker (~25 Hz)
                a, self.dc_zi = signal.lfilter([1, -1], [1, -0.995], np.abs(y), zi=self.dc_zi)
        # simple AGC: follow the block peak up fast, let it fall slowly
        self.peak = max(float(np.abs(a).max()), self.peak * 0.97, 1e-4)
        return (np.clip(a / self.peak * 0.7, -1, 1) * 32767).astype("<i2").tobytes()


class Player:
    """32 kHz mono s16 to the PC's default audio output."""

    def __init__(self):
        exe = shutil.which("pacat") or shutil.which("pw-play")
        args = ([exe, "--raw", "--format=s16le", f"--rate={AUDIO_RATE}", "--channels=1", "--latency-msec=200",
                 "--client-name=dc32-sdr"] if exe and exe.endswith("pacat") else
                [exe, "--format=s16", f"--rate={AUDIO_RATE}", "--channels=1", "-"])
        self.p = subprocess.Popen(args, stdin=subprocess.PIPE) if exe else None

    def write(self, pcm: bytes):
        if self.p and self.p.poll() is None:
            try:
                self.p.stdin.write(pcm)
                self.p.stdin.flush()
            except (BrokenPipeError, OSError):
                pass

    def close(self):
        if self.p:
            try:
                self.p.stdin.close()
            except OSError:
                pass
            self.p.terminate()
            try:
                self.p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.p.kill()
            self.p = None
