#!/usr/bin/env bash
# Ubuntu/Linux installer for the DC32 Display host (current user, X11 session, autostart at login).
#   host/install_linux.sh            install
#   host/install_linux.sh --xorg     also make GDM default to Xorg (disables Wayland login; reversible)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/dc32-display"
VENV="$DATA/venv"

# --- system packages (Ubuntu/Debian) ---
if command -v apt-get >/dev/null; then
  NEED=()
  for p in python3-venv python3-pip libusb-1.0-0 libxss1 xdotool; do
    dpkg -s "$p" >/dev/null 2>&1 || NEED+=("$p")
  done
  if [ ${#NEED[@]} -gt 0 ]; then sudo apt-get update -qq && sudo apt-get install -y -qq "${NEED[@]}"; fi
fi

mkdir -p "$DATA"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e "$HERE"
mkdir -p "$HOME/.local/bin"
ln -sf "$VENV/bin/dc32host" "$HOME/.local/bin/dc32host"
chmod +x "$ROOT/tools/bin/linux/picotool"

# --- USB access without root: badge (1209:0001) and RP2350 bootloader (2e8a:000f, picotool) ---
RULE=/etc/udev/rules.d/70-dc32-display.rules
sudo mkdir -p /etc/udev/rules.d
if [ ! -f "$RULE" ]; then
  sudo tee "$RULE" >/dev/null <<'R'
SUBSYSTEM=="usb", ATTR{idVendor}=="1209", ATTR{idProduct}=="0001", TAG+="uaccess", MODE="0660", GROUP="plugdev"
SUBSYSTEM=="usb", ATTR{idVendor}=="2e8a", ATTR{idProduct}=="000f", TAG+="uaccess", MODE="0660", GROUP="plugdev"
R
  (sudo udevadm control --reload-rules && sudo udevadm trigger) || true
fi
# ModemManager probes new CDC-ACM ports; tell it to leave the badge's debug port alone
if ! grep -q ID_MM_DEVICE_IGNORE "$RULE"; then
  echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="1209", ATTR{idProduct}=="0001", ENV{ID_MM_DEVICE_IGNORE}="1"' | sudo tee -a "$RULE" >/dev/null
  sudo udevadm control --reload-rules || true
fi

# --- autostart: XDG autostart (gets DISPLAY/XAUTHORITY of the login session) + restart loop ---
cat > "$DATA/run.sh" <<R
#!/usr/bin/env bash
while true; do "$VENV/bin/python" -m dc32host run; sleep 3; done
R
chmod +x "$DATA/run.sh"
mkdir -p "$HOME/.config/autostart"
cat > "$HOME/.config/autostart/dc32-display.desktop" <<R
[Desktop Entry]
Type=Application
Name=DC32 Display
Exec=$DATA/run.sh
X-GNOME-Autostart-enabled=true
NoDisplay=true
R

# --- Wayland check ---
if [ "${1:-}" = "--xorg" ]; then
  CONF=/etc/gdm3/custom.conf
  sudo cp -n "$CONF" "$CONF.dc32-backup" 2>/dev/null || true
  if grep -q '^#\?WaylandEnable=' "$CONF"; then
    sudo sed -i 's/^#\?WaylandEnable=.*/WaylandEnable=false/' "$CONF"
  else
    sudo sed -i '/^\[daemon\]/a WaylandEnable=false' "$CONF"
  fi
  echo "GDM now defaults to Xorg (backup: $CONF.dc32-backup). Log out and back in."
elif [ "${XDG_SESSION_TYPE:-}" = "wayland" ]; then
  echo
  echo "!! This is a Wayland session: screen capture and window switching will NOT work."
  echo "   Log out, click the gear icon on the login screen, choose 'Ubuntu on Xorg', log in."
  echo "   (or re-run: host/install_linux.sh --xorg  to make Xorg the default)"
fi

# picotool for backup/flash/restore (official build, checksum-verified; not bundled in the repo)
python3 "$ROOT/tools/get_picotool.py" || echo "!! picotool download failed; run tools/get_picotool.py later"

# Ctrl+Alt+B: runner view <-> mirroring (GNOME only; tools/gnome_shortcuts.sh --remove undoes it)
command -v gsettings >/dev/null && "$ROOT/tools/gnome_shortcuts.sh" || true

pkill -f "$DATA/run.sh" 2>/dev/null || true
pkill -f "dc32host run" 2>/dev/null || true
nohup "$DATA/run.sh" >/dev/null 2>&1 &
echo "Installed. Autostart: ~/.config/autostart/dc32-display.desktop. CLI: ~/.local/bin/dc32host"
echo "Logs: ${XDG_STATE_HOME:-$HOME/.local/state}/dc32-display/logs/host.log"
