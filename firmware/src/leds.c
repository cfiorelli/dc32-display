// WS2812 driver on PIO1 SM0 (PIO0 belongs to the LCD). Frames are 9 x 24 bits = 270 us on the wire;
// the 8-deep joined TX FIFO plus a short blocking tail keeps the main loop's stall well under 1 ms.
#include "leds.h"
#include "board_dc32.h"
#include "hardware/clocks.h"
#include "hardware/pio.h"
#include "ws2812.pio.h"

#define LED_PIO pio1
#define LED_SM  0
// Current cap: each channel draws ~20 mA at 255. Keep all 27 channels together under ~170 mA so the
// badge stays inside a USB 2.0 port's 500 mA budget with the LCD backlight on.
#define LED_SUM_MAX (LEDS_COUNT * 3 * 80)

static bool s_ok;

void leds_init(void)
{
    if (!pio_can_add_program(LED_PIO, &ws2812_program)) return;
    uint off = pio_add_program(LED_PIO, &ws2812_program);
    pio_gpio_init(LED_PIO, PIN_WS2812);
    pio_sm_set_consecutive_pindirs(LED_PIO, LED_SM, PIN_WS2812, 1, true);
    pio_sm_config c = ws2812_program_get_default_config(off);
    sm_config_set_sideset_pins(&c, PIN_WS2812);
    sm_config_set_out_shift(&c, false, true, 24);
    sm_config_set_fifo_join(&c, PIO_FIFO_JOIN_TX);
    int cycles = ws2812_T1 + ws2812_T2 + ws2812_T3;
    sm_config_set_clkdiv(&c, (float)clock_get_hz(clk_sys) / (800000.0f * cycles));
    pio_sm_init(LED_PIO, LED_SM, off, &c);
    pio_sm_set_enabled(LED_PIO, LED_SM, true);
    s_ok = true;
    leds_off();
}

void leds_set(const uint8_t *rgb, uint32_t n)
{
    if (!s_ok) return;
    if (n > LEDS_COUNT) n = LEDS_COUNT;
    uint32_t sum = 0;
    for (uint32_t i = 0; i < n * 3; i++) sum += rgb[i];
    uint32_t scale = sum > LED_SUM_MAX ? (LED_SUM_MAX * 256u) / sum : 256u;
    for (uint32_t i = 0; i < LEDS_COUNT; i++) {
        uint32_t r = 0, g = 0, b = 0;
        if (i < n) {
            r = (rgb[3 * i] * scale) >> 8;
            g = (rgb[3 * i + 1] * scale) >> 8;
            b = (rgb[3 * i + 2] * scale) >> 8;
        }
        pio_sm_put_blocking(LED_PIO, LED_SM, ((g << 16) | (r << 8) | b) << 8);
    }
}

void leds_off(void)
{
    static const uint8_t zero[LEDS_COUNT * 3];
    leds_set(zero, LEDS_COUNT);
}
