from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WindowInfo:
    handle: int
    title: str
    app: str            # executable name (Windows) or WM_CLASS (X11)
    pid: int
    minimized: bool = False

    @property
    def label(self) -> str:
        app = self.app[:-4] if self.app.lower().endswith(".exe") else self.app
        t = self.title.strip() or app
        if app and app.lower() not in t.lower():
            return f"{t} - {app}"
        return t
