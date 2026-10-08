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
        src = SimpleNamespace(freq=433.92e6, status='running', error=None, rows=[], tuned=[])
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
        none = SimpleNamespace(freq=1e8, status='no SDR', error='no device', snapshot=lambda: (None, None, []))
        self.assertEqual(V.SdrView(none).render().shape, (240, 320, 3))


if __name__ == '__main__':
    unittest.main()
