# dc32-display

Turns a **DEF CON 32 Human Badge** (RP2350, 320×240 LCD) into a live USB mini-display and
controller for a Linux PC. It mirrors the app you're using (and follows your typing at 1:1, so text
is readable), has a badge-native home menu and app switcher, and can show a badge-native GitHub
Actions runner cost view.

```
 PC (host daemon, Python)                                    badge (firmware, C / pico-sdk)
 capture window ─ scale/zoom ─ RGB565 ─ tile diff ─ FILL/RLE/RAW ──USB bulk──► decoder ─► RAM fb ─► DMA ─► LCD
 window mgmt  ◄─ button actions ◄────────────── BUTTON / MENU_RESULT / ACK ◄── buttons, menus
```

## Photos

<!-- Add photos here, e.g. docs/img/badge-terminal.jpg, docs/img/runner-view.jpg -->
_Photos coming soon: the badge mirroring a terminal at 1:1, the runner cost view, the home menu._

## Buttons

| Control | Action |
|---|---|
| **FN** (center) tap | home menu: Mirror screen · Runner costs · Badge terminal · Switch app · Zoom · Open dashboard on PC · Pause · Status info |
| **A** tap | zoom: fit → 2:1 → 1:1 (zoomed views follow your typing or the mouse) |
| **A** hold | runner cost view on/off |
| **B** tap | back to fit (and back/cancel in menus) |
| **SELECT** tap | switch to the previous app (tap again to come back) |
| **SELECT** hold | cycle display mode: Follow active → Pinned → Whole desktop |
| **START** tap | app switcher (Up/Down move, **A** focus, **RIGHT** pin to badge without changing PC focus, **B** cancel) |
| **START** hold | pin the app currently shown |
| **D-pad** | move the zoomed view. It never sends keys to the PC |
| **FN + B** | info overlay: mode, app, FPS, throughput, latency, versions |
| **FN + Up/Down** | brightness |
| **FN + START + SELECT** hold 2 s | reboot into the USB bootloader (for re-flashing) |

Typing while in fit mode zooms to 1:1 around the caret and stays there until **B**. Only screen
changes that follow a keystroke move the view, so spinners, clocks and page loads don't drag it
around. The backlight dims after 10 minutes without PC or badge input; any input wakes it.

All mappings live in `~/.config/dc32-display/config.json` (`"buttons"`).

### Keyboard shortcuts (GNOME, installed by `tools/gnome_shortcuts.sh`)

| Keys | Action |
|---|---|
| Ctrl+Alt+B | runner cost view ↔ screen mirroring |
| Ctrl+Alt+Z / Ctrl+Alt+X | zoom cycle / fit |
| Ctrl+Alt+I / J / K / L | move the badge view up / left / down / right |

Any action can be sent from a script: `dc32host ctl <action>` (e.g. `toggle_runner`, `home_menu`,
`zoom_cycle`, `pan_left`, `toggle_pause`). `dc32host restart` restarts (or starts) the service.

## Setup (Ubuntu, X11 session)

The session must be **X11**: GNOME on Wayland blocks screen capture and window switching for tools
like this. On the login screen pick **"Ubuntu on Xorg"**, or run the installer with `--xorg`.

```
host/install_linux.sh            # deps, udev rules, venv, autostart, picotool download, shortcuts (--xorg: default to Xorg)
python3 tools/badgetool.py backup        # mandatory before flashing: full-flash backup, two reads + verify, 2 copies
python3 tools/badgetool.py flash         # refuses without a verified backup
dc32host find-dashboard --write          # optional: locate your runner cost dashboard script
```

* **Entering the bootloader (BOOTSEL):** with the badge unplugged and batteries out, hold the
  BOOTSEL button on the back and plug in USB. On the badge this was tested on, that is the
  **bottom-left** button with the back facing you. Another back button is reset: holding it keeps
  the screen dark and nothing appears on USB. Keep holding until the PC lists `2e8a:000f`.
* **picotool** is not bundled: `python3 tools/get_picotool.py` downloads the official 2.3.0 build and
  checks its SHA-256 (the installer does this for you).
* **Stock firmware** is not bundled either (it's Dmitry Grinberg's): `python3 tools/fetch_stock_firmware.py`
  downloads the DEF CON images from a pinned commit and checks them. You only need it for
  `badgetool.py restore --stock`; your own full-flash backup is the preferred restore. See `RESTORE.md`.
* **Monitor on another input?** If the PC's only monitor is switched to another input, the kernel
  sees it as unplugged and GNOME repaints about once a second, so the badge lags ~1 s.
  `sudo tools/dell_force_display.sh` keeps the HDMI connector "connected" using the monitor's own
  EDID (reboot to apply, `--remove` undoes it). An HDMI dummy plug does the same in hardware.

Config: `~/.config/dc32-display/config.json`. Logs: `~/.local/state/dc32-display/logs/host.log`.
Autostart: `~/.config/autostart/dc32-display.desktop` (supervised restart loop). A Windows
installer (`host/install_windows.ps1`) exists but is untested on real Windows.

## Runner cost view

A badge-native 320×240 screen: runner state (idle / busy + current job), two bar strips of estimated
cost per 30 minutes over the last 12 hours (GitHub-hosted vs. what the self-hosted runner saved, on
one shared scale), and month-to-date billed totals. GitHub is polled every 30 minutes and the
runner's local systemd/journald state every 10 seconds. When the terminal dashboard window is on
screen, the badge shows this view instead of the (unreadable at 320×240) terminal.

It reuses your own dashboard script as a Python module (configured via `find-dashboard` or
`"runner_view": {"script": ...}`), so tokens and org/repo settings stay in one place. The module
must provide: `load_token`, `TOKEN_FILE`, `CACHE_FILE`, `UTC`, `SELF_HOSTED_SINCE`, `Api`, `State`,
`fetch_billing`, `fetch_runners`, `fetch_runs`, `fetch_local` and `compute` (see
`host/dc32host/runner_view.py` for the fields used). No script, no runner view; everything else works.

## Layout

```
firmware/   pico-sdk 2.2 firmware (LCD scanout, buttons, TinyUSB vendor+CDC, decoder, menus)
  dist/     built dc32_display.uf2 + SHA-256 + BUILDINFO
host/       dc32host Python package + Linux/Windows installers
protocol/   PROTOCOL.md + shared C header
tools/      badgetool.py (backup / flash / restore), get_picotool.py, fetch_stock_firmware.py,
            build_firmware.sh, gnome_shortcuts.sh, dell_*.sh (setup helpers for the test machine)
tests/      decoder<->encoder round trip, full-daemon E2E on Xvfb, hw_dell.py (real badge + desktop)
docs/       HARDWARE.md (verified pin map), ACCEPTANCE.md, ENGINEERING_LOG.md
backups/    badge backups land here (never committed) + stock firmware checksums
RESTORE.md  how to put the badge back exactly as it was
```

## Design notes

* The USB link is full-speed (12 Mbit/s; ~450 kB/s measured for raw frames), so the host sends only
  changed 16×16 tiles, merged into rectangles, each as FILL, RLE16 or RAW, whichever is smallest.
  A keystroke costs tens of bytes.
* The badge never blocks on the USB link. A 2-channel DMA loop streams the RAM framebuffer to the
  panel at ~50 Hz, and the decoder writes straight into that RAM.
* Latency is bounded by flow control (≤2 un-ACKed frames). After a keystroke the host polls at
  100 Hz for a moment, so the echo reaches the badge as soon as the app draws it (~50–80 ms
  measured end to end on the test machine).
* Buttons send semantic events. There is no HID interface, so the real keyboard and mouse are untouched.
* Backup / flash / restore only use picotool `info/save/verify/load/reboot`; OTP and partition
  commands are unreachable by design.

## Credits

* **Dmitry Grinberg** — the DEF CON 32 badge firmware. The hardware reference (pin map, LCD init,
  PIO SPI timing, power latch) was verified against
  [DC32-cfw](https://github.com/neednotapply/DC32-cfw), which descends from his firmware; the stock
  images are fetched from [jaku/DEFCON-32-BadgeFirmware](https://github.com/jaku/DEFCON-32-BadgeFirmware).
* **[GNU Unifont](https://unifoundry.com/unifont/)** — the badge's 8×16 font, under the SIL Open Font
  License 1.1 (see `LICENSES/OFL-1.1-Unifont.txt`).
* **[pico-sdk](https://github.com/raspberrypi/pico-sdk)**, **[TinyUSB](https://github.com/hathach/tinyusb)**
  and **[picotool](https://github.com/raspberrypi/picotool)** — Raspberry Pi Ltd and contributors (BSD-3-Clause / MIT).

## License

MIT (see `LICENSE`), except the Unifont-derived glyph data in `firmware/src/font8x16.h` (OFL-1.1).
Not affiliated with DEF CON.
