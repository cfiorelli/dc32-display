// USB mass storage: the microSD card as a plain USB drive (read/write). The PC owns the filesystem;
// the badge only moves 512-byte blocks. No card / dead card = "medium not present", retried every 2 s.
#include <string.h>
#include "tusb.h"
#include "sdcard.h"
#include "pico/time.h"

void dbg(const char *fmt, ...);   // main.c, CDC debug log

static uint32_t s_retry_ms;
bool msc_writable = false;           // read-only until reads are verified on this card

static bool card_ok(void)
{
    if (sd_ready()) return true;
    uint32_t now = to_ms_since_boot(get_absolute_time());
    if (now - s_retry_ms < 2000 && s_retry_ms) return false;
    s_retry_ms = now ? now : 1;
    bool ok = sd_init();
    dbg("sd init: %s, %lu blocks, %s, csd v%u, %s", ok ? "ok" : "no card", (unsigned long)sd_block_count(),
        sd_info_block_addr ? "block-addressed (SDHC/XC)" : "byte-addressed (SDSC)", sd_info_csd + 1,
        msc_writable ? "read/write" : "read-only");
    return ok;
}

void tud_msc_inquiry_cb(uint8_t lun, uint8_t vendor_id[8], uint8_t product_id[16], uint8_t product_rev[4])
{
    (void)lun;
    memcpy(vendor_id, "DEFCON32", 8);
    // last two chars: accelerometer status for diagnostics (A+ found, A- not found)
    extern bool accel_present;
    memcpy(product_id, accel_present ? "Badge microSD A+" : "Badge microSD A-", 16);
    // revision = card diagnostics, visible in /sys/block/sdX/device/rev:
    //   [0] B = block-addressed (SDHC/XC), b = byte-addressed (SDSC), - = no card; [1] CSD version 1/2
    bool ok = card_ok();
    product_rev[0] = !ok ? '-' : sd_info_block_addr ? 'B' : 'b';
    product_rev[1] = ok ? (char)('1' + sd_info_csd) : '-';
    product_rev[2] = msc_writable ? 'w' : 'r';
    product_rev[3] = !ok ? '-' : (char)('0' + sd_info_wp);    // 0 = not write protected
}

bool tud_msc_test_unit_ready_cb(uint8_t lun)
{
    (void)lun;
    if (card_ok()) return true;
    tud_msc_set_sense(lun, SCSI_SENSE_NOT_READY, 0x3A, 0x00);   // medium not present
    return false;
}

void tud_msc_capacity_cb(uint8_t lun, uint32_t *block_count, uint16_t *block_size)
{
    (void)lun;
    *block_count = card_ok() ? sd_block_count() : 0;
    *block_size = 512;
}

bool tud_msc_start_stop_cb(uint8_t lun, uint8_t power_condition, bool start, bool load_eject)
{
    (void)lun; (void)power_condition;
    if (load_eject && !start) sd_mark_failed();    // "eject": re-initialise the card on next access
    return true;
}

int32_t tud_msc_read10_cb(uint8_t lun, uint32_t lba, uint32_t offset, void *buffer, uint32_t bufsize)
{
    (void)lun;
    if (offset % 512 || bufsize % 512) return -1;
    if (!sd_read(lba + offset / 512, buffer, bufsize / 512)) {
        dbg("sd read lba %lu+%lu failed: cmd%u r1=0x%02x stage %u", (unsigned long)lba, (unsigned long)(offset / 512),
            sd_last_cmd, sd_last_r1, sd_last_stage);
        tud_msc_set_sense(lun, SCSI_SENSE_MEDIUM_ERROR, 0x11, 0x00);
        return -1;
    }
    return (int32_t)bufsize;
}

bool tud_msc_is_writable_cb(uint8_t lun)
{
    (void)lun;
    return msc_writable;
}

int32_t tud_msc_write10_cb(uint8_t lun, uint32_t lba, uint32_t offset, uint8_t *buffer, uint32_t bufsize)
{
    (void)lun;
    if (offset % 512 || bufsize % 512 || !msc_writable) return -1;
    if (!sd_write(lba + offset / 512, buffer, bufsize / 512)) {
        dbg("sd write lba %lu+%lu failed: cmd%u r1=0x%02x stage %u", (unsigned long)lba, (unsigned long)(offset / 512),
            sd_last_cmd, sd_last_r1, sd_last_stage);
        tud_msc_set_sense(lun, SCSI_SENSE_MEDIUM_ERROR, 0x03, 0x00);
        return -1;
    }
    return (int32_t)bufsize;
}

int32_t tud_msc_scsi_cb(uint8_t lun, uint8_t const scsi_cmd[16], void *buffer, uint16_t bufsize)
{
    switch (scsi_cmd[0]) {
    case 0xC0: {                                   // vendor: accelerometer diagnostics (tools)
        extern int accel_diag(uint8_t *out);
        uint8_t d[32];
        int n = accel_diag(d);
        if (n > bufsize) n = bufsize;
        memcpy(buffer, d, (size_t)n);
        return n;
    }
    case 0xC1: {                                   // vendor: simulate a tap via accelerometer self-test
        extern void accel_self_test_pulse(void);
        accel_self_test_pulse();
        return 0;
    }
    case 0x35:                                     // SYNCHRONIZE CACHE: writes are already on the card
        return 0;
    default:
        tud_msc_set_sense(lun, SCSI_SENSE_ILLEGAL_REQUEST, 0x20, 0x00);
        return -1;
    }
}
