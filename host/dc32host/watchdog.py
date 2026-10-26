"""Linux user timer: diagnose and stop only the stale badge child; run.sh restarts it."""
from __future__ import annotations

import json
import os
from pathlib import Path
import select
import signal
import sys
import time

from .health import heartbeat_path, start_ticks

STALE_S = 90


def stale(record, now):
    age = now - float(record["monotonic"])
    return age < 0 or age > STALE_S


def matches(record):
    pid = int(record["pid"])
    if pid <= 1:
        return False
    proc = Path(f"/proc/{pid}")
    # Do not print argv or environments. Check exact tokens, UID, executable and birth time.
    args = (proc / "cmdline").read_bytes().rstrip(b"\0").split(b"\0")
    return (proc.stat().st_uid == os.getuid()
            and (proc / "exe").resolve() == Path(sys.executable).resolve()
            and args[1:] == [b"-m", b"dc32host", b"run"]
            and start_ticks(pid) == record["start_ticks"])


def check(path=None):
    path = path or heartbeat_path()
    try:
        record = json.loads(path.read_text())
        if not stale(record, time.monotonic()):
            return False
        pid = int(record["pid"])
        # pidfd pins identity even if the PID is recycled during a signal/grace period.
        fd = os.pidfd_open(pid)
        try:
            if not matches(record):
                return False
            print(f"dc32 watchdog: stale main loop, pid={pid}; requesting stacks", flush=True)
            signal.pidfd_send_signal(fd, signal.SIGUSR1)
            if select.select([fd], [], [], 1)[0]:
                return True
            if not stale(json.loads(path.read_text()), time.monotonic()):
                return False
            signal.pidfd_send_signal(fd, signal.SIGTERM)
            if not select.select([fd], [], [], 3)[0]:
                signal.pidfd_send_signal(fd, signal.SIGKILL)
            print(f"dc32 watchdog: stopped pid={pid}; restart owned by run.sh", flush=True)
            return True
        finally:
            os.close(fd)
    except (OSError, ValueError, KeyError, TypeError):
        # Missing/obsolete record or an exited child must never target another process.
        return False


if __name__ == "__main__":
    check()
