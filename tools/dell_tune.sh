#!/usr/bin/env bash
# dell_tune.sh — make the Ubuntu runner box lean and fast WITHOUT touching the GitHub Actions runner.
#
#   sudo ./tools/dell_tune.sh            audit, print the plan, ask before applying
#   sudo ./tools/dell_tune.sh -y         audit + apply without asking
#   sudo ./tools/dell_tune.sh --audit    audit only, change nothing
#   sudo ./tools/dell_tune.sh --undo     revert everything this script changed
#   options: --keep-lock (default) | --no-lock (disable GNOME auto screen lock; badge would otherwise show the lock screen)
#            --no-xorg (leave the login session type alone)
#
# Principles: every change is reversible (inverse recorded in /var/lib/dell-tune/undo.sh), services
# are disabled/masked rather than packages purged, security updates stay on, and anything the runner
# can depend on (runner units, docker/containerd, network, DNS, time sync, ssh) is on a protect list
# that the script refuses to touch. The runner is checked before and after; the script aborts on any change.
set -uo pipefail

MODE=ask; LOCK=keep; XORG=1
for a in "$@"; do
  case "$a" in
    -y|--yes) MODE=apply ;; --audit) MODE=audit ;; --undo) MODE=undo ;;
    --no-lock) LOCK=off ;; --keep-lock) LOCK=keep ;; --no-xorg) XORG=0 ;;
    -h|--help) sed -n 2,14p "$0"; exit 0 ;;
    *) echo "unknown option $a"; exit 2 ;;
  esac
done
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }

STATE=/var/lib/dell-tune
UNDO=$STATE/undo.sh
LOG=$STATE/tune.log
mkdir -p "$STATE"
USR="${SUDO_USER:-$(logname 2>/dev/null || true)}"
UID_="$(id -u "$USR" 2>/dev/null || echo)"
c() { printf '\033[%sm%s\033[0m\n' "$1" "$2"; }
say() { echo "  $*"; }

if [ "$MODE" = undo ]; then
  [ -f "$UNDO" ] || { echo "nothing to undo"; exit 0; }
  echo "reverting changes recorded in $UNDO"
  bash "$UNDO" && mv "$UNDO" "$UNDO.done.$(date +%s)"
  echo "done. Reboot (or log out/in) to fully revert session settings."
  exit 0
fi

as_user() {  # run in the desktop user's session (gsettings, systemctl --user)
  [ -n "$USR" ] && [ -n "$UID_" ] || return 1
  sudo -u "$USR" XDG_RUNTIME_DIR="/run/user/$UID_" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$UID_/bus" "$@"
}

# ---------------------------------------------------------------- 1. protect the runner
PROTECT='^(actions\.runner\..*|docker.*|containerd.*|ssh.*|sshd.*|NetworkManager.*|systemd-networkd.*|systemd-resolved.*|systemd-timesyncd.*|chrony.*|wpa_supplicant.*|gdm.*|dbus.*|snapd.*|unattended-upgrades.*|apt-daily.*|polkit.*|udisks2.*|power-profiles-daemon.*)$'

runner_snapshot() {
  systemctl list-units --all --plain --no-legend 'actions.runner.*' 2>/dev/null | awk '{print $1, $3, $4}'
  for u in $(systemctl list-unit-files --plain --no-legend 'actions.runner.*' 2>/dev/null | awk '{print $1}'); do
    echo "$u enabled=$(systemctl is-enabled "$u" 2>/dev/null)"
  done
}

c 1 "== GitHub runner =="
RUNNER_UNITS=$(systemctl list-unit-files --plain --no-legend 'actions.runner.*' 2>/dev/null | awk '{print $1}')
RUNNER_PIDS=$(pgrep -f 'Runner\.Listener' || true)
if [ -n "$RUNNER_UNITS" ]; then
  for u in $RUNNER_UNITS; do say "service $u: $(systemctl is-active "$u") / $(systemctl is-enabled "$u")"; done
elif [ -n "$RUNNER_PIDS" ]; then
  c 33 "  runner is running but NOT as a systemd service (started from a terminal)."
  say "Logging out (needed for the Xorg switch) or rebooting would stop it. Recommended once:"
  say "  cd <runner dir> && sudo ./svc.sh install $USR && sudo ./svc.sh start"
  say "This script will not change the runner itself."
  XORG_BLOCK=1
else
  c 33 "  no runner service or process found (is it installed elsewhere / stopped?)"
fi
for p in $RUNNER_PIDS; do say "Runner.Listener pid $p cwd=$(readlink /proc/$p/cwd 2>/dev/null)"; done

if systemctl is-active --quiet docker 2>/dev/null; then say "docker active: protected (container jobs/actions need it)"; fi
BEFORE=$(runner_snapshot)

# ---------------------------------------------------------------- 2. audit
c 1 "== Machine =="
say "$(lsb_release -ds 2>/dev/null || grep PRETTY /etc/os-release | cut -d= -f2)  kernel $(uname -r)"
say "CPU: $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | xargs)  cores $(nproc)"
MEM_KB=$(awk '/MemTotal/{print $2}' /proc/meminfo)
say "RAM: $((MEM_KB/1024)) MB  swap: $(free -m | awk '/Swap/{print $2}') MB  swappiness: $(cat /proc/sys/vm/swappiness)"
SID=$(loginctl list-sessions --no-legend 2>/dev/null | awk -v u="$USR" '$3==u{print $1; exit}')
say "session: $( [ -n "$SID" ] && loginctl show-session "$SID" -p Type --value 2>/dev/null || echo unknown)"
say "power profile: $(powerprofilesctl get 2>/dev/null || echo n/a)   governor: $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo n/a)"
say "disk: $(df -h / | awk 'NR==2{print $4" free of "$2}')   journal: $(journalctl --disk-usage 2>/dev/null | grep -o '[0-9.]*[KMG]' | head -1)"
say "top memory users:"; ps -eo rss,comm --sort=-rss | head -8 | awk 'NR>1{printf "      %6d MB  %s\n", $1/1024, $2}'
say "slowest boot units:"; systemd-analyze blame 2>/dev/null | head -6 | sed 's/^/      /'

# ---------------------------------------------------------------- 3. plan
declare -a PLAN=()
plan() { PLAN+=("$1"); }
unit_exists() { systemctl list-unit-files --plain --no-legend "$1" 2>/dev/null | grep -q .; }
unit_on() { [ "$(systemctl is-enabled "$1" 2>/dev/null)" = enabled ] || systemctl is-active --quiet "$1" 2>/dev/null; }

# system services nobody needs on a headless-ish runner desktop
BT_INPUT=$(ls -d /sys/bus/hid/devices/0005:* 2>/dev/null | head -1)
SVCS="cups.service cups-browsed.service cups.socket cups.path avahi-daemon.service avahi-daemon.socket
      ModemManager.service whoopsie.service apport.service kerneloops.service packagekit.service
      fwupd.service fwupd-refresh.timer switcheroo-control.service speech-dispatcher.service
      gnome-remote-desktop.service motd-news.timer update-notifier-download.timer update-notifier-motd.timer
      ubuntu-advantage.service ua-timer.timer esm-cache.service"
if [ -z "$BT_INPUT" ] && ! (timeout 3 bluetoothctl devices Paired 2>/dev/null || timeout 3 bluetoothctl paired-devices 2>/dev/null) | grep -q Device; then
  SVCS="$SVCS bluetooth.service"
else
  say "bluetooth kept: a paired/connected Bluetooth device exists (your wireless keyboard/mouse?)"
fi
for u in $SVCS; do
  [[ "$u" =~ $PROTECT ]] && continue
  if unit_exists "$u" && unit_on "$u"; then plan "svc:$u"; fi
done

# user session (GNOME) — only if a desktop user exists
if [ -n "$USR" ]; then
  plan "gs:org.gnome.desktop.interface enable-animations false"
  plan "gs:org.gnome.desktop.search-providers disable-external true"
  plan "gs:org.gnome.software download-updates false"
  plan "gs:org.gnome.software allow-updates false"
  plan "gs:org.gnome.desktop.background picture-uri ''"
  plan "gs:org.gnome.desktop.background picture-uri-dark ''"
  plan "gs:org.gnome.desktop.background primary-color '#202020'"
  plan "gs:org.gnome.desktop.session idle-delay uint32 0"          # never blank: the badge mirrors the screen
  [ "$LOCK" = off ] && plan "gs:org.gnome.desktop.screensaver lock-enabled false"
  plan "gs:org.freedesktop.Tracker3.Miner.Files crawling-interval -2"
  plan "gs:org.freedesktop.Tracker3.Miner.Files enable-monitors false"
  plan "usvc:tracker-miner-fs-3.service"
  plan "usvc:tracker-extract-3.service"
  plan "usvc:evolution-addressbook-factory.service"
  plan "autostart:gnome-software-service.desktop"
  plan "autostart:update-notifier.desktop"
  plan "autostart:org.gnome.Evolution-alarm-notify.desktop"
  plan "ext:ding@rastersoft.com"
fi

# kernel / memory / power / logs
[ "$(cat /proc/sys/vm/swappiness)" -gt 10 ] && plan "sysctl"
command -v powerprofilesctl >/dev/null && [ "$(powerprofilesctl get 2>/dev/null)" != performance ] && plan "power:performance"
[ "$MEM_KB" -le 16777216 ] && ! swapon --show=NAME --noheadings | grep -q zram && plan "zram"
plan "journald"
command -v snap >/dev/null && plan "snap:refresh-window"
plan "clean"
if [ "$XORG" = 1 ] && [ -f /etc/gdm3/custom.conf ] && ! grep -q '^WaylandEnable=false' /etc/gdm3/custom.conf; then
  plan "xorg"
fi

c 1 "== Plan (${#PLAN[@]} changes, all reversible) =="
for p in "${PLAN[@]}"; do
  case "$p" in
    svc:*) say "disable+mask system service ${p#svc:}" ;;
    usvc:*) say "mask user service ${p#usvc:} (desktop file indexer / contacts daemon)" ;;
    gs:*) say "gsettings set ${p#gs:}" ;;
    autostart:*) say "disable login autostart ${p#autostart:}" ;;
    ext:*) say "disable GNOME extension ${p#ext:} (desktop icons)" ;;
    sysctl) say "vm.swappiness=10, vm.vfs_cache_pressure=50 (keep apps in RAM)" ;;
    power:*) say "power profile -> performance (desktop on mains)" ;;
    zram) say "zram compressed swap (zstd, 50% of RAM) — less disk swapping under job load" ;;
    journald) say "cap systemd journal at 300 MB" ;;
    snap:*) say "snap auto-refresh only Sun 03:00-05:00; keep 2 revisions" ;;
    clean) say "apt autoremove/clean, drop disabled snap revisions, vacuum journal (runner _work/caches untouched)" ;;
    xorg) say "login session -> Xorg (Wayland off) — required by the badge display; takes effect at next login" ;;
  esac
done
[ "${XORG_BLOCK:-0}" = 1 ] && grep -q '^xorg$' <(printf '%s\n' "${PLAN[@]}") && c 33 "  NOTE: Xorg switch only takes effect after logout — move the runner to a service first (see above)."
echo
say "Never touched: runner units, docker/containerd, network, DNS, time sync, ssh, security updates, kernel mitigations."

[ "$MODE" = audit ] && exit 0
if [ "$MODE" = ask ]; then read -r -p "Apply these changes? [y/N] " r; [[ "$r" =~ ^[Yy] ]] || exit 0; fi

# ---------------------------------------------------------------- 4. apply
[ -f "$UNDO" ] || { echo '#!/usr/bin/env bash' > "$UNDO"; echo 'set +e' >> "$UNDO"; }
undo() { echo "$*" >> "$UNDO"; }
exec > >(tee -a "$LOG") 2>&1
echo "=== apply $(date -Is) ==="

for p in "${PLAN[@]}"; do
  case "$p" in
    svc:*)
      u=${p#svc:}
      [[ "$u" =~ $PROTECT ]] && { echo "skip protected $u"; continue; }
      st=$(systemctl is-enabled "$u" 2>/dev/null || echo disabled)
      systemctl disable --now "$u" >/dev/null 2>&1; systemctl mask "$u" >/dev/null 2>&1
      undo "systemctl unmask $u; [ '$st' = enabled ] && systemctl enable --now $u"
      say "disabled $u" ;;
    usvc:*)
      u=${p#usvc:}
      as_user systemctl --user mask --now "$u" >/dev/null 2>&1 && \
        undo "sudo -u $USR XDG_RUNTIME_DIR=/run/user/$UID_ systemctl --user unmask $u" && say "masked user $u" ;;
    gs:*)
      read -r schema key val <<<"${p#gs:}"
      as_user gsettings list-keys "$schema" 2>/dev/null | grep -qx "$key" || { say "skip $schema $key (n/a)"; continue; }
      old=$(as_user gsettings get "$schema" "$key")
      as_user gsettings set "$schema" "$key" "$val" || { say "failed $schema $key"; continue; }
      undo "sudo -u $USR DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$UID_/bus gsettings set $schema $key \"$old\""
      say "gsettings $schema $key: $old -> $val" ;;
    autostart:*)
      f=${p#autostart:}; src=/etc/xdg/autostart/$f; dst=/home/$USR/.config/autostart/$f
      [ -f "$src" ] || continue
      if [ ! -f "$dst" ]; then
        sudo -u "$USR" mkdir -p "/home/$USR/.config/autostart"
        sudo -u "$USR" sh -c "cp '$src' '$dst' && printf 'Hidden=true\nX-GNOME-Autostart-enabled=false\n' >> '$dst'"
        undo "rm -f '$dst'"; say "autostart off: $f"
      fi ;;
    ext:*)
      e=${p#ext:}
      if as_user gnome-extensions info "$e" >/dev/null 2>&1; then
        as_user gnome-extensions disable "$e" && undo "sudo -u $USR DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$UID_/bus gnome-extensions enable $e" && say "extension off: $e"
      fi ;;
    sysctl)
      printf 'vm.swappiness=10\nvm.vfs_cache_pressure=50\n' > /etc/sysctl.d/90-dell-tune.conf
      sysctl -q --system; undo "rm -f /etc/sysctl.d/90-dell-tune.conf; sysctl -q --system"; say "sysctl applied" ;;
    power:*)
      old=$(powerprofilesctl get); powerprofilesctl set performance && undo "powerprofilesctl set $old" && say "power profile performance" ;;
    zram)
      if apt-get install -y -qq zram-tools >/dev/null 2>&1; then
        cp -n /etc/default/zramswap /etc/default/zramswap.dell-tune.bak 2>/dev/null
        printf 'ALGO=zstd\nPERCENT=50\nPRIORITY=100\n' > /etc/default/zramswap
        systemctl restart zramswap >/dev/null 2>&1
        undo "systemctl disable --now zramswap; apt-get remove -y -qq zram-tools"
        say "zram swap on"
      else say "zram-tools install failed (offline?) — skipped"; fi ;;
    journald)
      mkdir -p /etc/systemd/journald.conf.d
      printf '[Journal]\nSystemMaxUse=300M\n' > /etc/systemd/journald.conf.d/90-dell-tune.conf
      systemctl restart systemd-journald; undo "rm -f /etc/systemd/journald.conf.d/90-dell-tune.conf; systemctl restart systemd-journald"
      say "journal capped" ;;
    snap:*)
      old=$(snap get system refresh.timer 2>/dev/null || true)
      snap set system refresh.timer=sun,03:00-05:00 refresh.retain=2
      undo "snap set system refresh.timer='${old}'"; say "snap refresh window set" ;;
    clean)
      apt-get autoremove -y -qq >/dev/null 2>&1; apt-get clean
      if command -v snap >/dev/null; then
        snap list --all 2>/dev/null | awk '/disabled/{print $1, $3}' | while read -r n r; do snap remove "$n" --revision="$r" >/dev/null 2>&1; done
      fi
      journalctl --vacuum-size=300M >/dev/null 2>&1
      say "cleaned (free now: $(df -h / | awk 'NR==2{print $4}'))" ;;
    xorg)
      cp -n /etc/gdm3/custom.conf /etc/gdm3/custom.conf.dell-tune.bak
      if grep -q '^#\?WaylandEnable=' /etc/gdm3/custom.conf; then sed -i 's/^#\?WaylandEnable=.*/WaylandEnable=false/' /etc/gdm3/custom.conf
      else sed -i '/^\[daemon\]/a WaylandEnable=false' /etc/gdm3/custom.conf; fi
      undo "cp /etc/gdm3/custom.conf.dell-tune.bak /etc/gdm3/custom.conf"
      say "GDM -> Xorg at next login" ;;
  esac
done

# ---------------------------------------------------------------- 5. verify runner unchanged
AFTER=$(runner_snapshot)
if [ "$BEFORE" != "$AFTER" ]; then
  c 31 "RUNNER STATE CHANGED — reverting everything"; echo "before:"; echo "$BEFORE"; echo "after:"; echo "$AFTER"
  bash "$UNDO"; exit 1
fi
for u in $RUNNER_UNITS; do systemctl is-active --quiet "$u" || c 31 "warning: $u not active (it wasn't touched; was it active before?)"; done
c 32 "Done. Runner state identical before/after. Undo: sudo $0 --undo   Log: $LOG"
grep -q '^xorg$' <(printf '%s\n' "${PLAN[@]}") && echo "Log out and back in (or reboot) for the Xorg session."
