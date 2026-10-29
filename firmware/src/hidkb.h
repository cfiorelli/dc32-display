#pragma once
#include <stdint.h>
#include <stdbool.h>

// USB HID keyboard: types a PIN into whatever host the badge is plugged into (e.g. a phone over OTG).
void hidkb_type_pin(const char *digits);   // queues wake keys, the digits, then Enter
bool hidkb_busy(void);
bool hidkb_ready(void);                     // host has mounted the keyboard interface
void hidkb_task(uint32_t now_ms);           // call every loop pass
