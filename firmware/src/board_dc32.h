// DEF CON 32 badge pin map — verified against DC32-cfw src/pinoutRp2350defcon.h (see docs/HARDWARE.md)
#pragma once

#define PIN_PSRAM_CS    0       // QSPI PSRAM chip select (stock: XIP_SS_N_1, idle high)

#define PIN_LCD_DC      5
#define PIN_LCD_MOSI    6
#define PIN_LCD_SCK     8
#define PIN_LCD_CS      9
#define PIN_LCD_BL      10      // PWM slice 5, channel A

#define PIN_SELF_PWR    11      // power latch: drive high to stay on
#define PIN_WS2812      4       // 9 WS2812 RGB LEDs (GRB, 800 kHz)

#define PIN_BTN_RIGHT   16
#define PIN_BTN_DOWN    17
#define PIN_BTN_UP      18
#define PIN_BTN_LEFT    19
#define PIN_BTN_B       20
#define PIN_BTN_A       21
#define PIN_BTN_START   22
#define PIN_BTN_SELECT  23
#define PIN_BTN_CENTER  24      // "FN"

#define LCD_HW_W        240     // native panel is portrait
#define LCD_HW_H        320

// microSD, SPI1 (stock: sdHwRP2350.c)
#define PIN_SD_MISO     12
#define PIN_SD_CS       13
#define PIN_SD_SCK      14
#define PIN_SD_MOSI     15

// LIS3DH accelerometer on I2C1, address 0x18 (stock: badgePower.c)
#define PIN_I2C_SDA     2
#define PIN_I2C_SCL     3
#define ACCEL_I2C_ADDR  0x18
