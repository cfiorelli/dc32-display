// USB composite: interface 0 = vendor bulk (display stream + events, WinUSB via MS OS 2.0),
// interfaces 1-2 = CDC ACM (human-readable debug log).
#include <string.h>
#include "tusb.h"
#include "pico/unique_id.h"
#include "dc32proto.h"
#include "version.h"

enum { ITF_VENDOR = 0, ITF_CDC, ITF_CDC_DATA, ITF_TOTAL };

#define EP_VENDOR_OUT 0x01
#define EP_VENDOR_IN  0x81
#define EP_CDC_OUT    0x02
#define EP_CDC_IN     0x82
#define EP_CDC_NOTIF  0x83

#define VENDOR_REQ_MS 0x20   // bMS_VendorCode for MS OS 2.0

static const tusb_desc_device_t desc_device = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = 0x0210,                         // 2.1 -> BOS (MS OS 2.0) is requested
    .bDeviceClass = TUSB_CLASS_MISC,
    .bDeviceSubClass = MISC_SUBCLASS_COMMON,
    .bDeviceProtocol = MISC_PROTOCOL_IAD,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor = DC32_USB_VID,
    .idProduct = DC32_USB_PID,
    .bcdDevice = DC32_BCD_DEVICE,             // bump to force Windows to re-read MS OS descriptors
    .iManufacturer = 1,
    .iProduct = 2,
    .iSerialNumber = 3,
    .bNumConfigurations = 1,
};

uint8_t const *tud_descriptor_device_cb(void) { return (uint8_t const *)&desc_device; }

#define CONFIG_TOTAL_LEN (TUD_CONFIG_DESC_LEN + TUD_VENDOR_DESC_LEN + TUD_CDC_DESC_LEN)

static const uint8_t desc_config[] = {
    TUD_CONFIG_DESCRIPTOR(1, ITF_TOTAL, 0, CONFIG_TOTAL_LEN, 0x00, 250),
    TUD_VENDOR_DESCRIPTOR(ITF_VENDOR, 4, EP_VENDOR_OUT, EP_VENDOR_IN, 64),
    TUD_CDC_DESCRIPTOR(ITF_CDC, 5, EP_CDC_NOTIF, 8, EP_CDC_OUT, EP_CDC_IN, 64),
};

uint8_t const *tud_descriptor_configuration_cb(uint8_t index) { (void)index; return desc_config; }

// ---- BOS + Microsoft OS 2.0: bind WinUSB to interface 0 without any .inf ----
#define BOS_TOTAL_LEN (TUD_BOS_DESC_LEN + TUD_BOS_MICROSOFT_OS_DESC_LEN)
#define MS_OS_20_DESC_LEN 0xB2

static const uint8_t desc_bos[] = {
    TUD_BOS_DESCRIPTOR(BOS_TOTAL_LEN, 1),
    TUD_BOS_MS_OS_20_DESCRIPTOR(MS_OS_20_DESC_LEN, VENDOR_REQ_MS),
};

uint8_t const *tud_descriptor_bos_cb(void) { return desc_bos; }

static const uint8_t desc_ms_os_20[] = {
    U16_TO_U8S_LE(0x000A), U16_TO_U8S_LE(MS_OS_20_SET_HEADER_DESCRIPTOR), U32_TO_U8S_LE(0x06030000), U16_TO_U8S_LE(MS_OS_20_DESC_LEN),
    U16_TO_U8S_LE(0x0008), U16_TO_U8S_LE(MS_OS_20_SUBSET_HEADER_CONFIGURATION), 0, 0, U16_TO_U8S_LE(MS_OS_20_DESC_LEN - 0x0A),
    U16_TO_U8S_LE(0x0008), U16_TO_U8S_LE(MS_OS_20_SUBSET_HEADER_FUNCTION), ITF_VENDOR, 0, U16_TO_U8S_LE(MS_OS_20_DESC_LEN - 0x0A - 0x08),
    U16_TO_U8S_LE(0x0014), U16_TO_U8S_LE(MS_OS_20_FEATURE_COMPATBLE_ID), 'W', 'I', 'N', 'U', 'S', 'B', 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0,
    U16_TO_U8S_LE(MS_OS_20_DESC_LEN - 0x0A - 0x08 - 0x08 - 0x14), U16_TO_U8S_LE(MS_OS_20_FEATURE_REG_PROPERTY),
    U16_TO_U8S_LE(0x0007), U16_TO_U8S_LE(0x002A),
    'D', 0, 'e', 0, 'v', 0, 'i', 0, 'c', 0, 'e', 0, 'I', 0, 'n', 0, 't', 0, 'e', 0,
    'r', 0, 'f', 0, 'a', 0, 'c', 0, 'e', 0, 'G', 0, 'U', 0, 'I', 0, 'D', 0, 's', 0, 0, 0,
    U16_TO_U8S_LE(0x0050),
    // {A7894049-A47C-45F8-A23B-8FD1F5B31F59}
    '{', 0, 'A', 0, '7', 0, '8', 0, '9', 0, '4', 0, '0', 0, '4', 0, '9', 0, '-', 0,
    'A', 0, '4', 0, '7', 0, 'C', 0, '-', 0, '4', 0, '5', 0, 'F', 0, '8', 0, '-', 0,
    'A', 0, '2', 0, '3', 0, 'B', 0, '-', 0, '8', 0, 'F', 0, 'D', 0, '1', 0, 'F', 0,
    '5', 0, 'B', 0, '3', 0, '1', 0, 'F', 0, '5', 0, '9', 0, '}', 0, 0, 0, 0, 0,
};
TU_VERIFY_STATIC(sizeof(desc_ms_os_20) == MS_OS_20_DESC_LEN, "MS OS 2.0 descriptor size");

// implemented in main.c
extern void app_reset_stream(void);

bool tud_vendor_control_xfer_cb(uint8_t rhport, uint8_t stage, tusb_control_request_t const *req)
{
    if (req->bmRequestType_bit.type != TUSB_REQ_TYPE_VENDOR) return false;
    if (req->bRequest == VENDOR_REQ_MS) {
        if (stage != CONTROL_STAGE_SETUP) return true;
        if (req->wIndex != 7) return false;
        return tud_control_xfer(rhport, req, (void *)(uintptr_t)desc_ms_os_20, MS_OS_20_DESC_LEN);
    }
    if (req->bRequest == DC32_CTRL_RESET_STREAM) {
        if (stage == CONTROL_STAGE_SETUP) {
            app_reset_stream();
            return tud_control_status(rhport, req);
        }
        return true;
    }
    return false;
}

// ---- strings ----
static const char *const s_strings[] = {
    NULL, "DEF CON 32 badge (dc32-display)", DC32_USB_PRODUCT, NULL, "DC32 Display Stream", "DC32 Display Debug Log",
};

static uint16_t s_desc_str[48];

uint16_t const *tud_descriptor_string_cb(uint8_t index, uint16_t langid)
{
    (void)langid;
    size_t n;
    if (index == 0) {
        s_desc_str[1] = 0x0409;
        n = 1;
    } else if (index == 3) {
        char serial[2 * PICO_UNIQUE_BOARD_ID_SIZE_BYTES + 1];
        pico_get_unique_board_id_string(serial, sizeof serial);
        n = strlen(serial);
        for (size_t i = 0; i < n; i++) s_desc_str[1 + i] = (uint8_t)serial[i];
    } else {
        if (index >= sizeof(s_strings) / sizeof(s_strings[0])) return NULL;
        const char *s = s_strings[index];
        n = strlen(s);
        if (n > 46) n = 46;
        for (size_t i = 0; i < n; i++) s_desc_str[1 + i] = (uint8_t)s[i];
    }
    s_desc_str[0] = (uint16_t)((TUSB_DESC_STRING << 8) | (2 * n + 2));
    return s_desc_str;
}
