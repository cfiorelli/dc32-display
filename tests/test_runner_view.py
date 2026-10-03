"""Headless badge state renders. No dashboard import, network, display or credentials.

Generate docs PNGs (2x nearest neighbour): python tests/test_runner_view.py --render
On non-Linux hosts, DC32_TEST_FONT_DIR can point at the same DejaVu fonts as the host.
"""
import copy
import datetime as dt
import json
import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
from dc32host import runner_view as rv

FIXTURES = json.loads((ROOT / 'tests/fixtures/runner-view.json').read_text())


def decode(value):
    if isinstance(value, dict):
        return {k: decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode(v) for v in value]
    if isinstance(value, str) and 'T' in value and value.endswith('+00:00'):
        return dt.datetime.fromisoformat(value)
    return value


class Snapshot:
    def __init__(self, name):
        self.fixture = decode(copy.deepcopy(FIXTURES[name]))
        self.refresh_s = self.fixture['refresh_s']
    def snapshot(self):
        s = self.fixture
        return s['v'], s['local'], s['error'], s['updated']


def view(data):
    font_dir = os.environ.get('DC32_TEST_FONT_DIR')
    if font_dir:
        def font(size, bold=False):
            return ImageFont.truetype(str(Path(font_dir) / ('DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf')), size)
        with patch.object(rv, '_ttf', font):
            return rv.RunnerView(data)
    return rv.RunnerView(data)


def render_all():
    dest = ROOT / 'docs/img'
    for name in FIXTURES:
        data = Snapshot(name)
        frame = view(data).render(now=data.fixture['now'])
        image = Image.fromarray(frame).resize((640, 480), Image.Resampling.NEAREST)
        image.save(dest / f'runner-view-{name}.png')
        if name == 'idle':
            image.save(dest / 'runner-view.png')


class RunnerViewTest(unittest.TestCase):
    def test_seven_states_and_status_colors(self):
        expected = {'idle': ('IDLE', rv.OK), 'busy': ('BUSY 4m', rv.WARN),
                    'waiting': ('2 WAITING', rv.WARN), 'stale': ('STALE 1h05', rv.BAD),
                    'service-down': ('SERVICE DOWN', rv.BAD), 'zero-data': ('IDLE', rv.OK),
                    'no-budget': ('IDLE', rv.OK)}
        for name, (word, color) in expected.items():
            data = Snapshot(name)
            v = view(data)
            self.assertEqual(v.status(*data.snapshot(), data.fixture['now']), (color, word))
            frame = v.render(now=data.fixture['now'])
            self.assertEqual(frame.shape, (240, 320, 3))
            self.assertEqual(tuple(frame[15, 14]), color)

    def test_priority_and_freshness_boundary(self):
        data = Snapshot('busy');v = view(data);s = data.fixture;now = s['now']
        s['v']['queue']['self_over_10m'] = 2
        self.assertEqual(v.status(*data.snapshot(), now)[1], '2 WAITING')
        s['updated'] = now-dt.timedelta(seconds=3600)
        self.assertEqual(v.status(*data.snapshot(), now)[1], '2 WAITING')
        self.assertTrue(v.status(*data.snapshot(), now+dt.timedelta(seconds=1))[1].startswith('STALE'))
        s['v']['runner_api'].update(status='offline',offline_since=now-dt.timedelta(minutes=25))
        self.assertEqual(v.status(*data.snapshot(), now)[1], 'OFFLINE 25m')
        s['local']['units'][0]['active']='failed'
        self.assertEqual(v.status(*data.snapshot(), now)[1], 'SERVICE DOWN')

    def test_empty_track_no_phantom_bars(self):
        data=Snapshot('zero-data');frame=view(data).render(now=data.fixture['now'])
        for color in (rv.SELF,rv.HOSTED):
            self.assertFalse(np.any(np.all(frame[31:180] == color,axis=2)))
        self.assertEqual(tuple(frame[146,180]),rv.GRID)

    def test_shared_scale_and_split(self):
        data=Snapshot('idle');frame=view(data).render(now=data.fixture['now'])
        self.assertEqual(tuple(frame[146,100]),rv.SELF)
        self.assertEqual(tuple(frame[146,300]),rv.HOSTED)
        # 62 self minutes and 42 hosted minutes: same 62-pixel scale.
        for color, expected in ((rv.SELF,62),(rv.HOSTED,42)):
            mask=np.all(frame[40:104] == color,axis=2)
            ys=np.where(mask)[0]
            self.assertEqual(104-(40+ys.min()),expected)

    def test_runtime_name_and_removed_content(self):
        data=Snapshot('idle');data.fixture['v']['runner_api']['name']='replacement-runner'
        texts=[];draw_text=rv.ImageDraw.ImageDraw.text
        def record(draw,xy,text,*a,**kw):
            texts.append(text);return draw_text(draw,xy,text,*a,**kw)
        with patch.object(rv.ImageDraw.ImageDraw,'text',record):
            view(data).render(now=data.fixture['now'])
        self.assertTrue(any(t.startswith('replacement') for t in texts))
        self.assertIn('Oct',texts)
        self.assertIn('$24.62',texts)
        self.assertIn('of $250 budget',texts)
        self.assertFalse(any(term in ' '.join(texts).lower() for term in ('saved','upd','12h','list price')))

    def test_errors_are_status_only_and_unknown_billing_is_not_zero(self):
        data=Snapshot('idle');data.fixture['error']='private diagnostic'
        data.fixture['v']['billing_ok']=False
        v=view(data)
        self.assertTrue(v.status(*data.snapshot(),data.fixture['now'])[1].startswith('STALE'))
        texts=[];original=rv.ImageDraw.ImageDraw.text
        def record(draw,xy,text,*a,**kw):
            texts.append(text);return original(draw,xy,text,*a,**kw)
        with patch.object(rv.ImageDraw.ImageDraw,'text',record):v.render(now=data.fixture['now'])
        self.assertNotIn('private diagnostic',texts)
        self.assertIn('$—',texts)

    def test_failed_and_partial_fetches_keep_last_success_time(self):
        data=rv.RunnerData('unused');now=decode(FIXTURES['idle'])['now']
        st=SimpleNamespace(billing_err=None,jobs_err=None,runners_err=None,budget_err=None)
        v=decode(copy.deepcopy(FIXTURES['idle']['v']))
        data._publish(v,st,now)
        st.runners_err='unavailable'
        data._publish(v,st,now+dt.timedelta(minutes=30))
        self.assertEqual(data.updated,now)
        st.runners_err=None;v['backfill']=(1,2)
        data._publish(v,st,now+dt.timedelta(minutes=60))
        self.assertEqual(data.updated,now)
        self.assertEqual(view(data).status(v,decode(FIXTURES['idle']['local']),None,now,now),(rv.WARN,'SYNC 50%'))

    def test_midnight_moves_today_to_right_and_drops_oldest_day(self):
        data=Snapshot('idle');v=view(data);texts=[];original=rv.ImageDraw.ImageDraw.text
        def record(draw,xy,text,*a,**kw):
            if xy[1]==108:texts.append((text,kw['font']))
            return original(draw,xy,text,*a,**kw)
        with patch.object(rv.ImageDraw.ImageDraw,'text',record):
            frame=v.render(now=data.fixture['now']+dt.timedelta(days=1))
        self.assertEqual(''.join(t for t,_ in texts),'MTWTFSS')
        self.assertIs(texts[-1][1],v.f_sb)
        # New local day has no fetched minutes yet; never relabel yesterday's bars.
        for color in (rv.SELF,rv.HOSTED):
            self.assertFalse(np.any(np.all(frame[40:104,282:308] == color,axis=2)))

    def test_month_rollover_never_labels_previous_totals_as_current(self):
        data=Snapshot('idle');v=view(data);now=dt.datetime(2026,11,2,tzinfo=dt.timezone.utc)
        self.assertTrue(v.status(*data.snapshot(),now)[1].startswith('STALE'))
        texts=[];original=rv.ImageDraw.ImageDraw.text
        def record(draw,xy,text,*a,**kw):
            texts.append(text);return original(draw,xy,text,*a,**kw)
        with patch.object(rv.ImageDraw.ImageDraw,'text',record):frame=v.render(now=now)
        self.assertIn('$—',texts)
        self.assertNotIn('$24.62',texts)
        self.assertEqual(tuple(frame[146,180]),rv.GRID)

    def test_refresh_button_still_kicks_existing_event(self):
        data=rv.RunnerData('unused')
        data.next_at=999
        data.refresh_now()
        self.assertEqual(data.next_at,0)
        self.assertTrue(data._kick.is_set())


if __name__ == '__main__':
    if '--render' in sys.argv:
        render_all()
    else:
        unittest.main()
