"""All LED modes produce valid 9-LED frames across time; the CPU meter tracks load."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from dc32host import lights as L


class Modes(unittest.TestCase):
    def test_every_mode_valid(self):
        for m in L.MODES:
            lt = L.Lights(m)
            for t in (0.0, 0.33, 1.3, 7.7, 123.4):
                f = lt.frame(t, {})
                self.assertEqual(len(f), L.N, m)
                for c in f:
                    self.assertEqual(len(c), 3)
                    self.assertTrue(all(isinstance(v, int) and 0 <= v <= 255 for v in c), (m, c))

    def test_cpu_meter_fills_with_load(self):
        lt = L.Lights("cpu")
        with patch.dict('sys.modules'):
            import types
            fake = types.SimpleNamespace(cpu_percent=lambda: 90.0)
            with patch.dict('sys.modules', {'psutil': fake}):
                f = lt.frame(100.0, {})
        lit = sum(1 for c in f if max(c) > 10)
        self.assertGreaterEqual(lit, 7)                 # ~90% -> most LEDs lit
        self.assertGreater(f[0][0], f[0][1])            # high load -> red dominant

    def test_labels_cover_modes(self):
        self.assertEqual(set(L.MODES), set(L.LABELS))


if __name__ == '__main__':
    unittest.main()
