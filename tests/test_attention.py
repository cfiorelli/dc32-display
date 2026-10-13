"""Attention lights: file -> LED frames and the report screen. Headless; render PNGs with --render."""
from pathlib import Path
import datetime as dt
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from dc32host import attention as A
from dc32host import lights as L

NOW = dt.datetime(2026, 10, 13, 12, 0, 0, tzinfo=dt.timezone.utc)


def iso(sec_ago):
    return (NOW - dt.timedelta(seconds=sec_ago)).strftime('%Y-%m-%dT%H:%M:%SZ')


LEVELS = {'production': 'yellow', 'pr_waiting': 'yellow', 'priority_blocked': 'red', 'spend': 'yellow'}


def doc(age, lit=()):
    inds = []
    for i, (k, label) in enumerate([('priority_blocked', 'Priority blocked'), ('production', 'Prod + tests'),
                                     ('spend', 'CI spend'), ('capacity', 'AI usage'), ('pr_waiting', 'PR waiting')]):
        on = k in lit
        inds.append({'id': k, 'label': label, 'lit': on, 'reason': lit[k] if on else '', 'since': iso(3 * 3600) if on else None,
                     'link': None, 'level': LEVELS.get(k) if on else None})
    return {'at': iso(age), 'indicators': inds}


def write(obj):
    f = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False)
    json.dump(obj, f)
    f.close()
    return f.name


FIXTURES = {
    'none': doc(40),
    'two': doc(40, {'production': 'labbox unreachable', 'pr_waiting': '2 PRs, oldest 31h'}),
    'stale': doc(25 * 60, {'production': 'health check failed twice'}),
    'red': doc(40, {'priority_blocked': 'Demo task', 'spend': '$70/$80'}),
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
