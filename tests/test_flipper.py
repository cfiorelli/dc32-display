"""Flipper Zero link: hand-rolled protobuf, screen-frame layout, view and badge-button routing. No hardware."""
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from dc32host import flipper as F
from dc32host import flipper_view as V
from dc32host import daemon as D


class Proto(unittest.TestCase):
    def test_varint_and_main(self):
        self.assertEqual(F.varint(300), b'\xac\x02')
        self.assertEqual(F.read_varint(b'\xac\x02', 0), (300, 2))
        m = F.main_msg(7, 23, F.field_varint(1, 4) + F.field_varint(2, 2))   # OK, SHORT
        ln, i = F.read_varint(m, 0)
        self.assertEqual(ln, len(m) - i)
        got = dict(F.fields(m[i:]))
        self.assertEqual(got[1], 7)
        self.assertEqual(dict(F.fields(got[23])), {1: 4, 2: 2})

    def test_empty_content_and_frame_layout(self):
        m = F.main_msg(1, 20)                                   # start screen stream: empty submessage
        self.assertEqual(dict(F.fields(m[1:])), {1: 1, 20: b''})
        data = bytearray(1024)
        data[1 * 128 + 3] = 1 << 2                              # page 1, column 3, bit 2 -> (x 3, y 10)
        bm = F.frame_to_bitmap(bytes(data))
        self.assertEqual(bm.shape, (64, 128))
        self.assertTrue(bm[10, 3])
        self.assertEqual(int(bm.sum()), 1)


class View(unittest.TestCase):
    def test_render_and_buttons(self):
        link = SimpleNamespace(frame=None, status='plug the Flipper into this PC (USB)', name=None, presses=[])
        self.assertEqual(V.FlipperView(link).render().shape, (240, 320, 3))
        link.frame, link.status, link.name = F.frame_to_bitmap(bytes([0xFF]) * 1024), 'connected', 'Nefll03'
        out = V.FlipperView(link).render()
        self.assertEqual(tuple(out[V.Y0 + 5, V.X0 + 5]), V.PIXEL)
        link.press = lambda k, long=False: link.presses.append((k, long))
        d = D.Daemon.__new__(D.Daemon)
        d.view, d.dimmed, d.sleeping, d.flipper = 'flipper', False, False, (link, None)
        d.last_badge_input, d.cfg, d.actions = 0, {'buttons': {'b.short': 'back'}}, []
        d.action = d.actions.append
        ev = lambda b, e, held=(): SimpleNamespace(button=b, event=e, held=list(held), fn_held='fn' in held)
        d.on_button(ev('a', 'short'))
        d.on_button(ev('b', 'long'))
        d.on_button(ev('b', 'short'))
        self.assertEqual(link.presses, [('ok', False), ('back', True), ('back', False)])
        self.assertEqual(d.actions, [])                        # B didn't leave the view


if __name__ == '__main__':
    unittest.main()
