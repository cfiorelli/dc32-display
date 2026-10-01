#pragma once
#include <stdint.h>
#include <stdbool.h>

void lcd_init(void);                  // panel init + start continuous DMA scanout
uint16_t *lcd_fb(void);               // hardware-order RGB565 framebuffer (240x320)
void lcd_set_brightness(uint8_t bri); // 0..31
uint32_t lcd_frames(void);            // scanout frame counter (for diagnostics)
