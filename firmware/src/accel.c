// LIS3DH click (tap) engine: 400 Hz, +-2 g, single + double click on all axes, latched until read.
#include "accel.h"

#include "board_dc32.h"
#include "hardware/gpio.h"
#include "hardware/i2c.h"
#include "pico/time.h"

#define I2C          i2c1
#define WHO_AM_I     0x0F    // = 0x33
#define CTRL_REG1    0x20
#define CTRL_REG3    0x22
#define CTRL_REG4    0x23
#define CTRL_REG5    0x24
#define CLICK_CFG    0x38
#define CLICK_SRC    0x39
#define CLICK_THS    0x3A
#define TIME_LIMIT   0x3B
#define TIME_LATENCY 0x3C
#define TIME_WINDOW  0x3D

static bool s_ok;
static uint8_t s_ths = 0x20;          // ~0.5 g: a finger tap on the badge, not desk vibration

static bool wr(uint8_t reg, uint8_t val)
{
    uint8_t b[2] = {reg, val};
    return i2c_write_timeout_us(I2C, ACCEL_I2C_ADDR, b, 2, false, 2000) == 2;
}

static bool rd(uint8_t reg, uint8_t *val)
{
    return i2c_write_timeout_us(I2C, ACCEL_I2C_ADDR, &reg, 1, true, 2000) == 1 &&
           i2c_read_timeout_us(I2C, ACCEL_I2C_ADDR, val, 1, false, 2000) == 1;
}

// A reset in the middle of a read can leave the LIS3DH holding SDA low: clock SCL until it lets go.
static void bus_recover(void)
{
    gpio_init(PIN_I2C_SCL);
    gpio_init(PIN_I2C_SDA);
    gpio_pull_up(PIN_I2C_SDA);
    gpio_pull_up(PIN_I2C_SCL);
    gpio_set_dir(PIN_I2C_SDA, GPIO_IN);
    for (int i = 0; i < 18 && !gpio_get(PIN_I2C_SDA); i++) {
        gpio_set_dir(PIN_I2C_SCL, GPIO_OUT);       // drive low (output latch is 0)
        gpio_put(PIN_I2C_SCL, 0);
        busy_wait_us(5);
        gpio_set_dir(PIN_I2C_SCL, GPIO_IN);        // release: pull-up takes it high
        busy_wait_us(5);
    }
}

bool accel_init(void)
{
    bus_recover();
    i2c_init(I2C, 400000);
    gpio_set_function(PIN_I2C_SDA, GPIO_FUNC_I2C);
    gpio_set_function(PIN_I2C_SCL, GPIO_FUNC_I2C);
    gpio_pull_up(PIN_I2C_SDA);
    gpio_pull_up(PIN_I2C_SCL);
    uint8_t id = 0;
    s_ok = rd(WHO_AM_I, &id) && id == 0x33 &&
           wr(CTRL_REG1, 0x77) &&     // 400 Hz, X/Y/Z on
           wr(CTRL_REG4, 0x80) &&     // block data update, +-2 g
           wr(CTRL_REG3, 0x00) &&     // no interrupt pins: we poll
           wr(CLICK_CFG, 0x3F) &&     // single + double click, X/Y/Z
           wr(TIME_LIMIT, 0x10) &&    // tap shorter than 40 ms
           wr(TIME_LATENCY, 0x20) &&  // 80 ms dead time after a tap
           wr(TIME_WINDOW, 0x80);     // second tap within 320 ms -> double
    accel_set_tap_threshold(s_ths);
    return s_ok;
}

void accel_set_tap_threshold(uint8_t ths)
{
    s_ths = ths & 0x7F;
    if (s_ok) wr(CLICK_THS, 0x80 | (s_ths ? s_ths : 0x7F));   // bit 7: latch until CLICK_SRC is read
}

uint8_t accel_poll_tap(void)
{
    uint8_t src;
    if (!s_ok || !s_ths || !rd(CLICK_SRC, &src) || !(src & 0x40)) return 0;
    return (src & 0x20) ? 2 : (src & 0x10) ? 1 : 0;
}
