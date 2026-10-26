"""Liveness follows completed ticks, not network/USB activity; no credentials or hardware."""
import json
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
from dc32host import health, watchdog, daemon, runner_view


class HealthTest(unittest.TestCase):
    def test_progress_atomic_record_and_throttle(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'heartbeat.json'
            with patch.object(health, 'heartbeat_path', return_value=path), patch.object(health.time, 'monotonic', return_value=100):
                progress = health.Progress()
                self.assertEqual(json.loads(path.read_text())['monotonic'], 100)
                progress.beat()
                self.assertFalse(path.with_suffix('.tmp').exists())
            with patch.object(health.time, 'monotonic', return_value=106):
                progress.beat()
            self.assertEqual(json.loads(path.read_text())['monotonic'], 106)

    def test_watchdog_fresh_obsolete_and_stale(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'heartbeat.json'
            record = {'pid': 12345, 'start_ticks': '123', 'monotonic': time.monotonic()}
            path.write_text(json.dumps(record))
            with patch.object(watchdog.os, 'pidfd_open') as opened:
                self.assertFalse(watchdog.check(path))
                opened.assert_not_called()
            record['monotonic'] -= 100
            path.write_text(json.dumps(record))
            with patch.object(watchdog.os, 'pidfd_open', return_value=7), patch.object(watchdog.os, 'close'), patch.object(watchdog, 'matches', return_value=False), patch.object(watchdog.signal, 'pidfd_send_signal') as send:
                self.assertFalse(watchdog.check(path))
                send.assert_not_called()
            with patch.object(watchdog.os, 'pidfd_open', return_value=7), patch.object(watchdog.os, 'close'), patch.object(watchdog, 'matches', return_value=True), patch.object(watchdog.select, 'select', side_effect=[([],[],[]), ([7],[],[])]), patch.object(watchdog.signal, 'pidfd_send_signal') as send:
                self.assertTrue(watchdog.check(path))
                self.assertEqual([c.args[1] for c in send.call_args_list], [signal.SIGUSR1, signal.SIGTERM])

    def test_stopped_child_escalates(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'heartbeat.json'
            path.write_text(json.dumps({'pid': 12345, 'start_ticks': '123', 'monotonic': time.monotonic()-100}))
            with patch.object(watchdog.os, 'pidfd_open', return_value=7), patch.object(watchdog.os, 'close'), patch.object(watchdog, 'matches', return_value=True), patch.object(watchdog.select, 'select', return_value=([],[],[])), patch.object(watchdog.signal, 'pidfd_send_signal') as send:
                self.assertTrue(watchdog.check(path))
                self.assertEqual([c.args[1] for c in send.call_args_list], [signal.SIGUSR1, signal.SIGTERM, signal.SIGKILL])

    def test_blocked_backfill_does_not_block_input_or_snapshot(self):
        data = runner_view.RunnerData('unused')
        entered, release = threading.Event(), threading.Event()
        class StopTest(BaseException): pass
        def fetch(*args):
            entered.set()
            release.wait(2)
            raise StopTest()
        now = runner_view.dt.datetime.now(runner_view.dt.timezone.utc)
        data.st = SimpleNamespace(year=now.astimezone().year, month=now.astimezone().month)
        data.api = object()
        data.mod = SimpleNamespace(UTC=runner_view.dt.timezone.utc, fetch_billing=fetch)
        def worker():
            try: data._gh_loop()
            except StopTest: pass
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            host = daemon.Daemon.__new__(daemon.Daemon)
            import queue
            host.ctl_q = queue.Queue()
            host.ctl_q.put('toggle_report')
            seen = []
            host.action = seen.append
            before = time.monotonic()
            data.snapshot()
            host.run_control()
            self.assertEqual(seen, ['toggle_report'])
            self.assertLess(time.monotonic()-before, .2)
        finally:
            release.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())


if __name__ == '__main__': unittest.main()
