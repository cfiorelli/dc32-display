"""Main-loop progress and stack-only diagnostics; never use worker logs as liveness."""
from __future__ import annotations

import faulthandler
import json
import os
from pathlib import Path
import signal
import sys
import time

from . import config as CFG


def start_ticks(pid):
    # comm may contain spaces or parentheses; starttime is field 22.
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


def heartbeat_path():
    return Path(CFG.app_dirs()[1]) / "heartbeat.json"


class Progress:
    def __init__(self):
        self.path = heartbeat_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.pid = os.getpid()
        self.start = start_ticks(self.pid) if sys.platform == "linux" else None
        self.last = None
        self.beat(force=True)

    def beat(self, force=False):
        now = time.monotonic()
        if not force and self.last is not None and now - self.last < 5:
            return
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"pid": self.pid, "start_ticks": self.start, "monotonic": now}))
        tmp.replace(self.path)
        self.last = now


def enable_diagnostics():
    path = heartbeat_path().parent / "logs" / "stacks.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 1_000_000:
        path.replace(path.with_suffix(".previous.log"))
    stream = os.fdopen(os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600), "a")
    faulthandler.enable(file=stream)
    if hasattr(signal, "SIGUSR1"):
        faulthandler.register(signal.SIGUSR1, file=stream, all_threads=True)
    return stream  # keep the descriptor alive for the entire host lifetime
