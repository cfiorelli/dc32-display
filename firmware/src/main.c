// DC32 Display firmware — turns the DEF CON 32 badge into a USB mini display/controller.
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include "pico/stdlib.h"
#include "pico/bootrom.h"
#include "pico/binary_info.h"
#include "hardware/clocks.h"
#include "hardware/gpio.h"
#include "hardware/watchdog.h"
#include "leds.h"
#include "accel.h"
#include "sdcard.h"
#include "ir.h"
#include "tusb.h"

#include "board_dc32.h"
#include "buttons.h"
#include "dc32proto.h"
#include "decoder.h"
#include "lcd.h"
#include "ui.h"
#include "version.h"

bi_decl(bi_program_description("DC32 Display: USB mini display/controller firmware for the DEF CON 32 badge"));
bi_decl(bi_program_version_string(DC32_FW_VERSION "+" DC32_BUILD_ID));
bi_decl(bi_program_url("dc32-display (local project)"));

#define HOST_TIMEOUT_MS     3000
#define BOOTSEL_HOLD_MS     2000
#define IDLE_POWEROFF_MS    (15u * 60u * 1000u)
#define RX_CHUNK            4096
#define RX_BUDGET           (24 * 1024)   // max bytes decoded per loop pass (keeps buttons responsive)

typedef enum { SCR_DISCONNECTED, SCR_STREAM, SCR_MENU } screen_t;

static decoder_t s_dec;
static ui_menu_t s_menu;
static screen_t s_screen = SCR_DISCONNECTED;
static bool s_screen_dirty = true;
static uint32_t s_last_host_ms;
static bool s_host_alive;
static bool s_reset_req;
static bool s_accel_stream;
static uint32_t s_decode_us_acc;
static uint32_t s_frames_acked;
static uint16_t s_last_btn_drawn = 0xffff;
static const char *s_note;

static inline uint32_t now_ms(void) { return to_ms_since_boot(get_absolute_time()); }

// ---------------------------------------------------------------- debug log (CDC)
void dbg(const char *fmt, ...)
{
    char buf[160];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    if (n <= 0 || !tud_cdc_connected()) return;
    if (n > (int)sizeof buf - 1) n = sizeof buf - 1;
    if (tud_cdc_write_available() < (uint32_t)n + 2) return;
    tud_cdc_write(buf, (uint32_t)n);
    tud_cdc_write_str("\r\n");
    tud_cdc_write_flush();
}

// ---------------------------------------------------------------- tx helpers
static inline void wr16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static inline void wr32(uint8_t *p, uint32_t v) { for (int i = 0; i < 4; i++) p[i] = (uint8_t)(v >> (8 * i)); }

static bool send_msg(uint8_t type, const void *payload, uint32_t len)
{
    if (!tud_vendor_mounted()) return false;
    if (tud_vendor_write_available() < DC32_HDR_LEN + len) return false;
    uint8_t hdr[DC32_HDR_LEN] = {DC32_MAGIC, type, 0, 0};
    wr32(hdr + 4, len);
    tud_vendor_write(hdr, DC32_HDR_LEN);
    if (len) tud_vendor_write(payload, len);
    tud_vendor_write_flush();
    return true;
}

static void send_info(void)
{
    uint8_t p[16 + 32];
    const char *ver = DC32_FW_VERSION "+" DC32_BUILD_ID;
    size_t vl = strlen(ver);
    if (vl > 32) vl = 32;
    wr16(p, DC32_PROTO_VERSION); wr16(p + 2, DC32_SCREEN_W); wr16(p + 4, DC32_SCREEN_H);
    p[6] = DC32_FW_MAJOR; p[7] = DC32_FW_MINOR; p[8] = DC32_FW_PATCH; p[9] = DC32_BTN_COUNT;
    wr32(p + 10, DC32_SCREEN_W * DC32_SCREEN_H * 2);
    p[14] = 0; p[15] = 0;
    memcpy(p + 16, ver, vl);
    send_msg(DC32_MSG_INFO, p, (uint32_t)(16 + vl));
}

static void send_menu_result(uint8_t action, uint32_t id)
{
    uint8_t p[8] = {s_menu.kind, action, 0, 0};
    wr32(p + 4, id);
    send_msg(DC32_MSG_MENU_RESULT, p, 8);
}

// ---------------------------------------------------------------- screens
static void enter_screen(screen_t s)
{
    if (s_screen == s) return;
    s_screen = s;
    s_dec.drop_pixels = (s != SCR_STREAM);
    s_screen_dirty = true;
    if (s == SCR_STREAM) ui_fill(0, 0, DC32_SCREEN_W, DC32_SCREEN_H, 0x0000);
    if (s != SCR_MENU) s_menu.open = false;
}

static void menu_close(uint8_t action)
{
    uint32_t id = (s_menu.count && s_menu.sel < s_menu.count) ? s_menu.e[s_menu.sel].id : 0;
    send_menu_result(action, id);
    s_menu.open = false;
    enter_screen(s_host_alive ? SCR_STREAM : SCR_DISCONNECTED);
}

static void parse_menu(const uint8_t *p, uint32_t len)
{
    // u8 kind, u8 count, u8 selected, u8 title_len, title[title_len], then entries:
    //   u32 id, u8 flags, u8 name_len, name[name_len]
    if (len < 4) return;
    memset(&s_menu, 0, sizeof s_menu);
    s_menu.kind = p[0];
    int count = p[1], sel = p[2], tl = p[3];
    uint32_t off = 4;
    if (off + tl > len) return;
    int n = tl > UI_NAME_MAX ? UI_NAME_MAX : tl;
    memcpy(s_menu.title, p + off, (size_t)n);
    off += tl;
    for (int i = 0; i < count && s_menu.count < UI_MENU_MAX; i++) {
        if (off + 6 > len) break;
        ui_menu_entry_t *e = &s_menu.e[s_menu.count];
        e->id = (uint32_t)p[off] | ((uint32_t)p[off + 1] << 8) | ((uint32_t)p[off + 2] << 16) | ((uint32_t)p[off + 3] << 24);
        e->flags = p[off + 4];
        int nl = p[off + 5];
        off += 6;
        if (off + nl > len) break;
        int m = nl > UI_NAME_MAX ? UI_NAME_MAX : nl;
        memcpy(e->name, p + off, (size_t)m);
        e->name[m] = 0;
        off += nl;
        s_menu.count++;
    }
    s_menu.sel = (sel < s_menu.count) ? sel : 0;
    s_menu.top = 0;
    ui_menu_move(&s_menu, 0);
    s_menu.open = true;
    enter_screen(SCR_MENU);
    s_screen_dirty = true;
}

// ---------------------------------------------------------------- protocol callbacks
static void on_msg(decoder_t *d, uint8_t type, const uint8_t *p, uint32_t len)
{
    (void)d;
    switch (type) {
    case DC32_MSG_HELLO:
        dbg("HELLO proto=%u (last reset: %s)", len >= 2 ? (unsigned)(p[0] | (p[1] << 8)) : 0u,
            s_note ? s_note : "power-on / reboot");
        send_info();
        break;
    case DC32_MSG_PING: {
        uint8_t t[4] = {0};
        if (len >= 4) memcpy(t, p, 4);
        send_msg(DC32_MSG_PONG, t, 4);
        break;
    }
    case DC32_MSG_FRAME_END: {
        uint8_t a[16];
        uint32_t id = len >= 4 ? (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24) : 0;
        wr32(a, id); wr32(a + 4, s_dec.rx_bytes); wr32(a + 8, now_ms()); wr32(a + 12, s_decode_us_acc);
        s_decode_us_acc = 0;
        s_frames_acked++;
        send_msg(DC32_MSG_ACK, a, 16);
        break;
    }
    case DC32_MSG_MENU_LIST:
        parse_menu(p, len);
        break;
    case DC32_MSG_MENU_CLOSE:
        if (s_screen == SCR_MENU) { s_menu.open = false; enter_screen(SCR_STREAM); }
        break;
    case DC32_MSG_SET_LEDS:
        if (len >= 1 && len >= 1u + 3u * p[0]) leds_set(p + 1, p[0]);
        break;
    case DC32_MSG_SET_BRIGHTNESS:
        if (len >= 1) lcd_set_brightness(p[0]);
        break;
    case DC32_MSG_SET_SD_WRITE: {
        extern bool msc_writable;
        if (len >= 1) msc_writable = p[0] != 0;
        break;
    }
    case DC32_MSG_SET_IR:
        if (len >= 1) ir_enable(p[0] != 0);
        break;
    case DC32_MSG_SET_ACCEL:
        if (len >= 1) s_accel_stream = p[0] != 0;
        break;
    case DC32_MSG_SET_TAP:
        if (len >= 1) accel_set_tap_threshold(p[0]);
        break;
    case DC32_MSG_SET_TIMING:
        if (len >= 6) buttons_set_timing((uint16_t)(p[0] | (p[1] << 8)), (uint16_t)(p[2] | (p[3] << 8)), (uint16_t)(p[4] | (p[5] << 8)));
        break;
    case DC32_MSG_REBOOT:
        if (len >= 5 && memcmp(p + 1, "BOOT", 4) == 0) {
            dbg("reboot kind=%u", p[0]);
            sleep_ms(20);
            if (p[0] == 1) { ui_draw_bootsel_notice(); sleep_ms(200); reset_usb_boot(0, 0); }
            else watchdog_reboot(0, 0, 0);
        }
        break;
    default:
        break;   // forward compatible: ignore unknown small messages
    }
}

static void on_err(decoder_t *d, uint8_t code, uint32_t detail)
{
    (void)d;
    uint8_t p[8] = {code, 0, 0, 0};
    wr32(p + 4, detail);
    send_msg(DC32_MSG_ERROR, p, 8);
    dbg("decode error %u detail %lu -> hunting for SYNC", code, (unsigned long)detail);
}

void app_reset_stream(void) { s_reset_req = true; }   // called from USB control request

// ---------------------------------------------------------------- buttons
static void handle_button(const btn_event_t *ev)
{
    if (s_screen == SCR_MENU) {
        bool act = ev->ev == DC32_EV_SHORT || ev->ev == DC32_EV_DOWN || ev->ev == DC32_EV_REPEAT;
        if ((ev->btn == DC32_BTN_UP || ev->btn == DC32_BTN_DOWN) && (ev->ev == DC32_EV_DOWN || ev->ev == DC32_EV_REPEAT)) {
            ui_menu_move(&s_menu, ev->btn == DC32_BTN_UP ? -1 : 1);
            s_screen_dirty = true;
        } else if (ev->ev == DC32_EV_SHORT && ev->btn == DC32_BTN_A) {
            menu_close(DC32_MENU_SELECT);
        } else if (ev->ev == DC32_EV_SHORT && ev->btn == DC32_BTN_RIGHT) {
            menu_close(DC32_MENU_PIN);
        } else if (ev->ev == DC32_EV_SHORT && (ev->btn == DC32_BTN_B || ev->btn == DC32_BTN_START)) {
            menu_close(DC32_MENU_CANCEL);
        }
        (void)act;
        return;   // menu consumes all buttons locally
    }
    if (s_screen == SCR_STREAM && s_host_alive) {
        uint8_t p[12];
        p[0] = ev->ev; p[1] = ev->btn; wr16(p + 2, ev->held); wr32(p + 4, ev->t_ms); wr16(p + 8, ev->held_ms); wr16(p + 10, 0);
        send_msg(DC32_MSG_BUTTON, p, 12);
    }
}

static void check_bootsel_combo(uint32_t now)
{
    const uint16_t combo = (1u << DC32_BTN_FN) | (1u << DC32_BTN_START) | (1u << DC32_BTN_SELECT);
    if ((buttons_down() & combo) != combo) return;
    uint32_t since = buttons_down_since(DC32_BTN_FN);
    if (buttons_down_since(DC32_BTN_START) > since) since = buttons_down_since(DC32_BTN_START);
    if (buttons_down_since(DC32_BTN_SELECT) > since) since = buttons_down_since(DC32_BTN_SELECT);
    if (now - since >= BOOTSEL_HOLD_MS) {
        ui_draw_bootsel_notice();
        sleep_ms(300);
        reset_usb_boot(0, 0);
    }
}

// ---------------------------------------------------------------- main
int main(void)
{
    // 1) power latch first, so a battery-powered badge stays on.
    gpio_init(PIN_SELF_PWR);
    gpio_set_dir(PIN_SELF_PWR, GPIO_OUT);
    gpio_put(PIN_SELF_PWR, 1);
    // PSRAM chip-select (GPIO0) shares the QSPI bus with flash: hold it deasserted (stock does the same).
    gpio_init(PIN_PSRAM_CS);
    gpio_set_dir(PIN_PSRAM_CS, GPIO_OUT);
    gpio_put(PIN_PSRAM_CS, 1);

    // 2) stock firmware runs at 125 MHz; the LCD PIO timing is derived from it.
    set_sys_clock_khz(125000, true);

    bool wd_reboot = watchdog_enable_caused_reboot();   // a real timeout, not picotool/BOOTSEL reboots
    // Watchdog from the first line of init: an (intermittent) hang before USB used to leave the badge
    // dark and off USB until a power cycle; now it resets and retries. Tightened to 3 s once running.
    watchdog_enable(8000, true);
    lcd_init();
    leds_init();
    ui_init(lcd_fb());
    buttons_init();
    dec_init(&s_dec, lcd_fb());
    s_dec.on_msg = on_msg;
    s_dec.on_err = on_err;
    s_dec.drop_pixels = true;
    if (wd_reboot) s_note = "recovered from watchdog reset";

    tud_init(BOARD_TUD_RHPORT);
    watchdog_enable(3000, true);
    // after USB + watchdog: a wedged I2C bus or missing accelerometer must never keep the badge off USB
    bool have_accel = accel_init();
    dbg("accelerometer %s", have_accel ? "ok" : "not found");
    uint32_t last_tap_poll = 0, last_accel = 0;

    static uint8_t rx[RX_CHUNK];
    uint32_t last_ui = 0, last_mount_ms = now_ms();
    bool was_mounted = false;

    for (;;) {
        watchdog_update();
        tud_task();
        uint32_t now = now_ms();

        bool mounted = tud_mounted();
        if (mounted != was_mounted) {
            was_mounted = mounted;
            dec_reset_hunt(&s_dec);
            s_screen_dirty = true;
            dbg("usb %s", mounted ? "mounted" : "unmounted");
        }
        if (mounted) last_mount_ms = now;

        if (s_reset_req) {
            s_reset_req = false;
            tud_vendor_read_flush();
            dec_reset_hunt(&s_dec);
            dbg("stream reset by host");
        }

        // drain host data
        uint32_t budget = RX_BUDGET;
        while (budget && tud_vendor_available()) {
            uint32_t n = tud_vendor_read(rx, sizeof rx);
            if (!n) break;
            uint32_t t0 = time_us_32();
            dec_feed(&s_dec, rx, n);
            s_decode_us_acc += time_us_32() - t0;
            s_last_host_ms = now;
            budget = n >= budget ? 0 : budget - n;
            if (!s_host_alive) {
                s_host_alive = true;
                if (s_screen == SCR_DISCONNECTED) enter_screen(SCR_STREAM);
            }
            tud_task();
        }

        // taps (LIS3DH click engine latches them; 50 Hz polling is plenty)
        if (have_accel && now - last_tap_poll >= 20) {
            last_tap_poll = now;
            uint8_t tap = accel_poll_tap();
            if (tap && s_host_alive) send_msg(DC32_MSG_TAP, &tap, 1);
        }
        if (have_accel && s_accel_stream && s_host_alive && now - last_accel >= 40) {
            last_accel = now;
            int16_t x, y, z;
            if (accel_read_xyz(&x, &y, &z)) {
                uint8_t ap[6];
                wr16(ap, (uint16_t)x); wr16(ap + 2, (uint16_t)y); wr16(ap + 4, (uint16_t)z);
                send_msg(DC32_MSG_ACCEL, ap, 6);
            }
        }

        // IR scope: completed frames of marks/spaces
        if (ir_enabled() && s_host_alive) {
            static uint16_t pairs[2 * 128];
            static uint8_t msg[1 + 4 * 128];
            int n = ir_take_frame(pairs, 128);
            if (n) {
                msg[0] = (uint8_t)n;
                for (int i = 0; i < 2 * n; i++) wr16(msg + 1 + 2 * i, pairs[i]);
                send_msg(DC32_MSG_IR_FRAME, msg, 1u + 4u * (uint32_t)n);
            }
        }

        // host liveness
        if (s_host_alive && (!mounted || now - s_last_host_ms > HOST_TIMEOUT_MS)) {
            s_host_alive = false;
            dbg("host timeout");
            leds_off();
            enter_screen(SCR_DISCONNECTED);
        }

        // buttons
        buttons_poll(now);
        btn_event_t ev;
        while (buttons_next(&ev)) handle_button(&ev);
        check_bootsel_combo(now);

        // badge-native screens (rate-limited)
        if (now - last_ui >= 40) {
            if (s_screen == SCR_DISCONNECTED && (s_screen_dirty || buttons_down() != s_last_btn_drawn || now - last_ui >= 1000)) {
                ui_status_t st = {mounted, s_host_alive, buttons_down(), s_dec.rx_bytes, s_dec.errors,
                                  s_frames_acked, now / 1000, s_note};
                ui_draw_disconnected(&st, s_screen_dirty);
                s_last_btn_drawn = buttons_down();
                s_screen_dirty = false;
                last_ui = now;
            } else if (s_screen == SCR_MENU && s_screen_dirty) {
                ui_menu_draw(&s_menu);
                s_screen_dirty = false;
                last_ui = now;
            }
        }

        // battery saver: no USB host for a long time -> release power latch
        if (!mounted && now - last_mount_ms > IDLE_POWEROFF_MS) gpio_put(PIN_SELF_PWR, 0);
    }
}
