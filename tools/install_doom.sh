#!/usr/bin/env bash
# Doom II on the badge, mirrored from the PC (Ctrl+Alt+P). No sudo: downloads Ubuntu's chocolate-doom,
# Freedoom and the few SDL libraries they need, unpacks them under ~/.local/share/dc32-display/doom and
# writes a doom2.sh launcher (320x240 window = the badge's native size, mirrored 1:1).
# Got the real game? Copy your doom2.wad into that folder; the launcher prefers it over Freedoom.
set -euo pipefail
D="$HOME/.local/share/dc32-display/doom"
PKGS=(chocolate-doom freedoom libsdl2-mixer-2.0-0 libsdl2-net-2.0-0 libmodplug1 libfluidsynth3 libopusfile0 libinstpatch-1.0-2)
mkdir -p "$D"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
(cd "$tmp" && apt-get download "${PKGS[@]}" >/dev/null)
for f in "$tmp"/*.deb; do dpkg-deb -x "$f" "$D"; done
cat > "$D/doom2.sh" <<'EOF'
#!/bin/sh
# Doom II for the badge: Chocolate Doom in a 320x240 window (the badge's native size, mirrored 1:1).
# Uses a real doom2.wad if one is present (put it next to this script), otherwise Freedoom: Phase 2.
D="$(cd "$(dirname "$0")" && pwd)"
export LD_LIBRARY_PATH="$D/usr/lib/x86_64-linux-gnu:$D/usr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
IWAD="$D/doom2.wad"
[ -f "$IWAD" ] || IWAD="$D/usr/share/games/doom/freedoom2.wad"
exec "$D/usr/games/chocolate-doom" -iwad "$IWAD" -window -geometry 320x240 "$@"
EOF
chmod +x "$D/doom2.sh"
missing=$(LD_LIBRARY_PATH="$D/usr/lib/x86_64-linux-gnu:$D/usr/lib" ldd "$D/usr/games/chocolate-doom" | grep "not found" || true)
[ -z "$missing" ] || { echo "still missing libraries:"; echo "$missing"; exit 1; }
echo "installed: $D/doom2.sh  ($( [ -f "$D/doom2.wad" ] && echo doom2.wad || echo 'Freedoom: Phase 2'))"
