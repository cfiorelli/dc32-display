#!/usr/bin/env python3
"""Download the official picotool 2.3.0 build and verify it (archive + binary SHA-256).

    python3 tools/get_picotool.py        -> tools/bin/linux/picotool  or  tools/bin/win/picotool.exe

Source: https://github.com/raspberrypi/pico-sdk-tools/releases/tag/v2.3.0-0 (see tools/bin/SOURCE.txt).
Not bundled in this repo; badgetool.py finds the downloaded copy (or a picotool on PATH).
"""
from __future__ import annotations

import hashlib
import io
import os
import platform
import stat
import sys
import tarfile
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "https://github.com/raspberrypi/pico-sdk-tools/releases/download/v2.3.0-0/"
BUILDS = {
    # platform: (archive, archive sha256, member name, output path, output sha256)
    "linux-x86_64": ("picotool-2.3.0-x86_64-lin.tar.gz",
                     "d8222dbb04e83427bcaef8466fe6e76b0e0193c3a140029934bd365dae49f61f",
                     "picotool/picotool", "tools/bin/linux/picotool",
                     "ab242e0b2e3301f4aba6d148b626d5ff0ad291e4f91aa09329e0d884b2ccb408"),
    "windows": ("picotool-2.3.0-x64-win.zip",
                "4dcad3bfbc9d126bdb3870bbce0668f5d300d0f2f505ce775b3444bfdd5eaa79",
                "picotool/picotool.exe", "tools/bin/win/picotool.exe",
                "0493c0cd826c4db1ea1e30db0e90f8aa19012ca0a0f05a074a395f4607143295"),
}


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def main():
    if os.name == "nt":
        key = "windows"
    elif sys.platform.startswith("linux") and platform.machine() in ("x86_64", "AMD64"):
        key = "linux-x86_64"
    else:
        sys.exit("no pinned picotool build for this platform: install picotool 2.x and put it on PATH")
    archive, a_sum, member, out, o_sum = BUILDS[key]
    out = os.path.join(ROOT, out)
    if os.path.exists(out) and sha256(open(out, "rb").read()) == o_sum:
        print(f"picotool already present and verified: {out}")
        return
    print(f"downloading {BASE + archive}")
    data = urllib.request.urlopen(BASE + archive, timeout=120).read()
    if sha256(data) != a_sum:
        sys.exit(f"archive checksum mismatch ({sha256(data)}); refusing to use it")
    if archive.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            name = next(n for n in z.namelist() if n.endswith(member.split("/")[-1]))
            binary = z.read(name)
    else:
        with tarfile.open(fileobj=io.BytesIO(data)) as t:
            m = next(m for m in t.getmembers() if m.name.endswith(member.split("/")[-1]) and m.isfile())
            binary = t.extractfile(m).read()
    if sha256(binary) != o_sum:
        sys.exit(f"picotool binary checksum mismatch ({sha256(binary)}); refusing to install it")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "wb") as f:
        f.write(binary)
    os.chmod(out, os.stat(out).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"installed {out} (sha256 {o_sum}, verified)")


if __name__ == "__main__":
    main()
