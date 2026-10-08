"""Flipper Zero link: hand-rolled protobuf, screen-frame layout, view and badge-button routing. No hardware."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
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

    def test_pc_keys_and_mouse(self):
        presses = []
        link = SimpleNamespace(press=lambda k, long=False: presses.append((k, long)))
        d = D.Daemon.__new__(D.Daemon)
        d.view, d.flipper = 'flipper', (link, None)
        for e in ('Up', 'Return', 'Shift+BackSpace', 'button4', 'button3', 'button1', 'F1'):
            D.Daemon.action(d, 'fkey:' + e)
        self.assertEqual(presses, [('up', False), ('ok', False), ('back', True), ('up', False),
                                   ('back', False), ('ok', False)])
        d.view = 'mirror'
        D.Daemon.action(d, 'fkey:Up')                          # view closed: ignored
        self.assertEqual(len(presses), 6)

    def test_firmware_label(self):
        link = SimpleNamespace(info={'firmware_origin_fork': 'Momentum', 'firmware_version': 'mntm-009'})
        self.assertEqual(F.FlipperLink.firmware(link), 'Momentum mntm-009')

    def test_keyboard_only_when_handed_over(self):
        calls = []
        be = SimpleNamespace(grab_input=lambda n, k, cb, buttons=(), shift=False: calls.append(('grab', n, tuple(k))),
                             release_input=lambda n: calls.append(('release', n)))
        d = D.Daemon.__new__(D.Daemon)
        d.be, d.cfg, d.view, d.kb_focus, d.toasts = be, {}, 'sdr', None, []
        d.say = lambda *a, **k: d.toasts.append(a)
        D.Daemon.action(d, 'keyboard')
        self.assertEqual(calls, [('grab', 'badge-keys', ('Up', 'Down', 'Left', 'Right', 'Return'))])
        self.assertEqual(d.kb_focus, 'sdr')
        D.Daemon.action(d, 'keyboard')                          # Ctrl+Alt+Y again: back to the PC
        self.assertEqual((calls[-1], d.kb_focus), (('release', 'badge-keys'), None))
        d.view = 'mirror'
        D.Daemon.action(d, 'keyboard')                          # nothing to control here
        self.assertEqual(len(calls), 2)



class KeyboardIndicator(unittest.TestCase):
    def texts(self, kb_here):
        from dc32host import flipper as F, flipper_view as V
        link = SimpleNamespace(frame=F.frame_to_bitmap(bytes([0x24]) * 1024), status='connected', name='Nefll03')
        v = V.FlipperView(link); v.kb_here = kb_here
        seen = []
        from PIL import ImageDraw
        orig = ImageDraw.ImageDraw.text
        def rec(self, xy, text, *a, **k):
            seen.append(text); return orig(self, xy, text, *a, **k)
        with patch.object(ImageDraw.ImageDraw, 'text', rec):
            v.render()
        return seen

    def test_pill_and_hint_per_state(self):
        pc = self.texts(False)
        self.assertIn('keys: PC', pc)
        self.assertTrue(any('Ctrl+Alt+Y: keyboard here' in t for t in pc))
        here = self.texts(True)
        self.assertIn('keys: HERE', here)
        self.assertTrue(any('no typing' not in t and 'releases keyboard' in t for t in here))


if __name__ == '__main__':
    unittest.main()
