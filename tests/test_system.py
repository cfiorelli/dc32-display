"""System monitor view renders a valid frame from psutil."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))


class SystemViewTest(unittest.TestCase):
    def test_render(self):
        from dc32host.system_view import SystemView, _rate
        v = SystemView()
        self.assertEqual(v.render().shape, (240, 320, 3))
        self.assertEqual(v.render().shape, (240, 320, 3))      # second call: net delta path
        self.assertEqual(_rate(1500000), '1.5 MB/s')
        self.assertEqual(_rate(900), '900 B/s')


if __name__ == '__main__':
    unittest.main()
