// IR receive: edge interrupt on the transceiver's RX line (idle high, low while IR light arrives).
// A remote's 38 kHz carrier can show up as one long low or as individual ~13 us pulses, so lows that
// are less than MERGE_US apart are merged into one "mark". A frame ends after IDLE_US of darkness.
#include "ir.h"

#include "board_dc32.h"
#include "hardware/gpio.h"
#include "hardware/sync.h"
#include "pico/time.h"

#define MERGE_US   150u        // gap shorter than this inside a burst = same mark (carrier)
#define IDLE_US    20000u      // darkness that ends a frame
#define MAX_PAIRS  128

static volatile bool s_on;
static volatile uint32_t s_mark_start, s_last_low_end, s_last_edge;
static volatile bool s_in_mark, s_have_mark;
static uint16_t s_buf[2 * MAX_PAIRS];
static volatile int s_n;               // pairs in the frame being built
static uint16_t s_done[2 * MAX_PAIRS];
static volatile int s_done_n;          // completed frame waiting for the host (0 = none)

static inline uint16_t clamp16(uint32_t v) { return v > 65535u ? 65535u : (uint16_t)v; }

static void close_mark(uint32_t end)
{
    if (s_n < MAX_PAIRS) {
        s_buf[2 * s_n] = clamp16(end - s_mark_start);
        s_buf[2 * s_n + 1] = 0;
        s_n++;
    }
    s_in_mark = false;
    s_have_mark = true;
}

static void on_edge(void)
{
    uint32_t ev = gpio_get_irq_event_mask(PIN_IRDA_IN);
    gpio_acknowledge_irq(PIN_IRDA_IN, ev);
    uint32_t now = time_us_32();
    s_last_edge = now;
    if (ev & GPIO_IRQ_EDGE_FALL) {                    // IR light on
        if (s_in_mark && now - s_last_low_end < MERGE_US)
            return;                                   // next carrier pulse of the same mark
        if (s_in_mark)
            close_mark(s_last_low_end);
        if (s_have_mark && s_n > 0)                   // space since the previous mark
            s_buf[2 * (s_n - 1) + 1] = clamp16(now - s_last_low_end);
        s_mark_start = now;
        s_in_mark = true;
    }
    if (ev & GPIO_IRQ_EDGE_RISE)                      // IR light off (maybe only between carrier pulses)
        s_last_low_end = now;
}

void ir_enable(bool on)
{
    if (on == s_on) return;
    if (on) {
        gpio_init(PIN_IRDA_SD);
        gpio_set_dir(PIN_IRDA_SD, GPIO_OUT);
        gpio_put(PIN_IRDA_SD, 0);                     // transceiver on
        gpio_init(PIN_IRDA_OUT);
        gpio_set_dir(PIN_IRDA_OUT, GPIO_OUT);
        gpio_put(PIN_IRDA_OUT, 0);                    // LED off
        gpio_init(PIN_IRDA_IN);
        gpio_set_dir(PIN_IRDA_IN, GPIO_IN);
        gpio_pull_up(PIN_IRDA_IN);
        s_n = 0; s_in_mark = s_have_mark = false; s_done_n = 0;
        gpio_add_raw_irq_handler(PIN_IRDA_IN, on_edge);
        gpio_set_irq_enabled(PIN_IRDA_IN, GPIO_IRQ_EDGE_FALL | GPIO_IRQ_EDGE_RISE, true);
        irq_set_enabled(IO_IRQ_BANK0, true);
    } else {
        gpio_set_irq_enabled(PIN_IRDA_IN, GPIO_IRQ_EDGE_FALL | GPIO_IRQ_EDGE_RISE, false);
        gpio_remove_raw_irq_handler(PIN_IRDA_IN, on_edge);
        gpio_put(PIN_IRDA_SD, 1);                     // transceiver shut down (saves power)
    }
    s_on = on;
}

bool ir_enabled(void) { return s_on; }

int ir_take_frame(uint16_t *pairs, int max_pairs)
{
    if (!s_on) return 0;
    uint32_t save = save_and_disable_interrupts();
    uint32_t now = time_us_32();
    // finish a mark whose light went off a while ago, and the frame after IDLE_US of darkness
    if (s_in_mark && gpio_get(PIN_IRDA_IN) && now - s_last_low_end >= MERGE_US)
        close_mark(s_last_low_end);
    if (!s_in_mark && s_n > 0 && now - s_last_edge >= IDLE_US && !s_done_n) {
        for (int i = 0; i < 2 * s_n; i++) s_done[i] = s_buf[i];
        s_done_n = s_n;
        s_n = 0;
        s_have_mark = false;
    }
    int n = 0;
    if (s_done_n) {
        n = s_done_n < max_pairs ? s_done_n : max_pairs;
        for (int i = 0; i < 2 * n; i++) pairs[i] = s_done[i];
        s_done_n = 0;
    }
    restore_interrupts(save);
    return n;
}

// ---------------------------------------------------------------- self-test transmitter
static void carrier(uint32_t us)          // 38 kHz, ~1/3 duty, bit-banged (IrDA TX limits pulse width)
{
    uint32_t end = time_us_32() + us;
    while ((int32_t)(end - time_us_32()) > 0) {
        gpio_put(PIN_IRDA_OUT, 1);
        busy_wait_us(9);
        gpio_put(PIN_IRDA_OUT, 0);
        busy_wait_us(17);
    }
}

void ir_send_test_nec(uint8_t addr, uint8_t cmd)
{
    if (!s_on) ir_enable(true);
    uint32_t bits = addr | ((uint32_t)(uint8_t)~addr << 8) | ((uint32_t)cmd << 16) | ((uint32_t)(uint8_t)~cmd << 24);
    carrier(9000); busy_wait_us(4500);
    for (int i = 0; i < 32; i++) {
        carrier(560);
        busy_wait_us((bits >> i) & 1 ? 1690 : 560);
    }
    carrier(560);
}
