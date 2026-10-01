#!/usr/bin/env bash
# Keep the Dell's HDMI monitor "connected" when the monitor is switched to another input.
#
# Why: when the monitor shows another input, the kernel sees HDMI as unplugged, GNOME has no display
# to draw for and repaints about once a second, so the badge mirror lags ~1 s. This pins the
# connector on with the monitor's own EDID, so GNOME keeps drawing at full rate.
#
#   sudo tools/dell_force_display.sh            install (monitor must be on this PC's input right now)
#   sudo tools/dell_force_display.sh --remove   undo (restores /etc/default/grub from its backup)
# Takes effect after a reboot. Touches only: /lib/firmware/edid/, an initramfs hook, /etc/default/grub.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
CONN=${DC32_CONNECTOR:-HDMI-A-1}
EDID=/lib/firmware/edid/dc32-monitor.bin
HOOK=/etc/initramfs-tools/hooks/dc32-edid
GRUB=/etc/default/grub
ARGS="drm.edid_firmware=$CONN:edid/dc32-monitor.bin video=$CONN:e"

if [ "${1:-}" = "--remove" ]; then
  [ -f "$GRUB.dc32-backup" ] && cp "$GRUB.dc32-backup" "$GRUB"
  rm -f "$HOOK" "$EDID"
  update-initramfs -u
  update-grub
  echo "removed; reboot to apply"
  exit 0
fi

SYS=$(ls -d /sys/class/drm/card*-"$CONN" | head -1)
[ "$(cat "$SYS/status")" = connected ] || { echo "$CONN is not connected: switch the monitor to this PC first"; exit 1; }
mkdir -p /lib/firmware/edid
cat "$SYS/edid" > "$EDID.tmp"
[ "$(stat -c %s "$EDID.tmp")" -ge 128 ] || { echo "could not read EDID from $SYS"; rm -f "$EDID.tmp"; exit 1; }
mv "$EDID.tmp" "$EDID"

# i915 loads from the initramfs, so the EDID file has to be in there too
cat > "$HOOK" <<'H'
#!/bin/sh
[ "$1" = prereqs ] && exit 0
. /usr/share/initramfs-tools/hook-functions
mkdir -p "$DESTDIR/lib/firmware/edid"
cp /lib/firmware/edid/dc32-monitor.bin "$DESTDIR/lib/firmware/edid/"
H
chmod +x "$HOOK"

cp -n "$GRUB" "$GRUB.dc32-backup"
if ! grep -q 'drm.edid_firmware=' "$GRUB"; then
  sed -i "s|^GRUB_CMDLINE_LINUX_DEFAULT=\"\(.*\)\"|GRUB_CMDLINE_LINUX_DEFAULT=\"\1 $ARGS\"|" "$GRUB"
fi
grep '^GRUB_CMDLINE_LINUX_DEFAULT' "$GRUB"
update-initramfs -u
update-grub
echo "dell_force_display: done; reboot to apply (undo: sudo $0 --remove)"
