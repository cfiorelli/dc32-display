"""Sleep / wake: backlight off on command, back on with PC input (after a grace) or a badge press."""
from pathlib import Path
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from dc32host import daemon as D
from dc32host import protocol as P


def stub(idle_s):
    d = D.Daemon.__new__(D.Daemon)
    d.sent, d.actions = [], []
    d.send = d.sent.append
    d.be = SimpleNamespace(_idle_ms=lambda: idle_s[0] * 1000)
    d.sleeping, d.sleep_t, d.dimmed, d.brightness = False, 0.0, False, 22
    d.prev, d._leds_last, d.last_badge_input = object(), None, 0.0
    d.force_refresh = lambda: setattr(d, 'prev', None)
    d.cfg = {'buttons': {'a.short': 'zoom_cycle'}}
    d.action = d.actions.append
    return d


class Sleep(unittest.TestCase):
    def test_sleep_turns_backlight_off_and_input_wakes_after_grace(self):
        idle = [0.0]
        d = stub(idle)
        d.sleep()
        self.assertTrue(d.sleeping)
        self.assertIn(P.set_brightness(0), d.sent[-1])
        d.check_wake()                          # the shortcut's own keypress, just now
        self.assertTrue(d.sleeping)
        with patch.object(D.time, 'time', return_value=d.sleep_t + 60):
            idle[0] = 120.0                     # nothing touched since well before the sleep
            d.check_wake()
            self.assertTrue(d.sleeping)
            idle[0] = 0.5                       # mouse moved half a second ago
            d.check_wake()
        self.assertFalse(d.sleeping)
        self.assertEqual(d.sent[-1], P.set_brightness(22))

    def test_badge_press_wakes_and_is_swallowed(self):
        d = stub([999.0])
        d.sleep()
        ev = SimpleNamespace(button='a', event='down', held=(), fn_held=False)
        d.on_button(ev)
        self.assertFalse(d.sleeping)
        d.on_button(SimpleNamespace(button='a', event='short', held=(), fn_held=False))
        self.assertEqual(d.actions, [])         # the waking press did not also zoom

    def test_manual_brightness_never_reaches_off(self):
        d = stub([0.0])
        d.brightness, d.say = 2, lambda *a, **k: None
        D.Daemon.action(d, 'brightness_down')
        self.assertEqual(d.brightness, 1)


if __name__ == '__main__':
    unittest.main()
