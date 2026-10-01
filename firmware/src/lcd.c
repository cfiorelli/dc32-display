// LCD driver: same electrical behaviour as the stock DC32 firmware (dispDefcon.c),
// re-expressed with pico-sdk. The panel is fed continuously from a RAM framebuffer by
// two chained DMA channels, so the rest of the firmware only ever writes to RAM.
#include "lcd.h"
#include "board_dc32.h"
#include "hardware/clocks.h"
#include "hardware/dma.h"
#include "hardware/gpio.h"
#include "hardware/irq.h"
#include "hardware/pio.h"
#include "hardware/pwm.h"
#include "pico/stdlib.h"
#include "lcd_spi.pio.h"

static uint16_t s_fb[LCD_HW_W * LCD_HW_H] __attribute__((aligned(4)));
static const uint16_t *s_fb_addr = s_fb;
static PIO s_pio = pio0;
static const uint s_sm = 0;
static uint s_prog;
static int s_dma_data, s_dma_ctrl;
static volatile uint32_t s_frames;

uint16_t *lcd_fb(void) { return s_fb; }
uint32_t lcd_frames(void) { return s_frames; }

static void wait_tx_idle(void)
{
    const uint32_t stall = 1u << (PIO_FDEBUG_TXSTALL_LSB + s_sm);
    s_pio->fdebug = stall;
    while (!(s_pio->fdebug & stall)) tight_loop_contents();
}

static void set_width(uint bits)
{
    pio_sm_set_enabled(s_pio, s_sm, false);
    hw_write_masked(&s_pio->sm[s_sm].shiftctrl, (bits & 0x1f) << PIO_SM0_SHIFTCTRL_PULL_THRESH_LSB,
                    PIO_SM0_SHIFTCTRL_PULL_THRESH_BITS);
    pio_sm_restart(s_pio, s_sm);
    pio_sm_clear_fifos(s_pio, s_sm);
    pio_sm_exec(s_pio, s_sm, pio_encode_jmp(s_prog) | pio_encode_sideset(1, 0));
    pio_sm_set_enabled(s_pio, s_sm, true);
}

static void lcd_byte(uint8_t v)
{
    pio_sm_put_blocking(s_pio, s_sm, (uint32_t)v << 24);
    wait_tx_idle();
}

// cmd followed by n parameter bytes; CS released afterwards unless keep_cs
static void lcd_cmd(uint8_t cmd, const uint8_t *args, uint n, bool keep_cs)
{
    gpio_put(PIN_LCD_CS, 0);
    gpio_put(PIN_LCD_DC, 0);
    lcd_byte(cmd);
    gpio_put(PIN_LCD_DC, 1);
    for (uint i = 0; i < n; i++) lcd_byte(args[i]);
    if (!keep_cs) gpio_put(PIN_LCD_CS, 1);
}

static void dma_irq(void)
{
    if (dma_channel_get_irq0_status(s_dma_data)) {
        dma_channel_acknowledge_irq0(s_dma_data);
        s_frames++;
    }
}

void lcd_set_brightness(uint8_t bri)
{
    if (bri > 31) bri = 31;
    pwm_set_chan_level(pwm_gpio_to_slice_num(PIN_LCD_BL), pwm_gpio_to_channel(PIN_LCD_BL), (uint16_t)(bri * bri + 61));
}

void lcd_init(void)
{
    // GPIO: D/C and CS are plain outputs; MOSI/SCK belong to PIO0.
    gpio_init(PIN_LCD_DC); gpio_set_dir(PIN_LCD_DC, GPIO_OUT); gpio_put(PIN_LCD_DC, 0);
    gpio_init(PIN_LCD_CS); gpio_set_dir(PIN_LCD_CS, GPIO_OUT); gpio_put(PIN_LCD_CS, 1);
    gpio_set_slew_rate(PIN_LCD_MOSI, GPIO_SLEW_RATE_FAST);
    gpio_set_slew_rate(PIN_LCD_SCK, GPIO_SLEW_RATE_FAST);

    s_prog = pio_add_program(s_pio, &lcd_spi_program);
    pio_gpio_init(s_pio, PIN_LCD_MOSI);
    pio_gpio_init(s_pio, PIN_LCD_SCK);
    pio_sm_set_consecutive_pindirs(s_pio, s_sm, PIN_LCD_MOSI, 1, true);
    pio_sm_set_consecutive_pindirs(s_pio, s_sm, PIN_LCD_SCK, 1, true);

    pio_sm_config c = lcd_spi_program_get_default_config(s_prog);
    sm_config_set_out_pins(&c, PIN_LCD_MOSI, 1);
    sm_config_set_sideset_pins(&c, PIN_LCD_SCK);
    sm_config_set_out_shift(&c, false /* shift left = MSB first */, true /* autopull */, 8);
    sm_config_set_fifo_join(&c, PIO_FIFO_JOIN_TX);
    // Stock firmware: integer divider = ceil((sys/2) / 70 MHz) -> 1 at 125 MHz (62.5 Mbit/s).
    uint32_t sys = clock_get_hz(clk_sys);
    uint32_t div = (sys / 2 + 70000000u - 1) / 70000000u;
    if (div < 1) div = 1;
    sm_config_set_clkdiv_int_frac(&c, (uint16_t)div, 0);
    pio_sm_init(s_pio, s_sm, s_prog, &c);
    pio_sm_set_enabled(s_pio, s_sm, true);

    // Backlight PWM (off until the panel shows something sane)
    gpio_set_function(PIN_LCD_BL, GPIO_FUNC_PWM);
    uint slice = pwm_gpio_to_slice_num(PIN_LCD_BL);
    pwm_config pc = pwm_get_default_config();
    pwm_config_set_wrap(&pc, 1022);
    pwm_config_set_clkdiv_int(&pc, 1);
    pwm_init(slice, &pc, true);
    pwm_set_chan_level(slice, pwm_gpio_to_channel(PIN_LCD_BL), 0);

    // Panel init: identical command sequence to the stock firmware.
    lcd_cmd(0x01, NULL, 0, false); sleep_ms(120);           // SWRESET
    lcd_cmd(0x11, NULL, 0, false); sleep_ms(120);           // SLPOUT
    lcd_cmd(0x3A, (const uint8_t[]){0x55}, 1, false); sleep_ms(10);  // COLMOD RGB565
    lcd_cmd(0x36, (const uint8_t[]){0x00}, 1, false);       // MADCTL
    lcd_cmd(0x2A, (const uint8_t[]){0, 0, (LCD_HW_W - 1) >> 8, (LCD_HW_W - 1) & 0xff}, 4, false);
    lcd_cmd(0x2B, (const uint8_t[]){0, 0, (LCD_HW_H - 1) >> 8, (LCD_HW_H - 1) & 0xff}, 4, false);
    lcd_cmd(0x20, NULL, 0, false); sleep_ms(10);            // INVOFF
    lcd_cmd(0x13, NULL, 0, false); sleep_ms(10);            // NORON
    lcd_cmd(0x29, NULL, 0, false); sleep_ms(10);            // DISPON

    // Start RAMWR and leave CS asserted; switch PIO to 16-bit and stream forever.
    lcd_cmd(0x2C, NULL, 0, true);
    set_width(16);

    s_dma_data = dma_claim_unused_channel(true);
    s_dma_ctrl = dma_claim_unused_channel(true);

    dma_channel_config dc = dma_channel_get_default_config(s_dma_data);
    channel_config_set_transfer_data_size(&dc, DMA_SIZE_16);
    channel_config_set_read_increment(&dc, true);
    channel_config_set_write_increment(&dc, false);
    channel_config_set_dreq(&dc, pio_get_dreq(s_pio, s_sm, true));
    channel_config_set_chain_to(&dc, s_dma_ctrl);
    channel_config_set_high_priority(&dc, true);
    dma_channel_configure(s_dma_data, &dc, &s_pio->txf[s_sm], s_fb, LCD_HW_W * LCD_HW_H, false);

    dma_channel_config cc = dma_channel_get_default_config(s_dma_ctrl);
    channel_config_set_transfer_data_size(&cc, DMA_SIZE_32);
    channel_config_set_read_increment(&cc, false);
    channel_config_set_write_increment(&cc, false);
    dma_channel_configure(s_dma_ctrl, &cc, &dma_hw->ch[s_dma_data].al3_read_addr_trig, &s_fb_addr, 1, false);

    dma_channel_set_irq0_enabled(s_dma_data, true);
    irq_add_shared_handler(DMA_IRQ_0, dma_irq, PICO_SHARED_IRQ_HANDLER_DEFAULT_ORDER_PRIORITY);
    irq_set_enabled(DMA_IRQ_0, true);

    dma_channel_start(s_dma_ctrl);
    lcd_set_brightness(20);
}
