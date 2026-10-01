#pragma once
#include <stdint.h>
#include <stdbool.h>
#include "dc32proto.h"

typedef struct {
    uint8_t ev;        // DC32_EV_*
    uint8_t btn;       // DC32_BTN_*
    uint16_t held;     // bitmask of buttons down when the event fired
    uint16_t held_ms;  // press duration (UP/SHORT/LONG/REPEAT)
    uint32_t t_ms;
} btn_event_t;

void buttons_init(void);
void buttons_set_timing(uint16_t long_ms, uint16_t repeat_delay_ms, uint16_t repeat_ms);
// Call often (>= every 2 ms); then drain events with buttons_next().
void buttons_poll(uint32_t now_ms);
bool buttons_next(btn_event_t *ev);
uint16_t buttons_down(void);           // debounced bitmask
uint32_t buttons_down_since(uint8_t btn);
