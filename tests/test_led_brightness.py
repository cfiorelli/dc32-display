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

    def test_step_cycles_down_through_every_level(self):
        d = stub()
        d.cfg['led_brightness_pct'] = 100
        seen = []
        for _ in range(5):
            D.Daemon.action(d, 'led_brightness')
            seen.append(d.cfg['led_brightness_pct'])
        self.assertEqual(seen, [50, 25, 10, 5, 2])
        self.assertEqual(d.saved['led_brightness_pct'], 2)        # the last step is what gets saved
        self.assertIsNone(d._leds_last)          # forces the next frame to be re-sent

    def test_up_down_step_and_stop_at_ends(self):
        d = stub(25)
        seen = []
        for a in ['led_brightness_up'] * 3 + ['led_brightness_down'] * 8:
            D.Daemon.action(d, a)
            seen.append(d.cfg['led_brightness_pct'])
        self.assertEqual(seen, [50, 100, 100, 50, 25, 10, 5, 2, 1, 1, 1])

    def test_fn_up_down_drive_the_lights(self):
        from dc32host import config as CF
        self.assertEqual(CF.DEFAULTS['buttons']['fn+up.short'], 'led_brightness_up')
        self.assertEqual(CF.DEFAULTS['buttons']['fn+down.short'], 'led_brightness_down')

    def test_explicit_percent(self):
        d = stub()
        D.Daemon.action(d, 'led_brightness:50')
        self.assertEqual(d.cfg['led_brightness_pct'], 50)
        D.Daemon.action(d, 'led_brightness:500')
        self.assertEqual(d.cfg['led_brightness_pct'], 100)
        D.Daemon.action(d, 'led_brightness:abc')
        self.assertEqual(d.cfg['led_brightness_pct'], 100)
        self.assertIn('not a percent', d.said[-1])

class LedsStayLit(unittest.TestCase):
    def test_lit_channel_never_rounds_to_off(self):
        self.assertEqual(D.scale_leds([(255, 0, 0), (255, 150, 0), (0, 0, 0)], 1), [(2, 0, 0), (2, 1, 0), (0, 0, 0)])
        self.assertEqual(D.scale_leds([(63, 37, 0)], 1), [(1, 1, 0)])
        self.assertEqual(D.scale_leds([(255, 150, 0)], 100), [(255, 150, 0)])

    def test_idle_dim_leaves_leds_alone(self):
        d = stub(1)
        d.dimmed = True
        d._leds_t, d._identify = 0.0, None
        d.lights = type('L', (), {'mode': 'attention', 'frame': lambda self, *a: [(255, 0, 0)] + [(0, 0, 0)] * 8})()
        d.attention_state = lambda: {}
        d.attention_leds = lambda: {}
        sent = []
        d.send = sent.append
        D.Daemon.update_leds(d)
        self.assertEqual(d._leds_last[0], (2, 0, 0))
        self.assertEqual(len(sent), 1)


if __name__ == '__main__':
    unittest.main()
