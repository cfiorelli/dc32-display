// USB HID boot keyboard + a tiny non-blocking typer (press, release, per-key delay).
#include <string.h>
#include "tusb.h"
#include "hidkb.h"

#define KEY_MS 60

static const uint8_t s_report_desc[] = { TUD_HID_REPORT_DESC_KEYBOARD() };

uint8_t const *tud_hid_descriptor_report_cb(uint8_t itf) { (void)itf; return s_report_desc; }
uint16_t tud_hid_get_report_cb(uint8_t itf, uint8_t id, hid_report_type_t t, uint8_t *buf, uint16_t len) { return 0; }
void tud_hid_set_report_cb(uint8_t itf, uint8_t id, hid_report_type_t t, uint8_t const *buf, uint16_t len) {}

typedef struct { uint8_t key; uint16_t wait_ms; } step_t;

static step_t s_steps[24];
static int s_n, s_i;
static bool s_down;
static uint32_t s_next_ms;

static uint8_t digit_key(char c) { return c == '0' ? HID_KEY_0 : (uint8_t)(HID_KEY_1 + (c - '1')); }

void hidkb_type_pin(const char *digits)
{
    s_n = s_i = 0;
    s_down = false;
    s_next_ms = 0;
    // Space wakes the screen, Menu dismisses a swipe lock on older Android; the PIN field ignores both.
    s_steps[s_n++] = (step_t){HID_KEY_SPACE, 1200};
    s_steps[s_n++] = (step_t){HID_KEY_MENU, 1200};
    for (const char *p = digits; *p && s_n < 22; p++) s_steps[s_n++] = (step_t){digit_key(*p), 150};
    s_steps[s_n++] = (step_t){HID_KEY_ENTER, 0};
}

bool hidkb_busy(void) { return s_i < s_n; }
bool hidkb_ready(void) { return tud_hid_ready(); }

void hidkb_task(uint32_t now)
{
    if (s_i >= s_n || (int32_t)(now - s_next_ms) < 0 || !tud_hid_ready()) return;
    if (!s_down) {
        uint8_t kc[6] = {s_steps[s_i].key};
        tud_hid_keyboard_report(0, 0, kc);
        s_down = true;
        s_next_ms = now + KEY_MS;
    } else {
        tud_hid_keyboard_report(0, 0, NULL);
        s_down = false;
        s_next_ms = now + KEY_MS + s_steps[s_i].wait_ms;
        s_i++;
    }
}
