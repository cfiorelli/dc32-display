// Badge-native screens: disconnected/status screen and the app-switcher menu.
#include "ui.h"
#include <stdio.h>
#include <string.h>
#include "decoder.h"
#include "font8x16.h"
#include "version.h"

#define W DC32_SCREEN_W
#define H DC32_SCREEN_H

#define C_BG      0x0000
#define C_PANEL   0x18E3
#define C_TEXT    0xFFFF
#define C_DIM     0x8410
#define C_ACCENT  0xFFE0
#define C_SEL     0x03EF
#define C_OK      0x07E0
#define C_BAD     0xF800
#define C_WARN    0xFD20

static uint16_t *s_fb;

void ui_init(uint16_t *fb) { s_fb = fb; }

void ui_fill(int x, int y, int w, int h, uint16_t c)
{
    if (x < 0) { w += x; x = 0; }
    if (y < 0) { h += y; y = 0; }
    if (x + w > W) w = W - x;
    if (y + h > H) h = H - y;
    if (w <= 0 || h <= 0) return;
    for (int cx = x; cx < x + w; cx++) {
        uint16_t *p = &s_fb[DEC_FB_INDEX(cx, y + h - 1)];
        for (int i = 0; i < h; i++) p[i] = c;
    }
}

static inline void px(int x, int y, uint16_t c)
{
    if ((unsigned)x < W && (unsigned)y < H) s_fb[DEC_FB_INDEX(x, y)] = c;
}

int ui_text(int x, int y, const char *s, uint16_t fg, int bg, int scale)
{
    for (; *s; s++) {
        unsigned ch = (unsigned char)*s;
        if (ch < FONT_FIRST || ch > FONT_LAST) ch = '?';
        const uint8_t *g = font8x16[ch - FONT_FIRST];
        for (int r = 0; r < FONT_H; r++)
            for (int c = 0; c < FONT_W; c++) {
                bool on = g[r] & (0x80 >> c);
                if (!on && bg < 0) continue;
                uint16_t col = on ? fg : (uint16_t)bg;
                for (int sy = 0; sy < scale; sy++)
                    for (int sx = 0; sx < scale; sx++)
                        px(x + c * scale + sx, y + r * scale + sy, col);
            }
        x += FONT_W * scale;
        if (x >= W) break;
    }
    return x;
}

static void text_center(int y, const char *s, uint16_t fg, int scale)
{
    int w = (int)strlen(s) * FONT_W * scale;
    ui_text((W - w) / 2, y, s, fg, -1, scale);
}

static const char *const BTN_LABEL[] = {"U", "D", "L", "R", "A", "B", "ST", "SE", "FN"};

void ui_draw_disconnected(const ui_status_t *st, bool full)
{
    char line[48];
    if (full) {
        ui_fill(0, 0, W, H, C_BG);
        ui_fill(0, 0, W, 40, C_PANEL);
        text_center(4, "DC32 DISPLAY", C_ACCENT, 2);
        snprintf(line, sizeof line, "fw %s  proto %d  %s", DC32_FW_VERSION, DC32_PROTO_VERSION, DC32_BUILD_ID);
        text_center(44, line, C_DIM, 1);
        ui_text(8, 196, "FN+START+SELECT 2s: USB flash mode", C_DIM, -1, 1);
        ui_text(8, 214, "Buttons are tested live above.", C_DIM, -1, 1);
    }
    // status block
    ui_fill(0, 68, W, 56, C_BG);
    if (!st->usb_mounted) {
        text_center(68, "USB: NOT CONNECTED", C_BAD, 1);
        text_center(88, "Plug into the PC", C_TEXT, 1);
    } else if (!st->host_alive) {
        text_center(68, "USB: CONNECTED", C_OK, 1);
        text_center(88, "Waiting for host app...", C_WARN, 1);
    } else {
        text_center(68, "HOST CONNECTED", C_OK, 1);
    }
    snprintf(line, sizeof line, "rx %lu B  err %lu  up %lus", (unsigned long)st->rx_bytes,
             (unsigned long)st->errors, (unsigned long)st->uptime_s);
    text_center(108, line, C_DIM, 1);

    // live button tester
    ui_fill(0, 132, W, 56, C_BG);
    int x = 6;
    for (int b = 0; b < 9; b++) {
        bool on = st->buttons & (1u << b);
        int w = (int)strlen(BTN_LABEL[b]) * 8 + 12;
        ui_fill(x, 140, w, 26, on ? C_ACCENT : C_PANEL);
        ui_text(x + 6, 145, BTN_LABEL[b], on ? C_BG : C_TEXT, -1, 1);
        x += w + 4;
    }
    if (st->note) text_center(172, st->note, C_WARN, 1);
}

void ui_draw_bootsel_notice(void)
{
    ui_fill(0, 0, W, H, C_BG);
    text_center(90, "USB FLASH MODE", C_ACCENT, 2);
    text_center(130, "Rebooting to BOOTSEL...", C_TEXT, 1);
}

#define MENU_HDR 22
#define MENU_ROW 18
#define MENU_FOOT 20
#define MENU_ROWS ((H - MENU_HDR - MENU_FOOT) / MENU_ROW)

void ui_menu_move(ui_menu_t *m, int delta)
{
    if (!m->count) return;
    m->sel += delta;
    if (m->sel < 0) m->sel = m->count - 1;           // wrap
    if (m->sel >= m->count) m->sel = 0;
    if (m->sel < m->top) m->top = m->sel;
    if (m->sel >= m->top + MENU_ROWS) m->top = m->sel - MENU_ROWS + 1;
}

void ui_menu_draw(const ui_menu_t *m)
{
    char line[64];
    ui_fill(0, 0, W, MENU_HDR, C_PANEL);
    snprintf(line, sizeof line, "%s (%d)", m->title[0] ? m->title : "Apps", m->count);
    ui_text(6, 3, line, C_ACCENT, -1, 1);
    ui_fill(0, MENU_HDR, W, H - MENU_HDR - MENU_FOOT, C_BG);
    if (!m->count) text_center(100, "Loading...", C_DIM, 1);
    for (int r = 0; r < MENU_ROWS && m->top + r < m->count; r++) {
        const ui_menu_entry_t *e = &m->e[m->top + r];
        int y = MENU_HDR + r * MENU_ROW;
        bool sel = (m->top + r) == m->sel;
        if (sel) ui_fill(0, y, W, MENU_ROW, C_SEL);
        char mark = (e->flags & 0x02) ? '*' : (e->flags & 0x01) ? '>' : ' ';
        char mk[2] = {mark, 0};
        ui_text(4, y + 1, mk, C_ACCENT, -1, 1);
        uint16_t col = (e->flags & 0x04) ? C_ACCENT : C_TEXT;
        ui_text(16, y + 1, e->name, col, -1, 1);
    }
    // scrollbar
    if (m->count > MENU_ROWS) {
        int track = H - MENU_HDR - MENU_FOOT;
        int bh = track * MENU_ROWS / m->count; if (bh < 6) bh = 6;
        int by = MENU_HDR + (track - bh) * m->top / (m->count - MENU_ROWS);
        ui_fill(W - 3, by, 3, bh, C_DIM);
    }
    ui_fill(0, H - MENU_FOOT, W, MENU_FOOT, C_PANEL);
    ui_text(6, H - MENU_FOOT + 2, "A:focus  RIGHT:pin  B:back", C_DIM, -1, 1);
}
