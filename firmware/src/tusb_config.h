#pragma once

#define CFG_TUSB_RHPORT0_MODE   OPT_MODE_DEVICE
#ifndef CFG_TUSB_OS
#define CFG_TUSB_OS             OPT_OS_PICO
#endif
#define CFG_TUD_ENABLED         1
#define CFG_TUD_ENDPOINT0_SIZE  64

#define CFG_TUD_CDC             1
#define CFG_TUD_MSC             0
#define CFG_TUD_HID             0
#define CFG_TUD_MIDI            0
#define CFG_TUD_VENDOR          1

// Large vendor RX FIFO so the decoder can drain in big chunks while USB keeps streaming.
#define CFG_TUD_VENDOR_EPSIZE        64
#define CFG_TUD_VENDOR_RX_BUFSIZE    8192
#define CFG_TUD_VENDOR_TX_BUFSIZE    1024

#define CFG_TUD_CDC_RX_BUFSIZE  64
#define CFG_TUD_CDC_TX_BUFSIZE  2048
#define CFG_TUD_CDC_EP_BUFSIZE  64
