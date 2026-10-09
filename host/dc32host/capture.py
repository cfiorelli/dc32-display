"""Screen capture, viewport selection (fit / 2x / 1x with activity tracking), cursor + text overlays."""
from __future__ import annotations

import threading
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

OUT_W, OUT_H = 320, 240

_tls = threading.local()


def _mss():
    if not hasattr(_tls, "sct"):
        import mss
        _tls.sct = mss.mss()
    return _tls.sct


def virtual_screen():
    m = _mss().monitors[0]
    return (m["left"], m["top"], m["width"], m["height"])


def grab_rect(rect):
    """Grab a screen rectangle (virtual-desktop coords) -> RGB uint8 array, clipped to the desktop."""
    vx, vy, vw, vh = virtual_screen()
    x, y, w, h = rect
    x0, y0 = max(x, vx), max(y, vy)
    x1, y1 = min(x + w, vx + vw), min(y + h, vy + vh)
    if x1 <= x0 or y1 <= y0:
        return None, None
    shot = _mss().grab({"left": x0, "top": y0, "width": x1 - x0, "height": y1 - y0})
    arr = np.frombuffer(shot.bgra, np.uint8).reshape(shot.height, shot.width, 4)
    return arr[..., 2::-1], (x0, y0, x1 - x0, y1 - y0)


# --------------------------------------------------------------------------- cursor sprite
_ARROW = [
    "X..........",
    "XX.........",
    "XOX........",
    "XOOX.......",
    "XOOOX......",
    "XOOOOX.....",
    "XOOOOOX....",
    "XOOOOOOX...",
    "XOOOOOOOX..",
    "XOOOOOOOOX.",
    "XOOOOOXXXXX",
    "XOOXOOX....",
    "XOX.XOOX...",
    "XX..XOOX...",
    "X....XOOX..",
    ".....XOOX..",
    "......XX...",
]
_ARROW_OUT = np.array([[c == "X" for c in r] for r in _ARROW])
_ARROW_IN = np.array([[c == "O" for c in r] for r in _ARROW])


def draw_cursor(img: np.ndarray, x: int, y: int):
    h, w = _ARROW_OUT.shape
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(OUT_W, x + w), min(OUT_H, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    sub = img[y0:y1, x0:x1]
    so = _ARROW_OUT[y0 - y:y1 - y, x0 - x:x1 - x]
    si = _ARROW_IN[y0 - y:y1 - y, x0 - x:x1 - x]
    sub[so] = (0, 0, 0)
    sub[si] = (255, 255, 255)


def draw_edge_marker(img: np.ndarray, x: int, y: int):
    """Pointer is off the badge: an orange tab on the nearest edge, pointing at where it went."""
    h, w = img.shape[:2]
    cx, cy = min(max(x, 0), w - 1), min(max(y, 0), h - 1)
    r = 6
    if x < 0 or x >= w:                 # left/right edge: vertical tab
        xs = slice(0, r) if x < 0 else slice(w - r, w)
        img[max(0, cy - 3 * r):min(h, cy + 3 * r), xs] = (255, 140, 0)
    if y < 0 or y >= h:                 # top/bottom edge: horizontal tab
        ys = slice(0, r) if y < 0 else slice(h - r, h)
        img[ys, max(0, cx - 3 * r):min(w, cx + 3 * r)] = (255, 140, 0)


# --------------------------------------------------------------------------- text
def _font(size=13):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


_FONT = None


def font():
    global _FONT
    if _FONT is None:
        _FONT = _font(13)
    return _FONT


def draw_lines(img: np.ndarray, lines: list[str], where="bottom", alpha=0.78) -> np.ndarray:
    if not lines:
        return img
    pil = Image.fromarray(img)
    d = ImageDraw.Draw(pil, "RGBA")
    lh = 16
    h = lh * len(lines) + 8
    y = OUT_H - h if where == "bottom" else 0
    d.rectangle([0, y, OUT_W, y + h], fill=(16, 16, 24, int(255 * alpha)))
    for i, t in enumerate(lines):
        d.text((6, y + 4 + i * lh), t, font=font(), fill=(255, 235, 120) if i == 0 else (235, 235, 235))
    return np.asarray(pil).copy()


def placeholder(lines: list[str]) -> np.ndarray:
    img = np.zeros((OUT_H, OUT_W, 3), np.uint8)
    pil = Image.fromarray(img)
    d = ImageDraw.Draw(pil)
    y = OUT_H // 2 - 10 * len(lines)
    for t in lines:
        w = d.textlength(t, font=font())
        d.text(((OUT_W - w) / 2, y), t, font=font(), fill=(200, 200, 200))
        y += 20
    return np.asarray(pil).copy()


# --------------------------------------------------------------------------- viewport
ZOOMS = {"fit": None, "2x": 2, "1x": 1}


MOUSE_MARGIN = 0.18    # pointer pushes the 1:1/2:1 view when it gets this close (fraction) to an edge
MOUSE_EASE = 0.5       # fraction of the remaining distance moved per frame


class Viewport:
    """Chooses which part of the source to show and how to scale it."""

    def __init__(self):
        self.zoom = "fit"
        self.center = None            # source-space (x, y)
        self.manual_until = 0.0
        self._prev_small = None
        self._prev_key = None
        self.activity = None
        self.last_bbox = None         # (bbox, time) of the last small change (typing, prompt)

    def reset(self):
        self.center = None
        self.manual_until = 0.0
        self._prev_small = None

    def set_zoom(self, zoom):
        """Change zoom without forgetting where the activity was: a new 1:1 view opens on the caret."""
        self.zoom = zoom
        self.center = None
        self.manual_until = 0.0
        if zoom != "fit" and self.last_bbox and time.time() - self.last_bbox[1] < 30:
            bx0, by0, bx1, by1 = self.last_bbox[0]
            self.center = ((bx0 + bx1) / 2, by1 - 8)

    def pan(self, dx, dy):
        if self.zoom == "fit" or self.center is None:
            return
        z = ZOOMS[self.zoom]
        step_x, step_y = 80 * z, 60 * z
        self.center = (self.center[0] + dx * step_x, self.center[1] + dy * step_y)
        self.manual_until = time.time() + 15

    def _activity(self, src: np.ndarray, key, typing=False):
        """Where the caret probably is, from what changed since the previous grab (1/4 resolution).

        Only changes that follow a keystroke count: spinners, clocks and page loads change the screen
        on their own and used to drag the view around. Changes are split into row bands (separate
        lines); a typed character is a small band, and the band nearest the previous caret wins, so a
        status line elsewhere can't steal the view."""
        small = src[::4, ::4].sum(axis=2, dtype=np.uint16)
        best = None
        if (typing and self._prev_small is not None and self._prev_key == key
                and self._prev_small.shape == small.shape):
            diff = np.abs(small.astype(np.int32) - self._prev_small) > 24
            rows = np.flatnonzero(diff.any(axis=1))
            bands, start = [], None
            for i, r in enumerate(rows):
                if start is None:
                    start = r
                if i + 1 == len(rows) or rows[i + 1] - r > 2:
                    xs = np.flatnonzero(diff[start:r + 1].any(axis=0))
                    bands.append((xs[0] * 4, start * 4, (xs[-1] + 1) * 4, (r + 1) * 4))
                    start = None
            # keystroke echo: at most ~3 text lines tall and not a whole-width repaint
            bands = [b for b in bands if b[3] - b[1] <= 64 and b[2] - b[0] <= max(160, 0.5 * src.shape[1])]
            if bands:
                prev = self.last_bbox[0] if self.last_bbox and time.time() - self.last_bbox[1] < 30 else None
                if prev is not None:
                    px, py = (prev[0] + prev[2]) / 2, (prev[1] + prev[3]) / 2
                    best = min(bands, key=lambda b: abs((b[0] + b[2]) / 2 - px) + 3 * abs((b[1] + b[3]) / 2 - py))
                else:
                    best = min(bands, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
                self.last_bbox = (best, time.time())
        self._prev_small = small.astype(np.int32)
        self._prev_key = key
        return best

    def compose(self, src: np.ndarray, key, focus_pt=None, letterbox=(0, 0, 0), typing=False, mouse=False,
                follow_activity=True):
        """src: RGB array of the source. focus_pt: preferred point (caret/mouse) in src coords.
        mouse=True: focus_pt is the pointer; glide just far enough to keep it inside a margin
        (a camera that gets pushed) instead of re-centring on it, which jumped and jittered.
        Returns (out_rgb, transform) where transform maps src (x, y) -> out (x, y)."""
        sh, sw = src.shape[:2]
        bbox = self._activity(src, key, typing)
        z = ZOOMS[self.zoom]
        if z is None:
            s = min(OUT_W / sw, OUT_H / sh, 1.0)
            nw, nh = max(1, round(sw * s)), max(1, round(sh * s))
            img = Image.fromarray(np.ascontiguousarray(src))
            if (nw, nh) != (sw, sh):
                img = img.resize((nw, nh), Image.Resampling.BILINEAR, reducing_gap=2.0)
            out = np.empty((OUT_H, OUT_W, 3), np.uint8)
            out[:] = letterbox
            ox, oy = (OUT_W - nw) // 2, (OUT_H - nh) // 2
            out[oy:oy + nh, ox:ox + nw] = np.asarray(img)
            return out, (s, ox, oy, 0, 0)

        vw, vh = min(OUT_W * z, sw), min(OUT_H * z, sh)
        now = time.time()
        target = None
        if now >= self.manual_until:
            if focus_pt is not None:
                target = focus_pt
            elif bbox is not None and follow_activity:
                bx0, by0, bx1, by1 = bbox
                target = (bx1, by1 - 8)        # right end of the change: where the caret went
        if self.center is None:
            self.center = target or (sw / 2, sh - vh / 2)
        elif target is not None and mouse and focus_pt is not None:
            cx, cy = self.center
            mx, my = 0.5 * vw - MOUSE_MARGIN * vw, 0.5 * vh - MOUSE_MARGIN * vh
            want = (min(max(cx, target[0] - mx), target[0] + mx), min(max(cy, target[1] - my), target[1] + my))
            # ease toward it; snap the last pixel so the frame settles and stops resending
            nx, ny = cx + MOUSE_EASE * (want[0] - cx), cy + MOUSE_EASE * (want[1] - cy)
            self.center = (want[0] if abs(want[0] - nx) < 1 else nx, want[1] if abs(want[1] - ny) < 1 else ny)
        elif target is not None:
            cx, cy = self.center
            # hysteresis: only move when the target leaves the inner 60% of the viewport
            if abs(target[0] - cx) > 0.3 * vw or abs(target[1] - cy) > 0.3 * vh:
                self.center = target
        if sw - vw <= 48:             # barely wider than the badge (e.g. a scrollbar): keep the left edge
            self.center = (vw / 2, self.center[1])
        cx = min(max(self.center[0], vw / 2), sw - vw / 2)
        cy = min(max(self.center[1], vh / 2), sh - vh / 2)
        self.center = (cx, cy)
        x0, y0 = int(round(cx - vw / 2)), int(round(cy - vh / 2))
        crop = src[y0:y0 + vh, x0:x0 + vw]
        if z != 1:
            crop = np.asarray(Image.fromarray(np.ascontiguousarray(crop)).reduce(z))
        ch, cw = crop.shape[:2]
        out = np.empty((OUT_H, OUT_W, 3), np.uint8)
        out[:] = letterbox
        ox, oy = (OUT_W - cw) // 2, (OUT_H - ch) // 2
        out[oy:oy + ch, ox:ox + cw] = crop
        return out, (1.0 / z, ox, oy, x0, y0)


def map_point(transform, x, y):
    s, ox, oy, x0, y0 = transform
    return int(round((x - x0) * s + ox)), int(round((y - y0) * s + oy))
