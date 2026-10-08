"""SDR spectrum maths and the badge spectrum/waterfall view, with a synthetic signal (no dongle)."""
from pathlib import Path
import sys
import threading
import unittest
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from dc32host import sdr as S
from dc32host import sdr_view as V


class Spectrum(unittest.TestCase):
    def test_tone_lands_in_its_bin(self):
        n = 64 * 1024
        off = 300e3                                         # +300 kHz from centre
        t = np.arange(n) / S.RATE
        x = np.exp(2j * np.pi * off * t) + 0.01 * (np.random.randn(n) + 1j * np.random.randn(n))
        s = S.spectrum_db(x)
        self.assertEqual(len(s), S.NBINS)
        peak_off = (np.argmax(s) - S.NBINS / 2) / S.NBINS * S.RATE
        self.assertAlmostEqual(peak_off, off, delta=S.RATE / S.NBINS)
        self.assertGreater(s.max() - np.median(s), 30)


class View(unittest.TestCase):
    def fake(self):
        src = SimpleNamespace(freq=433.92e6, status='running', error=None, rows=[], tuned=[], audio_mode=None)
        src.set_audio = lambda m: setattr(src, 'audio_mode', m)
        src.tune = lambda f: (src.tuned.append(f), setattr(src, 'freq', f))
        spec = np.full(S.NBINS, -60.0)
        spec[700] = -10
        src.snapshot = lambda: (spec, spec, [spec] * 50)
        return src

    def test_render_and_keys(self):
        src = self.fake()
        v = V.SdrView(src)
        out = v.render()
        self.assertEqual(out.shape, (240, 320, 3))
        self.assertGreater(int(out[V.WF_Y0:V.WF_Y0 + 50].max()), 200)    # the strong bin lights up
        v.key('right')
        self.assertAlmostEqual(src.freq, 433.92e6 + S.STEPS[1])
        v.key('up'); v.key('left')
        self.assertAlmostEqual(src.freq, 433.92e6 + S.STEPS[1] - S.STEPS[2])
        v.key('preset')
        self.assertEqual(src.freq, S.PRESETS[1][1])
        v.key('audio')
        self.assertEqual(src.audio_mode, 'WFM')
        self.assertEqual(v.render().shape, (240, 320, 3))
        none = SimpleNamespace(freq=1e8, status='no SDR', error='no device', snapshot=lambda: (None, None, []))
        self.assertEqual(V.SdrView(none).render().shape, (240, 320, 3))


class Audio(unittest.TestCase):
    def tone_out(self, mode, iq):
        from dc32host import sdr_audio as A
        d = A.Demod(mode, S.RATE, S.OFFSET)
        pcm = b''.join(d.process(iq[i:i + 65536]) for i in range(0, len(iq), 65536))
        a = np.frombuffer(pcm, '<i2').astype(float)[A.AUDIO_RATE // 4:]        # skip filter warm-up
        f = np.fft.rfftfreq(len(a), 1 / A.AUDIO_RATE)
        return f[np.argmax(np.abs(np.fft.rfft(a * np.hanning(len(a)))))]

    def test_fm_nfm_am_recover_a_1khz_tone(self):
        n = 65536 * 16
        t = np.arange(n) / S.RATE
        tone = np.sin(2 * np.pi * 1000 * t)
        carrier = -S.OFFSET                                 # the listening freq sits at -OFFSET in the IQ
        for mode, dev in (('WFM', 50e3), ('NFM', 3e3)):
            phase = 2 * np.pi * carrier * t + 2 * np.pi * dev * np.cumsum(tone) / S.RATE
            self.assertAlmostEqual(self.tone_out(mode, np.exp(1j * phase)), 1000, delta=20, msg=mode)
        am = (1 + 0.5 * tone) * np.exp(2j * np.pi * carrier * t)
        self.assertAlmostEqual(self.tone_out('AM', am), 1000, delta=20)


if __name__ == '__main__':
    unittest.main()
