"""IR scope: protocol decoding of (mark_us, space_us) frames and a render smoke test. Headless."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from dc32host import ir_view as I
from dc32host import protocol as P


def nec(addr, cmd, leader=(9000, 4500), jitter=0):
    bits = addr | (~addr & 0xFF) << 8 | cmd << 16 | (~cmd & 0xFF) << 24
    pairs = [leader]
    for i in range(32):
        pairs.append((560 + jitter, 1690 if bits >> i & 1 else 560))
    return pairs + [(560, 0)]


class Decode(unittest.TestCase):
    def test_nec_samsung_repeat(self):
        self.assertEqual(I.decode(nec(0x04, 0x08)), 'NEC  addr 0x04  cmd 0x08')
        self.assertEqual(I.decode(nec(0x04, 0x08, jitter=120)), 'NEC  addr 0x04  cmd 0x08')   # sloppy remote
        self.assertEqual(I.decode(nec(0x07, 0x02, leader=(4500, 4500))), 'Samsung  addr 0x07  cmd 0x02')
        self.assertEqual(I.decode([(9000, 2250), (560, 0)]), 'NEC repeat')

    def test_sony(self):
        cmd, dev = 0x15, 0x01                      # Sony TV power
        bits = [cmd >> i & 1 for i in range(7)] + [dev >> i & 1 for i in range(5)]
        pairs = [(2400, 600)] + [(1200 if b else 600, 600) for b in bits]
        self.assertEqual(I.decode(pairs), 'Sony 12-bit  dev 0x01  cmd 0x15')

    def test_unknown_and_wire_format(self):
        self.assertIsNone(I.decode([(300, 300)] * 7))
        payload = bytes([2]) + (9000).to_bytes(2, 'little') + (2250).to_bytes(2, 'little') \
            + (560).to_bytes(2, 'little') + bytes(2)
        self.assertEqual(P.parse(P.IR_FRAME, payload), P.IrFrame([(9000, 2250), (560, 0)]))

    def test_render(self):
        s = I.IrScope()
        self.assertEqual(s.render().shape, (240, 320, 3))       # empty: instructions
        for c in range(6):
            s.add(nec(0x04, c))
        self.assertEqual(len(s.frames), 5)
        self.assertEqual(s.render().shape, (240, 320, 3))


if __name__ == '__main__':
    unittest.main()
