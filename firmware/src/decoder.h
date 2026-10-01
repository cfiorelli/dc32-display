// Streaming decoder for the DC32 Display protocol. Platform-neutral: it is compiled into
// the firmware and, unchanged, into the host test suite (tests/decoder_harness.c).
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "dc32proto.h"

// Logical (landscape 320x240) -> hardware framebuffer index (240x320 portrait panel).
// Verified against DC32 stock UI_ROTATED mapping: idx = x*240 + (239 - y).
#ifndef DEC_FB_INDEX
#define DEC_FB_INDEX(x, y) ((uint32_t)(x) * DC32_SCREEN_H + (DC32_SCREEN_H - 1u - (uint32_t)(y)))
#endif

#define DEC_SMALL_MAX 2048   // max payload for non-pixel messages

typedef struct decoder decoder_t;
typedef void (*dec_msg_cb)(decoder_t *d, uint8_t type, const uint8_t *payload, uint32_t len);
typedef void (*dec_err_cb)(decoder_t *d, uint8_t code, uint32_t detail);

struct decoder {
    uint16_t *fb;            // hardware-order framebuffer, 320*240 entries
    bool drop_pixels;        // true while a badge-native screen owns the panel
    dec_msg_cb on_msg;       // complete non-pixel messages
    dec_err_cb on_err;
    void *user;

    // state
    enum { DS_HUNT, DS_HDR, DS_SMALL, DS_RECT_HDR, DS_RAW, DS_RLE, DS_SKIP } st;
    uint8_t hdr[DC32_HDR_LEN];
    uint32_t hdr_n;
    uint8_t type;
    uint32_t len, got;       // payload length / bytes consumed of payload
    uint8_t small[DEC_SMALL_MAX];
    uint16_t rx, ry, rw, rh, cx, cy;
    uint32_t px_left;
    // RLE / pixel assembly
    uint8_t lo; bool have_lo;
    uint8_t rle_ctrl; bool rle_have_ctrl; uint32_t rle_count; bool rle_is_run;
    uint32_t sync_pos;
    // stats
    uint32_t rx_bytes, errors, rects, pixels;
};

void dec_init(decoder_t *d, uint16_t *fb);
void dec_reset_hunt(decoder_t *d);          // discard state; wait for SYNC
void dec_feed(decoder_t *d, const uint8_t *buf, size_t n);
void dec_fill(decoder_t *d, uint16_t x, uint16_t y, uint16_t w, uint16_t h, uint16_t color);
void dec_copy(decoder_t *d, uint16_t sx, uint16_t sy, uint16_t w, uint16_t h, uint16_t dx, uint16_t dy);
