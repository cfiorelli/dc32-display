// Native test harness: the firmware's decoder.c compiled for the host and driven from Python.
#include <string.h>
#include "decoder.h"

static uint16_t fb[DC32_SCREEN_W * DC32_SCREEN_H];
static decoder_t dec;
static int n_msgs, n_errs, last_type, last_err;
#define RING 256
static uint8_t ring_t[RING]; static uint8_t ring_p[RING][64]; static uint32_t ring_l[RING]; static int ring_n;
static void on_msg(decoder_t *d, uint8_t t, const uint8_t *p, uint32_t l) {
    (void)d; n_msgs++; last_type = t;
    if (ring_n < RING) { ring_t[ring_n] = t; ring_l[ring_n] = l; memcpy(ring_p[ring_n], p, l < 64 ? l : 64); ring_n++; }
}
int h_ring_count(void) { return ring_n; }
int h_ring_type(int i) { return ring_t[i]; }
uint32_t h_ring_len(int i) { return ring_l[i]; }
const uint8_t *h_ring_payload(int i) { return ring_p[i]; }
void h_ring_clear(void) { ring_n = 0; }
int h_drop_get(void) { return dec.drop_pixels; }
static void on_err(decoder_t *d, uint8_t c, uint32_t x) { (void)d; (void)x; n_errs++; last_err = c; }

void h_init(void) { ring_n = 0; memset(fb, 0, sizeof fb); dec_init(&dec, fb); dec.on_msg = on_msg; dec.on_err = on_err; n_msgs = n_errs = 0; }
void h_feed(const uint8_t *b, size_t n) { dec_feed(&dec, b, n); }
void h_reset(void) { dec_reset_hunt(&dec); }
void h_drop(int on) { dec.drop_pixels = on != 0; }
const uint16_t *h_fb(void) { return fb; }
int h_msgs(void) { return n_msgs; }
int h_errs(void) { return n_errs; }
int h_last_type(void) { return last_type; }
int h_last_err(void) { return last_err; }
int h_state(void) { return dec.st; }
