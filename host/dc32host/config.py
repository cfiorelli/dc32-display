"""Config file handling. JSON, one file, created with defaults on first run."""
from __future__ import annotations

import copy
import json
import logging
import os
import sys

log = logging.getLogger("dc32.config")

DEFAULTS = {
    "version": 1,
    "mode": "follow",                 # follow | pinned | desktop
    "zoom": "fit",                    # fit | 2x | 1x   (1x = native pixels, 2x = 2:1 downscale)
    "fps_active": 30,                 # capture polls/s while content is changing
    "fps_idle": 12,                   # capture polls/s when nothing changed recently
    "max_frames_in_flight": 2,
    "brightness": 22,                 # 0..31
    "show_cursor": True,
    "cursor_only_when_moving_s": 3.0, # hide the cursor overlay this long after it stops (0 = always)
    "letterbox_color": [0, 0, 0],
    "long_press_ms": 600,
    "toast_seconds": 1.5,
    "exclude_titles": ["^Program Manager$", "^Windows Input Experience$", "^Settings$", "^NVIDIA GeForce Overlay$"],
    "exclude_processes": ["TextInputHost.exe", "ShellExperienceHost.exe", "SearchHost.exe", "StartMenuExperienceHost.exe"],
    "focus_alt_fallback": True,       # Windows: tap ALT if SetForegroundWindow is refused (foreground-lock workaround)
    "dashboard_favorite": "GitHub Runner Dashboard",
    "favorites": [
        {
            "name": "GitHub Runner Dashboard",
            # filled in by: python -m dc32host find-dashboard --write
            "match": {"title": "(?i)(runner.*(cost|dashboard|billing))|((cost|dashboard|billing).*runner)",
                      "process": None, "cmdline": None},
            "launch": None,           # {"cmd": [...], "cwd": "...", "title": "GitHub Runner Dashboard", "terminal": true}
        }
    ],
    # Button map. Keys: "<button>.<event>" or "fn+<button>.<event>"; events: short, long, down, repeat.
    "buttons": {
        "select.short": "toggle_last_app",
        "select.long": "cycle_mode",
        "start.short": "app_switcher",
        "start.long": "pin_current",
        "a.short": "zoom_cycle",
        "a.long": "dashboard",
        "b.short": "zoom_fit",
        "b.long": "refresh",
        "fn.short": "info",
        "up.down": "pan_up", "up.repeat": "pan_up",
        "down.down": "pan_down", "down.repeat": "pan_down",
        "left.down": "pan_left", "left.repeat": "pan_left",
        "right.down": "pan_right", "right.repeat": "pan_right",
        "fn+up.short": "brightness_up",
        "fn+down.short": "brightness_down",
        "fn+left.short": "none",       # e.g. "key:left" to inject arrow keys (disabled by default)
        "fn+right.short": "none",
        "fn+a.short": "focus_shown",   # bring the app currently shown on the badge to the front
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
    return _merge(DEFAULTS, user)


def save(cfg: dict, path: str | None = None):
    path = path or config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, path)
