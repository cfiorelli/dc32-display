// DC32 Display wire protocol — shared definitions (firmware + host tests).
// Authoritative description: protocol/PROTOCOL.md. All integers little-endian.
#pragma once
#include <stdint.h>

#define DC32_PROTO_VERSION      1

#define DC32_USB_VID            0x1209   // pid.codes
#define DC32_USB_PID            0x0001   // pid.codes test PID; host also matches product string
#define DC32_USB_PRODUCT        "DC32 Display"

#define DC32_SCREEN_W           320
#define DC32_SCREEN_H           240

// Vendor control requests (bmRequestType 0x40: host->device, vendor, device recipient)
#define DC32_CTRL_RESET_STREAM  0x10    // flush RX, decoder -> HUNT (waits for SYNC)

// Message header: magic, type, reserved u16, payload length u32
#define DC32_MAGIC              0xD3
#define DC32_HDR_LEN            8

// host -> badge
#define DC32_MSG_SYNC           0x00    // payload "SYNC"
#define DC32_MSG_HELLO          0x01    // u16 proto, u16 flags
#define DC32_MSG_PING           0x02    // u32 token
#define DC32_MSG_RECT_RAW       0x10    // u16 x,y,w,h + w*h u16 pixels
#define DC32_MSG_RECT_RLE       0x11    // u16 x,y,w,h + RLE16 stream
#define DC32_MSG_FILL           0x12    // u16 x,y,w,h,color
#define DC32_MSG_COPY           0x13    // u16 sx,sy,w,h,dx,dy
#define DC32_MSG_FRAME_END      0x14    // u32 frame_id -> badge replies ACK
#define DC32_MSG_MENU_LIST      0x20    // see PROTOCOL.md
#define DC32_MSG_MENU_CLOSE     0x21    // (empty)
#define DC32_MSG_SET_BRIGHTNESS 0x22    // u8 0..31
#define DC32_MSG_SET_TIMING     0x23    // u16 long_ms, u16 repeat_delay_ms, u16 repeat_ms, u16 idle_timeout_ms
#define DC32_MSG_REBOOT         0x7E    // u8 kind(0=app,1=BOOTSEL) + "BOOT"

// badge -> host
#define DC32_MSG_INFO           0x80    // u16 proto,w,h, u8 maj,min,patch, u8 nbuttons, u32 fb_bytes, char version[]
#define DC32_MSG_BUTTON         0x81    // u8 event, u8 button, u16 held_mask, u32 t_ms, u16 held_ms, u16 rsv
#define DC32_MSG_ACK            0x82    // u32 frame_id, u32 rx_bytes, u32 t_ms, u32 decode_us
#define DC32_MSG_MENU_RESULT    0x83    // u8 kind, u8 action, u16 rsv, u32 entry_id
#define DC32_MSG_PONG           0x84    // u32 token
#define DC32_MSG_ERROR          0x8F    // u8 code, u8 rsv[3], u32 detail

// button ids
enum { DC32_BTN_UP = 0, DC32_BTN_DOWN, DC32_BTN_LEFT, DC32_BTN_RIGHT, DC32_BTN_A, DC32_BTN_B,
       DC32_BTN_START, DC32_BTN_SELECT, DC32_BTN_FN, DC32_BTN_COUNT };

// button events
enum { DC32_EV_DOWN = 1, DC32_EV_UP = 2, DC32_EV_SHORT = 3, DC32_EV_LONG = 4, DC32_EV_REPEAT = 5 };

// menu kinds / actions
#define DC32_MENU_APPS          0
enum { DC32_MENU_CANCEL = 0, DC32_MENU_SELECT = 1, DC32_MENU_PIN = 2 };
#define DC32_MENU_FLAG_ACTIVE   0x01
#define DC32_MENU_FLAG_FAVORITE 0x02
#define DC32_MENU_FLAG_PINNED   0x04

// error codes
enum { DC32_ERR_BAD_MAGIC = 1, DC32_ERR_BAD_TYPE, DC32_ERR_BAD_RECT, DC32_ERR_BAD_LEN, DC32_ERR_RLE_OVERRUN };
