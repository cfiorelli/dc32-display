// LIS3DH accelerometer (I2C1, 0x18): hardware tap detection, polled.
#pragma once
#include <stdbool.h>
#include <stdint.h>

bool accel_init(void);              // false: no accelerometer answered
void accel_set_tap_threshold(uint8_t ths);   // 1..127, 16 mg/LSB at +-2 g; 0 = taps off
// Poll at ~50 Hz. Returns 0 (nothing), 1 (single tap) or 2 (double tap).
uint8_t accel_poll_tap(void);
void accel_self_test_pulse(void);   // simulated tap (tests)
