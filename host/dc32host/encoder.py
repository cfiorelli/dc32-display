"""Frame -> protocol messages: RGB565 conversion, tile diff, rect merge, FILL / RLE / RAW choice."""
from __future__ import annotations

import numpy as np

from . import protocol as P

TILE = 16
TX, TY = P.W // TILE, P.H // TILE   # 20 x 15 tiles


def rgb_to_565(rgb: np.ndarray) -> np.ndarray:
    """(H, W, 3) uint8 -> (H, W) uint16 RGB565."""
    r = rgb[..., 0].astype(np.uint16)
    g = rgb[..., 1].astype(np.uint16)
    b = rgb[..., 2].astype(np.uint16)
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def rle_encode(px: np.ndarray) -> bytes | None:
    """RLE16: ctrl<0x80 -> ctrl+1 literal px; ctrl>=0x80 -> run of (ctrl&0x7f)+2 copies."""
    n = px.size
    if n == 0:
        return b""
    flat = px.reshape(-1)
    change = np.flatnonzero(flat[1:] != flat[:-1]) + 1
    starts = np.concatenate(([0], change))
    lens = np.diff(np.concatenate((starts, [n])))
    # cheap size estimate before doing the Python loop
    runs = lens >= 2
    est = int(np.sum((lens[runs] + 128) // 129) * 3 + np.sum(lens[~runs]) * 2 + (np.sum(~runs) // 64 + 1))
    if est >= n * 2 * 0.85:
        return None
    raw = flat.astype("<u2").tobytes()
    out = bytearray()
    lit_start = -1
    lit_len = 0

    def flush_lit(start, count):
        while count:
            k = min(count, 128)
            out.append(k - 1)
            out.extend(raw[2 * start:2 * (start + k)])
            start += k
            count -= k

    for s, ln in zip(starts.tolist(), lens.tolist()):
        if ln == 1:
            if lit_len == 0:
                lit_start = s
            lit_len += 1
            continue
        if lit_len:
            flush_lit(lit_start, lit_len)
            lit_len = 0
        val = raw[2 * s:2 * s + 2]
        while ln:
            k = min(ln, 129)
            if k == 1:          # leftover single pixel after a long run
                out.append(0)
                out += val
            else:
                out.append(0x80 | (k - 2))
                out += val
            ln -= k
    if lit_len:
        flush_lit(lit_start, lit_len)
    return bytes(out)


def encode_rect(frame: np.ndarray, x: int, y: int, w: int, h: int, stats: dict | None = None) -> bytes:
    region = frame[y:y + h, x:x + w]
    first = region.flat[0]
    if np.all(region == first):
        if stats is not None:
            stats["fill"] = stats.get("fill", 0) + 1
        return P.fill(x, y, w, h, int(first))
    rle = rle_encode(region)
    if rle is not None and len(rle) < w * h * 2:
        if stats is not None:
            stats["rle"] = stats.get("rle", 0) + 1
        return P.rect_rle(x, y, w, h, rle)
    if stats is not None:
        stats["raw"] = stats.get("raw", 0) + 1
    return P.rect_raw(x, y, w, h, np.ascontiguousarray(region).astype("<u2").tobytes())


def dirty_rects(cur: np.ndarray, prev: np.ndarray | None) -> list[tuple[int, int, int, int]]:
    if prev is None:
        return [(0, y, P.W, 16) for y in range(0, P.H, 16)]
    diff = cur != prev
    tiles = diff.reshape(TY, TILE, TX, TILE).any(axis=(1, 3))
    if not tiles.any():
        return []
    if tiles.mean() > 0.6:   # mostly changed: full-width bands encode better
        rows = np.flatnonzero(tiles.any(axis=1))
        return [(0, int(r) * TILE, P.W, TILE) for r in rows]
    # horizontal runs per tile row
    spans = []
    for ty in range(TY):
        row = tiles[ty]
        tx = 0
        while tx < TX:
            if row[tx]:
                s = tx
                while tx < TX and row[tx]:
                    tx += 1
                spans.append([s, tx, ty, ty + 1])
            else:
                tx += 1
    # vertical merge of identical spans
    merged = []
    for sp in spans:
        for m in merged:
            if m[0] == sp[0] and m[1] == sp[1] and m[3] == sp[2]:
                m[3] = sp[3]
                break
        else:
            merged.append(sp)
    rects = []
    for x0, x1, y0, y1 in merged:
        x, y, w, h = x0 * TILE, y0 * TILE, (x1 - x0) * TILE, (y1 - y0) * TILE
        # tighten to the exact changed bounding box
        sub = diff[y:y + h, x:x + w]
        ys = np.flatnonzero(sub.any(axis=1))
        xs = np.flatnonzero(sub.any(axis=0))
        rects.append((x + int(xs[0]), y + int(ys[0]), int(xs[-1] - xs[0] + 1), int(ys[-1] - ys[0] + 1)))
    return rects


def encode_frame(cur: np.ndarray, prev: np.ndarray | None, stats: dict | None = None) -> list[bytes]:
    return [encode_rect(cur, x, y, w, h, stats) for (x, y, w, h) in dirty_rects(cur, prev)]
