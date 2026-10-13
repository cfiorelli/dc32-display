"""LED brightness: percent scaling of every LED frame, the 100/50/25/10 step and an explicit percent."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from dc32host import daemon as D


def stub(pct=None):
    d = D.Daemon.__new__(D.Daemon)
    d.cfg = {} if pct is None else {'led_brightness_pct': pct}
    d.saved, d.said = {}, []
    d._save_pref = lambda k, v: d.saved.__setitem__(k, v)
    d.say = lambda m, *a, **k: d.said.append(m)
    d._leds_last = 'x'
    return d


class LedBrightness(unittest.TestCase):
    def test_default_is_25(self):
        self.assertEqual(D.led_pct({}), 25)
        self.assertEqual(D.led_pct({'led_brightness_pct': 'junk'}), 25)
        self.assertEqual(D.led_pct({'led_brightness_pct': 0}), 1)

    def test_step_cycles_100_50_25_10(self):
        d = stub()
        d.cfg['led_brightness_pct'] = 100
        seen = []
        for _ in range(5):
            D.Daemon.action(d, 'led_brightness')
            seen.append(d.cfg['led_brightness_pct'])
        self.assertEqual(seen, [50, 25, 10, 100, 50])
        self.assertEqual(d.saved['led_brightness_pct'], 50)
        self.assertIsNone(d._leds_last)          # forces the next frame to be re-sent

    def test_explicit_percent(self):
        d = stub()
        D.Daemon.action(d, 'led_brightness:50')
        self.assertEqual(d.cfg['led_brightness_pct'], 50)
        D.Daemon.action(d, 'led_brightness:500')
        self.assertEqual(d.cfg['led_brightness_pct'], 100)
        D.Daemon.action(d, 'led_brightness:abc')
        self.assertEqual(d.cfg['led_brightness_pct'], 100)
        self.assertIn('not a percent', d.said[-1])


if __name__ == '__main__':
    unittest.main()
