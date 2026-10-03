// The badge's 9 WS2812 RGB LEDs (GPIO4). Front: 0,2,4,5,6. Rear: 1,3,7,8 (from DC32-cfw badgeLeds.c).
#pragma once
#include <stdint.h>

#define LEDS_COUNT 9

void leds_init(void);
// rgb: n * 3 bytes (r, g, b). Scaled down if the total would draw too much USB current.
void leds_set(const uint8_t *rgb, uint32_t n);
void leds_off(void);
