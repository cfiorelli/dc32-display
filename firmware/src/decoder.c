#include "decoder.h"
#include <string.h>

static const uint8_t SYNC_SEQ[12] = {DC32_MAGIC, DC32_MSG_SYNC, 0, 0, 4, 0, 0, 0, 'S', 'Y', 'N', 'C'};

static inline uint16_t rd16(const uint8_t *p) { return (uint16_t)(p[0] | (p[1] << 8)); }
static inline uint32_t rd32(const uint8_t *p) { return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24); }

void dec_init(decoder_t *d, uint16_t *fb)
{
    memset(d, 0, sizeof(*d));
    d->fb = fb;
    d->st = DS_HUNT;
}

void dec_reset_hunt(decoder_t *d)
{
    d->st = DS_HUNT;
    d->sync_pos = 0;
    d->hdr_n = 0;
    d->have_lo = false;
}

static void dec_error(decoder_t *d, uint8_t code, uint32_t detail)
{
    d->errors++;
    if (d->on_err) d->on_err(d, code, detail);
    dec_reset_hunt(d);
}

static bool rect_ok(uint32_t x, uint32_t y, uint32_t w, uint32_t h)
{
    return w && h && x + w <= DC32_SCREEN_W && y + h <= DC32_SCREEN_H;
}

void dec_fill(decoder_t *d, uint16_t x, uint16_t y, uint16_t w, uint16_t h, uint16_t color)
{
    if (!rect_ok(x, y, w, h) || d->drop_pixels) return;
    for (uint32_t cx = x; cx < (uint32_t)x + w; cx++) {
        // a logical column is a contiguous run in the hardware buffer
        uint16_t *p = &d->fb[DEC_FB_INDEX(cx, y + h - 1)];
        for (uint32_t i = 0; i < h; i++) p[i] = color;
    }
}

void dec_copy(decoder_t *d, uint16_t sx, uint16_t sy, uint16_t w, uint16_t h, uint16_t dx, uint16_t dy)
{
    if (!rect_ok(sx, sy, w, h) || !rect_ok(dx, dy, w, h) || d->drop_pixels) return;
    // Columns are contiguous in hardware order, so copy column by column with memmove
    // (handles vertical overlap); choose column order to handle horizontal overlap.
    bool rev = dx > sx;
    for (uint32_t i = 0; i < w; i++) {
        uint32_t c = rev ? (w - 1 - i) : i;
        uint16_t *src = &d->fb[DEC_FB_INDEX(sx + c, sy + h - 1)];
        uint16_t *dst = &d->fb[DEC_FB_INDEX(dx + c, dy + h - 1)];
        memmove(dst, src, (size_t)h * 2);
    }
}

static inline void put_px(decoder_t *d, uint16_t px)
{
    if (!d->drop_pixels) d->fb[DEC_FB_INDEX(d->cx, d->cy)] = px;
    if (++d->cx == d->rx + d->rw) { d->cx = d->rx; d->cy++; }
    d->px_left--;
}

static void finish_small(decoder_t *d)
{
    const uint8_t *p = d->small;
    switch (d->type) {
    case DC32_MSG_SYNC:
        break;
    case DC32_MSG_FILL:
        if (d->len >= 10) dec_fill(d, rd16(p), rd16(p + 2), rd16(p + 4), rd16(p + 6), rd16(p + 8));
        break;
    case DC32_MSG_COPY:
        if (d->len >= 12) dec_copy(d, rd16(p), rd16(p + 2), rd16(p + 4), rd16(p + 6), rd16(p + 8), rd16(p + 10));
        break;
    default:
        if (d->on_msg) d->on_msg(d, d->type, p, d->len);
        break;
    }
    d->st = DS_HDR;
    d->hdr_n = 0;
}

static void start_payload(decoder_t *d)
{
    const uint8_t *h = d->hdr;
    if (h[0] != DC32_MAGIC) { dec_error(d, DC32_ERR_BAD_MAGIC, h[0]); return; }
    d->type = h[1];
    d->len = rd32(h + 4);
    d->got = 0;
    switch (d->type) {
    case DC32_MSG_RECT_RAW:
    case DC32_MSG_RECT_RLE:
        if (d->len < 8) { dec_error(d, DC32_ERR_BAD_LEN, d->len); return; }
        d->st = DS_RECT_HDR;
        return;
    case DC32_MSG_SYNC:
        if (d->len != 4) { dec_error(d, DC32_ERR_BAD_LEN, d->len); return; }
        break;
    default:
        break;
    }
    if (d->len > DEC_SMALL_MAX) {
        if (d->type < 0x30) { dec_error(d, DC32_ERR_BAD_LEN, d->len); return; }
        d->st = DS_SKIP;     // unknown large extension message: skip it
        return;
    }
    d->st = DS_SMALL;
    if (d->len == 0) finish_small(d);
}

static void start_rect(decoder_t *d)
{
    const uint8_t *p = d->small;
    d->rx = rd16(p); d->ry = rd16(p + 2); d->rw = rd16(p + 4); d->rh = rd16(p + 6);
    if (!rect_ok(d->rx, d->ry, d->rw, d->rh)) { dec_error(d, DC32_ERR_BAD_RECT, ((uint32_t)d->rw << 16) | d->rh); return; }
    d->cx = d->rx; d->cy = d->ry;
    d->px_left = (uint32_t)d->rw * d->rh;
    d->have_lo = false;
    d->rects++;
    d->pixels += d->px_left;
    if (d->type == DC32_MSG_RECT_RAW) {
        if (d->len != 8 + d->px_left * 2) { dec_error(d, DC32_ERR_BAD_LEN, d->len); return; }
        d->st = DS_RAW;
    } else {
        d->rle_have_ctrl = false;
        d->rle_count = 0;
        d->st = DS_RLE;
    }
}

static inline void payload_done(decoder_t *d)
{
    d->st = DS_HDR;
    d->hdr_n = 0;
}

void dec_feed(decoder_t *d, const uint8_t *buf, size_t n)
{
    size_t i = 0;
    d->rx_bytes += (uint32_t)n;
    while (i < n) {
        switch (d->st) {
        case DS_HUNT: {
            uint8_t b = buf[i++];
            if (b == SYNC_SEQ[d->sync_pos]) {
                if (++d->sync_pos == sizeof(SYNC_SEQ)) { d->sync_pos = 0; d->st = DS_HDR; d->hdr_n = 0; }
            } else {
                // MAGIC appears only at SYNC_SEQ[0], so restarting at 0 is exact.
                d->sync_pos = (b == SYNC_SEQ[0]) ? 1 : 0;
            }
            break;
        }
        case DS_HDR:
            d->hdr[d->hdr_n++] = buf[i++];
            if (d->hdr_n == DC32_HDR_LEN) start_payload(d);
            break;
        case DS_SMALL: {
            size_t take = d->len - d->got;
            if (take > n - i) take = n - i;
            memcpy(d->small + d->got, buf + i, take);
            d->got += (uint32_t)take; i += take;
            if (d->got == d->len) finish_small(d);
            break;
        }
        case DS_RECT_HDR:
            d->small[d->got++] = buf[i++];
            if (d->got == 8) start_rect(d);
            break;
        case DS_RAW: {
            size_t avail = n - i, want = d->len - d->got;
            if (avail > want) avail = want;
            const uint8_t *p = buf + i;
            size_t k = 0;
            if (d->have_lo && avail) {
                put_px(d, (uint16_t)(d->lo | (p[0] << 8)));
                d->have_lo = false; k = 1;
            }
            if (!d->drop_pixels) {
                for (; k + 1 < avail; k += 2) {
                    d->fb[DEC_FB_INDEX(d->cx, d->cy)] = (uint16_t)(p[k] | (p[k + 1] << 8));
                    if (++d->cx == d->rx + d->rw) { d->cx = d->rx; d->cy++; }
                }
            } else {
                for (; k + 1 < avail; k += 2) {
                    if (++d->cx == d->rx + d->rw) { d->cx = d->rx; d->cy++; }
                }
            }
            if (k < avail) { d->lo = p[k]; d->have_lo = true; k++; }
            d->got += (uint32_t)avail; i += avail;
            if (d->got == d->len) { d->px_left = 0; payload_done(d); }
            break;
        }
        case DS_RLE: {
            uint8_t b = buf[i++];
            d->got++;
            if (!d->rle_have_ctrl) {
                d->rle_ctrl = b;
                d->rle_have_ctrl = true;
                d->rle_is_run = (b & 0x80) != 0;
                d->rle_count = d->rle_is_run ? (uint32_t)(b & 0x7F) + 2 : (uint32_t)b + 1;
                if (d->rle_count > d->px_left) { dec_error(d, DC32_ERR_RLE_OVERRUN, d->rle_count); break; }
                d->have_lo = false;
            } else if (!d->have_lo) {
                d->lo = b; d->have_lo = true;
            } else {
                uint16_t px = (uint16_t)(d->lo | (b << 8));
                d->have_lo = false;
                if (d->rle_is_run) {
                    while (d->rle_count) { put_px(d, px); d->rle_count--; }
                } else {
                    put_px(d, px);
                    d->rle_count--;
                }
                if (!d->rle_count) d->rle_have_ctrl = false;
            }
            if (d->st != DS_RLE) break;
            if (d->px_left == 0 && !d->rle_have_ctrl) {
                if (d->got == d->len) payload_done(d);
                else dec_error(d, DC32_ERR_BAD_LEN, d->len - d->got);
            } else if (d->got == d->len) {
                dec_error(d, DC32_ERR_BAD_LEN, d->px_left);
            }
            break;
        }
        case DS_SKIP: {
            size_t take = d->len - d->got;
            if (take > n - i) take = n - i;
            d->got += (uint32_t)take; i += take;
            if (d->got == d->len) payload_done(d);
            break;
        }
        }
    }
}
