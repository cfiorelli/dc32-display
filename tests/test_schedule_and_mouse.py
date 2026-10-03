"""Runner refresh schedule (2 h, quiet hours) and the pointer-pushed zoom camera. Headless."""
import datetime as dt
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from dc32host import capture as C
from dc32host import runner_view as rv


def at(h, m=0):
    return dt.datetime(2026, 10, 3, h, m).timestamp()      # local time


class Schedule(unittest.TestCase):
    def setUp(self):
        self.d = rv.RunnerData('unused', refresh_s=7200, quiet_hours=(22, 7))

    def test_quiet_window_wraps_midnight(self):
        self.assertEqual([self.d.quiet_now(at(h)) for h in (6, 7, 21, 22, 23, 0)],
                         [True, False, False, True, True, True])
        self.assertEqual(self.d.quiet_end(at(23)), at(7) + 86400)
        self.assertEqual(self.d.quiet_end(at(3)), at(7))
        self.assertFalse(rv.RunnerData('unused', quiet_hours=None).quiet_now(at(23)))

    def test_footer_text(self):
        view = rv.RunnerView(self.d)
        v = {'backfill': (5, 5)}
        now = dt.datetime.fromtimestamp(at(14))
        self.d.next_at = at(15, 42)
        self.assertEqual(view.schedule_text(v, now), 'next 1h42')
        self.d.next_at = at(23)
        self.assertEqual(view.schedule_text(v, now), 'paused till 7:00')
        self.d.fetching = True
        self.assertEqual(view.schedule_text(v, now), 'updating...')
        self.d.fetching = False
        self.assertIsNone(view.schedule_text({'backfill': (1, 5)}, now))

    def test_old_data_is_not_stale_overnight(self):
        view = rv.RunnerView(self.d)
        now = dt.datetime.fromtimestamp(at(3)).astimezone()
        v = {'backfill': (5, 5), 'days7': [], 'month_min': {}, 'month_label': now.strftime('%B %Y')}
        loc = {'units': [{'unit': 'a.b.c.d', 'active': 'active'}]}
        updated = now - dt.timedelta(hours=6)
        self.assertEqual(view.status(v, loc, None, updated, now)[1], 'IDLE')
        noon = now.replace(hour=12)
        self.assertTrue(view.status(v, loc, None, noon - dt.timedelta(hours=6), noon)[1].startswith('STALE'))

    def test_permission_error_says_no_access(self):
        view = rv.RunnerView(self.d)
        now = dt.datetime.fromtimestamp(at(12)).astimezone()
        v = {'backfill': (5, 5), 'days7': [], 'month_min': {}, 'month_label': now.strftime('%B %Y')}
        loc = {'units': [{'unit': 'a.b.c.d', 'active': 'active'}]}
        self.assertEqual(view.status(v, loc, 'no access - token needs repo Actions: read', now, now),
                         (rv.BAD, 'NO ACCESS'))


class MouseCamera(unittest.TestCase):
    def setUp(self):
        self.src = np.zeros((1000, 1600, 3), np.uint8)
        self.vp = C.Viewport()
        self.vp.set_zoom('1x')
        self.vp.center = (800, 500)

    def frame(self, pt, mouse=True):
        self.vp.compose(self.src, 'k', pt, mouse=mouse)
        return self.vp.center

    def test_small_moves_inside_margin_dont_move_view(self):
        for pt in ((700, 450), (900, 560), (720, 520)):
            self.assertEqual(self.frame(pt), (800, 500))

    def test_push_glides_and_settles_with_pointer_inside(self):
        xs = [self.frame((1000, 500))[0] for _ in range(12)]
        self.assertTrue(all(b >= a for a, b in zip(xs, xs[1:])))          # monotonic, no overshoot
        self.assertLess(xs[0], xs[-1])
        x0 = xs[-1] - 160
        self.assertAlmostEqual(1000 - x0, 320 * (1 - C.MOUSE_MARGIN), delta=1)   # pointer held at the margin
        self.assertEqual(self.frame((1000, 500)), (xs[-1], 500))                 # settled exactly


if __name__ == '__main__':
    unittest.main()
