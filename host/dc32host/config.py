"""Config file handling. JSON, one file, created with defaults on first run."""
from __future__ import annotations

import copy
import json
import logging
import os
import sys

log = logging.getLogger("dc32.config")

# A terminal sized to the badge (34x13 cells of Ubuntu Mono 13 = 320 px wide) and always shown 1:1.
BADGE_TERMINAL = {
    "name": "Badge Terminal",
    "match": {"title": "^Badge Terminal$", "process": None, "cmdline": None},
    "launch": {"cmd": ["bash", "--rcfile", "{badge_bashrc}", "-i"], "cwd": None, "title": "Badge Terminal",
               "terminal": True, "geometry": "34x13"},
    "zoom": "1x",
}

DEFAULTS = {
    "version": 2,
    "mode": "follow",                 # follow | pinned | desktop
    "zoom": "fit",                    # fit | 2x | 1x   (1x = native pixels, 2x = 2:1 downscale)
    "fps_active": 30,                 # capture polls/s while content is changing
    "fps_idle": 12,                   # capture polls/s when nothing changed recently
    "max_frames_in_flight": 2,
    "brightness": 22,                 # 0..31
    "dim_after_s": 600,               # dim the badge backlight after this long without PC/badge input (0 = never)
    "dim_brightness": 2,
    "tap_sleep": True,                # double-tap the badge: sleep (screen off); any tap wakes it
    "tap_threshold": 32,
    "sd_writable": False,             # microSD as a USB drive: read-only unless enabled (ctl sd_rw / sd_ro)              # 1..127 x 16 mg; lower = lighter taps count (fw >= 0.3)
    "lights_mode": "off",             # off | bright | wave | rainbow | rave | runner  (Ctrl+Alt+G cycles)
    "show_cursor": True,
    "mouse_moves_view": True,         # zoomed in: pushing the pointer near an edge glides the view (False: keys/D-pad only)
    "cursor_only_when_moving_s": 3.0, # hide the cursor overlay this long after it stops (0 = always)
    "letterbox_color": [0, 0, 0],
    "long_press_ms": 600,
    "toast_seconds": 1.5,
    "exclude_titles": ["^Program Manager$", "^Windows Input Experience$", "^Settings$", "^NVIDIA GeForce Overlay$"],
    "exclude_processes": ["TextInputHost.exe", "ShellExperienceHost.exe", "SearchHost.exe", "StartMenuExperienceHost.exe"],
    "focus_alt_fallback": True,       # Windows: tap ALT if SetForegroundWindow is refused (foreground-lock workaround)
    "dashboard_favorite": "GitHub Runner Dashboard",
    "runner_view_for_dashboard": True, # show the badge-native runner view instead of the dashboard window
    # script: defaults to the dashboard favorite's .py. GitHub refresh every 2 h, none 22:00-07:00
    # (B-hold on the runner view fetches now); quiet_hours: null = always refresh.
    "runner_view": {"script": None, "refresh_s": 7200, "quiet_hours": [22, 7]},
    "typing_zoom": "1x",              # typing in fit mode zooms to this around the caret (None = off)
    "typing_zoom_hold_s": 0,          # 0: stay zoomed until B; >0: go back to fit after this many idle seconds
    "favorites": [
        {
            "name": "GitHub Runner Dashboard",
            # filled in by: python -m dc32host find-dashboard --write
            "match": {"title": "(?i)(runner.*(cost|dashboard|billing))|((cost|dashboard|billing).*runner)",
                      "process": None, "cmdline": None},
            "launch": None,           # {"cmd": [...], "cwd": "...", "title": "GitHub Runner Dashboard", "terminal": true}
        },
        BADGE_TERMINAL,
    ],
    # Button map. Keys: "<button>.<event>" or "fn+<button>.<event>"; events: short, long, down, repeat.
    "buttons": {
        "select.short": "toggle_last_app",
        "select.long": "cycle_mode",
        "start.short": "app_switcher",
        "start.long": "pin_current",
        "a.short": "zoom_cycle",
        "a.long": "toggle_runner",     # badge-native runner costs view (A-hold again: back to mirror)
        "b.short": "back",         # leave runner/help/pause view > un-zoom > send Esc
        "b.long": "refresh",
        "fn.short": "home_menu",       # Mirror / Runner costs / Badge terminal / Apps / Pause / Info
        "up.down": "pan_up", "up.repeat": "pan_up",
        "down.down": "pan_down", "down.repeat": "pan_down",
        "left.down": "pan_left", "left.repeat": "pan_left",
        "right.down": "pan_right", "right.repeat": "pan_right",
        "fn+up.short": "brightness_up",
        "fn+down.short": "brightness_down",
        "fn+left.short": "none",       # e.g. "key:left" to inject arrow keys (disabled by default)
        "fn+right.short": "none",
        "fn+a.short": "focus_shown",
        "fn+b.short": "info",   # bring the app currently shown on the badge to the front
    },
}


def app_dirs():
    if sys.platform == "win32":
        cfg = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "dc32-display")
        data = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "dc32-display")
    else:
        cfg = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "dc32-display")
        data = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "dc32-display")
    return cfg, data


def config_path():
    return os.path.join(app_dirs()[0], "config.json")


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path: str | None = None) -> dict:
    path = path or config_path()
    if not os.path.exists(path):
        save(DEFAULTS, path)
        return copy.deepcopy(DEFAULTS)
    try:
        with open(path, encoding="utf-8") as f:
            user = json.load(f)
    except Exception as e:
        log.error("config %s unreadable (%s); using defaults", path, e)
        return copy.deepcopy(DEFAULTS)
    if int(user.get("version", 1)) < 2:
        _migrate_v2(user)
    if (user.get("buttons") or {}).get("b.short") == "zoom_fit":   # B used to only un-zoom
        user["buttons"]["b.short"] = "back"
        try:
            save(user, path)
        except OSError as e:
            log.warning("could not save migrated config: %s", e)
    return _merge(DEFAULTS, user)


def _migrate_v2(user: dict):
    """v2: FN tap opens the badge home menu, A-hold shows the runner view, Badge Terminal favorite."""
    b = user.get("buttons")
    if isinstance(b, dict):
        for key, old, new in (("fn.short", "info", "home_menu"), ("a.long", "dashboard", "toggle_runner")):
            if b.get(key, old) == old:
                b[key] = new
        b.setdefault("fn+b.short", "info")
    favs = user.get("favorites")
    if isinstance(favs, list) and not any(f.get("name") == BADGE_TERMINAL["name"] for f in favs):
        favs.append(copy.deepcopy(BADGE_TERMINAL))
    user["version"] = 2


def save(cfg: dict, path: str | None = None):
    path = path or config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, path)
