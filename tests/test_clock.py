"""Clock screensaver renders a valid frame and drifts over time."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))


class ClockTest(unittest.TestCase):
    def test_render_and_drift(self):
        from dc32host import clock_view as CV
        v = CV.ClockView()
        with patch.object(CV.time, 'time', return_value=1000.0):
            a = v.render()
        with patch.object(CV.time, 'time', return_value=1030.0):
            b = v.render()
        self.assertEqual(a.shape, (240, 320, 3))
        self.assertFalse((a == b).all())          # position drifts between the two times


if __name__ == '__main__':
    unittest.main()
