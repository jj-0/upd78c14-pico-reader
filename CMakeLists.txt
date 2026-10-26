cmake_minimum_required(VERSION 3.13)

# Pico 2 (RP2350) by default: its GPIOs tolerate the chip's 5 V outputs.
set(PICO_BOARD pico2 CACHE STRING "Board type")

include(pico_sdk_import.cmake)
project(upd78c14_pico_reader C CXX ASM)
set(CMAKE_C_STANDARD 11)
pico_sdk_init()

add_executable(upd78c14_reader main.c)
pico_generate_pio_header(upd78c14_reader ${CMAKE_CURRENT_LIST_DIR}/rom7810.pio)
target_include_directories(upd78c14_reader PRIVATE ${CMAKE_CURRENT_LIST_DIR})
target_link_libraries(upd78c14_reader pico_stdlib hardware_pio hardware_dma hardware_pwm hardware_uart)

pico_enable_stdio_usb(upd78c14_reader 1)
pico_enable_stdio_uart(upd78c14_reader 0)
pico_add_extra_outputs(upd78c14_reader)
