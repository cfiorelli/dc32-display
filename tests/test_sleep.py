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
    d._tap_last = 0.0
    d._knock_t = 0.0
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

    def run_taps(self, times):
        d = stub([999.0])
        d.wake = lambda why: setattr(d, 'sleeping', False)
        states = []
        for t in times:
            with patch.object(D.time, 'time', return_value=t):
                was = d.sleeping
                d.on_tap(P.TapEvent(1))
                if d.sleeping != was:
                    states.append(d.sleeping)
        return states

    def test_single_knocks_are_ignored(self):
        # 35 tap events recorded from real single taps (2026-10-05), each ringing as a burst
        rec = [2443.552, 2443.555, 2443.595, 2602.953, 2604.154] + [2604.154] * 6 + [
            2607.193, 2607.213, 2607.233, 2607.260, 2607.313, 2607.350, 2607.353, 2607.453, 2607.473,
            2607.493, 2607.517, 2607.533, 2607.613, 2607.653, 2607.713, 2629.994, 2630.017, 2630.085,
            2630.094, 2630.152, 2630.154, 2630.179, 2646.254, 2646.306]
        self.assertEqual(self.run_taps(rec), [])

    def test_knock_knock_toggles(self):
        ring = [0, 0.02, 0.05, 0.09, 0.15]                  # one knock's ringing events
        knock = lambda t: [t + r for r in ring]
        seq = knock(100) + knock(100.5)                      # knock-knock: sleep
        seq += knock(110) + knock(110.6) + knock(111.2)      # knock-knock (+ a stray third): wake
        seq += knock(120) + knock(121.5)                     # too slow: nothing
        self.assertEqual(self.run_taps(seq), [True, False])
        self.assertEqual(P.parse(P.TAP, bytes([2])), P.TapEvent(2))

    def test_help_number_keys(self):
        from dc32host.help_view import NUMBERED, KEYS
        self.assertEqual(NUMBERED[:2], ['toggle_runner', 'refresh_data'])
        self.assertEqual(len(NUMBERED), sum(1 for k in KEYS if k[2]))
        d = stub([0.0])
        views = []
        d.view, d.prev_view = 'help', 'mirror'
        d.set_view = lambda v: (views.append(v), setattr(d, 'view', v))
        D.Daemon.action(d, 'helpkey:2')               # 2 = refresh data: closes help, then runs it
        self.assertEqual((views, d.actions), (['mirror'], ['refresh_data']))
        d.view = 'help'
        D.Daemon.action(d, 'helpkey:x')               # not a number: help stays up
        self.assertEqual(d.view, 'help')
        D.Daemon.action(d, 'helpkey:Escape')
        self.assertEqual(d.view, 'mirror')
        D.Daemon.action(d, 'helpkey:1')               # help not open any more: ignored
        self.assertEqual(d.actions, ['refresh_data'])

    def test_manual_brightness_never_reaches_off(self):
        d = stub([0.0])
        d.brightness, d.say = 2, lambda *a, **k: None
        D.Daemon.action(d, 'brightness_down')
        self.assertEqual(d.brightness, 1)


if __name__ == '__main__':
    unittest.main()
