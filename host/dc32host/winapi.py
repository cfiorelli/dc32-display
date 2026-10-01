"""Windows backend: window enumeration/focus, cursor, caret, PrintWindow capture (ctypes only)."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import os
import subprocess
import time

import numpy as np

from .wininfo import WindowInfo

log = logging.getLogger("dc32.win")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOACTIVATE = 0x08000000
SW_RESTORE = 9
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PW_RENDERFULLCONTENT = 2
CURSOR_SHOWING = 1
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x2
KEYEVENTF_EXTENDEDKEY = 0x1

WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindow.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
user32.GetWindow.argtypes = [wt.HWND, ctypes.c_uint]
user32.GetWindow.restype = wt.HWND
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.BringWindowToTop.argtypes = [wt.HWND]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowDC.argtypes = [wt.HWND]
user32.GetWindowDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, ctypes.c_uint]
user32.GetAncestor.argtypes = [wt.HWND, ctypes.c_uint]
user32.GetAncestor.restype = wt.HWND
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
dwmapi.DwmGetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD), ("hCursor", wt.HANDLE), ("ptScreenPos", wt.POINT)]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD), ("hwndActive", wt.HWND), ("hwndFocus", wt.HWND),
                ("hwndCapture", wt.HWND), ("hwndMenuOwner", wt.HWND), ("hwndMoveSize", wt.HWND),
                ("hwndCaret", wt.HWND), ("rcCaret", wt.RECT)]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def set_dpi_aware():
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # PER_MONITOR_AWARE_V2
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            user32.SetProcessDPIAware()


class Backend:
    name = "windows"

    def __init__(self, cfg):
        set_dpi_aware()
        self.cfg = cfg
        self._exe_cache: dict[int, str] = {}
        self.own_pid = os.getpid()

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _title(h) -> str:
        n = user32.GetWindowTextLengthW(h)
        if n <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(h, buf, n + 1)
        return buf.value

    @staticmethod
    def _pid_tid(h):
        pid = wt.DWORD()
        tid = user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        return pid.value, tid

    def _exe(self, pid: int) -> str:
        if pid in self._exe_cache:
            return self._exe_cache[pid]
        name = ""
        hp = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if hp:
            buf = ctypes.create_unicode_buffer(1024)
            sz = wt.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(hp, 0, buf, ctypes.byref(sz)):
                name = os.path.basename(buf.value)
            kernel32.CloseHandle(hp)
        if len(self._exe_cache) > 512:
            self._exe_cache.clear()
        self._exe_cache[pid] = name
        return name

    @staticmethod
    def _cloaked(h) -> bool:
        v = ctypes.c_int(0)
        dwmapi.DwmGetWindowAttribute(h, DWMWA_CLOAKED, ctypes.byref(v), ctypes.sizeof(v))
        return v.value != 0

    def rect(self, h):
        r = wt.RECT()
        if dwmapi.DwmGetWindowAttribute(h, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) != 0:
            if not user32.GetWindowRect(h, ctypes.byref(r)):
                return None
        w, hh = r.right - r.left, r.bottom - r.top
        if w <= 0 or hh <= 0:
            return None
        return (r.left, r.top, w, hh)

    def _info(self, h) -> WindowInfo:
        pid, _ = self._pid_tid(h)
        return WindowInfo(handle=int(h), title=self._title(h), app=self._exe(pid), pid=pid,
                          minimized=bool(user32.IsIconic(h)))

    def _switchable(self, h) -> bool:
        if not user32.IsWindowVisible(h) or user32.GetWindow(h, GW_OWNER):
            return False
        ex = user32.GetWindowLongW(h, GWL_EXSTYLE)
        if ex & WS_EX_TOOLWINDOW and not ex & WS_EX_APPWINDOW:
            return False
        if ex & WS_EX_NOACTIVATE:
            return False
        if self._cloaked(h):
            return False
        return user32.GetWindowTextLengthW(h) > 0

    # ------------------------------------------------------------ API
    def list_windows(self) -> list[WindowInfo]:
        out = []

        def cb(h, _):
            if self._switchable(h):
                wi = self._info(h)
                if wi.pid != self.own_pid:
                    out.append(wi)
            return True

        user32.EnumWindows(WNDENUMPROC(cb), 0)
        return out     # EnumWindows order == z-order, top first

    def foreground(self) -> WindowInfo | None:
        h = user32.GetForegroundWindow()
        if not h:
            return None
        root = user32.GetAncestor(h, 3)   # GA_ROOTOWNER: dialogs map to their app window
        if not root or not user32.IsWindowVisible(root):
            root = h
        return self._info(root)

    def alive(self, handle) -> bool:
        return bool(user32.IsWindow(handle))

    def focus(self, handle) -> bool:
        h = wt.HWND(handle)
        if not user32.IsWindow(h):
            return False
        if user32.IsIconic(h):
            user32.ShowWindow(h, SW_RESTORE)
        fg = user32.GetForegroundWindow()
        if fg == handle:
            return True
        _, fg_tid = self._pid_tid(fg) if fg else (0, 0)
        me = kernel32.GetCurrentThreadId()
        attached = bool(fg_tid) and fg_tid != me and user32.AttachThreadInput(me, fg_tid, True)
        try:
            user32.BringWindowToTop(h)
            ok = user32.SetForegroundWindow(h)
        finally:
            if attached:
                user32.AttachThreadInput(me, fg_tid, False)
        if (not ok or user32.GetForegroundWindow() != handle) and self.cfg.get("focus_alt_fallback", True):
            # Foreground-lock workaround: a synthetic ALT tap makes this process "last input" owner.
            user32.keybd_event(VK_MENU, 0, KEYEVENTF_EXTENDEDKEY, 0)
            user32.keybd_event(VK_MENU, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
            ok = user32.SetForegroundWindow(h)
        time.sleep(0.02)
        return user32.GetForegroundWindow() == handle

    def cursor(self):
        ci = CURSORINFO(cbSize=ctypes.sizeof(CURSORINFO))
        if not user32.GetCursorInfo(ctypes.byref(ci)):
            return None
        return (ci.ptScreenPos.x, ci.ptScreenPos.y, bool(ci.flags & CURSOR_SHOWING))

    def caret(self, handle):
        """Screen-space caret rect for classic Win32 edit controls (None for most modern apps)."""
        _, tid = self._pid_tid(wt.HWND(handle))
        gi = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
        if not user32.GetGUIThreadInfo(tid, ctypes.byref(gi)) or not gi.hwndCaret:
            return None
        pt = wt.POINT(gi.rcCaret.left, gi.rcCaret.top)
        user32.ClientToScreen(gi.hwndCaret, ctypes.byref(pt))
        return (pt.x, pt.y, max(1, gi.rcCaret.right - gi.rcCaret.left), max(1, gi.rcCaret.bottom - gi.rcCaret.top))

    def input_mark(self) -> int:
        lii = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
        user32.GetLastInputInfo(ctypes.byref(lii))
        return lii.dwTime

    def input_since(self, mark) -> bool:
        return self.input_mark() != mark

    def desktop_rect(self):
        g = user32.GetSystemMetrics
        return (g(76), g(77), g(78), g(79))

    def capture_window(self, handle):
        """Render a (possibly occluded) window with PrintWindow; returns RGB array cropped to visible frame."""
        h = wt.HWND(handle)
        wr = wt.RECT()
        if not user32.GetWindowRect(h, ctypes.byref(wr)):
            return None
        w, hh = wr.right - wr.left, wr.bottom - wr.top
        if w <= 0 or hh <= 0 or user32.IsIconic(h):
            return None
        wdc = user32.GetWindowDC(h)
        mdc = gdi32.CreateCompatibleDC(wdc)
        bmp = gdi32.CreateCompatibleBitmap(wdc, w, hh)
        old = gdi32.SelectObject(mdc, bmp)
        try:
            if not user32.PrintWindow(h, mdc, PW_RENDERFULLCONTENT):
                return None
            bi = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=w, biHeight=-hh,
                                  biPlanes=1, biBitCount=32, biCompression=0)
            buf = np.empty((hh, w, 4), np.uint8)
            gdi32.GetDIBits(mdc, bmp, 0, hh, buf.ctypes.data, ctypes.byref(bi), 0)
        finally:
            gdi32.SelectObject(mdc, old)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mdc)
            user32.ReleaseDC(h, wdc)
        vis = self.rect(handle)
        if vis:
            ox, oy = vis[0] - wr.left, vis[1] - wr.top
            buf = buf[max(0, oy):max(0, oy) + vis[3], max(0, ox):max(0, ox) + vis[2]]
        rgb = buf[..., 2::-1]
        if not rgb.any():
            return None          # some GPU-composited windows render black: caller falls back
        return rgb

    def send_key(self, name: str):
        vk = {"up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "enter": 0x0D, "esc": 0x1B,
              "tab": 0x09, "space": 0x20, "pgup": 0x21, "pgdn": 0x22}.get(name)
        if vk is None:
            return
        ext = KEYEVENTF_EXTENDEDKEY if vk in (0x21, 0x22, 0x25, 0x26, 0x27, 0x28) else 0
        user32.keybd_event(vk, 0, ext, 0)
        user32.keybd_event(vk, 0, ext | KEYEVENTF_KEYUP, 0)

    def launch(self, spec: dict) -> bool:
        cmd = spec.get("cmd")
        if not cmd:
            return False
        title = spec.get("title") or "dc32"
        cwd = spec.get("cwd") or None
        if spec.get("terminal", True):
            wt_exe = shutil_which("wt.exe")
            if wt_exe:
                args = [wt_exe, "-w", "new", "--title", title, "--suppressApplicationTitle"]
                if cwd:
                    args += ["-d", cwd]
                args += ["--"] + list(cmd)
            else:
                args = ["cmd.exe", "/c", "start", title] + list(cmd)
        else:
            args = list(cmd)
        log.info("launching: %s", args)
        subprocess.Popen(args, cwd=cwd, creationflags=0x00000008 if not spec.get("terminal", True) else 0)
        return True


def shutil_which(name):
    import shutil
    return shutil.which(name)
