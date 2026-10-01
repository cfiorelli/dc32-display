"""Round-trip tests: host encoder (Python) -> firmware decoder (C, compiled natively).

Run:  python3 tests/test_protocol.py      (or: pytest tests/)
"""
import ctypes
import os
import random
import subprocess
import sys
import tempfile

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "host"))
from dc32host import encoder as E  # noqa: E402
from dc32host import protocol as P  # noqa: E402

_lib = None


def lib():
    global _lib
    if _lib is None:
        out = os.path.join(tempfile.mkdtemp(), "libdec.so")
        subprocess.check_call(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC",
                               "-I", os.path.join(ROOT, "firmware/src"), "-I", os.path.join(ROOT, "protocol"),
                               os.path.join(ROOT, "firmware/src/decoder.c"),
                               os.path.join(ROOT, "tests/decoder_harness.c"), "-o", out])
        _lib = ctypes.CDLL(out)
        _lib.h_fb.restype = ctypes.POINTER(ctypes.c_uint16)
        _lib.h_feed.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
    return _lib


def feed(data: bytes, chunk=None):
    L = lib()
    if chunk is None:
        L.h_feed(data, len(data))
        return
    i = 0
    while i < len(data):
        k = chunk() if callable(chunk) else chunk
        L.h_feed(data[i:i + k], len(data[i:i + k]))
        i += k


def logical_fb() -> np.ndarray:
    hw = np.ctypeslib.as_array(lib().h_fb(), shape=(320 * 240,)).copy()
    # hw index = x*240 + (239-y)  ->  hw.reshape(320, 240)[x, 239-y]
    return hw.reshape(320, 240)[:, ::-1].T.copy()


def start():
    lib().h_init()
    feed(P.SYNC_BYTES)


def send_frame(cur, prev, chunk=None, stats=None):
    data = b"".join(E.encode_frame(cur, prev, stats)) + P.frame_end(1)
    feed(data, chunk)
    return len(data)


def rand_frame(rng):
    f = np.zeros((240, 320), np.uint16)
    for _ in range(rng.integers(5, 30)):
        x, y = rng.integers(0, 320), rng.integers(0, 240)
        w, h = rng.integers(1, 120), rng.integers(1, 80)
        f[y:y + h, x:x + w] = rng.integers(0, 65536)
    noise = rng.random((240, 320)) < 0.02
    f[noise] = rng.integers(0, 65536, noise.sum())
    return f


def test_full_frames_random_chunking():
    rng = np.random.default_rng(1)
    start()
    prev = None
    for i in range(12):
        cur = rand_frame(rng)
        send_frame(cur, prev, chunk=lambda: random.randint(1, 300))
        assert lib().h_errs() == 0, lib().h_last_err()
        assert np.array_equal(logical_fb(), cur), f"frame {i} mismatch"
        prev = cur


def test_incremental_typing():
    rng = np.random.default_rng(2)
    start()
    cur = np.full((240, 320), 0x0841, np.uint16)
    send_frame(cur, None)
    prev = cur.copy()
    total = 0
    for i in range(80):                       # simulate a caret moving along a line
        cur = prev.copy()
        x, y = 8 + (i % 38) * 8, 16 + (i // 38) * 16
        cur[y:y + 14, x:x + 7] = rng.integers(0, 65536, (14, 7))
        total += send_frame(cur, prev, chunk=64)
        assert np.array_equal(logical_fb(), cur)
        prev = cur
    assert total / 80 < 400, f"typing deltas too big: {total / 80:.0f} B/keystroke"


def test_rle_edges():
    start()
    for runs in ([1], [2], [128], [129], [130], [258], [1, 1, 1], [129, 1, 129], [3, 1, 200, 1, 1, 7]):
        n = sum(runs)
        px = np.concatenate([np.full(r, (i * 7919) & 0xFFFF, np.uint16) for i, r in enumerate(runs)])
        w = n if n <= 320 else 320
        h = (n + w - 1) // w
        px = np.concatenate([px, np.full(w * h - n, 0x1234, np.uint16)])
        frame = np.zeros((240, 320), np.uint16)
        frame[0:h, 0:w] = px.reshape(h, w)
        rle = E.rle_encode(px)
        data = P.rect_rle(0, 0, w, h, rle) if rle is not None else P.rect_raw(0, 0, w, h, px.astype("<u2").tobytes())
        feed(data, chunk=3)
        assert lib().h_errs() == 0
        assert np.array_equal(logical_fb()[0:h, 0:w], px.reshape(h, w)), runs


def test_fill_and_copy():
    start()
    rng = np.random.default_rng(3)
    ref = rng.integers(0, 65536, (240, 320)).astype(np.uint16)
    send_frame(ref, None)
    feed(P.fill(10, 20, 30, 40, 0xF800))
    ref[20:60, 10:40] = 0xF800
    feed(P.copy(0, 16, 320, 200, 0, 0))     # terminal scroll up by 16 rows
    ref[0:200, 0:320] = ref[16:216, 0:320].copy()
    feed(P.copy(5, 5, 50, 50, 20, 30))      # overlapping down-right
    ref[30:80, 20:70] = ref[5:55, 5:55].copy()
    assert np.array_equal(logical_fb(), ref)


def test_error_recovery():
    rng = np.random.default_rng(4)
    start()
    good = rand_frame(rng)
    msgs = E.encode_frame(good, None)
    feed(msgs[0][:20])                        # truncated message (host died mid-rect)
    feed(bytes(rng.integers(0, 256, 5000, dtype=np.uint8)))  # garbage
    feed(P.rect_raw(300, 230, 40, 40, b"\0" * 3200))         # out of bounds rect -> error
    lib().h_reset()                            # what RESET_STREAM does
    feed(P.SYNC_BYTES)
    send_frame(good, None)
    assert np.array_equal(logical_fb(), good)


def test_drop_pixels_keeps_stream_in_sync():
    rng = np.random.default_rng(5)
    start()
    a, b = rand_frame(rng), rand_frame(rng)
    send_frame(a, None)
    lib().h_drop(1)
    send_frame(b, a)                          # menu open: pixels ignored but parsed
    assert np.array_equal(logical_fb(), a)
    lib().h_drop(0)
    send_frame(b, None)
    assert np.array_equal(logical_fb(), b)
    assert lib().h_errs() == 0


def test_small_messages_reach_callback():
    start()
    before = lib().h_msgs()
    feed(P.hello() + P.ping(7) + P.menu_list("Apps", [(1, 1, "Terminal — runner"), (2, 0, "Café")]) + P.frame_end(9))
    assert lib().h_msgs() == before + 4
    assert lib().h_last_type() == P.FRAME_END


def test_stream_parser():
    sp = P.StreamParser()
    import struct
    blob = P.msg(P.BUTTON, struct.pack("<BBHIHH", 3, 7, 0x100, 1234, 80, 0)) + P.msg(P.ACK, struct.pack("<IIII", 5, 100, 2, 3))
    got = []
    for i in range(0, len(blob), 5):
        got += sp.feed(blob[i:i + 5])
    ev = P.parse(*got[0])
    assert ev.button == "select" and ev.event == "short" and ev.held == ["fn"]
    assert P.parse(*got[1]).frame_id == 5


def bench_bandwidth():
    """Bytes on the wire for typical workloads (no USB involved)."""
    rng = np.random.default_rng(9)
    term = np.full((240, 320), 0x0000, np.uint16)
    for row in range(14):                     # fake terminal text: sparse bright glyph pixels
        for col in range(40):
            if rng.random() < 0.7:
                g = rng.random((12, 6)) < 0.35
                term[row * 16 + 2:row * 16 + 14, col * 8 + 1:col * 8 + 7][g] = 0xC618
    out = {}
    st = {}
    out["full terminal frame"] = sum(map(len, E.encode_frame(term, None, st)))
    out["full random-noise frame"] = sum(map(len, E.encode_frame(rng.integers(0, 65536, (240, 320)).astype(np.uint16), None)))
    t2 = term.copy()
    t2[224:238, 8:15] = 0xFFFF
    out["one keystroke"] = sum(map(len, E.encode_frame(t2, term)))
    scrolled = np.vstack([term[16:], np.zeros((16, 320), np.uint16)])
    out["terminal scroll (no COPY)"] = sum(map(len, E.encode_frame(scrolled, term)))
    return out


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
    for k, v in bench_bandwidth().items():
        print(f"BENCH {k:28s} {v:7d} bytes  ({v / 1000:.1f} ms @ 1 MB/s)")
