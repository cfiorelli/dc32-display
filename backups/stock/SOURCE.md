# Stock DEF CON 32 badge firmware (independent recovery path)

Source: https://github.com/jaku/DEFCON-32-BadgeFirmware (commit 229e351ab33e725f6219dcefbb4aaa7ee033ba09,
2024-08-15). Images are (c) 2024 Dmitry Grinberg, posted there with his permission for use by anyone not
employed by / paid by DEF CON. 1.31 = original DEF CON 32 release, 1.5 = SD card fixes, 1.6 = latest.

`SHA256SUMS` lists the files as downloaded. The .bin files cover flash 0x10000000-0x10300000 (3 MiB).
These restore the *program* only; your saves/settings live elsewhere in flash and are only restored
by the raw full-flash backup in ../<date>-original-full-flash/.
