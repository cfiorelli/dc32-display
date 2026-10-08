#!/usr/bin/env bash
# GNOME keyboard shortcuts for the badge (user-level, no sudo). Re-running is safe; --remove undoes them.
#   Ctrl+Alt+B        runner costs view <-> screen mirroring
#   Ctrl+Alt+H        cheat sheet of all shortcuts on the badge (number keys run them while it's open)
#   Ctrl+Alt+R        refresh the runner dashboard's GitHub data now
#   Ctrl+Alt+P        Doom II, mirrored on the badge (install: tools/install_doom.sh)
#   Ctrl+Alt+E        IR scope: what the badge's IR receiver sees (remotes), decoded
#   Ctrl+Alt+F        Flipper Zero (USB) screen on the badge, badge buttons drive it
#   Ctrl+Alt+W        RTL-SDR spectrum + waterfall (arrows tune/step, Enter = next band)
#   Ctrl+Alt+V        RF bench (with Ctrl+Alt+Y: Enter arm, Space replay+verify, Shift+Enter self-test)
#   Ctrl+Alt+M        command menu: every action, arrow keys + Enter (no reaching for the badge)
#   Ctrl+Alt+Y        keyboard -> badge view (Flipper / spectrum / bench) and back; off on every view change
#   Ctrl+Alt+S        sleep: backlight off until any keyboard/mouse/badge input
#   Ctrl+Alt+G        next light mode (off, bright, wave, rainbow, rave, runner status)
#   Ctrl+Alt+Z        zoom: fit -> 2:1 -> 1:1        (B on the badge / Ctrl+Alt+X: back to fit)
#   Ctrl+Alt+I/J/K/L  move the badge view up/left/down/right (when zoomed)
set -euo pipefail
SCHEMA=org.gnome.settings-daemon.plugins.media-keys
BASE=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings
BIN="$HOME/.local/bin/dc32host"
KEYS=(
  "dc32-runner|DC32 badge: runner view / mirror|<Primary><Alt>b|toggle_runner"
  "dc32-help|DC32 badge: shortcut cheat sheet|<Primary><Alt>h|toggle_help"
  "dc32-refresh|DC32 badge: refresh runner data now|<Primary><Alt>r|refresh_data"
  "dc32-doom2|DC32 badge: play Doom II (mirrored)|<Primary><Alt>p|doom2"
  "dc32-ir|DC32 badge: IR scope|<Primary><Alt>e|ir_scope"
  "dc32-flipper|DC32 badge: Flipper Zero screen + control|<Primary><Alt>f|flipper"
  "dc32-sdr|DC32 badge: SDR spectrum + waterfall|<Primary><Alt>w|sdr_view"
  "dc32-bench|DC32 badge: RF bench (capture, decode, Flipper replay, verify)|<Primary><Alt>v|rf_bench"
  "dc32-cmd|DC32 badge: command menu (all actions, keyboard)|<Primary><Alt>m|command_menu"
  "dc32-keyboard|DC32 badge: hand the keyboard to the badge view / back|<Primary><Alt>y|keyboard"
  "dc32-lights|DC32 badge: next light mode|<Primary><Alt>g|lights_next"
  "dc32-sleep|DC32 badge: sleep (screen off, any input wakes)|<Primary><Alt>s|sleep"
  "dc32-zoom|DC32 badge: zoom|<Primary><Alt>z|zoom_cycle"
  "dc32-fit|DC32 badge: fit whole window|<Primary><Alt>x|zoom_fit"
  "dc32-up|DC32 badge: view up|<Primary><Alt>i|pan_up"
  "dc32-left|DC32 badge: view left|<Primary><Alt>j|pan_left"
  "dc32-down|DC32 badge: view down|<Primary><Alt>k|pan_down"
  "dc32-right|DC32 badge: view right|<Primary><Alt>l|pan_right"
)
paths=()
for k in "${KEYS[@]}"; do paths+=("$BASE/${k%%|*}/"); done
cur=$(gsettings get $SCHEMA custom-keybindings)
new=$(python3 - "$cur" "${1:-}" "${paths[@]}" <<'PY'
import ast, sys
cur = ast.literal_eval(sys.argv[1].replace("@as ", ""))
mode, ours = sys.argv[2], sys.argv[3:]
keep = [p for p in cur if p not in ours]
print(keep if mode == "--remove" else keep + ours)
PY
)
gsettings set $SCHEMA custom-keybindings "$new"
if [ "${1:-}" = "--remove" ]; then echo "removed DC32 badge shortcuts"; exit 0; fi
for k in "${KEYS[@]}"; do
  IFS='|' read -r id name binding action <<<"$k"
  S="$SCHEMA.custom-keybinding:$BASE/$id/"
  gsettings set "$S" name "$name"
  gsettings set "$S" command "$BIN ctl $action"
  gsettings set "$S" binding "$binding"
  echo "$binding -> dc32host ctl $action"
done
