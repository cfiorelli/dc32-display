"""Accelerometer protocol round-trip and bubble-level rendering."""
from pathlib import Path
import struct
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from dc32host import protocol as P
from dc32host import level_view as LV


class Accel(unittest.TestCase):
    def test_parse_and_setaccel(self):
        pkt = struct.pack("<hhh", 100, -200, 16000)
        self.assertEqual(P.parse(P.ACCEL, pkt), P.AccelEvent(100, -200, 16000))
        self.assertEqual(P.set_accel(True), P.msg(P.SET_ACCEL, b"\x01"))
        self.assertEqual(P.set_accel(False), P.msg(P.SET_ACCEL, b"\x00"))

    def test_level_render_flat_is_level(self):
        v = LV.LevelView()
        for _ in range(12):
            v.update(0, 0, int(LV.G))
        v.ts = time.time()
        self.assertEqual(v.render().shape, (240, 320, 3))


if __name__ == '__main__':
    unittest.main()
