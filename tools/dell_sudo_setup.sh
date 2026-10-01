#!/usr/bin/env bash
# One-time root steps for firmware development on the Dell (run once: sudo tools/dell_sudo_setup.sh).
#  * ARM toolchain + cmake/ninja for tools/build_firmware.sh
#  * python3-tk for `dc32host latency-test` (it also has an Xlib fallback)
#  * lets the logged-in user read the badge's CDC debug log (/dev/ttyACM*) without the dialout group
# Touches nothing else (no services, no network config).
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
apt-get install -y gcc-arm-none-eabi libnewlib-arm-none-eabi libstdc++-arm-none-eabi-newlib cmake ninja-build python3-tk
cat > /etc/udev/rules.d/71-dc32-display-tty.rules <<'EOF'
SUBSYSTEM=="tty", ATTRS{idVendor}=="1209", ATTRS{idProduct}=="0001", TAG+="uaccess", MODE="0660"
EOF
udevadm control --reload-rules
udevadm trigger --subsystem-match=tty
echo "dell_sudo_setup: done"
