# dc32-display

Turns a **DEF CON 32 Human Badge** (RP2350, 320×240 LCD) into a live USB mini-display and
controller for a PC. It shows the focused app as you type, has a badge-native app switcher, and
gives one-button access to the GitHub runner cost dashboard.

```
 PC (host daemon, Python)                                    badge (firmware, C / pico-sdk)
 capture window ─ scale/zoom ─ RGB565 ─ tile diff ─ FILL/RLE/RAW ──USB bulk──► decoder ─► RAM fb ─► DMA ─► LCD
 window mgmt  ◄─ button actions ◄────────────── BUTTON / MENU_RESULT / ACK ◄── buttons, app menu
```

## Buttons

| Control | Action |
|---|---|
| **SELECT** tap | switch to the previous app (tap again to come back) |
| **SELECT** hold | cycle display mode: Follow active → Pinned → Whole desktop |
| **START** tap | app switcher on the badge (Up/Down move, **A** focus, **RIGHT** pin to badge, **B** cancel) |
| **START** hold | pin the app currently shown |
| **A** hold | **GitHub Runner Dashboard**: focus it, or start it if it isn't running |
| **A** tap | zoom: fit → 2:1 → 1:1 (zoomed views follow caret / typing / mouse) |
| **B** tap | back to fit (and back/cancel in menus) |
| **D-pad** | pan the zoomed view only. It never sends keys to the PC |
| **FN** tap | info overlay: mode, app, FPS, throughput, latency, versions |
| **FN + Up/Down** | brightness |
| **FN + START + SELECT** hold 2 s | reboot into USB bootloader (for re-flashing) |

All mappings live in `config.json` (`"buttons"`). Optional key injection (`"key:up"` etc.) is off by
default.

## Layout

```
firmware/   pico-sdk 2.2 firmware (LCD scanout, buttons, TinyUSB vendor+CDC, decoder, menus)
  dist/     built dc32_display.uf2 + SHA-256 + BUILDINFO
host/       dc32host Python package + Windows/Linux installers
protocol/   PROTOCOL.md + shared C header
tools/      badgetool.py (backup / flash / restore), build_firmware.sh, bundled picotool 2.3.0
tests/      decoder<->encoder round trip (native C), full-daemon E2E on Xvfb, fake picotool
docs/       HARDWARE.md (verified pin map), ACCEPTANCE.md, ENGINEERING_LOG.md
backups/    raw badge backups (created on the Dell) + stock DEF CON firmware with checksums
RESTORE.md  how to put the badge back exactly as it was
```

## Quick start (Ubuntu on the Dell)

The session must be **X11**: on the login screen click the gear icon and choose
**"Ubuntu on Xorg"** (or run the installer with `--xorg` to make that the default). Under Wayland,
GNOME blocks screen capture and window switching for tools like this. The badge then shows a
message saying so instead of a black screen.

```
host/install_linux.sh                     # first: deps + udev rules (picotool needs them), autostart (add --xorg if needed)
python3 tools/badgetool.py backup         # mandatory before flashing: full-flash backup, verified, 2 copies
python3 tools/badgetool.py flash          # refuses without a verified backup
dc32host find-dashboard --write           # locate the runner cost dashboard
```

Config: `~/.config/dc32-display/config.json`. Logs: `~/.local/state/dc32-display/logs/host.log`.
Autostart: `~/.config/autostart/dc32-display.desktop` (supervised restart loop). A Windows
installer (`host/install_windows.ps1`) also exists, but Ubuntu is the target and the tested path.

## Design notes

* The USB link is full-speed (12 Mbit/s, ~1 MB/s in practice), so a raw full frame (150 KB) would
  cap at ~6 fps. Instead the host sends only changed 16×16 tiles, merged into rectangles. Each
  rectangle goes out as FILL, RLE16 or RAW, whichever is smallest. A keystroke costs tens of bytes;
  a full terminal frame is ~42 KB.
* The badge never blocks on the USB link. A 2-channel DMA loop streams the RAM framebuffer to the
  panel at ~50 Hz, and the decoder writes straight into that RAM.
* Latency is bounded by flow control (≤2 un-ACKed frames). The host also wakes ~12 ms after any
  local keyboard/mouse input instead of waiting for the next poll.
* Buttons send semantic events. No HID keyboard interface exists, so the real keyboard and mouse
  are untouched.
