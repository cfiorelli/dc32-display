#!/usr/bin/env bash
# One-time (sudo): let the logged-in user open the Flipper Zero's USB serial port, like qFlipper's own
# rule, and keep ModemManager off it. Needed for the badge's Flipper view (Ctrl+Alt+F).
set -euo pipefail
RULE=/etc/udev/rules.d/42-flipperzero.rules
cat > "$RULE" <<'EOF'
# Flipper Zero serial port (normal mode) and DFU, user access (dc32-display / qFlipper)
SUBSYSTEMS=="usb", ATTRS{idVendor}=="0483", ATTRS{idProduct}=="5740", ATTRS{manufacturer}=="Flipper Devices Inc.", TAG+="uaccess", ENV{ID_MM_DEVICE_IGNORE}="1"
SUBSYSTEMS=="usb", ATTRS{idVendor}=="0483", ATTRS{idProduct}=="df11", TAG+="uaccess"
EOF
udevadm control --reload-rules
udevadm trigger --subsystem-match=tty
echo "installed $RULE"
