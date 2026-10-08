"""The keyboard-capture pill is consistent across every keyboard view (Flipper, SDR, RF bench)."""
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from dc32host import sdr as S


def drawn_text(render_fn):
    seen = []
    from PIL import ImageDraw
    orig = ImageDraw.ImageDraw.text
    def rec(self, xy, t, *a, **k):
        seen.append(t); return orig(self, xy, t, *a, **k)
    with patch.object(ImageDraw.ImageDraw, 'text', rec):
        render_fn()
    return seen


class Pills(unittest.TestCase):
    def views(self):
        from dc32host import flipper as F, flipper_view as FV, sdr_view as SV, bench_view as BV
        fl = FV.FlipperView(SimpleNamespace(frame=F.frame_to_bitmap(bytes([0x24]) * 1024), status='connected', name='X'))
        src = SimpleNamespace(freq=433.92e6, status='running', error=None, audio_mode=None,
                              snapshot=lambda: (np.full(S.NBINS, -60.0), np.full(S.NBINS, -60.0), [np.full(S.NBINS, -60.0)] * 5))
        sd = SV.SdrView(src)
        bn = BV.BenchView(BV.Bench())
        return [fl, sd, bn]

    def test_every_view_shows_the_pill_in_both_states(self):
        for v in self.views():
            v.kb_here = False
            self.assertIn('keys: PC', drawn_text(v.render), type(v).__name__)
            v.kb_here = True
            self.assertIn('keys: HERE', drawn_text(v.render), type(v).__name__)


if __name__ == '__main__':
    unittest.main()
