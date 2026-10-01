#pragma once
#include <stdint.h>
#include <stdbool.h>

#define UI_MENU_MAX     48
#define UI_NAME_MAX     40

typedef struct {
    uint32_t id;
    uint8_t flags;
    char name[UI_NAME_MAX + 1];
} ui_menu_entry_t;

typedef struct {
    bool open;
    uint8_t kind;
    char title[UI_NAME_MAX + 1];
    ui_menu_entry_t e[UI_MENU_MAX];
    int count, sel, top;
} ui_menu_t;

typedef struct {
    bool usb_mounted;
    bool host_alive;
    uint16_t buttons;
    uint32_t rx_bytes, errors, frames_acked;
    uint32_t uptime_s;
    const char *note;
} ui_status_t;

void ui_init(uint16_t *fb);
void ui_fill(int x, int y, int w, int h, uint16_t c);
int ui_text(int x, int y, const char *s, uint16_t fg, int bg, int scale);  // bg<0: transparent
void ui_draw_disconnected(const ui_status_t *st, bool full);
void ui_menu_draw(const ui_menu_t *m);
void ui_menu_move(ui_menu_t *m, int delta);
void ui_draw_bootsel_notice(void);
