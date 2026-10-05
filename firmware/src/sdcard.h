// microSD in SPI mode on SPI1 (GPIO12 MISO, 13 CS, 14 SCK, 15 MOSI). 512-byte blocks.
#pragma once
#include <stdbool.h>
#include <stdint.h>

bool sd_init(void);                 // (re)initialise the card; false = no card / not responding
bool sd_ready(void);
uint32_t sd_block_count(void);      // capacity in 512-byte blocks (0 if not ready)
bool sd_read(uint32_t lba, uint8_t *buf, uint32_t count);
bool sd_write(uint32_t lba, const uint8_t *buf, uint32_t count);
void sd_mark_failed(void);          // force a re-init on next use
extern uint8_t sd_last_cmd, sd_last_r1, sd_last_stage;
extern bool sd_info_block_addr;      // card takes block numbers (SDHC/SDXC)
extern uint8_t sd_info_csd;
extern uint8_t sd_info_wp;           // CSD write protect: bit 1 permanent, bit 0 temporary          // CSD structure: 0 = v1 (SDSC), 1 = v2   // stage 1 = command rejected, 2 = data
