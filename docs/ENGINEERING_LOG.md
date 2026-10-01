# Engineering log

## 2026-10-01 — session 1 (cloud workspace; Dell not reachable)

**Context.** Computer use on the Dell was unavailable this session: no remote-device tools, so no
shell, USB or screen on the Dell. Everything that doesn't need the hardware was built and tested
here. Nothing has been flashed and no backup has been taken yet.

### Phase 1 — hardware verification (done, from source)
* Cloned `neednotapply/DC32-cfw` (descends from Dmitry Grinberg's stock firmware) and
  `jaku/DEFCON-32-BadgeFirmware` (stock images). Findings are in `docs/HARDWARE.md`.
* Key corrections to the brief's assumptions:
  * The panel is **240×320 portrait**. Landscape is a software rotation, `fb[x*240 + 239-y]`.
  * The LCD is driven by **PIO 1-bit SPI** with continuous DMA scanout, not a hardware SPI peripheral.
  * GPIO11 is a **power latch** and must be driven high.
  * The 9th button (GPIO24) is "CENTER"; this project uses it as FN.
  * BOOTSEL is the ROM button on the back, not a GPIO.
* The stock image covers flash up to 0x10400000, including settings at 0x100C0000 and the GB ROM
  at 0x10100000. Hence a full-flash (`save -a`) backup, not a program-only one.

### Phase 2 — backup tooling (done; hardware run pending)
* `tools/badgetool.py`: does two independent `save -a -v` reads, which must hash-identical, then
  `verify` against the device. It also checks for blank/odd sizes, identifies the matching stock
  version, writes a manifest/SHA256SUMS/BACKUP_RECORD and keeps two copies (repo `backups/` +
  `~/dc32-badge-backups/`). `flash` refuses unless the latest backup re-verifies.
* A picotool subcommand allowlist (info/save/verify/load/reboot/version) means OTP and partition
  commands are unreachable.
* Tested the full backup → flash → restore cycle against `tests/fake_picotool.py`. Restore wrote
  the image back and `verify` passed byte-for-byte. No OTP calls.
* Bundled official picotool 2.3.0 for Windows and Linux (checksums in `tools/bin/`). Bundled stock
  firmware 1.31 / 1.5 / 1.6 with SHA-256 (`backups/stock/`).
* Confirmed `save`/`verify`/`load` syntax against picotool 2.2.0 built here with libusb, and the
  2.3.0 release binary.

### Phase 3–7 — firmware (built; hardware run pending)
* pico-sdk 2.2.0, TinyUSB, `-Wall -Wextra -Werror` clean. 41 KB code, 177 KB RAM (incl. 150 KB fb).
* The LCD init sequence, PIO program, SPI bit rate (125 MHz sys, 62.5 Mbit/s) and backlight PWM
  all mirror the stock driver.
* USB: vendor bulk (WinUSB via MS OS 2.0, no driver install) + CDC debug log. RESET_STREAM control
  request plus in-band SYNC hunt for desync recovery.
* The decoder (`firmware/src/decoder.c`) is platform-neutral and the same file is compiled into the
  host tests.
* Badge-native screens: status/disconnected with live button tester, app-switcher menu, BOOTSEL
  notice. Watchdog 3 s. FN+START+SELECT gives software BOOTSEL.

### Phase 4–9 — host (done; Windows paths untested on real Windows)
* Python 3.9+, numpy/Pillow/mss/pyusb/psutil (+python-xlib on Linux). Backends: Win32 via ctypes
  (EnumWindows, DWM bounds, PrintWindow for occluded pinned windows, caret via GetGUIThreadInfo,
  foreground-lock workaround) and X11 via EWMH.
* `tests/test_protocol.py`: 8/8 pass (random frames with random USB chunking, RLE edge cases,
  FILL/COPY, error recovery, menu drop mode).
* `tests/test_e2e_x11.py`: 20/20 pass on Xvfb + openbox + xterm with the software badge.
  Keypress → badge framebuffer: median **27 ms** (1:1), **46 ms** (fit). Terminal scroll ≈29
  updates/s. Found and fixed a rapid double-SELECT MRU bug.
* Wire cost: a keystroke is 18–60 B, a full terminal frame ~42 KB, a scroll ~39 KB, worst-case
  noise 154 KB.

### Open items (need the Dell)
1. OS? Runner dashboard location? (`dc32host find-dashboard`)
2. Backup → flash → acceptance tests (`docs/ACCEPTANCE.md` run book).
3. Real USB numbers: `dc32host bench`, `dc32host latency-test`.
4. Windows-only code paths (winapi.py, scheduled task) on real Windows.
