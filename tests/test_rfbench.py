"""RF bench signal chain on synthetic IQ (no SDR, no Flipper): OOK slicing, encoding inference,
re-synthesis and the Flipper RAW .sub format."""
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from dc32host import rfbench as B
from dc32host import sdr as S


def ook_iq(raw_us, snr_db=20, lead_us=20000, seed=1):
    """IQ at S.RATE for a +on/-off microsecond sequence, carrier at the listening frequency (-OFFSET)."""
    rng = np.random.default_rng(seed)
    env = [np.zeros(int(lead_us * S.RATE / 1e6))]
    for v in raw_us:
        env.append(np.full(int(abs(v) * S.RATE / 1e6), 1.0 if v > 0 else 0.0))
    env.append(np.zeros(int(lead_us * S.RATE / 1e6)))
    env = np.concatenate(env)
    t = np.arange(len(env)) / S.RATE
    noise = 10 ** (-snr_db / 20) * (rng.standard_normal(len(env)) + 1j * rng.standard_normal(len(env))) / np.sqrt(2)
    return (env * np.exp(-2j * np.pi * S.OFFSET * t) + noise).astype(np.complex64)


def princeton(key_bits, te=350, repeats=4):
    d = B.Decoded("PWM", te, key_bits)
    return B.synthesize(d, repeats)


class Chain(unittest.TestCase):
    def test_princeton_key_recovered_and_resynthesised(self):
        bits = format(0xABC123, '024b')
        d = B.analyse(ook_iq(princeton(bits)))
        self.assertIsNotNone(d)
        self.assertEqual((d.encoding, d.bits, d.hex, d.note), ('PWM', bits, 'ABC123', 'sync pulse'))
        self.assertAlmostEqual(d.te_us, 350, delta=15)
        self.assertGreaterEqual(d.repeats, 3)
        again = B.analyse(ook_iq(B.synthesize(d, 4), seed=2))             # replay of the re-synthesis
        self.assertEqual(again.bits, bits)

    def test_pulse_distance(self):
        bits = '1100101011110000'
        raw = []
        for _ in range(4):
            for b in bits:
                raw += [500, -1500 if b == '1' else -500]
            raw += [500, -12000]
        d = B.analyse(ook_iq(raw))
        self.assertEqual((d.encoding, d.bits), ('pulse-distance', bits))

    def test_noise_only_and_sub_format(self):
        rng = np.random.default_rng(0)
        noise = (rng.standard_normal(200000) + 1j * rng.standard_normal(200000)).astype(np.complex64)
        self.assertIsNone(B.analyse(noise))
        sub = B.sub_file(433.92e6, list(range(1, 600)))
        lines = sub.splitlines()
        self.assertEqual(lines[:5], ['Filetype: Flipper SubGhz RAW File', 'Version: 1', 'Frequency: 433920000',
                                     'Preset: FuriHalSubGhzPresetOok650Async', 'Protocol: RAW'])
        self.assertEqual([len(l.split()) - 1 for l in lines[5:]], [512, 87])   # <= 512 values per line


class View(unittest.TestCase):
    def test_render_states(self):
        from dc32host import bench_view as BV
        b = BV.Bench()
        v = BV.BenchView(b)
        self.assertEqual(v.render().shape, (240, 320, 3))
        b.orig = B.analyse(ook_iq(princeton(format(0x123456, '024b'))))
        b.replay, b.state, b.msg = b.orig, 'pass', 'bits match, timing 0.4%'
        self.assertEqual(v.render().shape, (240, 320, 3))
        b.band(1)
        self.assertEqual(b.freq, 315.0e6)



    def test_hint_states_and_no_duplicate(self):
        from unittest.mock import patch
        from PIL import ImageDraw
        from dc32host import bench_view as BV
        b = BV.Bench(); v = BV.BenchView(b)
        def texts(kb):
            v.kb_here = kb; seen = []
            orig = ImageDraw.ImageDraw.text
            def rec(self, xy, t, *a, **k):
                seen.append(t); return orig(self, xy, t, *a, **k)
            with patch.object(ImageDraw.ImageDraw, 'text', rec):
                v.render()
            return seen
        pc = texts(False)
        self.assertTrue(any('Ctrl+Alt+Y' in t for t in pc))
        self.assertTrue(any(t.startswith('idle') for t in pc))              # status line, not a hint repeat
        self.assertFalse(any('A arm' in t and 'START' in t for t in [b.msg]))  # default msg no longer = hint
        here = texts(True)
        self.assertTrue(any('Enter=arm' in t for t in here))

if __name__ == '__main__':
    unittest.main()
