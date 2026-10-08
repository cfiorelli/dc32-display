// IrDA transceiver (GPIO26 TX LED, GPIO27 RX, GPIO7 shutdown) used as a raw IR receiver for the
// "IR scope": marks (carrier bursts merged) and spaces in microseconds.
#pragma once
#include <stdbool.h>
#include <stdint.h>

void ir_enable(bool on);            // power the transceiver + edge interrupt on/off (off by default)
bool ir_enabled(void);
// Completed IR frame (signal idle > 20 ms): fills up to max (mark_us, space_us) pairs, returns the
// count, 0 if nothing new. The last space of a frame is 0.
int ir_take_frame(uint16_t *pairs, int max_pairs);
void ir_send_test_nec(uint8_t addr, uint8_t cmd);   // blink an NEC code from the badge's own IR LED
