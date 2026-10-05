# DEF CON 32 badge — verified hardware facts

Every value below was read from source, not assumed. Primary source is
[neednotapply/DC32-cfw](https://github.com/neednotapply/DC32-cfw), which descends from
Dmitry Grinberg's original badge firmware (the firmware that shipped on the badge).

| Item | Value | Source |
|---|---|---|
| MCU | RP2350 (ARM, UF2 family `0xe48bff59`) | stock UF2 block headers |
| Crystal | 12 MHz (PLL fbdiv 62 / postdiv 6 → ~124 MHz) | `main_rp2350_defcon.c` clock init |
| Stock sys clock | `TICKS_PER_SECOND=125000000` | `CMakeLists.txt` |
| Flash used by stock image | `0x10000000`–`0x10400000` (game ROM at `0x10100000`, settings at `0x100C0000`) | `memMap.h`, stock UF2 range `0x10000000-0x10300000` |
| PSRAM | CS on GPIO0 (`XIP_SS_N_1`) — unused by our firmware | `gpiosConfig()` |

## LCD

| Item | Value |
|---|---|
| Interface | 1-bit SPI driven by **PIO0 SM0**, mode 0, MSB first |
| Pins | D/C = GPIO5, MOSI = GPIO6, SCK = GPIO8, CS = GPIO9, backlight = GPIO10 (PWM slice 5 A) |
| Controller | MIPI-DCS / ST7789-class command set (`0x01,0x11,0x3A 0x55,0x36 0x00,0x20,0x13,0x29,0x2C`) |
| Native geometry | **240 × 320 portrait** (`HARDWARE_WIDTH 240`, `HARDWARE_HEIGHT 320`), RGB565 |
| PIO bit clock | sys/1 PIO, 2 instructions/bit → 62.5 Mbit/s at 125 MHz → ~50 Hz full-panel scanout |
| Scanout model | RAM framebuffer streamed continuously by 2 chained DMA channels (data ch → ctrl ch reloads read addr); CS held low across frames |
| Landscape mapping | Stock UI is built with `UI_ROTATED`: logical (x,y) in 320×240 → `fb[x*240 + (239-y)]` (`dcAppDrawPrvDisplayIndex`) |
| Pixel word | native `uint16_t` RGB565; 16-bit DMA writes are replicated on the bus and the PIO shifts the top 16 bits out MSB-first |
| Backlight | PWM top 1022, duty = `bri² + 61`, `bri` 0..31 |

## Buttons (all active-low, internal pull-up, Schmitt)

| GPIO | Button |
|---|---|
| 16 | RIGHT |
| 17 | DOWN |
| 18 | UP |
| 19 | LEFT |
| 20 | B |
| 21 | A |
| 22 | START |
| 23 | SELECT |
| 24 | CENTER (the "FN" key in this project) |

BOOTSEL is **not** a GPIO: it is the ROM boot button on the back (documented as top-right when the
badge is face-down; on the badge tested here it was **bottom-left** with the back facing you, and another
back button is reset) — hold it while plugging in USB with the badge powered off.

## Power

GPIO11 `PIN_SELF_PWR` is a power latch: the firmware drives it **high** to stay on;
driving it low powers the badge off when on battery (`sleepDefcon.c`). Our firmware
latches it high as its first action.

## Other peripherals (left untouched)

microSD on SPI1 (GPIO12 MISO, 13 CS, 14 SCK, 15 MOSI): since fw 0.3 exposed to the PC as a
USB drive (read/write; `firmware/src/sdcard.c`, `msc_disk.c`). LIS3DH accelerometer on I²C1
(GPIO2 SDA, GPIO3 SCL, address 0x18): since fw 0.3 its click engine reports taps
(`firmware/src/accel.c`). WS2812 LEDs GPIO4, IR GPIO26/27/7, speaker PWM GPIO25, touch IRQ GPIO1.

## Stock firmware images (independent recovery path)

From [jaku/DEFCON-32-BadgeFirmware](https://github.com/jaku/DEFCON-32-BadgeFirmware)
(commit `229e351`, published with Dmitry Grinberg's permission):

| Version | File | SHA-256 |
|---|---|---|
| 1.31 (original DEF CON release) | stock-firmware.bin | `a838c4d36d49c69a64623221be0573c5d4c79224684af0742358326331630521` |
| 1.31 | stock-firmware.uf2 | `7767c837bf495893c810a6a2f4cb496ea1a9caa9bcc318de057f0db13b19251b` |
| 1.5 | stock-firmware.bin | `2ab9e9e820d8fa215c110696144262f62d37296db1eb52d425adbebb579823bd` |
| 1.5 | stock-firmware.uf2 | `41fe4e9f17e334aec536df1fb4da3e04f3b868d241c83342d87a4e1441abc37f` |
| 1.6 | stock-firmware.bin | `13b2308eff397e410b1c92e8e44288d9035a69d8a2c357fd64b7bb70a4f982b5` |
| 1.6 | stock-firmware.uf2 | `9a18f095437bcf4309c0b75daebf21e920014743c30e8c038224ba339060dc4b` |

Each `.bin` is 3 MiB covering `0x10000000–0x10300000`. The backup script compares the
raw dump against all three to identify which version the badge is running.
