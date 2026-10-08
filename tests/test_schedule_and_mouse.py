"""Runner refresh schedule (2 h, quiet hours) and the pointer-pushed zoom camera. Headless."""
import datetime as dt
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

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
        self.assertIsNone(view.schedule_text(v, now))      # overnight: no footer text
        self.d.fetching = True
        self.assertEqual(view.schedule_text(v, now), 'updating...')
        self.d.fetching = False
        self.assertIsNone(view.schedule_text({'backfill': (1, 5)}, now))

    def test_age_color(self):
        now = dt.datetime(2026, 10, 8, 12, tzinfo=dt.timezone.utc)
        ago = lambda m: now - dt.timedelta(minutes=m)
        self.assertEqual([rv.age_color(ago(m), now) for m in (4, 14, 16, 119, 121)],
                         [rv.OK, rv.OK, rv.WARN, rv.WARN, rv.BAD])
        self.assertEqual(rv.age_color(None, now), rv.MUTED)

    def test_cycle_days_left(self):
        utc = dt.timezone.utc
        self.assertEqual(rv.cycle_days_left(dt.datetime(2026, 10, 8, 11, 0, tzinfo=utc)), 24)
        self.assertEqual(rv.cycle_days_left(dt.datetime(2026, 10, 31, 23, 0, tzinfo=utc)), 1)
        self.assertEqual(rv.cycle_days_left(dt.datetime(2026, 12, 15, tzinfo=utc)), 17)

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


class StateFile(unittest.TestCase):
    """A restart shows the last snapshot at once and keeps the refresh schedule (no refetch)."""

    def make(self, path):
        UTC = dt.timezone.utc
        m = SimpleNamespace(UTC=UTC, compute=lambda st, now: {'backfill': (3, 3), 'actions_net': len(st.billing_items)})
        st = SimpleNamespace(year=2026, month=10, lock=threading.RLock(), billing_items=None, billing_at=None,
                             budget=None, runners=None, listed_at={})
        d = rv.RunnerData('unused', refresh_s=7200)
        d.mod, d.st = m, st
        return d

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(rv, 'STATE_FILE', Path(tmp) / 's.json'):
            now = dt.datetime.now(dt.timezone.utc)
            a = self.make(tmp)
            a.st.billing_items, a.st.budget, a.st.runners = [{'netAmount': 0}] * 4, {'budget_amount': 250}, [{'name': 'x'}]
            a.st.billing_at = now
            a.st.listed_at = {('PathReaderAI', '2026-10-01'): now}
            a.updated = now - dt.timedelta(minutes=20)
            a._save()
            b = self.make(tmp)
            b._restore()
            v, _, err, updated = b.snapshot()
            self.assertEqual(v['actions_net'], 4)
            self.assertEqual(updated, a.updated)
            self.assertEqual(b.st.listed_at, a.st.listed_at)
            self.assertAlmostEqual(b.next_at, a.updated.timestamp() + 7200, delta=1)   # waits for the schedule
            self.assertTrue(b._synced)

    def test_other_month_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(rv, 'STATE_FILE', Path(tmp) / 's.json'):
            a = self.make(tmp); a.st.billing_items = []; a.st.month = 9; a._save()
            b = self.make(tmp); b._restore()
            self.assertIsNone(b.snapshot()[0])
            self.assertEqual(b.next_at, 0.0)


class MouseCamera(unittest.TestCase):
    def setUp(self):
        self.src = np.zeros((1000, 1600, 3), np.uint8)
        self.vp = C.Viewport()
        self.vp.set_zoom('1x')
        self.vp.center = (800, 500)

    def frame(self, pt, mouse=True):
        self.vp.compose(self.src, 'k', pt, mouse=mouse)
        return self.vp.center

    def test_mouse_moves_after_keyboard_pan(self):
        self.vp.pan(1, 0)                                  # I/J/K/L: holds the view for 15 s
        held = self.frame((1300, 500))
        self.assertEqual(held, self.vp.center)             # pointer ignored while held
        from dc32host import daemon as D
        d = D.Daemon.__new__(D.Daemon)
        d.vp, d.typing_until = self.vp, time.time() + 3
        d.mouse_takes_over(time.time())                    # the mouse moved: it wins
        xs = [self.frame((1300, 500))[0] for _ in range(10)]
        self.assertGreater(xs[-1], held[0])                # view follows the pointer again
        self.assertLessEqual(d.typing_until, time.time())

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
