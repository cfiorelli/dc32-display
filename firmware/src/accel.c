// LIS3DH click (tap) engine: 400 Hz, +-2 g, single + double click on all axes, latched until read.
#include "accel.h"

#include "board_dc32.h"
#include "hardware/gpio.h"
#include "hardware/i2c.h"
#include "pico/time.h"

#define I2C          i2c1
#define WHO_AM_I     0x0F    // = 0x33
#define CTRL_REG1    0x20
#define CTRL_REG2    0x21
#define CTRL_REG3    0x22
#define REFERENCE    0x26
#define CTRL_REG4    0x23
#define CTRL_REG5    0x24
#define CLICK_CFG    0x38
#define CLICK_SRC    0x39
#define CLICK_THS    0x3A
#define TIME_LIMIT   0x3B
#define TIME_LATENCY 0x3C
#define TIME_WINDOW  0x3D

static bool s_ok;
bool accel_present;                  // exported for diagnostics (msc_disk.c)
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
           wr(CTRL_REG2, 0x04) &&     // high-pass filter on the click path: gravity (1 g) is not a tap
           wr(CTRL_REG3, 0x00) &&     // no interrupt pins: we poll
           wr(CLICK_CFG, 0x3F) &&     // single + double click, X/Y/Z
           wr(TIME_LIMIT, 0x10) &&    // tap shorter than 40 ms
           wr(TIME_LATENCY, 0x20) &&  // 80 ms dead time after a tap
           wr(TIME_WINDOW, 0x80);     // second tap within 320 ms -> double
    uint8_t ref;
    if (s_ok) rd(REFERENCE, &ref);   // reading REFERENCE settles the high-pass filter
    accel_set_tap_threshold(s_ths);
    accel_present = s_ok;
    return s_ok;
}

void accel_set_tap_threshold(uint8_t ths)
{
    s_ths = ths & 0x7F;
    if (s_ok) wr(CLICK_THS, 0x80 | (s_ths ? s_ths : 0x7F));   // bit 7: latch until CLICK_SRC is read
}

uint32_t accel_polls, accel_taps_seen;
uint8_t accel_last_src;

// Diagnostics: WHO_AM_I, CTRL_REG1..5, CLICK_CFG, CLICK_THS, TIME_LIMIT/LATENCY/WINDOW, then OUT X/Y/Z
// (6 bytes, little-endian), last CLICK_SRC, polls (u32), taps seen (u32). 26 bytes.
int accel_diag(uint8_t *out)
{
    static const uint8_t regs[] = {WHO_AM_I, CTRL_REG1, CTRL_REG2, CTRL_REG3, CTRL_REG4, CTRL_REG5,
                                   CLICK_CFG, CLICK_THS, TIME_LIMIT, TIME_LATENCY, TIME_WINDOW};
    int n = 0;
    for (unsigned i = 0; i < sizeof regs; i++) { uint8_t v = 0xEE; rd(regs[i], &v); out[n++] = v; }
    for (uint8_t r = 0x28; r < 0x2E; r++) { uint8_t v = 0xEE; rd(r, &v); out[n++] = v; }
    out[n++] = accel_last_src;
    for (int i = 0; i < 4; i++) out[n++] = (uint8_t)(accel_polls >> (8 * i));
    for (int i = 0; i < 4; i++) out[n++] = (uint8_t)(accel_taps_seen >> (8 * i));
    return n;
}

// Test hook: the LIS3DH self-test applies an electrostatic force to the proof mass. Pulsing it for
// ~20 ms gives the click engine a real, sharp step, i.e. a simulated tap, end to end.
void accel_self_test_pulse(void)
{
    if (!s_ok) return;
    wr(CTRL_REG4, 0x82);              // self-test 0
    sleep_ms(20);
    wr(CTRL_REG4, 0x80);
}

uint8_t accel_poll_tap(void)
{
    uint8_t src;
    accel_polls++;
    if (!s_ok || !s_ths || !rd(CLICK_SRC, &src)) return 0;
    if (src) accel_last_src = src;
    // SClick/DClick bits; IA (0x40) only rises when the click is routed to an INT pin, which we don't use
    if (!(src & 0x30)) return 0;
    accel_taps_seen++;
    return (src & 0x20) ? 2 : (src & 0x10) ? 1 : 0;
}
