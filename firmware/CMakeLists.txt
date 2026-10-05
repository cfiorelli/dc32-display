cmake_minimum_required(VERSION 3.13)

set(PICO_BOARD pico2 CACHE STRING "")            # RP2350A, ARM; pins come from src/board_dc32.h
set(PICO_PLATFORM rp2350-arm-s CACHE STRING "")
include(pico_sdk_import.cmake)

project(dc32_display C CXX ASM)
set(CMAKE_C_STANDARD 11)

set(DC32_FW_MAJOR 0)
set(DC32_FW_MINOR 2)
set(DC32_FW_PATCH 1)
set(DC32_FW_VERSION "${DC32_FW_MAJOR}.${DC32_FW_MINOR}.${DC32_FW_PATCH}")
math(EXPR DC32_BCD_DEVICE "${DC32_FW_MAJOR} * 256 + ${DC32_FW_MINOR} * 16 + ${DC32_FW_PATCH}")
execute_process(COMMAND git rev-parse --short=7 HEAD WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
                OUTPUT_VARIABLE DC32_BUILD_ID OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
if(NOT DC32_BUILD_ID)
  set(DC32_BUILD_ID "nogit")
endif()
configure_file(src/version.h.in ${CMAKE_CURRENT_BINARY_DIR}/version.h @ONLY)

pico_sdk_init()

add_executable(dc32_display
  src/main.c src/lcd.c src/buttons.c src/decoder.c src/ui.c src/usb_descriptors.c src/leds.c)
pico_generate_pio_header(dc32_display ${CMAKE_CURRENT_SOURCE_DIR}/src/lcd_spi.pio)
pico_generate_pio_header(dc32_display ${CMAKE_CURRENT_SOURCE_DIR}/src/ws2812.pio)
target_include_directories(dc32_display PRIVATE src ../protocol ${CMAKE_CURRENT_BINARY_DIR})
target_compile_options(dc32_display PRIVATE -Wall -Wextra -Werror -Wno-unused-parameter -O2)
target_compile_definitions(dc32_display PRIVATE
  PICO_FLASH_SIZE_BYTES=4194304
  BOARD_TUD_RHPORT=0)
target_link_libraries(dc32_display PRIVATE
  pico_stdlib pico_unique_id pico_bootrom tinyusb_device
  hardware_pio hardware_dma hardware_pwm hardware_watchdog hardware_clocks)
pico_set_program_name(dc32_display "dc32-display")
pico_set_program_version(dc32_display "${DC32_FW_VERSION}")
pico_enable_stdio_usb(dc32_display 0)
pico_enable_stdio_uart(dc32_display 0)
pico_add_extra_outputs(dc32_display)
