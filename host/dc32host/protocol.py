"""DC32 Display wire protocol (host side). Mirrors protocol/dc32proto.h; see protocol/PROTOCOL.md."""
from __future__ import annotations

import struct
from dataclasses import dataclass

PROTO_VERSION = 1
USB_VID = 0x1209
USB_PID = 0x0001
USB_PRODUCT = "DC32 Display"
CTRL_RESET_STREAM = 0x10

W, H = 320, 240
MAGIC = 0xD3
HDR = struct.Struct("<BBHI")

# host -> badge
SYNC, HELLO, PING = 0x00, 0x01, 0x02
RECT_RAW, RECT_RLE, FILL, COPY, FRAME_END = 0x10, 0x11, 0x12, 0x13, 0x14
MENU_LIST, MENU_CLOSE, SET_BRIGHTNESS, SET_TIMING, SET_LEDS = 0x20, 0x21, 0x22, 0x23, 0x24
REBOOT = 0x7E
# badge -> host
INFO, BUTTON, ACK, MENU_RESULT, PONG, ERROR = 0x80, 0x81, 0x82, 0x83, 0x84, 0x8F

BUTTONS = ["up", "down", "left", "right", "a", "b", "start", "select", "fn"]
EVENTS = {1: "down", 2: "up", 3: "short", 4: "long", 5: "repeat"}
MENU_APPS = 0
MENU_CANCEL, MENU_SELECT, MENU_PIN = 0, 1, 2
MENU_FLAG_ACTIVE, MENU_FLAG_FAVORITE, MENU_FLAG_PINNED = 1, 2, 4


def msg(mtype: int, payload: bytes = b"") -> bytes:
    return HDR.pack(MAGIC, mtype, 0, len(payload)) + payload


SYNC_BYTES = msg(SYNC, b"SYNC")


def hello() -> bytes:
    return msg(HELLO, struct.pack("<HH", PROTO_VERSION, 0))


def ping(token: int) -> bytes:
    return msg(PING, struct.pack("<I", token & 0xFFFFFFFF))


def rect_raw(x: int, y: int, w: int, h: int, pixels_le: bytes) -> bytes:
    assert len(pixels_le) == w * h * 2
    return msg(RECT_RAW, struct.pack("<HHHH", x, y, w, h) + pixels_le)


def rect_rle(x: int, y: int, w: int, h: int, rle: bytes) -> bytes:
    return msg(RECT_RLE, struct.pack("<HHHH", x, y, w, h) + rle)


def fill(x: int, y: int, w: int, h: int, color: int) -> bytes:
    return msg(FILL, struct.pack("<HHHHH", x, y, w, h, color))


def copy(sx: int, sy: int, w: int, h: int, dx: int, dy: int) -> bytes:
    return msg(COPY, struct.pack("<HHHHHH", sx, sy, w, h, dx, dy))


def frame_end(frame_id: int) -> bytes:
    return msg(FRAME_END, struct.pack("<I", frame_id & 0xFFFFFFFF))


def _ascii(s: str, limit: int) -> bytes:
    import unicodedata
    t = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    t = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in t)
    return t[:limit].encode("ascii")


def menu_list(title: str, entries: list[tuple[int, int, str]], selected: int = 0, kind: int = MENU_APPS) -> bytes:
    """entries: (id, flags, name). Names are transliterated to ASCII for the badge font."""
    entries = entries[:48]
    t = _ascii(title, 40)
    body = struct.pack("<BBBB", kind, len(entries), min(selected, 255), len(t)) + t
    for eid, flags, name in entries:
        n = _ascii(name, 40)
        body += struct.pack("<IBB", eid & 0xFFFFFFFF, flags, len(n)) + n
    return msg(MENU_LIST, body)


def menu_close() -> bytes:
    return msg(MENU_CLOSE)


def set_leds(colors) -> bytes:
    """colors: up to 9 (r, g, b) tuples. Firmware >= 0.2; older firmware ignores the message."""
    colors = list(colors)[:9]
    return msg(SET_LEDS, bytes([len(colors)]) + bytes(max(0, min(255, int(v))) for c in colors for v in c))


def set_brightness(level: int) -> bytes:
    return msg(SET_BRIGHTNESS, bytes([max(0, min(31, level))]))


def set_timing(long_ms: int, repeat_delay_ms: int, repeat_ms: int) -> bytes:
    return msg(SET_TIMING, struct.pack("<HHHH", long_ms, repeat_delay_ms, repeat_ms, 0))


def reboot(bootsel: bool) -> bytes:
    return msg(REBOOT, bytes([1 if bootsel else 0]) + b"BOOT")


# ---------------------------------------------------------------- badge -> host parsing
@dataclass
class Info:
    proto: int
    width: int
    height: int
    fw: str
    buttons: int


@dataclass
class ButtonEvent:
    event: str
    button: str
    held: list
    t_ms: int
    held_ms: int

    @property
    def fn_held(self) -> bool:
        return "fn" in self.held and self.button != "fn"


@dataclass
class Ack:
    frame_id: int
    rx_bytes: int
    t_ms: int
    decode_us: int


@dataclass
class MenuResult:
    kind: int
    action: int
    entry_id: int


@dataclass
class DeviceError:
    code: int
    detail: int


def parse(mtype: int, p: bytes):
    if mtype == INFO and len(p) >= 16:
        proto, w, h, ma, mi, pa, nb = struct.unpack_from("<HHHBBBB", p)
        ver = p[16:].decode("ascii", "replace") or f"{ma}.{mi}.{pa}"
        return Info(proto, w, h, ver, nb)
    if mtype == BUTTON and len(p) >= 10:
        ev, btn, held, t, held_ms = struct.unpack_from("<BBHIH", p)
        return ButtonEvent(EVENTS.get(ev, str(ev)), BUTTONS[btn] if btn < len(BUTTONS) else str(btn),
                           [BUTTONS[i] for i in range(len(BUTTONS)) if held & (1 << i)], t, held_ms)
    if mtype == ACK and len(p) >= 16:
        return Ack(*struct.unpack_from("<IIII", p))
    if mtype == MENU_RESULT and len(p) >= 8:
        k, a, _, i = struct.unpack_from("<BBHI", p)
        return MenuResult(k, a, i)
    if mtype == PONG and len(p) >= 4:
        return ("pong", struct.unpack_from("<I", p)[0])
    if mtype == ERROR and len(p) >= 8:
        c, _, _, _, d = struct.unpack_from("<BBBBI", p)
        return DeviceError(c, d)
    return None


class StreamParser:
    """Reassembles badge->host messages from arbitrary bulk reads."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data: bytes):
        self.buf += data
        out = []
        while True:
            # resync on the magic byte
            i = self.buf.find(bytes([MAGIC]))
            if i < 0:
                self.buf.clear()
                break
            if i:
                del self.buf[:i]
            if len(self.buf) < HDR.size:
                break
            _, mtype, _, ln = HDR.unpack_from(self.buf)
            if ln > 4096:
                del self.buf[:1]
                continue
            if len(self.buf) < HDR.size + ln:
                break
            payload = bytes(self.buf[HDR.size:HDR.size + ln])
            del self.buf[:HDR.size + ln]
            out.append((mtype, payload))
        return out
