#!/usr/bin/env bash
# Installs the DC32 Display host for the current user (X11 session) and starts it at login.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/dc32-display"
VENV="$DATA/venv"
mkdir -p "$DATA"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e "$HERE"

# USB access without root: badge (1209:0001) and RP2350 bootloader (2e8a:000f) for picotool
RULE=/etc/udev/rules.d/70-dc32-display.rules
if [ ! -f "$RULE" ]; then
  sudo tee "$RULE" >/dev/null <<'R'
SUBSYSTEM=="usb", ATTR{idVendor}=="1209", ATTR{idProduct}=="0001", TAG+="uaccess", MODE="0660", GROUP="plugdev"
SUBSYSTEM=="usb", ATTR{idVendor}=="2e8a", ATTR{idProduct}=="000f", TAG+="uaccess", MODE="0660", GROUP="plugdev"
R
  sudo udevadm control --reload-rules && sudo udevadm trigger
fi

cat > "$DATA/run.sh" <<R
#!/usr/bin/env bash
# supervisor: restart the daemon if it ever exits
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
if [ "${XDG_SESSION_TYPE:-x11}" = "wayland" ]; then
  echo "WARNING: Wayland session detected. Screen capture needs an X11 session (log in with 'Ubuntu on Xorg')."
fi
nohup "$DATA/run.sh" >/dev/null 2>&1 &
echo "Installed. Starts at login via ~/.config/autostart/dc32-display.desktop. Logs: ${XDG_STATE_HOME:-$HOME/.local/state}/dc32-display/logs/host.log"
