# DC32 Display protocol v1

Transport: USB full-speed composite device `1209:0001`, product string **"DC32 Display"**.

| Interface | Class | Endpoints | Use |
|---|---|---|---|
| 0 | vendor (WinUSB via MS OS 2.0 descriptors) | bulk OUT `0x01`, bulk IN `0x81` | display stream + events (this protocol) |
| 1–2 | CDC ACM | `0x83` notify, `0x02`/`0x82` data | human-readable debug log (open any serial terminal) |

Windows binds WinUSB to interface 0 automatically, with no .inf and no Zadig. DeviceInterfaceGUID
is `{A7894049-A47C-45F8-A23B-8FD1F5B31F59}`. `bcdDevice` = firmware version; bump it if Windows
cached old descriptors.

## Out-of-band control

`bmRequestType=0x40, bRequest=0x10 (RESET_STREAM)`, no data. The badge flushes its RX FIFO and puts
the decoder into **HUNT** (it ignores bytes until it sees SYNC). The host sends this on every
(re)connect, so a host that died mid-message can never desync a fresh session.

## Framing (both directions, little-endian)

```
u8 magic = 0xD3 | u8 type | u16 reserved = 0 | u32 payload_len | payload...
```

The badge decodes as a stream: payloads may span any number of USB packets. After an error the
badge sends `ERROR` and HUNTs for the 12-byte SYNC message
`D3 00 00 00 04 00 00 00 'S' 'Y' 'N' 'C'`. `0xD3` occurs only at offset 0 of that sequence,
so the hunt is exact.

## Host → badge

| Type | Name | Payload |
|---|---|---|
| 0x00 | SYNC | `"SYNC"` |
| 0x01 | HELLO | u16 proto, u16 flags → badge replies INFO |
| 0x02 | PING | u32 token → PONG. Host sends ≥1/s; badge shows "disconnected" after 3 s of silence |
| 0x10 | RECT_RAW | u16 x, y, w, h + w·h RGB565 (u16 LE), row-major, logical landscape coords |
| 0x11 | RECT_RLE | u16 x, y, w, h + RLE16 stream that must decode exactly w·h pixels and end with the payload |
| 0x12 | FILL | u16 x, y, w, h, color |
| 0x13 | COPY | u16 sx, sy, w, h, dx, dy (overlap-safe; for scrolling) |
| 0x14 | FRAME_END | u32 frame_id → badge replies ACK after everything before it is in the framebuffer |
| 0x20 | MENU_LIST | u8 kind, u8 count, u8 selected, u8 title_len, title, then per entry: u32 id, u8 flags (1 active, 2 favorite, 4 pinned), u8 name_len, name (ASCII ≤40) |
| 0x21 | MENU_CLOSE | – |
| 0x22 | SET_BRIGHTNESS | u8 0..31 (0 = backlight off, fw >= 0.2.1) |
| 0x24 | SET_LEDS | u8 n, then n × (r, g, b), n ≤ 9 (fw ≥ 0.2). Front LEDs 0,2,4,5,6; rear 1,3,7,8. The badge caps total brightness for USB power and turns the LEDs off when the host goes quiet. |
| 0x23 | SET_TIMING | u16 long_press_ms, u16 repeat_delay_ms, u16 repeat_ms, u16 reserved |
| 0x7E | REBOOT | u8 kind (0 app, 1 BOOTSEL) + `"BOOT"` |

Unknown types with payload ≤2048 bytes are ignored. Unknown types ≥0x30 with larger payloads are
skipped. This leaves room for extensions.

**RLE16:** control byte `c`. If `c < 0x80`, `c+1` literal pixels follow. If `c ≥ 0x80`, one pixel
follows and is repeated `(c & 0x7F) + 2` times (2..129).

**Coordinates:** logical 320×240 landscape, origin top-left. The panel is natively 240×320; the
badge maps `(x, y) → fb[x·240 + 239 − y]` (verified against the stock UI's rotation).

## Badge → host

| Type | Name | Payload |
|---|---|---|
| 0x80 | INFO | u16 proto, u16 w, u16 h, u8 major, minor, patch, u8 n_buttons, u32 fb_bytes, u16 rsv, ASCII version |
| 0x81 | BUTTON | u8 event (1 down, 2 up, 3 short, 4 long, 5 repeat), u8 button, u16 held_mask, u32 t_ms, u16 held_ms, u16 rsv |
| 0x82 | ACK | u32 frame_id, u32 rx_bytes_total, u32 t_ms, u32 decode_us since last ACK |
| 0x83 | MENU_RESULT | u8 kind, u8 action (0 cancel, 1 select, 2 pin), u16 rsv, u32 entry_id |
| 0x84 | PONG | u32 token |
| 0x8F | ERROR | u8 code (1 magic, 2 type, 3 rect, 4 length, 5 RLE overrun), u8 rsv[3], u32 detail |

Buttons: 0 up, 1 down, 2 left, 3 right, 4 A, 5 B, 6 START, 7 SELECT, 8 FN (centre). Debounce is
8 ms. LONG fires once at `long_press_ms` while still held, and suppresses SHORT on release. REPEAT
is for the D-pad only.

## Badge-local behaviour

* **App menu open:** the badge consumes all buttons itself. Up/Down move, A sends MENU_RESULT
  select, RIGHT sends pin, B or START cancels. Stream pixels keep being parsed but are not drawn;
  the host does a full refresh after MENU_RESULT.
* **FN + START + SELECT held for 2 s:** reboot into the BOOTSEL bootloader. Works with no host.
* **No host for 3 s:** status screen with live button tester. **No USB for 15 min:** releases the
  power latch (battery only).

## Flow control

The host keeps at most `max_frames_in_flight` (default 2) frames un-ACKed. This bounds queueing
latency to about one frame. If an ACK is overdue by more than 1.5 s, the host sends SYNC and a
full refresh.
