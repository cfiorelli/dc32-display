# Acceptance tests

Status key: **SIM** = passed against the real firmware decoder + real host daemon on a virtual
X11 desktop (`tests/test_e2e_x11.py`). **OFFLINE** = passed without hardware. **PENDING** = needs
the Dell + badge (run book below).

| # | Test | Status | How (on the Dell) |
|---|---|---|---|
| 1 | Verified full-flash backup of original firmware | PENDING (tool tested vs fake picotool) | `python tools/badgetool.py backup` |
| 2 | Recovery instructions exist | DONE | `RESTORE.md` |
| 3 | Badge boots custom firmware | PENDING | `python tools/badgetool.py flash`; status screen shows "DC32 DISPLAY fw 0.1.0" |
| 4 | Host detects badge automatically | SIM | start service; log shows `badge connected` |
| 5 | Foreground app appears on badge | SIM | focus any app |
| 6 | Badge follows app switches | SIM | Alt+Tab around |
| 7 | Typing appears live | SIM (keypress→badge fb median 27 ms at 1:1, 46 ms fit) | type in a terminal with the wireless keyboard |
| 8 | Terminal scrolling updates | SIM (≈29 updates/s) | `ping -t localhost` / `yes` |
| 9 | Mouse cursor | SIM | move mouse over shown window |
| 10 | START opens app list | SIM (host side) | press START |
| 11 | Navigate list with badge | PENDING (firmware-local) | Up/Down |
| 12 | Selecting focuses app | SIM | A |
| 13 | Quick previous/next switching | SIM | SELECT short ×2 |
| 14 | Dashboard shortcut | SIM (with stand-in dashboard) | hold A; run `dc32host find-dashboard --write` first |
| 15 | Pinned mode | SIM | hold SELECT once |
| 16 | Whole-desktop mode | SIM | hold SELECT twice |
| 17 | Wireless keyboard/mouse unaffected | by design (no HID interface; D-pad never injects keys) | use them normally |
| 18 | Unplug/replug recovery | SIM (host) / PENDING (USB) | unplug 5 s, replug |
| 19 | Service starts after reboot | PENDING | reboot Dell |
| 20 | FPS / throughput / latency measured | PENDING | `dc32host bench`, `dc32host latency-test`, FN overlay |

## Run book (once the Dell is reachable)

1. `git clone`/copy `dc32-display` to the Dell, e.g. `C:\dc32-display` or `~/dc32-display`.
2. `python tools/badgetool.py backup` → follow the single physical instruction (BOOTSEL).
   Confirm `BACKUP OK`, two paths and the SHA-256. Record them in `docs/ENGINEERING_LOG.md`.
3. `python tools/badgetool.py check-backup`
4. `python tools/badgetool.py flash` (refuses without step 2). The badge shows the status screen
   with a live button tester. Press all 9 buttons (test 3 + every button).
5. Install the host: Windows `powershell -ExecutionPolicy Bypass -File host\install_windows.ps1`,
   Linux `host/install_linux.sh`.
6. `dc32host find-dashboard` → review the candidates → `dc32host find-dashboard --write --pick N`.
7. Stop the service, then run `dc32host bench` and `dc32host latency-test` (test 20). Start the
   service again.
8. Walk tests 4–19 and tick them here.
