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


def doc(age, lit=(), tags=None, quiet=None, levels=None):
    """lit: id -> descriptor; tags: id -> DWT|Ops|API|Eng; quiet: id -> descriptor kept while unlit (CI spend)."""
    inds = []
    for i, (k, label) in enumerate([('priority_blocked', 'Priority blocked'), ('production', 'Prod + tests'),
                                     ('spend', 'CI spend'), ('capacity', 'AI usage'), ('pr_waiting', 'PR waiting')]):
        on = k in lit
        inds.append({'id': k, 'label': label, 'lit': on, 'reason': lit[k] if on else (quiet or {}).get(k, ''),
                     'tag': (tags or {}).get(k) if on else None, 'since': iso(3 * 3600) if on else None,
                     'link': None, 'level': (levels or {}).get(k, LEVELS.get(k)) if on else None})
    return {'at': iso(age), 'indicators': inds}


def write(obj):
    f = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False)
    json.dump(obj, f)
    f.close()
    return f.name


FIXTURES = {
    'none': doc(40),
    'two': doc(40, {'production': 'wintest off', 'pr_waiting': 'engine#197'}, {'pr_waiting': 'Ops'}),
    'stale': doc(25 * 60, {'production': 'Health down'}),
    'red': doc(40, {'priority_blocked': 'Sec call', 'spend': '$210/$250'}, {'priority_blocked': 'DWT'}, levels={'spend': 'red'}),
    # all five lit with the longest real strings (the live state on 2026-10-13 plus the longest short a writer can emit)
    'longest': doc(40, {'priority_blocked': 'Wintest SAC', 'production': 'testmac off', 'spend': '$210/$250',
                        'capacity': 'Claude 83%', 'pr_waiting': 'engine#2055'},
                   {'priority_blocked': 'Ops', 'pr_waiting': 'Ops'}, levels={'spend': 'red', 'capacity': 'red', 'production': 'yellow'}),
    'api': doc(40, {'priority_blocked': 'API on site'}, {'priority_blocked': 'API'}),
    'notag': doc(40, {'priority_blocked': 'fq-13'}, {'priority_blocked': 'Eng'}),
    'spend_low': doc(40, quiet={'spend': '$90/$250'}),
    'spend_mid': doc(40, {'spend': '$140/$250'}, levels={'spend': 'yellow'}),
    'spend_high': doc(40, {'spend': '$210/$250'}, levels={'spend': 'red'}),
    # 12 widest characters in every descriptor, 12-char tag-prefixed descriptors, and a label that would overflow
    'worst': doc(40, {k: ('W' * 12 if k != 'production' else 'M' * 12) for k in ('priority_blocked', 'production', 'spend', 'capacity', 'pr_waiting')},
                 {'priority_blocked': 'DWT', 'pr_waiting': 'API'}),
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
            # and across rows: no left text pixel within 8 px of ANY right text pixel, so a taller glyph cannot clip either
            if left.any() and right.any():
                self.assertGreaterEqual(np.flatnonzero(right.any(axis=0)).min() - np.flatnonzero(left.any(axis=0)).max(), 8, name)

    def test_recorded_boxes_stay_in_their_column(self):
        for name, obj in FIXTURES.items():
            boxes = []
            A.render(A.load(write(obj), now=NOW), now=NOW, boxes=boxes)
            for side, x0, y0, x1, y1 in boxes:
                if side == 'left':
                    self.assertTrue(22 <= x0 and x1 <= 22 + A.COLW + 2, (name, side, x0, x1))
                else:
                    self.assertTrue(320 - 22 - A.COLW - 2 <= x0 and x1 <= 320 - 22 + 2, (name, side, x0, x1))
            lefts = [b[3] for b in boxes if b[0] == 'left']; rights = [b[1] for b in boxes if b[0] == 'right']
            if lefts and rights:
                self.assertGreaterEqual(min(rights) - max(lefts), 8, name)

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
        self.assertEqual(set(A.TAG_RGB), {'DWT', 'Ops', 'API', 'Eng'})
        im = A.render(A.load(write(FIXTURES['api']), now=NOW), now=NOW)
        self.assertTrue(((im == np.array(A.TAG_RGB['API'])).all(axis=2)).any())

    def test_stale_hides_descriptors(self):
        quiet = dict(FIXTURES['spend_low'], at=iso(25 * 60))
        bare = json.loads(json.dumps(quiet))
        for i in bare['indicators']:
            i['reason'] = ''
        self.assertTrue((A.render(A.load(write(quiet), now=NOW), now=NOW) == A.render(A.load(write(bare), now=NOW), now=NOW)).all())


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
