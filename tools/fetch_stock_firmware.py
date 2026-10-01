#!/usr/bin/env python3
"""Download the stock DEF CON 32 badge firmware images and verify them against backups/stock/SHA256SUMS.

    python3 tools/fetch_stock_firmware.py            all versions (1.31, 1.5, 1.6)
    python3 tools/fetch_stock_firmware.py 1.6        one version

The images are (c) 2024 Dmitry Grinberg and are not redistributed in this repo; they come from
https://github.com/jaku/DEFCON-32-BadgeFirmware at a pinned commit (see backups/stock/SOURCE.md).
Only needed for `badgetool.py restore --stock <ver>`; the preferred restore is your own full-flash backup.
"""
from __future__ import annotations

import hashlib
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STOCK = os.path.join(ROOT, "backups", "stock")
COMMIT = "229e351ab33e725f6219dcefbb4aaa7ee033ba09"
RAW = f"https://raw.githubusercontent.com/jaku/DEFCON-32-BadgeFirmware/{COMMIT}/firmware/"


def main():
    sums = {}
    with open(os.path.join(STOCK, "SHA256SUMS")) as f:
        for line in f:
            if line.strip():
                h, name = line.split()
                sums[name] = h
    want = sys.argv[1:] or sorted({n.split("/")[0] for n in sums})
    for name, h in sorted(sums.items()):
        if name.split("/")[0] not in want:
            continue
        out = os.path.join(STOCK, name)
        if os.path.exists(out) and hashlib.sha256(open(out, "rb").read()).hexdigest() == h:
            print(f"ok (cached)  {name}")
            continue
        data = urllib.request.urlopen(RAW + name, timeout=120).read()
        got = hashlib.sha256(data).hexdigest()
        if got != h:
            sys.exit(f"checksum mismatch for {name}: {got} != {h}")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as f:
            f.write(data)
        print(f"ok           {name}")


if __name__ == "__main__":
    main()
