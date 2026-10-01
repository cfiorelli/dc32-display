# RESTORE — put the badge back exactly as it was

Everything here only uses `picotool load / verify / reboot`. Nothing touches OTP or fuses, so every
step is reversible.

## 1. Get the badge into BOOTSEL (USB bootloader) mode

Pick whichever works:

* **Running dc32-display firmware:** hold **FN + START + SELECT** for 2 seconds. The screen says
  "USB FLASH MODE". Or run `python -m dc32host bootsel` with the host service stopped.
* **Any firmware, or a badge that won't boot:** unplug and switch the badge off. Flip it face-down,
  hold the **BOOTSEL button on the back** (bottom-left with the back facing you on the tested badge), and plug in USB-C while holding it. Release after 2 s.
  The screen stays dark and a drive called **RP2350** appears.

Check: `python tools/badgetool.py bootsel-check` → `in BOOTSEL`.

## 2a. Exact restore from the raw full-flash backup (preferred)

This restores the original program and also your saves and settings.

```
python tools/badgetool.py restore
```

What it runs, if you want to do it by hand (picotool comes from `python3 tools/get_picotool.py`, which puts it in
`tools/bin/linux/picotool` or `tools\bin\win\picotool.exe`):

```
picotool load --ignore-partitions -u -v backups/<DATE>-original-full-flash/badge-full-flash.bin -t bin -o 0x10000000
picotool verify backups/<DATE>-original-full-flash/badge-full-flash.bin -t bin -o 0x10000000
picotool reboot
```

`backups/LATEST` names the newest backup directory. A second identical copy lives in
`~/dc32-badge-backups/<DATE>-original-full-flash/`. Each backup directory has `manifest.json`,
`SHA256SUMS`, `BACKUP_RECORD.md` (size, SHA-256, date, matching stock version) and
`picotool-info.txt`. Check integrity of both copies first with
`python tools/badgetool.py check-backup`.

## 2b. Independent path: stock DEF CON firmware

Use this if the raw backup were ever lost. It restores the program only, not saves/settings.

```
python tools/badgetool.py restore --stock 1.6        # or 1.31 (original DEF CON release) / 1.5
```

Fetch the images first (not bundled): `python3 tools/fetch_stock_firmware.py 1.6`.
By hand: `picotool load -v backups/stock/1.6/stock-firmware.uf2 && picotool reboot`. You can also drag
`stock-firmware.uf2` onto the RP2350 drive. Checksums are in `backups/stock/SHA256SUMS` and the source
is in `backups/stock/SOURCE.md`.

## 3. Afterwards

* Stop the host service so it doesn't keep looking for the badge. Windows:
  `Disable-ScheduledTask -TaskName "DC32 Display"`. Linux: remove
  `~/.config/autostart/dc32-display.desktop`.
* The microSD card is never touched by dc32-display firmware, so there is nothing to restore there.
