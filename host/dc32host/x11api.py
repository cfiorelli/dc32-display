"""Linux/X11 backend (EWMH via python-xlib). Wayland sessions are not supported for capture."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess

from .wininfo import WindowInfo

log = logging.getLogger("dc32.x11")


class Backend:
    name = "x11"

    def __init__(self, cfg):
        from Xlib import display
        self.cfg = cfg
        # Under Wayland, XWayland only exposes X11 clients: no capture of native apps, no focus control.
        self.wayland = os.environ.get("XDG_SESSION_TYPE") == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))
        if self.wayland:
            log.error("Wayland session detected: log in with 'Ubuntu on Xorg' (gear icon on the login screen) "
                      "or run host/install_linux.sh --xorg")
        if not os.environ.get("DISPLAY"):
            raise RuntimeError("no X11 DISPLAY")
        self.d = display.Display()
        self.root = self.d.screen().root
        a = self.d.intern_atom
        self.A = {n: a(n) for n in ("_NET_CLIENT_LIST_STACKING", "_NET_CLIENT_LIST", "_NET_ACTIVE_WINDOW",
                                    "_NET_WM_NAME", "UTF8_STRING", "_NET_WM_PID", "_NET_WM_WINDOW_TYPE",
                                    "_NET_WM_WINDOW_TYPE_NORMAL", "_NET_WM_WINDOW_TYPE_DIALOG",
                                    "_NET_WM_STATE", "_NET_WM_STATE_SKIP_TASKBAR", "_NET_WM_STATE_HIDDEN",
                                    "_NET_FRAME_EXTENTS", "_GTK_FRAME_EXTENTS", "WM_CHANGE_STATE")}
        self.own_pid = os.getpid()

    def _prop(self, win, name, typ=0):
        try:
            p = win.get_full_property(self.A[name], typ)
            return p.value if p else None
        except Exception:
            return None

    def _win(self, handle):
        return self.d.create_resource_object("window", handle)

    def _title(self, w) -> str:
        v = self._prop(w, "_NET_WM_NAME", self.A["UTF8_STRING"])
        if v:
            return v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)
        try:
            n = w.get_wm_name()
            return n.decode("latin-1") if isinstance(n, bytes) else (n or "")
        except Exception:
            return ""

    def _info(self, handle) -> WindowInfo:
        w = self._win(handle)
        try:
            cls = w.get_wm_class()
            app = cls[1] if cls else ""
        except Exception:
            app = ""
        pid = self._prop(w, "_NET_WM_PID")
        state = self._prop(w, "_NET_WM_STATE") or []
        return WindowInfo(handle=handle, title=self._title(w), app=app, pid=int(pid[0]) if pid is not None and len(pid) else 0,
                          minimized=self.A["_NET_WM_STATE_HIDDEN"] in state)

    def list_windows(self):
        ids = self._prop(self.root, "_NET_CLIENT_LIST_STACKING") or self._prop(self.root, "_NET_CLIENT_LIST") or []
        out = []
        for h in reversed(list(ids)):          # stacking list is bottom->top
            w = self._win(h)
            types = self._prop(w, "_NET_WM_WINDOW_TYPE") or []
            if len(types) and self.A["_NET_WM_WINDOW_TYPE_NORMAL"] not in types and self.A["_NET_WM_WINDOW_TYPE_DIALOG"] not in types:
                continue
            if self.A["_NET_WM_STATE_SKIP_TASKBAR"] in (self._prop(w, "_NET_WM_STATE") or []):
                continue
            wi = self._info(h)
            if wi.pid != self.own_pid and wi.title:
                out.append(wi)
        return out

    def foreground(self):
        v = self._prop(self.root, "_NET_ACTIVE_WINDOW")
        if v is None or not len(v) or not v[0]:
            return None
        return self._info(int(v[0]))

    def alive(self, handle) -> bool:
        try:
            self._win(handle).get_geometry()
            return True
        except Exception:
            return False

    def rect(self, handle):
        try:
            w = self._win(handle)
            g = w.get_geometry()
            t = w.translate_coords(self.root, 0, 0)
            x, y, wd, ht = -t.x, -t.y, g.width, g.height
            # GTK client-side decorations put an invisible shadow inside the X window; drop it
            ext = self._prop(w, "_GTK_FRAME_EXTENTS")
            if ext is not None and len(ext) == 4:
                l, r, tp, b = (int(v) for v in ext)
                if wd > l + r and ht > tp + b:
                    x, y, wd, ht = x + l, y + tp, wd - l - r, ht - tp - b
            return (x, y, wd, ht)
        except Exception:
            return None

    def focus(self, handle) -> bool:
        from Xlib import X
        from Xlib.protocol import event
        ev = event.ClientMessage(window=self._win(handle), client_type=self.A["_NET_ACTIVE_WINDOW"],
                                 data=(32, [2, X.CurrentTime, 0, 0, 0]))   # source=2: pager/tool
        self.root.send_event(ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
        self.d.flush()
        return True

    def cursor(self):
        try:
            p = self.root.query_pointer()
            return (p.root_x, p.root_y, True)
        except Exception:
            return None

    # keyboard/mouse activity via the MIT-SCREEN-SAVER extension (libXss), if present
    def _idle_ms(self):
        if not hasattr(self, "_xss"):
            self._xss = None
            try:
                import ctypes
                import ctypes.util

                class Info(ctypes.Structure):
                    _fields_ = [("window", ctypes.c_ulong), ("state", ctypes.c_int), ("kind", ctypes.c_int),
                                ("til_or_since", ctypes.c_ulong), ("idle", ctypes.c_ulong), ("eventMask", ctypes.c_ulong)]
                xlib = ctypes.cdll.LoadLibrary(ctypes.util.find_library("X11"))
                xss = ctypes.cdll.LoadLibrary(ctypes.util.find_library("Xss"))
                xlib.XOpenDisplay.restype = ctypes.c_void_p
                xlib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
                xlib.XDefaultRootWindow.restype = ctypes.c_ulong
                xss.XScreenSaverAllocInfo.restype = ctypes.POINTER(Info)
                xss.XScreenSaverQueryInfo.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Info)]
                dpy = xlib.XOpenDisplay(None)
                self._xss = (xss, dpy, xlib.XDefaultRootWindow(dpy), xss.XScreenSaverAllocInfo())
            except Exception:
                self._xss = None
        if not self._xss:
            return None
        xss, dpy, root, info = self._xss
        xss.XScreenSaverQueryInfo(dpy, root, info)
        return int(info.contents.idle)

    def input_mark(self):
        import time
        return (time.monotonic(), self._idle_ms())

    def input_since(self, mark) -> bool:
        import time
        t0, idle0 = mark
        if idle0 is None:
            return False
        idle = self._idle_ms()
        return idle is not None and idle + 30 < idle0 + (time.monotonic() - t0) * 1000

    def caret(self, handle):
        return None

    def desktop_rect(self):
        g = self.root.get_geometry()
        return (0, 0, g.width, g.height)

    def capture_window(self, handle):
        return None     # region capture via mss is used on X11

    def send_key(self, name: str):
        key = {"up": "Up", "down": "Down", "left": "Left", "right": "Right", "enter": "Return",
               "esc": "Escape", "tab": "Tab", "space": "space", "pgup": "Prior", "pgdn": "Next"}.get(name)
        if key and shutil.which("xdotool"):
            subprocess.Popen(["xdotool", "key", key])

    def launch(self, spec: dict) -> bool:
        cmd = spec.get("cmd")
        if not cmd:
            return False
        title = spec.get("title") or "dc32"
        cwd = spec.get("cwd") or None
        if spec.get("terminal", True):
            # gnome-terminal ignores --title, so set the title from inside with an OSC escape
            # (works in every VTE/xterm-style terminal), then exec the real command.
            inner = ["bash", "-c", 'printf "\\033]0;%s\\007" "$1"; shift; exec "$@"', "dc32", title] + list(cmd)
            geo = spec.get("geometry")      # "COLSxROWS"
            if shutil.which("gnome-terminal"):
                args = ["gnome-terminal"] + ([f"--geometry={geo}"] if geo else []) + ["--"] + inner
            elif shutil.which("konsole"):
                args = ["konsole", "-e"] + inner
            elif shutil.which("xterm"):
                args = ["xterm", "-T", title] + (["-geometry", geo] if geo else []) + ["-e"] + inner
            else:
                args = ["x-terminal-emulator", "-e"] + inner
        else:
            args = list(cmd)
        log.info("launching: %s", args)
        subprocess.Popen(args, cwd=cwd, start_new_session=True)
        return True
