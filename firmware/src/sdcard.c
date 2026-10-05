// Minimal SD/SDHC/SDXC driver, SPI mode. Single-block CMD17/CMD24 (simple, and fast enough for
// USB full speed). Every wait is bounded and pets the watchdog, so a bad card can't reset the badge.
#include "sdcard.h"

#include "board_dc32.h"
#include "hardware/gpio.h"
#include "hardware/spi.h"
#include "hardware/watchdog.h"
#include "pico/time.h"

#define SD_SPI      spi1
#define SLOW_HZ     400000
#define FAST_HZ     12500000    // conservative: long traces to the slot

static bool s_ready, s_block_addr;      // block_addr: SDHC/SDXC take block numbers, SDSC bytes
static uint32_t s_blocks;
uint8_t sd_last_cmd, sd_last_r1, sd_last_stage;
bool sd_info_block_addr;
uint8_t sd_info_csd;
uint8_t sd_info_wp;           // bit 1: permanent, bit 0: temporary write protect (CSD)   // why the last I/O failed (debug log)

static inline void cs(bool on) { gpio_put(PIN_SD_CS, !on); }

static uint8_t xfer(uint8_t b)
{
    uint8_t r;
    spi_write_read_blocking(SD_SPI, &b, &r, 1);
    return r;
}

static bool wait_ready(uint32_t ms)
{
    absolute_time_t until = make_timeout_time_ms(ms);
    do {
        if (xfer(0xFF) == 0xFF) return true;
        watchdog_update();
    } while (absolute_time_diff_us(get_absolute_time(), until) > 0);
    return false;
}

static void deselect(void) { cs(false); xfer(0xFF); }

static bool select_card(void)
{
    cs(true);
    xfer(0xFF);
    if (wait_ready(500)) return true;
    deselect();
    return false;
}

static uint8_t cmd(uint8_t c, uint32_t arg)
{
    if (c & 0x80) {                       // ACMD: CMD55 first
        c &= 0x7F;
        uint8_t r = cmd(55, 0);
        if (r > 1) return r;
    }
    deselect();
    if (!select_card()) return 0xFF;
    uint8_t crc = c == 0 ? 0x95 : c == 8 ? 0x87 : 0x01;
    uint8_t pkt[6] = {(uint8_t)(0x40 | c), (uint8_t)(arg >> 24), (uint8_t)(arg >> 16), (uint8_t)(arg >> 8), (uint8_t)arg, crc};
    spi_write_blocking(SD_SPI, pkt, 6);
    if (c == 12) xfer(0xFF);
    uint8_t r;
    int n = 10;
    do { r = xfer(0xFF); } while ((r & 0x80) && --n);
    return r;
}

static bool rx_block(uint8_t *buf, uint32_t len)
{
    absolute_time_t until = make_timeout_time_ms(200);
    uint8_t t;
    do {
        t = xfer(0xFF);
    } while (t == 0xFF && absolute_time_diff_us(get_absolute_time(), until) > 0);
    if (t != 0xFE) return false;
    spi_read_blocking(SD_SPI, 0xFF, buf, len);
    xfer(0xFF); xfer(0xFF);               // CRC (ignored)
    return true;
}

static bool tx_block(const uint8_t *buf, uint8_t token)
{
    if (!wait_ready(500)) return false;
    xfer(token);
    spi_write_blocking(SD_SPI, buf, 512);
    xfer(0xFF); xfer(0xFF);               // dummy CRC
    return (xfer(0xFF) & 0x1F) == 0x05;   // data accepted
}

bool sd_init(void)
{
    s_ready = false;
    s_blocks = 0;
    spi_init(SD_SPI, SLOW_HZ);
    gpio_set_function(PIN_SD_MISO, GPIO_FUNC_SPI);
    gpio_set_function(PIN_SD_SCK, GPIO_FUNC_SPI);
    gpio_set_function(PIN_SD_MOSI, GPIO_FUNC_SPI);
    gpio_pull_up(PIN_SD_MISO);
    gpio_init(PIN_SD_CS);
    gpio_set_dir(PIN_SD_CS, GPIO_OUT);
    cs(false);
    for (int i = 0; i < 10; i++) xfer(0xFF);          // >= 74 clocks with CS high

    bool ok = false;
    if (cmd(0, 0) == 1) {                              // idle
        uint32_t hcs = 0;
        if (cmd(8, 0x1AA) == 1) {                      // SD v2
            uint8_t r7[4];
            for (int i = 0; i < 4; i++) r7[i] = xfer(0xFF);
            if (r7[2] != 0x01 || r7[3] != 0xAA) goto done;
            hcs = 1u << 30;
        }
        absolute_time_t until = make_timeout_time_ms(1500);
        uint8_t r;
        do {
            r = cmd(0x80 | 41, hcs);
            watchdog_update();
        } while (r != 0 && absolute_time_diff_us(get_absolute_time(), until) > 0);
        if (r != 0) goto done;
        s_block_addr = false;
        if (hcs && cmd(58, 0) == 0) {
            uint8_t ocr[4];
            for (int i = 0; i < 4; i++) ocr[i] = xfer(0xFF);
            s_block_addr = (ocr[0] & 0x40) != 0;       // CCS
        }
        if (!s_block_addr) cmd(16, 512);
        uint8_t csd[16];
        if (cmd(9, 0) == 0 && rx_block(csd, 16)) {
            sd_info_csd = csd[0] >> 6;
            sd_info_wp = (csd[14] >> 4) & 3;
            if ((csd[0] >> 6) == 1) {                  // CSD v2: (C_SIZE + 1) * 512 KiB
                uint32_t c_size = ((uint32_t)(csd[7] & 0x3F) << 16) | ((uint32_t)csd[8] << 8) | csd[9];
                s_blocks = (c_size + 1) * 1024;
            } else {                                   // CSD v1
                uint32_t c_size = ((uint32_t)(csd[6] & 0x03) << 10) | ((uint32_t)csd[7] << 2) | (csd[8] >> 6);
                uint32_t mult = ((csd[9] & 0x03) << 1) | (csd[10] >> 7);
                uint32_t bl_len = csd[5] & 0x0F;
                s_blocks = ((c_size + 1) << (mult + 2)) << (bl_len - 9);
            }
            ok = s_blocks != 0;
        }
    }
done:
    deselect();
    sd_info_block_addr = s_block_addr;
    if (ok) spi_set_baudrate(SD_SPI, FAST_HZ);
    s_ready = ok;
    return ok;
}

bool sd_ready(void) { return s_ready; }
uint32_t sd_block_count(void) { return s_ready ? s_blocks : 0; }
void sd_mark_failed(void) { s_ready = false; }

static bool read_one(uint32_t lba, uint8_t *buf)
{
    uint32_t a = s_block_addr ? lba : lba * 512;
    uint8_t r = cmd(17, a);
    bool ok = r == 0 && rx_block(buf, 512);
    deselect();
    if (!ok) { sd_last_cmd = 17; sd_last_r1 = r; sd_last_stage = r == 0 ? 2 : 1; }
    return ok;
}

// Each block: 3 tries, then one card re-init and a last try. A single glitch must not take the
// whole card away from the PC (it would see "medium not present" for every read after it).
bool sd_read(uint32_t lba, uint8_t *buf, uint32_t count)
{
    if (!s_ready && !sd_init()) return false;
    for (uint32_t i = 0; i < count; i++) {
        int tries = 0;
        while (!read_one(lba + i, buf + 512 * i)) {
            watchdog_update();
            if (++tries == 3 && !sd_init()) return false;
            if (tries > 3) return false;
        }
    }
    return true;
}

static bool write_one(uint32_t lba, const uint8_t *buf)
{
    uint32_t a = s_block_addr ? lba : lba * 512;
    uint8_t r = cmd(24, a);
    bool ok = r == 0 && tx_block(buf, 0xFE) && wait_ready(500);
    deselect();
    if (!ok) { sd_last_cmd = 24; sd_last_r1 = r; sd_last_stage = r == 0 ? 2 : 1; return false; }
    // CMD13 (R2): a write-protected or failing card can accept the data and not program it
    uint8_t r1 = cmd(13, 0), r2 = xfer(0xFF);
    deselect();
    if (r1 || r2) { sd_last_cmd = 13; sd_last_r1 = r1; sd_last_stage = r2; return false; }
    return true;
}

bool sd_write(uint32_t lba, const uint8_t *buf, uint32_t count)
{
    if (!s_ready && !sd_init()) return false;
    for (uint32_t i = 0; i < count; i++) {
        int tries = 0;
        while (!write_one(lba + i, buf + 512 * i)) {
            watchdog_update();
            if (++tries == 3 && !sd_init()) return false;
            if (tries > 3) return false;
        }
    }
    return true;
}
