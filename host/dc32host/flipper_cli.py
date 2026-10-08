"""Flipper Zero text CLI over USB serial (used by the RF bench: upload .sub files, transmit them).

The badge's Flipper view uses the binary RPC session on the same port, so the bench only talks CLI
while that view is closed (FlipperLink.stop() ends its RPC session).
"""
from __future__ import annotations

import glob
import os
import termios
import time


class FlipperCli:
    def __init__(self, port=None):
        port = port or (sorted(glob.glob("/dev/serial/by-id/*Flipper*")) or [None])[0]
        if not port:
            raise RuntimeError("no Flipper on USB")
        self.fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        a = termios.tcgetattr(self.fd)
        a[0] = a[1] = a[3] = 0
        a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        termios.tcsetattr(self.fd, termios.TCSANOW, a)
        os.write(self.fd, b"\r\n")
        self._read_until(b">: ", 1.0)

    def close(self):
        os.close(self.fd)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def _read_until(self, token: bytes, timeout: float) -> str:
        out, end = b"", time.time() + timeout
        while time.time() < end and token not in out:
            try:
                out += os.read(self.fd, 8192)
            except BlockingIOError:
                time.sleep(0.01)
        return out.decode(errors="replace")

    def run(self, cmd: str, timeout=5.0) -> str:
        """Run one CLI command and return its output (prompt stripped)."""
        os.write(self.fd, cmd.encode() + b"\r")
        out = self._read_until(b">: ", timeout)
        body = out.split("\n", 1)[1] if "\n" in out else out       # drop the echoed command line
        return body.replace(">: ", "").strip()

    def write_file(self, path: str, data: bytes, chunk=512):
        """storage remove + write_chunk: the CLI says 'Ready' and then reads exactly <size> bytes."""
        self.run(f"storage remove {path}")
        for i in range(0, len(data), chunk):
            part = data[i:i + chunk]
            os.write(self.fd, f"storage write_chunk {path} {len(part)}\r".encode())
            got = self._read_until(b"Ready", 3.0)
            if "Ready" not in got:
                raise RuntimeError(f"write_chunk refused: {got.strip()[-80:]}")
            os.write(self.fd, part)
            self._read_until(b">: ", 3.0)
        size = self.run(f"storage stat {path}")
        if str(len(data)) not in size:
            raise RuntimeError(f"upload size mismatch: {size}")

    def tx_file(self, path: str, repeat=1, timeout=15.0) -> str:
        return self.run(f"subghz tx_from_file {path} {repeat} 0", timeout=timeout)
