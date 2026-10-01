#!/usr/bin/env python3
"""Fake picotool for testing tools/badgetool.py without hardware. State in $FAKE_PT_DIR."""
import os, sys, shutil
D = os.environ["FAKE_PT_DIR"]
flash = os.path.join(D, "flash.bin")
a = sys.argv[1:]
log = open(os.path.join(D, "calls.log"), "a"); log.write(" ".join(a) + "\n")
cmd = a[0]
assert cmd in {"info", "save", "verify", "load", "reboot", "version"}, cmd
if cmd == "version": print("picotool v2.2.0 (fake)"); sys.exit(0)
if not os.path.exists(os.path.join(D, "bootsel")):
    print("No accessible RP-series devices in BOOTSEL mode were found."); sys.exit(1)
if cmd == "info": print("Program Information\n none\nDevice Information\n type: RP2350\n flash size: 4096K"); sys.exit(0)
if cmd == "save":
    out = [x for x in a[1:] if not x.startswith("-") and x != "bin"][0]
    shutil.copy(flash, out); sys.exit(0)
pos = [x for x in a[1:] if not x.startswith("-")]
if cmd == "verify":
    f = pos[0]; ok = open(f, "rb").read() == open(flash, "rb").read()[:os.path.getsize(f)]
    print("verify", "OK" if ok else "FAILED"); sys.exit(0 if ok else 1)
if cmd == "load":
    f = pos[0]; data = open(f, "rb").read()
    if f.endswith(".uf2"):
        import struct
        img = bytearray(open(flash, "rb").read())
        for i in range(0, len(data), 512):
            addr, = struct.unpack_from("<I", data, i + 12); n, = struct.unpack_from("<I", data, i + 16)
            img[addr - 0x10000000: addr - 0x10000000 + n] = data[i + 32:i + 32 + n]
        open(flash, "wb").write(img)
    else:
        img = bytearray(open(flash, "rb").read()); img[:len(data)] = data; open(flash, "wb").write(img)
    sys.exit(0)
if cmd == "reboot": os.remove(os.path.join(D, "bootsel")); sys.exit(0)
