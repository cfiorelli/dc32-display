# Engineering log

> **WHERE WE ARE (2026-10-01 ~15:15).** Badge on fw 0.1.0, Dell on Xorg, host autostarts; reboot test passed
> (runner, host service and badge all came back unattended). New host features: badge home menu (FN),
> badge-native runner view (A-hold / Ctrl+Alt+B), Badge Terminal, keystroke-only caret follow, typing zoom
> without timeout, backlight idle-dim, `dc32host ctl/restart`, GNOME shortcuts. `tests/hw_dell.py` 17/17.
> Open: intermittent badge USB re-enumeration (CDC debug log now captured to badge-cdc.log).

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

## 2026-10-01 — Dell is Ubuntu (correction from the owner)

* Ubuntu is now the primary target. The Linux/X11 backend was already the one exercised end-to-end.
* The main risk is the **Wayland** default session: XWayland can't capture or focus native apps.
  The host detects it, logs it, and shows instructions on the badge. `install_linux.sh --xorg`
  makes GDM default to Xorg (backup of `/etc/gdm3/custom.conf` kept). Decision: run on Xorg rather
  than build a portal/PipeWire + GNOME-extension backend.
* Installer now does `apt` deps (python3-venv, libusb, libxss1, xdotool), udev rules for the badge
  and the RP2350 bootloader, and a ModemManager ignore for the CDC debug port. It links
  `~/.local/bin/dc32host`. Tested in a scratch home.
* gnome-terminal ignores `--title`, so launched dashboards set their title with an OSC escape
  before `exec`. Matching by command line now skips terminal-server pids that own several
  windows; that would otherwise match any gnome-terminal window.
* Daemon startup failures (e.g. display not ready at login) are logged and retried every 5 s.
* E2E suite: 21/21 pass, including "dashboard not running → A-long starts it and shows it".

## 2026-10-01 — on the Dell: badge backup

* **BACKUP OK** (`badgetool.py backup`, two identical reads + `picotool verify`; `check-backup` passed).
  * two copies (repo `backups/` + `~/dc32-badge-backups/`), path + SHA-256 recorded locally (not published);
  * 4194304 bytes (4 MiB flash), program matches stock **1.31**.
* BOOTSEL lessons: hold **only** the BOOTSEL button on the back, which on this badge is **bottom-left** when looking at the back (not top-right as documented; another back button holds the chip
  in reset → dark screen, nothing on USB). Batteries out. Keep holding until the PC sees `2e8a:000f`.
  The stock "Firmware update" menu item is an SD-card updater, not BOOTSEL.
* An earlier attempt dropped off USB at 21% of the first read; its partial folder
  `backups/<date>-original-full-flash/` (no manifest, never in `LATEST`) is unusable and can be deleted.

## 2026-10-01 — on the Dell: flash + acceptance (Xorg session)

* Flashed fw 0.1.0+e8aa168 (`badgetool.py flash`, `load -v` verified). Status screen + all 9 buttons OK.
* Dashboard favorite: the owner's terminal runner-cost dashboard script (find-dashboard candidate [0]).
* `install_linux.sh --xorg` run by the owner; session is now x11. Runner service stayed `active` throughout.
* `dc32host bench`: raw full frames 2.9 fps / 452 kB/s (below the ~1 MB/s design estimate), decode 17.8 ms/frame,
  small-update RTT 0.9 ms. `latency-test`: median 48 ms, p95 79 ms (n=30).
* `latency-test` needed tkinter (not installed on Ubuntu); added an Xlib fallback in `host/dc32host/latency.py`.
* New `tests/hw_dell.py`: the E2E checks on the real desktop + real badge (every frame must be ACKed by the
  badge). 10/11 pass. FAIL: dashboard "focus if already running" (second A-hold didn't refocus the same window).
* **Incident:** the first version closed its terminals with `xdotool windowclose`, which killed
  gnome-terminal-server and closed all of the owner's terminal windows. Fixed: it now types `exit`.
* **Open:** the badge dropped off USB twice (once recovered in 1 s during latency-test; once screen went
  blank and needed a replug). Cause unknown: cable/port, power, or a firmware hang. Earlier, a BOOTSEL
  read also dropped at 21%. Needs watching.
* Not yet done physically: 10–12 (START menu on badge), 17, 18 (unplug/replug), 19 (reboot).

## 2026-10-01 — monitor input switching, reboot test

* With the HDMI monitor switched to another input, the kernel reports the connector as disconnected and
  GNOME repaints about once a second, so the mirrored window lagged 0.4–1.3 s (keypress -> badge median
  ~850 ms). `tools/dell_force_display.sh` pins the connector with the monitor's EDID
  (`drm.edid_firmware=` + `video=HDMI-A-1:e`, EDID copied into the initramfs). After reboot: median 81 ms,
  p95 218 ms with the monitor on another input.
* Reboot test (ACCEPTANCE 19): runner service active, host autostarted at login, badge connected unattended.
* Ubuntu's python3-rich 11.2 lacks `Table.add_section`, so the terminal dashboard crashed when launched
  from the badge; the host venv now ships `rich>=13` and launches Python dashboards with it.
* Dashboard matching by command line could pick an unrelated window owned by gnome-terminal-server
  (one process owns every terminal window); shared window owners are now skipped.
* X11 window rects now exclude GTK client-side-decoration shadows (`_GTK_FRAME_EXTENTS`).

## 2026-10-26 — Dell host title and stall recovery

The runner header uses the local hostname (`runner-host`) rather than the first API runner or systemd unit, so it names the machine running both `runner-a` and `runner-b`.

The original PID 169879 stopped main-loop stats at 2026-10-25 15:52:30 PDT while the GitHub worker continued from 2567/5111 to 4959/5187 runs. Its main thread was runnable with 77.6% cumulative CPU; USB/worker threads remained alive. Logs and per-thread wait states were saved before termination. `py-spy dump` was denied by Linux ptrace policy; the original host had no SIGUSR1 handler. No original Python stack was obtainable through those methods. After a diagnostic restart, SIGUSR1 stacks showed the main loop waiting normally for input while the GitHub thread was in HTTPS fetches. An offline reconstructed 50% snapshot rendered and encoded successfully. The precise original spin location is **unconfirmed**, not attributed to the API worker.

The existing background USB keepalive and ongoing fetch logs masked the stalled main loop. A separate progress record now advances only after a completed main-loop iteration (including sleep/menu/USB-disconnected paths). Stack-only diagnostics are registered before startup. The watchdog checks this progress, requests all thread stacks after 90 seconds without progress, terminates the identified Python child, and escalates to SIGKILL after three seconds if needed. The existing `run.sh` restarts the child. UID, executable, argv tokens and process start ticks are checked, and pidfds prevent signalling a reused PID. No process command line, environment or token is printed.

Install the explicitly requested user timer after updating the host code:

```sh
mkdir -p ~/.config/systemd/user
cp host/systemd/dc32-display-watchdog.{service,timer} ~/.config/systemd/user/
loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user enable --now dc32-display-watchdog.timer
loginctl show-user "$USER" -p Linger
```

Unit: `dc32-display-watchdog.timer`. Interval: 30 seconds, accuracy one second. Recovery begins after 90–121 seconds of stalled progress. Progress: `~/.local/state/dc32-display/heartbeat.json`; diagnostic stacks: `~/.local/state/dc32-display/logs/stacks.log`. A missing/obsolete heartbeat never authorizes killing an unrelated process. The timer does not launch a GUI session or replace XDG autostart.

Targeted validation: 34 tests passed (runner rendering, blocked API fetch/input independence, protocol, scheduling and watchdog decisions). The broader available suite had 108 passes and six failures: three missing optional scipy dependencies and three existing sleep-test stubs missing `view`; the latter were reproduced against the unchanged base daemon. X11 end-to-end/hardware-button tests were not run as part of that suite.

### Recovery acceptance (2026-10-26T00:53:43Z)

* The live SIGSTOP drill stopped PID 1565186; the timer replaced it with PID 1651725 after 110.0 seconds. The completed drill recorded success at 2026-10-26T00:48:38.255053Z in `~/.local/state/dc32-display/health-evidence/watchdog-recovery.json`. No drill process remains running.
* Post-recovery acceptance at 2026-10-26T00:52:18Z: PID 1651725's heartbeat advanced; `toggle_report` and `view_runner` were each handled within the 50 ms sampling interval. The associated USB frame stats reported 2.6 ms average ACK RTT and 38.0 ms average capture-to-badge latency. Evidence: `health-evidence/post-recovery-input.json` and `logs/host.log` beneath the same state directory.
* X11-injected Ctrl+Alt+B on the Dell reached `ctl: toggle_report` at 2026-10-25 17:52:39.157 PDT and switched to the Needs-you report. Following USB stats reported 0.6 ms average ACK RTT. This exercises the configured desktop shortcut; physical badge buttons were not pressed during this follow-up.
* Backfill reached 5189/5189 at 2026-10-25 17:52:13.096 PDT. A fresh SIGUSR1 dump again showed the main thread waiting in `queue.get` through `drain_events`, not a blocked backfill lock. `dc32-display-watchdog.timer` is enabled and active, and the user has `Linger=yes`.
* Investigation disposition: recovery and current input/rendering are verified; the original spin trigger and a permanent trigger-specific fix remain unconfirmed. The original process was not instrumented and its stack cannot be reconstructed from later healthy samples. On recurrence, preserve the watchdog journal, `logs/stacks.log` (and `.previous.log` if present), heartbeat and host log before restarting or updating the host. No repeated restart or speculative backfill rewrite is justified by these observations.

Done: host-wide title, deployed watchdog, live recovery and shortcut/USB acceptance. Left: identify the original spin trigger from a recurrence stack or a reproducer; physical button acceptance.
