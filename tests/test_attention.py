"""Attention lights: file -> LED frames and the report screen. Headless; render PNGs with --render."""
from pathlib import Path
import datetime as dt
import json
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from dc32host import attention as A
from dc32host import lights as L

NOW = dt.datetime(2026, 10, 13, 12, 0, 0, tzinfo=dt.timezone.utc)


def iso(sec_ago):
    return (NOW - dt.timedelta(seconds=sec_ago)).strftime('%Y-%m-%dT%H:%M:%SZ')


LEVELS = {'production': 'yellow', 'pr_waiting': 'yellow', 'priority_blocked': 'red', 'spend': 'yellow'}


def doc(age, lit=(), tags=None, quiet=None, levels=None, counts=None):
    """lit: id -> descriptor; tags: id -> Lab|Ops|API|Eng; quiet: id -> descriptor kept while unlit (CI spend)."""
    inds = []
    for i, (k, label) in enumerate([('priority_blocked', 'Priority blocked'), ('production', 'Prod + tests'),
                                     ('spend', 'CI spend'), ('capacity', 'AI usage'), ('pr_waiting', 'PR waiting')]):
        on = k in lit
        inds.append({'id': k, 'label': label, 'lit': on, 'reason': lit[k] if on else (quiet or {}).get(k, ''),
                     'tag': (tags or {}).get(k) if on else None, 'since': iso(3 * 3600) if on else None,
                     'link': None, 'level': (levels or {}).get(k, LEVELS.get(k)) if on else None,
                     'counts': (counts or {}).get(k, {'red': 0, 'yellow': 0})})
    return {'at': iso(age), 'indicators': inds}


def write(obj):
    f = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False)
    json.dump(obj, f)
    f.close()
    return f.name


FIXTURES = {
    'none': doc(40),
    'two': doc(40, {'production': 'labbox off', 'pr_waiting': 'sample#12'}, {'pr_waiting': 'Ops'}),
    'stale': doc(25 * 60, {'production': 'Health down'}),
    'red': doc(40, {'priority_blocked': 'Demo task', 'spend': '$70/$80'}, {'priority_blocked': 'Lab'}, levels={'spend': 'red'}),
    # all five lit with the longest real strings (the live state on 2026-10-13 plus the longest short a writer can emit)
    'longest': doc(40, {'priority_blocked': 'Lab account', 'production': 'deskpc off', 'spend': '$70/$80',
                        'capacity': 'Claude bal: 17%', 'pr_waiting': 'sample#1234'},
                   {'priority_blocked': 'Ops', 'pr_waiting': 'Ops'}, levels={'spend': 'red', 'capacity': 'red', 'production': 'yellow'}),
    # several items behind a light: 'nR nY' next to the label, only above one
    'counts': doc(40, {'priority_blocked': 'Lab account', 'pr_waiting': 'sample#1234', 'capacity': 'Codex bal: 4%', 'production': 'deskpc off'},
                  {'priority_blocked': 'Ops', 'pr_waiting': 'Ops'}, levels={'capacity': 'red'},
                  counts={'priority_blocked': {'red': 2, 'yellow': 0}, 'pr_waiting': {'red': 0, 'yellow': 3},
                          'capacity': {'red': 1, 'yellow': 1}, 'production': {'red': 0, 'yellow': 1}}),
    'counts_big': doc(40, {'priority_blocked': 'Lab account', 'pr_waiting': 'sample#1234'}, {'priority_blocked': 'Ops'},
                      counts={'priority_blocked': {'red': 12, 'yellow': 10}, 'pr_waiting': {'red': 0, 'yellow': 12}}),
    'api': doc(40, {'priority_blocked': 'API limits'}, {'priority_blocked': 'API'}),
    'notag': doc(40, {'priority_blocked': 'task-12'}, {'priority_blocked': 'Eng'}),
    'spend_low': doc(40, quiet={'spend': '$20/$80'}),
    'spend_mid': doc(40, {'spend': '$45/$80'}, levels={'spend': 'yellow'}),
    'spend_high': doc(40, {'spend': '$70/$80'}, levels={'spend': 'red'}),
    # 12 widest characters in every descriptor, 12-char tag-prefixed descriptors, and a label that would overflow
    'worst': doc(40, {k: ('W' * 12 if k != 'production' else 'M' * 12) for k in ('priority_blocked', 'production', 'spend', 'capacity', 'pr_waiting')},
                 {'priority_blocked': 'Lab', 'pr_waiting': 'API'}),
}


class Attention(unittest.TestCase):
    def test_fresh_none_lit(self):
        st = A.load(write(FIXTURES['none']), now=NOW)
        self.assertFalse(st['stale'])
        self.assertEqual(A.lit_leds(st, A.DEFAULT_LEDS), set())
        self.assertTrue(all(c == (0, 0, 0) for c in A.frame(0.0, st, A.DEFAULT_LEDS)))

    def test_lit_follow_mapping(self):
        st = A.load(write(FIXTURES['two']), now=NOW)
        m = dict(A.DEFAULT_LEDS, production=1)               # remapping moves the light
        self.assertEqual(A.lit_leds(st, m), {1, m['pr_waiting']})
        f = A.frame(5.0, st, m)
        self.assertEqual([i for i, c in enumerate(f) if c != (0, 0, 0)], sorted({1, m['pr_waiting']}))
        self.assertEqual(f[1], A.LEVEL_LED['yellow'])          # colour follows the indicator's level

    def test_stale_blinks_all_mapped_white(self):
        for obj in (FIXTURES['stale'], None):
            st = A.load(write(obj) if obj else '/nonexistent/attention.json', now=NOW)
            self.assertTrue(st['stale'])
            on, off = A.frame(0.0, st, A.DEFAULT_LEDS), A.frame(1.6, st, A.DEFAULT_LEDS)
            self.assertEqual(sorted(i for i, c in enumerate(on) if c == A.STALE_RGB), sorted(A.DEFAULT_LEDS.values()))
            self.assertTrue(all(c == (0, 0, 0) for c in off))

    def test_boundary_and_garbage(self):
        self.assertFalse(A.load(write(doc(599)), now=NOW)['stale'])
        self.assertTrue(A.load(write(doc(601)), now=NOW)['stale'])
        p = write({}); open(p, 'w').write('{not json')
        self.assertTrue(A.load(p, now=NOW)['stale'])
        self.assertTrue(A.load(write({'at': 'garbage', 'indicators': []}), now=NOW)['stale'])
        self.assertTrue(A.load(write(doc(-3600)), now=NOW)['stale'])      # clock far in the future

    def test_lights_mode(self):
        self.assertIn('attention', L.MODES)
        self.assertEqual(set(L.MODES), set(L.LABELS))
        st = A.load(write(FIXTURES['two']), now=NOW)
        f = L.Lights('attention').frame(1.0, None, st, A.DEFAULT_LEDS)
        self.assertEqual(len(f), L.N)
        self.assertEqual(len(L.Lights('attention').frame(1.0)), L.N)    # no data: stale blink, never an error

    def test_identify_sequence(self):
        seen = [A.identify_frame(100.0, 100.0 + k * A.IDENTIFY_S + 0.1)[1] for k in range(A.N_LEDS)]
        self.assertEqual(seen, list(range(A.N_LEDS)))
        f, idx = A.identify_frame(100.0, 100.0 + A.N_LEDS * A.IDENTIFY_S + 1)
        self.assertIsNone(idx)
        self.assertEqual(A.render_identify(4).shape, (240, 320, 3))

    def test_render_states(self):
        imgs = {}
        for name, obj in FIXTURES.items():
            imgs[name] = A.render(A.load(write(obj), now=NOW), now=NOW)
            self.assertEqual(imgs[name].shape, (240, 320, 3))
        self.assertFalse((imgs['none'] == imgs['two']).all())
        self.assertFalse((imgs['none'] == imgs['stale']).all())
        self.assertEqual(A.render({'stale': True, 'age': None, 'indicators': []}).shape, (240, 320, 3))


def text_pixels(state, side, now=NOW):
    """Pixels the given column's text changes: the full render minus the same render with that side's text blanked."""
    full = A.render(state, now=now)
    blank = dict(state, indicators=[dict(i, label=' ', reason='', tag=None) if A.DEFAULT_SLOTS[i['id']].startswith(side) else i
                                    for i in state['indicators']])
    return (full != A.render(blank, now=now)).any(axis=2)


class Columns(unittest.TestCase):
    def test_left_and_right_text_never_meet(self):
        for name, obj in FIXTURES.items():
            st = A.load(write(obj), now=NOW)
            left, right = text_pixels(st, 'left'), text_pixels(st, 'right')
            for y in range(left.shape[0]):
                lx, rx = np.flatnonzero(left[y]), np.flatnonzero(right[y])
                if len(lx) and len(rx):
                    self.assertGreaterEqual(rx.min() - lx.max(), 8, '%s row %d' % (name, y))
            # and per slot row band (same y range), so a glyph taller than its row cannot clip a neighbour
            for lo, hi in ((0, 60), (130, 190), (196, 240)):
                lx, rx = np.flatnonzero(left[lo:hi].any(axis=0)), np.flatnonzero(right[lo:hi].any(axis=0))
                if len(lx) and len(rx):
                    self.assertGreaterEqual(rx.min() - lx.max(), 8, '%s band %d' % (name, lo))

    def test_tag_and_descriptor_share_a_baseline_and_size(self):
        boxes = []
        A.render(A.load(write(FIXTURES['api']), now=NOW), now=NOW, boxes=boxes)
        pri = sorted((b for b in boxes if b[0] == 'right' and 150 < b[2] < 170), key=lambda b: b[1])   # priority_blocked descriptor row
        self.assertEqual(len(pri), 2, pri)                       # tag, reason (the label sits above)
        tag, reason = pri
        self.assertEqual(tag[4], reason[4])                      # same bottom: no descenders in "API"/"API limits", so the baseline
        self.assertGreaterEqual(reason[4] - reason[2], 10)       # taller than the old 11 px text
        self.assertEqual(A.TAG_RGB['API'], (0xff, 0x87, 0xd7))

    def test_recorded_boxes_stay_in_their_column(self):
        for name, obj in FIXTURES.items():
            boxes = []
            A.render(A.load(write(obj), now=NOW), now=NOW, boxes=boxes)
            for side, x0, y0, x1, y1 in boxes:
                if side == 'left':
                    self.assertTrue(22 <= x0 and x1 <= 22 + A.L1W + 2, (name, side, x0, x1))
                else:
                    self.assertTrue(320 - 22 - A.L1W - 2 <= x0 and x1 <= 320 - 22 + 2, (name, side, x0, x1))
            for lo, hi in ((0, 60), (130, 190), (196, 240)):
                lefts = [b[3] for b in boxes if b[0] == 'left' and lo <= b[2] < hi]; rights = [b[1] for b in boxes if b[0] == 'right' and lo <= b[2] < hi]
                if lefts and rights:
                    self.assertGreaterEqual(min(rights) - max(lefts), 8, (name, lo))

    def test_counts_only_above_one(self):
        def texts(obj):
            seen = []
            orig = A.ImageDraw.ImageDraw.text
            A.ImageDraw.ImageDraw.text = lambda self, xy, text, *a, **k: (seen.append(str(text)), orig(self, xy, text, *a, **k))[1]
            try:
                A.render(A.load(write(obj), now=NOW), now=NOW)
            finally:
                A.ImageDraw.ImageDraw.text = orig
            return seen
        t = texts(FIXTURES['counts'])
        for want in ('2R', '3Y', '1R', '1Y'):
            self.assertIn(want, t)
        self.assertEqual([x for x in t if x[-1:] in 'RY' and x[:-1].isdigit()].count('1Y'), 1)    # capacity 1R+1Y only; production (1 item) draws none
        self.assertFalse([x for x in texts(FIXTURES['longest']) if x[-1:] in 'RY' and x[:-1].isdigit()], 'one item per light: no counts')

    def test_only_descriptor_and_label_are_drawn(self):
        seen = []
        orig = A.ImageDraw.ImageDraw.text
        A.ImageDraw.ImageDraw.text = lambda self, xy, text, *a, **k: (seen.append(str(text)), orig(self, xy, text, *a, **k))[1]
        try:
            for obj in FIXTURES.values():
                A.render(A.load(write(obj), now=NOW), now=NOW)
        finally:
            A.ImageDraw.ImageDraw.text = orig
        for gone in ('needs you', 'need you', 'all clear'):
            self.assertNotIn(gone, seen)
        self.assertFalse([t for t in seen if t in ('3h', '3 h')], 'age next to a label is gone')

    def test_since_is_not_drawn(self):
        a = FIXTURES['longest']
        b = json.loads(json.dumps(a))
        for i in b['indicators']:
            i['since'] = None
        self.assertTrue((A.render(A.load(write(a), now=NOW), now=NOW) == A.render(A.load(write(b), now=NOW), now=NOW)).all())

    def test_spend_descriptor_shows_unlit(self):
        quiet = FIXTURES['spend_low']
        bare = json.loads(json.dumps(quiet))
        for i in bare['indicators']:
            i['reason'] = ''
        self.assertFalse((A.render(A.load(write(quiet), now=NOW), now=NOW) == A.render(A.load(write(bare), now=NOW), now=NOW)).all())
        self.assertEqual(A.lit_leds(A.load(write(quiet), now=NOW), A.DEFAULT_LEDS), set())

    def test_tag_colour_follows_the_dashboard(self):
        self.assertEqual(set(A.TAG_RGB), {'Lab', 'Ops', 'API', 'Eng'})
        im = A.render(A.load(write(FIXTURES['api']), now=NOW), now=NOW)
        self.assertTrue(((im == np.array(A.TAG_RGB['API'])).all(axis=2)).any())

    def test_stale_hides_descriptors(self):
        quiet = dict(FIXTURES['spend_low'], at=iso(25 * 60))
        bare = json.loads(json.dumps(quiet))
        for i in bare['indicators']:
            i['reason'] = ''
        self.assertTrue((A.render(A.load(write(quiet), now=NOW), now=NOW) == A.render(A.load(write(bare), now=NOW), now=NOW)).all())


ORDER = {'stages': [
    {'id': 1, 'name': 'Current work', 'state': 'active'},
    {'id': 2, 'name': 'Sample client', 'state': 'blocked', 'blocked_short': 'needs account'},
    {'id': 3, 'name': 'Local dashboard', 'state': 'next'},
    {'id': 4, 'name': 'Next project', 'state': 'later'},
    {'id': 5, 'name': 'Stage five with a name too long to fit the row', 'state': 'next'},
]}
EXTRA = [{'tag': 'Lab', 'short': 'Review notes'}, {'tag': 'Eng', 'short': 'Demo copy'}, {'tag': 'API', 'short': 'API limits'}]


class Backlog(unittest.TestCase):
    def test_renders_live_shape_and_eight_items(self):
        for order, extra in ((ORDER, []), (ORDER, EXTRA), (ORDER, EXTRA * 2), (None, []), ({'stages': []}, None)):
            self.assertEqual(A.render_backlog(order, extra).shape, (240, 320, 3))

    def test_rows_fit_and_blocked_reason_shows(self):
        boxes = []
        A.render_backlog(ORDER, EXTRA, boxes=boxes)
        self.assertEqual(len(boxes), 8)
        for _, x0, y0, x1, y1 in boxes:
            self.assertTrue(28 <= x0 and x1 <= 320 - 12 and y1 <= 240, (x0, y0, x1, y1))
        self.assertFalse((A.render_backlog(ORDER) == A.render_backlog({'stages': [dict(s, state='next') for s in ORDER['stages']]})).all())

    def test_loads_order_file(self):
        self.assertEqual(A.load_order(write(ORDER))['stages'][1]['name'], 'Sample client')
        self.assertIsNone(A.load_order('/nonexistent/eng-order.json'))
        self.assertEqual(A.load(write(dict(doc(40), backlog=EXTRA)), now=NOW)['backlog'], EXTRA)


def render_all(outdir):
    from PIL import Image
    for name, obj in FIXTURES.items():
        im = Image.fromarray(A.render(A.load(write(obj), now=NOW), now=NOW)).resize((640, 480), Image.NEAREST)
        im.save(Path(outdir) / f'attention-{name}.png')
        st = A.load(write(obj), now=NOW)
        led_strip(A.frame(0.0, st, A.DEFAULT_LEDS)).save(Path(outdir) / f'attention-leds-{name}.png')


def led_strip(cols):
    """The 9 LED values as a row of discs labelled with their index (white-blink frame for stale)."""
    from PIL import Image, ImageDraw
    im = Image.new('RGB', (9 * 60, 80), (20, 20, 20))
    d = ImageDraw.Draw(im)
    for i, c in enumerate(cols):
        d.ellipse([i * 60 + 8, 8, i * 60 + 52, 52], fill=c if c != (0, 0, 0) else (45, 45, 45))
        d.text((i * 60 + 26, 60), str(i), fill=(200, 200, 200))
    return im


if __name__ == '__main__':
    if '--render' in sys.argv:
        render_all(Path(__file__).resolve().parents[1] / 'docs' / 'img')
    else:
        unittest.main()
