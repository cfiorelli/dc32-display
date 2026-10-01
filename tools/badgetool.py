#!/usr/bin/env python3
"""Badge backup / flash / restore. Cross-platform (Windows + Linux), drives picotool.

    python tools/badgetool.py backup            full-flash backup (2 reads, verify, 2 copies, manifest)
    python tools/badgetool.py check-backup      re-verify the newest backup against its manifest
    python tools/badgetool.py flash             flash firmware/dist/dc32_display.uf2 (refuses w/o verified backup)
    python tools/badgetool.py restore           write the raw full-flash backup back (exact original state)
    python tools/badgetool.py restore --stock 1.6   flash a stock DEF CON image instead

Safety: only the picotool subcommands info/save/verify/load/reboot are ever executed. Nothing here
touches OTP, security fuses or boot configuration.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUPS = os.path.join(ROOT, "backups")
MIRROR = os.path.join(os.path.expanduser("~"), "dc32-badge-backups")
STOCK = os.path.join(BACKUPS, "stock")
DIST = os.path.join(ROOT, "firmware", "dist")
ALLOWED = {"info", "save", "verify", "load", "reboot", "version"}
FLASH_BASE = 0x10000000

PHYSICAL_BOOTSEL = ("Unplug the badge and take the batteries out. With the back facing you, hold ONLY the BOOTSEL "
                    "button (bottom-left on the tested badge; documented as top-right) and plug the USB-C cable in. "
                    "Keep holding until this tool reports the badge; the screen stays dark. "
                    "(If you hold the reset button instead, nothing appears on USB.)")


def picotool_path():
    for cand in (os.environ.get("PICOTOOL"), shutil.which("picotool"),
                 os.path.join(ROOT, "tools", "bin", "win", "picotool.exe") if os.name == "nt" else os.path.join(ROOT, "tools", "bin", "linux", "picotool")):
        if cand and os.path.exists(cand):
            return cand
    sys.exit("picotool not found: run  python3 tools/get_picotool.py  (downloads + verifies the official 2.3.0 build)")


def pt(*args, check=True, capture=True, timeout=900):
    if args[0] not in ALLOWED:
        raise RuntimeError(f"refusing picotool subcommand {args[0]!r}")
    cmd = [picotool_path(), *args]
    print("$ " + " ".join(cmd), flush=True)
    r = subprocess.run(cmd, capture_output=capture, text=True, timeout=timeout)
    if capture and r.stdout:
        print(r.stdout.rstrip())
    if capture and r.stderr:
        print(r.stderr.rstrip(), file=sys.stderr)
    if check and r.returncode != 0:
        raise RuntimeError(f"picotool {args[0]} failed ({r.returncode})")
    return r


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def in_bootsel() -> bool:
    r = pt("info", check=False)
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode == 0 and "No accessible" not in out


def ensure_bootsel():
    if in_bootsel():
        return
    # our firmware can reboot itself into BOOTSEL; the stock firmware cannot
    try:
        sys.path.insert(0, os.path.join(ROOT, "host"))
        from dc32host.device import Badge
        from dc32host import protocol as P
        b = Badge()
        if b.open(timeout=2):
            print("dc32-display firmware found: asking it to reboot into BOOTSEL")
            b.write(P.reboot(bootsel=True))
            b.close()
            for _ in range(20):
                time.sleep(0.5)
                if in_bootsel():
                    return
    except Exception as e:
        print(f"(software BOOTSEL unavailable: {e})")
    print("\nPHYSICAL ACTION NEEDED:\n  " + PHYSICAL_BOOTSEL + "\nWaiting up to 5 minutes...", flush=True)
    for _ in range(300):
        time.sleep(1)
        if in_bootsel():
            return
    sys.exit("badge not detected in BOOTSEL mode")


def identify_stock(path):
    with open(path, "rb") as f:
        head = f.read(3 * 1024 * 1024)
    hits = []
    for binf in sorted(glob.glob(os.path.join(STOCK, "*", "stock-firmware.bin"))):
        with open(binf, "rb") as f:
            ref = f.read()
        # program region (first 1 MiB minus the settings area at 0xC0000) is the reliable comparison
        same_prog = head[:0xC0000] == ref[:0xC0000]
        same_rom = head[0x100000:0x300000] == ref[0x100000:0x300000]
        hits.append({"version": os.path.basename(os.path.dirname(binf)), "program_match": same_prog, "rom_match": same_rom})
    return hits


# ------------------------------------------------------------------------------------------- backup
def cmd_backup(a):
    ensure_bootsel()
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"{stamp}-original-full-flash"
    d1 = os.path.join(BACKUPS, name)
    d2 = os.path.join(MIRROR, name)
    os.makedirs(d1)
    info = pt("info", "-a").stdout
    devinfo = pt("info", "-d", check=False).stdout or ""
    ver = pt("version").stdout.strip()
    with open(os.path.join(d1, "picotool-info.txt"), "w") as f:
        f.write(f"# {dt.datetime.now().isoformat()}  {ver}\n# picotool info -a\n{info}\n# picotool info -d\n{devinfo}\n")

    f1 = os.path.join(d1, "badge-full-flash.bin")
    f2 = os.path.join(d1, "badge-full-flash.read2.bin")
    pt("save", "-a", "-v", f1, "-t", "bin")
    pt("save", "-a", "-v", f2, "-t", "bin")           # independent second read
    h1, h2 = sha256(f1), sha256(f2)
    if h1 != h2:
        sys.exit(f"INCONSISTENT READS ({h1} vs {h2}); do not flash. Re-run backup.")
    os.remove(f2)
    pt("verify", f1, "-t", "bin", "-o", hex(FLASH_BASE))   # device flash == file, byte for byte
    size = os.path.getsize(f1)
    if size < 2 * 1024 * 1024 or size % 4096:
        sys.exit(f"unexpected backup size {size}")
    stock = identify_stock(f1)
    blank = False
    with open(f1, "rb") as f:
        blank = f.read(4096) == b"\xff" * 4096
    if blank:
        sys.exit("backup starts with erased flash: something is wrong; do not flash")

    manifest = {
        "file": "badge-full-flash.bin", "bytes": size, "sha256": h1, "date": dt.datetime.now().isoformat(),
        "host": platform.node(), "picotool": ver, "flash_base": hex(FLASH_BASE),
        "flash_end": hex(FLASH_BASE + size), "second_read_sha256": h2, "verified_against_device": True,
        "stock_comparison": stock,
    }
    with open(os.path.join(d1, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    with open(os.path.join(d1, "SHA256SUMS"), "w") as f:
        f.write(f"{h1}  badge-full-flash.bin\n")
    matched = [s["version"] for s in stock if s["program_match"]]
    with open(os.path.join(d1, "BACKUP_RECORD.md"), "w") as f:
        f.write(f"""# Badge full-flash backup {stamp}

| | |
|---|---|
| File | `badge-full-flash.bin` |
| Size | {size} bytes ({size / 1048576:.2f} MiB, flash {hex(FLASH_BASE)}-{hex(FLASH_BASE + size)}) |
| SHA-256 | `{h1}` |
| Second independent read | identical |
| `picotool verify` against device | passed |
| Date | {manifest['date']} |
| Host | {manifest['host']} |
| picotool | {ver} |
| Program matches stock | {', '.join(matched) or 'none (custom or modified firmware)'} |

Device information (`picotool info -a` / `-d`) is in `picotool-info.txt`.
Restore with: `python tools/badgetool.py restore --backup "{d1}"` (see RESTORE.md).
""")
    shutil.copytree(d1, d2)
    for d in (d1, d2):
        if sha256(os.path.join(d, "badge-full-flash.bin")) != h1:
            sys.exit(f"copy in {d} does not match")
    with open(os.path.join(BACKUPS, "LATEST"), "w") as f:
        f.write(name + "\n")
    print(f"\nBACKUP OK\n  {f1}\n  {os.path.join(d2, 'badge-full-flash.bin')}\n  sha256 {h1}\n  size {size}\n  stock match: {matched}")
    pt("reboot", check=False)      # back to the original firmware


def latest_backup():
    p = os.path.join(BACKUPS, "LATEST")
    if not os.path.exists(p):
        return None
    d = os.path.join(BACKUPS, open(p).read().strip())
    return d if os.path.isdir(d) else None


def check_backup(d) -> dict:
    with open(os.path.join(d, "manifest.json")) as f:
        m = json.load(f)
    h = sha256(os.path.join(d, m["file"]))
    if h != m["sha256"] or not m.get("verified_against_device"):
        raise SystemExit(f"backup {d} FAILED integrity check")
    mirror = os.path.join(MIRROR, os.path.basename(d), m["file"])
    if not os.path.exists(mirror) or sha256(mirror) != h:
        raise SystemExit(f"second copy missing or different: {mirror}")
    return m


def cmd_check_backup(a):
    d = a.backup or latest_backup()
    if not d:
        sys.exit("no backup found")
    m = check_backup(d)
    print(f"backup OK: {d}\n  sha256 {m['sha256']}  {m['bytes']} bytes  (both copies match)")


# ------------------------------------------------------------------------------------------- flash
def cmd_flash(a):
    d = latest_backup()
    if not d:
        sys.exit("REFUSING: no verified full-flash backup. Run: python tools/badgetool.py backup")
    check_backup(d)
    uf2 = a.uf2 or os.path.join(DIST, "dc32_display.uf2")
    man = os.path.join(DIST, "dc32_display.sha256")
    if os.path.exists(man):
        want = open(man).read().split()[0]
        if sha256(uf2) != want:
            sys.exit("firmware UF2 checksum mismatch vs firmware/dist/dc32_display.sha256")
    print(f"flashing {uf2} (sha256 {sha256(uf2)})")
    ensure_bootsel()
    pt("load", "-v", uf2)          # -v: read back and verify
    pt("reboot")
    print("flashed and rebooted")


def cmd_restore(a):
    ensure_bootsel()
    if a.stock:
        uf2 = os.path.join(STOCK, a.stock, "stock-firmware.uf2")
        if not os.path.exists(uf2):
            sys.exit(f"{uf2} missing: run  python3 tools/fetch_stock_firmware.py {a.stock}")
        sums = dict(reversed(l.split()) for l in open(os.path.join(STOCK, "SHA256SUMS")))
        if sha256(uf2) != sums[f"{a.stock}/stock-firmware.uf2"]:
            sys.exit("stock UF2 checksum mismatch")
        pt("load", "-v", uf2)
    else:
        d = a.backup or latest_backup()
        if not d:
            sys.exit("no backup found")
        m = check_backup(d)
        f = os.path.join(d, m["file"])
        pt("load", "--ignore-partitions", "-u", "-v", f, "-t", "bin", "-o", hex(FLASH_BASE))
        pt("verify", f, "-t", "bin", "-o", hex(FLASH_BASE))
    pt("reboot")
    print("restore complete")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("backup")
    cb = sub.add_parser("check-backup")
    cb.add_argument("--backup")
    fl = sub.add_parser("flash")
    fl.add_argument("--uf2")
    rs = sub.add_parser("restore")
    rs.add_argument("--backup")
    rs.add_argument("--stock", choices=["1.31", "1.5", "1.6"])
    sub.add_parser("bootsel-check")
    a = ap.parse_args()
    if a.cmd == "bootsel-check":
        print("in BOOTSEL" if in_bootsel() else "not in BOOTSEL")
        return
    {"backup": cmd_backup, "check-backup": cmd_check_backup, "flash": cmd_flash, "restore": cmd_restore}[a.cmd](a)


if __name__ == "__main__":
    main()
