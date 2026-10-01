# Acceptance tests

Status key: **SIM** = passed against the real firmware decoder + real host daemon on a virtual
X11 desktop (`tests/test_e2e_x11.py`). **OFFLINE** = passed without hardware. **PENDING** = needs
the Dell + badge (run book below).

| # | Test | Status | How (on the Dell) |
|---|---|---|---|
| 1 | Verified full-flash backup of original firmware | PASS (Dell; two identical reads + verify; program matched stock 1.31) | `python tools/badgetool.py backup` |
| 2 | Recovery instructions exist | DONE | `RESTORE.md` |
| 3 | Badge boots custom firmware | PASS (Dell, status screen + all 9 buttons) | `python tools/badgetool.py flash`; status screen shows "DC32 DISPLAY fw 0.1.0" |
| 4 | Host detects badge automatically | PASS (Dell) | start service; log shows `badge connected` |
| 5 | Foreground app appears on badge | PASS (Dell, `tests/hw_dell.py`) | focus any app |
| 6 | Badge follows app switches | PASS (Dell) | Alt+Tab around |
| 7 | Typing appears live | PASS (Dell: keypress→badge ACK median 72 ms 1:1, 84 ms fit) | type in a terminal with the wireless keyboard |
| 8 | Terminal scrolling updates | PASS (Dell, 30 ACKed frames/s) | `ping localhost` / `yes` |
| 9 | Mouse cursor | PASS (Dell) | move mouse over shown window |
| 10 | START opens app list | SIM (host side) | press START |
| 11 | Navigate list with badge | PENDING (firmware-local) | Up/Down |
| 12 | Selecting focuses app | SIM | A |
| 13 | Quick previous/next switching | PASS (Dell, host action) | SELECT short ×2 |
| 14 | Dashboard shortcut (focus if running, start if not) | PARTIAL (Dell: start-if-not-running PASS; focus-if-running FAIL, open) | hold A; run `dc32host find-dashboard --write` first |
| 15 | Pinned mode | PASS (Dell, host action) | hold SELECT once |
| 16 | Whole-desktop mode | PASS (Dell, host action) | hold SELECT twice |
| 17 | Wireless keyboard/mouse unaffected | by design (no HID interface; D-pad never injects keys) | use them normally |
| 18 | Unplug/replug recovery | SIM (host) / PENDING (USB) | unplug 5 s, replug |
| 19 | Service starts after reboot | PASS (Dell 2026-10-01: runner active, host autostarted, badge connected 30 s after login) | reboot Dell |
| 20 | FPS / throughput / latency measured | PASS (Dell: raw 2.9 fps / 452 kB/s, decode 17.8 ms; small-update RTT 0.9 ms; latency-test median 48 ms, p95 79 ms) | `dc32host bench`, `dc32host latency-test`, FN overlay |

## Run book (once the Dell is reachable)

1. Copy `dc32-display` to the Dell (`~/dc32-display`). Log in with **Ubuntu on Xorg**.
2. `host/install_linux.sh` (add `--xorg` to make Xorg the default). This installs the udev rules
   picotool needs to reach the badge without root.
3. `python3 tools/badgetool.py backup` → follow the single physical instruction (BOOTSEL).
   Confirm `BACKUP OK`, two paths and the SHA-256. Record them in `docs/ENGINEERING_LOG.md`.
   Then run `python3 tools/badgetool.py check-backup`.
4. `python3 tools/badgetool.py flash` (refuses without step 3). The badge shows the status screen
   with a live button tester. Press all 9 buttons (test 3 + every button).
5. The already-running host service picks the badge up within about a second.
6. `dc32host find-dashboard` → review the candidates → `dc32host find-dashboard --write --pick N`.
7. Stop the service, then run `dc32host bench` and `dc32host latency-test` (test 20). Start the
   service again.
8. Walk tests 4–19 and tick them here.
