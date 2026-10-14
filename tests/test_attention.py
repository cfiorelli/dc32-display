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
from dc32host import daemon as D

NOW = dt.datetime(2026, 10, 13, 12, 0, 0, tzinfo=dt.timezone.utc)


def iso(sec_ago):
    return (NOW - dt.timedelta(seconds=sec_ago)).strftime('%Y-%m-%dT%H:%M:%SZ')


LEVELS = {'production': 'yellow', 'pr_waiting': 'yellow', 'priority_blocked': 'red', 'spend': 'yellow'}


def doc(age, lit=(), tags=None, quiet=None, levels=None, counts=None):
    """lit: id -> descriptor; tags: id -> DWT|Ops|API|Eng; quiet: id -> descriptor kept while unlit (CI spend)."""
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
    'two': doc(40, {'production': 'wintest off', 'pr_waiting': 'engine#197'}, {'pr_waiting': 'Ops'}),
    'stale': doc(25 * 60, {'production': 'Health down'}),
    'red': doc(40, {'priority_blocked': 'Sec call', 'spend': '$210/$250'}, {'priority_blocked': 'DWT'}, levels={'spend': 'red'}),
    # all five lit with the longest real strings (the live state on 2026-10-13 plus the longest short a writer can emit)
    'longest': doc(40, {'priority_blocked': 'Wintest SAC', 'production': 'testmac off', 'spend': '$210/$250',
                        'capacity': 'Claude bal: 17%', 'pr_waiting': 'engine#2055'},
                   {'priority_blocked': 'Ops', 'pr_waiting': 'Ops'}, levels={'spend': 'red', 'capacity': 'red', 'production': 'yellow'}),
    # several items behind a light: 'nR nY' next to the label, only above one
    'counts': doc(40, {'priority_blocked': 'Wintest SAC', 'pr_waiting': 'engine#2055', 'capacity': 'Codex bal: 4%', 'production': 'testmac off'},
                  {'priority_blocked': 'Ops', 'pr_waiting': 'Ops'}, levels={'capacity': 'red'},
                  counts={'priority_blocked': {'red': 2, 'yellow': 0}, 'pr_waiting': {'red': 0, 'yellow': 3},
                          'capacity': {'red': 1, 'yellow': 1}, 'production': {'red': 0, 'yellow': 1}}),
    'counts_big': doc(40, {'priority_blocked': 'Wintest SAC', 'pr_waiting': 'engine#2055'}, {'priority_blocked': 'Ops'},
                      counts={'priority_blocked': {'red': 12, 'yellow': 10}, 'pr_waiting': {'red': 0, 'yellow': 12}}),
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
        self.assertEqual(tag[4], reason[4])                      # same bottom: no descenders in "API"/"API on site", so the baseline
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
        self.assertEqual(set(A.TAG_RGB), {'DWT', 'Ops', 'API', 'Eng'})
        im = A.render(A.load(write(FIXTURES['api']), now=NOW), now=NOW)
        self.assertTrue(((im == np.array(A.TAG_RGB['API'])).all(axis=2)).any())

    def test_stale_hides_descriptors(self):
        quiet = dict(FIXTURES['spend_low'], at=iso(25 * 60))
        bare = json.loads(json.dumps(quiet))
        for i in bare['indicators']:
            i['reason'] = ''
        self.assertTrue((A.render(A.load(write(quiet), now=NOW), now=NOW) == A.render(A.load(write(bare), now=NOW), now=NOW)).all())


ORDER = {'stages': [
    {'id': 1, 'name': 'Finish in flight', 'state': 'active'},
    {'id': 2, 'name': 'Harvey', 'state': 'blocked', 'blocked_short': 'needs DWT acct'},
    {'id': 3, 'name': 'Self-hosted console', 'state': 'next'},
    {'id': 4, 'name': 'Any prospect', 'state': 'later'},
    {'id': 5, 'name': 'Stage five with a name too long to fit the row', 'state': 'next'},
]}
EXTRA = [{'tag': 'DWT', 'short': 'Security call'}, {'tag': 'Eng', 'short': 'Website copy'}, {'tag': 'API', 'short': 'API on site'}]


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
        self.assertEqual(A.load_order(write(ORDER))['stages'][1]['name'], 'Harvey')
        self.assertIsNone(A.load_order('/nonexistent/eng-order.json'))
        self.assertEqual(A.load(write(dict(doc(40), backlog=EXTRA)), now=NOW)['backlog'], EXTRA)


BLUE = doc(40, {'capacity': 'Claude bal: 60%'}, levels={'capacity': 'blue'}, counts={'capacity': {'red': 0, 'yellow': 0, 'blue': 1}})
MIXED = doc(40, {'capacity': 'Codex bal: 4%', 'priority_blocked': 'Windows CI'}, {'priority_blocked': 'Ops'}, levels={'capacity': 'yellow'},
            counts={'capacity': {'red': 0, 'yellow': 1, 'blue': 1}})


def seg_doc(segments, level='yellow', lit=True, counts=None):
    d = doc(40, {'capacity': ' '.join(t for t, _ in segments)} if lit else {}, levels={'capacity': level}, quiet=None if lit else {'capacity': ' '.join(t for t, _ in segments)},
            counts={'capacity': counts or {'red': 0, 'yellow': int(level == 'yellow'), 'blue': 0}})
    for i in d['indicators']:
        if i['id'] == 'capacity':
            i['segments'] = [{'t': t, 'level': lv} for t, lv in segments]
    return d


class Segments(unittest.TestCase):
    CASES = {
        'today': ([('Cl 30%', 'neutral'), ('Cx 4%', 'yellow')], 'yellow'),
        'red': ([('Cl 15%', 'red'), ('Cx 80%', 'neutral')], 'red'),
        'blue_yellow': ([('Cl 60%', 'blue'), ('Cx 4%', 'yellow')], 'yellow'),
        'widest': ([('Cl 100%', 'blue'), ('Cx 100%', 'blue')], 'blue'),
    }

    def colours_in_descriptor_row(self, name):
        segs, level = self.CASES[name]
        im = A.render(A.load(write(seg_doc(segs, level)), now=NOW), now=NOW)
        band = im[150:178, 22:152]                  # AI usage descriptor (left_middle: y 140 + 31 baseline), column 22..152
        return {lv: bool((band == np.array(A.LEVEL_SCREEN[lv])).all(axis=2).any()) for lv in ('red', 'yellow', 'blue')}, \
               bool((band == np.array(A.TH_SOFT)).all(axis=2).any())

    def test_each_segment_in_its_own_colour(self):
        found, soft = self.colours_in_descriptor_row('today')
        self.assertEqual((found['yellow'], found['red'], found['blue'], soft), (True, False, False, True))     # Cl neutral (soft), Cx yellow
        found, soft = self.colours_in_descriptor_row('red')
        self.assertEqual((found['red'], found['yellow'], soft), (True, False, True))
        found, soft = self.colours_in_descriptor_row('blue_yellow')
        self.assertEqual((found['blue'], found['yellow'], soft), (True, True, False))

    def test_segment_boxes_are_separate_runs_in_order(self):
        boxes = []
        A.render(A.load(write(seg_doc(*self.CASES['today'])), now=NOW), now=NOW, boxes=boxes)
        runs = sorted((b for b in boxes if b[0] == 'left' and 150 < b[2] < 178), key=lambda b: b[1])
        self.assertEqual(len(runs), 2)
        self.assertLess(runs[0][3], runs[1][1])                  # Claude segment left of the Codex segment, with a gap

    def test_widest_descriptor_fits_the_column_and_keeps_the_gap(self):
        boxes = []
        d = seg_doc(*self.CASES['widest'])
        d['indicators'] = [dict(i, label=i['label']) for i in d['indicators']]
        for i in d['indicators']:
            if i['id'] == 'priority_blocked':                    # right_middle, same row band: the worst neighbour
                i.update(lit=True, level='red', reason='W' * 12, tag='DWT', counts={'red': 12, 'yellow': 0, 'blue': 0})
        A.render(A.load(write(d), now=NOW), now=NOW, boxes=boxes)
        lefts = [b for b in boxes if b[0] == 'left' and 130 <= b[2] < 190]
        rights = [b for b in boxes if b[0] == 'right' and 130 <= b[2] < 190]
        self.assertTrue(lefts and rights)
        self.assertTrue(all(b[3] <= 22 + A.COLW for b in lefts), lefts)
        self.assertGreaterEqual(min(b[1] for b in rights) - max(b[3] for b in lefts), 8)

    def test_unlit_both_neutral_still_shows_the_descriptor(self):
        d = seg_doc([('Cl 40%', 'neutral'), ('Cx 80%', 'neutral')], level=None, lit=False)
        im = A.render(A.load(write(d), now=NOW), now=NOW)
        self.assertTrue(((im[150:178, 22:152] == np.array(A.TH_SOFT)).all(axis=2)).any())
        self.assertEqual(A.lit_leds(A.load(write(d), now=NOW), A.DEFAULT_LEDS), set())

    def test_stale_hides_segments(self):
        d = dict(seg_doc(*self.CASES['today']), at=iso(25 * 60))
        im = A.render(A.load(write(d), now=NOW), now=NOW)
        self.assertFalse(((im[150:178, 22:152] == np.array(A.LEVEL_SCREEN['yellow'])).all(axis=2)).any())


class Blue(unittest.TestCase):
    def test_led_colour_and_steady_frame(self):
        st = A.load(write(BLUE), now=NOW)
        f = A.frame(0.0, st, A.DEFAULT_LEDS)
        self.assertEqual(f[A.DEFAULT_LEDS['capacity']], (0, 0, 255))
        self.assertNotIn(A.LEVEL_LED['blue'], (A.STALE_RGB, A.LEVEL_LED['red'], A.LEVEL_LED['yellow']))
        # distinct from the screen's all-clear cyan border colour
        self.assertGreater(sum(abs(a - b) for a, b in zip(A.LEVEL_SCREEN['blue'], A.TH_CYAN)), 100)

    def test_counts_draw_1y_1b(self):
        seen = []
        orig = A.ImageDraw.ImageDraw.text
        A.ImageDraw.ImageDraw.text = lambda self, xy, text, *a, **k: (seen.append(str(text)), orig(self, xy, text, *a, **k))[1]
        try:
            A.render(A.load(write(MIXED), now=NOW), now=NOW)
        finally:
            A.ImageDraw.ImageDraw.text = orig
        self.assertIn('1Y', seen)
        self.assertIn('1B', seen)
        self.assertNotIn('1R', seen)

    def test_count_and_border_colour_follow_the_worst_level(self):
        im = A.render(A.load(write(BLUE), now=NOW), now=NOW)
        self.assertTrue((im == np.array(A.LEVEL_SCREEN['blue'])).all(axis=2).any())          # the big count and the lit dot
        im = A.render(A.load(write(MIXED), now=NOW), now=NOW)
        self.assertTrue((im == np.array(A.LEVEL_SCREEN['yellow'])).all(axis=2).any())        # yellow outranks blue

    def test_blue_pulse_peak_matches_red_at_1_and_25_percent(self):
        for pct, peak in ((1, (0, 0, 2)), (25, (0, 0, 63))):
            scaled = D.scale_leds([A.LEVEL_LED['blue']], pct)
            self.assertEqual(scaled[0], peak)
            seq = [A.apply_pulse(scaled, {0}, k * 0.1)[0] for k in range(21)]
            self.assertEqual((seq[0], seq[10], seq[20]), ((0, 0, 0), peak, (0, 0, 0)))
            self.assertTrue(all(c[0] == 0 and c[1] == 0 for c in seq))
            levels = {c[2] for c in seq}
            self.assertTrue(levels >= {0, 1, 2} if pct == 1 else len(levels) > 8, levels)
        self.assertEqual(max(D.scale_leds([A.LEVEL_LED['red']], 1)[0]), max(D.scale_leds([A.LEVEL_LED['blue']], 1)[0]))

    def test_blue_pulses_when_flagged(self):
        d = dict(BLUE); d['indicators'] = [dict(i, pulse=True) for i in BLUE['indicators']]
        self.assertEqual(A.pulse_leds(A.load(write(d), now=NOW), A.DEFAULT_LEDS), {A.DEFAULT_LEDS['capacity']})


FIXTURES['seg_today'] = seg_doc(*Segments.CASES['today'])
FIXTURES['seg_widest'] = seg_doc(*Segments.CASES['widest'])
FIXTURES['blue'] = BLUE
FIXTURES['blue_mixed'] = MIXED


def pdoc(pulses, lit=('priority_blocked', 'production', 'spend', 'capacity', 'pr_waiting')):
    """Fixture where each id in `lit` is lit and pulses only if listed in `pulses`."""
    d = doc(40, {k: 'x' for k in lit})
    for i in d['indicators']:
        if i['lit']:
            i['pulse'] = i['id'] in pulses
    return d


class Pulse(unittest.TestCase):
    def led(self, k): return A.DEFAULT_LEDS[k]

    def test_pulse_vs_solid_per_state_mix(self):
        cases = [
            ((), set()),                                            # every item in progress: all solid
            (('priority_blocked',), {self.led('priority_blocked')}),   # one needs fio: that light pulses
            (('priority_blocked', 'pr_waiting', 'production'), {self.led(k) for k in ('priority_blocked', 'pr_waiting', 'production')}),
        ]
        for pulses, want in cases:
            st = A.load(write(pdoc(pulses)), now=NOW)
            self.assertEqual(A.pulse_leds(st, A.DEFAULT_LEDS), want, pulses)
            self.assertEqual(A.lit_leds(st, A.DEFAULT_LEDS), set(A.DEFAULT_LEDS.values()))       # lit either way

    def test_unlit_and_missing_field(self):
        st = A.load(write(pdoc((), lit=())), now=NOW)
        self.assertEqual(A.pulse_leds(st, A.DEFAULT_LEDS), set())
        old = doc(40, {'production': 'x'})                              # older writer: no `pulse` key -> needs fio -> pulses
        self.assertEqual(A.pulse_leds(A.load(write(old), now=NOW), A.DEFAULT_LEDS), {self.led('production')})

    def test_count_does_not_drive_the_led(self):
        d = pdoc(())
        for i in d['indicators']:
            i['counts'] = {'red': 5, 'yellow': 5}
        self.assertEqual(A.pulse_leds(A.load(write(d), now=NOW), A.DEFAULT_LEDS), set())
        d = pdoc(('priority_blocked',))
        for i in d['indicators']:
            i['counts'] = {'red': 1, 'yellow': 0}
        self.assertEqual(A.pulse_leds(A.load(write(d), now=NOW), A.DEFAULT_LEDS), {self.led('priority_blocked')})

    def test_stale_data_does_not_pulse_it_blinks_white(self):
        st = A.load(write(dict(pdoc(('production',)), at=iso(25 * 60))), now=NOW)
        self.assertEqual(A.pulse_leds(st, A.DEFAULT_LEDS), set())
        on = A.frame(0.0, st, A.DEFAULT_LEDS)
        self.assertTrue(all(c in (A.STALE_RGB, (0, 0, 0)) for c in on))

    def sequence(self, pct, color=(255, 0, 0)):
        scaled = D.scale_leds([color], pct)
        return scaled[0], [A.apply_pulse(scaled, {0}, k * 0.1)[0] for k in range(0, 21)]     # one 2 s period at 10 Hz plus the closing sample

    def test_frame_sequence_at_1_percent(self):
        peak, seq = self.sequence(1)
        self.assertEqual(peak, (2, 0, 0))                                # the configured 1% level
        self.assertEqual(seq[0], (0, 0, 0))                              # off at the start
        self.assertEqual(seq[10], peak)                                  # peak is exactly the configured level, never above
        self.assertEqual(seq[20], (0, 0, 0))                             # and off again after 2 s
        self.assertEqual({c[0] for c in seq}, {0, 1, 2})                 # visible ramp through the one in-between step
        self.assertTrue(all(c[1] == 0 and c[2] == 0 for c in seq))       # own colour only
        self.assertTrue(all(max(c) <= max(peak) for c in seq))
        for a, b in zip(seq[:10], seq[1:11]): self.assertLessEqual(a[0], b[0])     # rises to the peak
        for a, b in zip(seq[10:20], seq[11:21]): self.assertGreaterEqual(a[0], b[0])   # then falls

    def test_frame_sequence_at_25_percent(self):
        peak, seq = self.sequence(25)
        self.assertEqual(peak, (63, 0, 0))
        self.assertEqual((seq[0], seq[10], seq[20]), ((0, 0, 0), peak, (0, 0, 0)))
        self.assertGreater(len({c[0] for c in seq}), 8)                  # smooth ramp
        self.assertTrue(all(max(c) <= 63 for c in seq))

    def test_yellow_keeps_its_hue_and_is_distinct_from_the_stale_blink(self):
        peak, seq = self.sequence(1, (255, 150, 0))
        self.assertEqual(peak, (2, 1, 0))
        self.assertTrue(all(c[2] == 0 for c in seq) and all(c != (255, 255, 255) for c in seq))
        self.assertEqual(A.PULSE_S, 2.0)                                 # stale blink is a 3 s hard on/off in white (frame())

    def test_solid_light_is_unchanged_by_apply_pulse(self):
        cols = D.scale_leds([(255, 0, 0)] * 3, 1)
        self.assertEqual(A.apply_pulse(cols, set(), 0.37), cols)
        self.assertEqual(A.apply_pulse(cols, {1}, 1.0), cols)            # pulsing LED at its peak equals the solid value

    def test_daemon_pulses_only_after_scaling(self):
        from test_led_brightness import stub
        d = stub(1)
        d._leds_t, d._identify = 0.0, None
        d.lights = type('L', (), {'mode': 'attention', 'frame': lambda self, *a: [(255, 0, 0)] + [(0, 0, 0)] * 8})()
        d.attention_state = lambda: A.load(write(pdoc(('priority_blocked',), lit=('priority_blocked',))), now=NOW)
        d.attention_leds = lambda: {'priority_blocked': 0}
        sent = []
        d.send = sent.append
        import time
        real = time.time
        try:
            seen = set()
            for t in (100.0, 100.5, 101.0, 101.5, 102.0):
                time.time = lambda t=t: t
                d._leds_t = 0.0
                D.Daemon.update_leds(d)
                seen.add(d._leds_last[0])
        finally:
            time.time = real
        self.assertEqual(seen, {(0, 0, 0), (1, 0, 0), (2, 0, 0)})


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
