// Button scanning, debounce and gesture detection (short / long / repeat).
// Raw GPIOs are active-low with internal pull-ups (as configured by the stock firmware).
#include "buttons.h"
#include "board_dc32.h"
#include "hardware/gpio.h"

static const uint8_t PINS[DC32_BTN_COUNT] = {
    [DC32_BTN_UP] = PIN_BTN_UP, [DC32_BTN_DOWN] = PIN_BTN_DOWN, [DC32_BTN_LEFT] = PIN_BTN_LEFT,
    [DC32_BTN_RIGHT] = PIN_BTN_RIGHT, [DC32_BTN_A] = PIN_BTN_A, [DC32_BTN_B] = PIN_BTN_B,
    [DC32_BTN_START] = PIN_BTN_START, [DC32_BTN_SELECT] = PIN_BTN_SELECT, [DC32_BTN_FN] = PIN_BTN_CENTER,
};

#define DEBOUNCE_MS 8
#define QLEN 32

static uint16_t s_long_ms = 600, s_rep_delay = 400, s_rep_ms = 90;
static uint16_t s_down;                       // debounced state
static uint16_t s_raw_last;
static uint32_t s_raw_change[DC32_BTN_COUNT];
static uint32_t s_press_t[DC32_BTN_COUNT];
static uint32_t s_next_rep[DC32_BTN_COUNT];
static uint16_t s_long_fired;
static btn_event_t s_q[QLEN];
static uint8_t s_qh, s_qt;
static uint32_t s_last_poll;

static bool is_dpad(uint8_t b) { return b <= DC32_BTN_RIGHT; }

static void push(uint8_t ev, uint8_t btn, uint32_t now, uint32_t held_ms)
{
    uint8_t nt = (uint8_t)((s_qt + 1) % QLEN);
    if (nt == s_qh) return;                   // full: drop newest
    s_q[s_qt] = (btn_event_t){ev, btn, s_down, (uint16_t)(held_ms > 65535 ? 65535 : held_ms), now};
    s_qt = nt;
}

void buttons_init(void)
{
    for (int i = 0; i < DC32_BTN_COUNT; i++) {
        gpio_init(PINS[i]);
        gpio_set_dir(PINS[i], GPIO_IN);
        gpio_pull_up(PINS[i]);
        gpio_set_input_hysteresis_enabled(PINS[i], true);
    }
}

void buttons_set_timing(uint16_t long_ms, uint16_t repeat_delay_ms, uint16_t repeat_ms)
{
    if (long_ms >= 150) s_long_ms = long_ms;
    if (repeat_delay_ms >= 100) s_rep_delay = repeat_delay_ms;
    if (repeat_ms >= 20) s_rep_ms = repeat_ms;
}

uint16_t buttons_down(void) { return s_down; }
uint32_t buttons_down_since(uint8_t btn) { return s_press_t[btn]; }

void buttons_poll(uint32_t now)
{
    if (now == s_last_poll) return;
    s_last_poll = now;
    uint32_t gpio = gpio_get_all();
    uint16_t raw = 0;
    for (int i = 0; i < DC32_BTN_COUNT; i++)
        if (!(gpio & (1u << PINS[i]))) raw |= (uint16_t)(1u << i);

    for (uint8_t b = 0; b < DC32_BTN_COUNT; b++) {
        uint16_t m = (uint16_t)(1u << b);
        if ((raw ^ s_raw_last) & m) s_raw_change[b] = now;
        bool stable = (now - s_raw_change[b]) >= DEBOUNCE_MS;
        if (stable && ((raw ^ s_down) & m)) {
            if (raw & m) {
                s_down |= m;
                s_press_t[b] = now;
                s_long_fired &= (uint16_t)~m;
                s_next_rep[b] = now + s_rep_delay;
                push(DC32_EV_DOWN, b, now, 0);
            } else {
                uint32_t held = now - s_press_t[b];
                s_down &= (uint16_t)~m;
                // "held" mask in UP/SHORT events excludes the released button itself
                push(DC32_EV_UP, b, now, held);
                if (!(s_long_fired & m)) push(DC32_EV_SHORT, b, now, held);
            }
        }
        if (s_down & m) {
            uint32_t held = now - s_press_t[b];
            if (!(s_long_fired & m) && held >= s_long_ms) {
                s_long_fired |= m;
                push(DC32_EV_LONG, b, now, held);
            }
            if (is_dpad(b) && (int32_t)(now - s_next_rep[b]) >= 0) {
                s_next_rep[b] = now + s_rep_ms;
                push(DC32_EV_REPEAT, b, now, held);
            }
        }
    }
    s_raw_last = raw;
}

bool buttons_next(btn_event_t *ev)
{
    if (s_qh == s_qt) return false;
    *ev = s_q[s_qh];
    s_qh = (uint8_t)((s_qh + 1) % QLEN);
    return true;
}
