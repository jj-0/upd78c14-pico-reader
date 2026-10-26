// uPD78C14 (and uPD7811/78C11/78C12/78C18) mask-ROM reader on a Raspberry Pi Pico 2.
//
// Port of devinacker/upd7811-reader: the Pico replaces the 27C256 EPROM,
// the 74HCT373 address latch, the crystal and the USB-serial adapter.
//
//  - PIO + DMA serve the upstream reader firmware (reader_rom.h) as the
//    chip's external ROM, mirrored at 0000h and 8000h (A15 ignored).
//  - PWM generates the chip clock (6.25 MHz; datasheet allows 4..15 MHz).
//  - UART1 receives the Intel HEX dump the chip sends from PC0/TxD
//    (baud = f_clk / 96, fixed by the reader firmware) and forwards it to USB.
//
// The trick (from upstream): the chip boots with MODE0 = MODE1 = 1 (64 KB
// external), jumps to the 8000h mirror, then pulls its own PC7 low, which is
// wired to MODE0. That switches the internal mask ROM in at 0000h while the
// code keeps running from external memory at 8000h+.
//
// USB commands: 'r' = reset the chip and dump again.

#include <stdio.h>
#include <string.h>
#include "pico/stdlib.h"
#include "hardware/pio.h"
#include "hardware/dma.h"
#include "hardware/pwm.h"
#include "hardware/uart.h"
#include "hardware/clocks.h"
#include "hardware/structs/bus_ctrl.h"
#include "rom7810.pio.h"
#include "reader_rom.h"

#define PIN_AD0        0    // GP0..7  = PD0..7, GP8..14 = PF0..6
#define PIN_ALE        rom7810_PIN_ALE
#define PIN_RD         rom7810_PIN_RD
#define PIN_RUN        19   // high = chip runs (drives RESET via 2 x 74HCT04)
#define PIN_CLK        20   // PWM slice 2A -> 74HCT04 -> X2 / X1
#define PIN_TXD_IN     21   // UART1 RX <- chip PC0/TxD
#define CHIP_CLK_HZ    6250000u

static uint8_t rom[32768] __attribute__((aligned(32768)));
static uint32_t chip_clk_hz;

// ---------------------------------------------------------------------------
// Intel HEX checker (the raw text is passed through to USB untouched)
// ---------------------------------------------------------------------------
static char line[80];
static int line_len;
static uint8_t image[0x10000];
static uint32_t bytes_ok, bad_records, max_addr;
static bool done;

static int hexval(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return -1;
}

static int hexbyte(const char *p) {
    int hi = hexval(p[0]), lo = hexval(p[1]);
    return (hi < 0 || lo < 0) ? -1 : (hi << 4) | lo;
}

static uint32_t crc32(const uint8_t *p, uint32_t n) {
    uint32_t c = 0xffffffffu;
    while (n--) {
        c ^= *p++;
        for (int k = 0; k < 8; k++) c = (c >> 1) ^ (0xedb88320u & -(c & 1));
    }
    return ~c;
}

static void hex_line(void) {
    if (line_len < 11 || line[0] != ':') return;
    int count = hexbyte(line + 1);
    if (count < 0 || line_len < 11 + 2 * count) { bad_records++; return; }
    uint8_t sum = 0;
    for (int i = 0; i < 5 + count; i++) {
        int b = hexbyte(line + 1 + 2 * i);
        if (b < 0) { bad_records++; return; }
        sum += b;
    }
    if (sum != 0) { bad_records++; return; }
    int addr = (hexbyte(line + 3) << 8) | hexbyte(line + 5);
    int type = hexbyte(line + 7);
    if (type == 0) {
        for (int i = 0; i < count; i++) image[(addr + i) & 0xffff] = hexbyte(line + 9 + 2 * i);
        bytes_ok += count;
        if (addr + count - 1 > (int)max_addr) max_addr = addr + count - 1;
    } else if (type == 1) {
        uint32_t size = bytes_ok ? max_addr + 1 : 0;
        printf("\n# DONE: %lu bytes (0000-%04lX), bad records: %lu, CRC32 %08lX\n",
               (unsigned long)bytes_ok, (unsigned long)(size ? size - 1 : 0),
               (unsigned long)bad_records, (unsigned long)crc32(image, size));
        if (size != 0x4000)
            printf("# note: a uPD78C14 should give 16384 bytes (4K = 78C11, 8K = 78C12, 32K = 78C18)\n");
        done = true;
    }
}

static void hex_feed(char c) {
    if (c == '\r') return;
    if (c == '\n') { line[line_len] = 0; hex_line(); line_len = 0; return; }
    if (line_len < (int)sizeof line - 1) line[line_len++] = c;
}

// ---------------------------------------------------------------------------
// Hardware
// ---------------------------------------------------------------------------
static void clock_init(void) {
    uint32_t sys = clock_get_hz(clk_sys);
    uint32_t div = (sys + CHIP_CLK_HZ / 2) / CHIP_CLK_HZ;   // 150 MHz / 24, 125 MHz / 20
    chip_clk_hz = sys / div;
    uint slice = pwm_gpio_to_slice_num(PIN_CLK);
    gpio_set_function(PIN_CLK, GPIO_FUNC_PWM);
    pwm_config c = pwm_get_default_config();
    pwm_config_set_clkdiv_int(&c, 1);
    pwm_config_set_wrap(&c, div - 1);
    pwm_init(slice, &c, false);
    pwm_set_gpio_level(PIN_CLK, div / 2);
    pwm_set_enabled(slice, true);
}

static void rom_emu_init(void) {
    memcpy(rom, reader_rom, sizeof rom);

    PIO pio = pio0;
    uint sm = 0;
    uint off = pio_add_program(pio, &rom7810_program);
    pio_sm_config c = rom7810_program_get_default_config(off);
    sm_config_set_in_pins(&c, PIN_AD0);
    sm_config_set_in_pin_count(&c, 32);     // "mov osr, pins" must see GP16 (ALE)
    sm_config_set_out_pins(&c, PIN_AD0, 8);
    sm_config_set_jmp_pin(&c, PIN_RD);
    sm_config_set_in_shift(&c, true, false, 32);
    sm_config_set_out_shift(&c, true, false, 32);
    sm_config_set_clkdiv(&c, 1.0f);

    for (uint i = 0; i < 8; i++) {
        pio_gpio_init(pio, PIN_AD0 + i);
        gpio_disable_pulls(PIN_AD0 + i);
    }
    const uint inputs[] = {8, 9, 10, 11, 12, 13, 14, PIN_ALE, PIN_RD};
    for (uint i = 0; i < count_of(inputs); i++) {
        gpio_init(inputs[i]);
        gpio_set_dir(inputs[i], GPIO_IN);
        gpio_disable_pulls(inputs[i]);
    }
    pio_sm_set_consecutive_pindirs(pio, sm, PIN_AD0, 8, false);
    pio_sm_init(pio, sm, off, &c);
    pio_sm_put(pio, sm, (uint32_t)rom >> 15);

    // ch_addr: PIO RX FIFO -> ch_data READ_ADDR (and trigger)
    // ch_data: one byte from the ROM image -> PIO TX FIFO, then re-arm ch_addr
    int ch_addr = dma_claim_unused_channel(true);
    int ch_data = dma_claim_unused_channel(true);

    dma_channel_config ca = dma_channel_get_default_config(ch_addr);
    channel_config_set_transfer_data_size(&ca, DMA_SIZE_32);
    channel_config_set_read_increment(&ca, false);
    channel_config_set_write_increment(&ca, false);
    channel_config_set_dreq(&ca, pio_get_dreq(pio, sm, false));
    dma_channel_configure(ch_addr, &ca, &dma_hw->ch[ch_data].al3_read_addr_trig,
                          &pio->rxf[sm], 1, false);

    dma_channel_config cd = dma_channel_get_default_config(ch_data);
    channel_config_set_transfer_data_size(&cd, DMA_SIZE_8);
    channel_config_set_read_increment(&cd, false);
    channel_config_set_write_increment(&cd, false);
    channel_config_set_dreq(&cd, pio_get_dreq(pio, sm, true));
    channel_config_set_chain_to(&cd, ch_addr);
    dma_channel_configure(ch_data, &cd, &pio->txf[sm], rom, 1, false);

    bus_ctrl_hw->priority = BUSCTRL_BUS_PRIORITY_DMA_W_BITS | BUSCTRL_BUS_PRIORITY_DMA_R_BITS;
    dma_channel_start(ch_addr);
    pio_sm_set_enabled(pio, sm, true);
}

static void chip_run(bool run) { gpio_put(PIN_RUN, run); }

static void start_dump(void) {
    chip_run(false);
    sleep_ms(20);                       // > a few hundred clocks of reset
    while (uart_is_readable(uart1)) uart_getc(uart1);
    line_len = 0;
    bytes_ok = bad_records = max_addr = 0;
    memset(image, 0xff, sizeof image);
    done = false;
    printf("# uPD78C1x reader: chip clock %lu Hz, UART %lu baud. Releasing RESET...\n",
           (unsigned long)chip_clk_hz, (unsigned long)(chip_clk_hz / 96));
    chip_run(true);
}

int main(void) {
    // Hold the chip in reset first (external 4.7k pull-down does this before boot, too).
    gpio_init(PIN_RUN);
    gpio_set_dir(PIN_RUN, GPIO_OUT);
    chip_run(false);

    stdio_init_all();
    gpio_init(PICO_DEFAULT_LED_PIN);
    gpio_set_dir(PICO_DEFAULT_LED_PIN, GPIO_OUT);

    clock_init();
    rom_emu_init();

    uart_init(uart1, chip_clk_hz / 96);
    uart_set_format(uart1, 8, 1, UART_PARITY_NONE);
    gpio_set_function(PIN_TXD_IN, GPIO_FUNC_UART);

    while (!stdio_usb_connected()) {    // don't lose the start of the dump
        gpio_xor_mask(1u << PICO_DEFAULT_LED_PIN);
        sleep_ms(250);
    }
    sleep_ms(200);
    start_dump();

    uint32_t activity = 0;
    for (;;) {
        int c = getchar_timeout_us(0);
        if (c == 'r' || c == 'R') start_dump();

        while (uart_is_readable(uart1)) {
            char ch = uart_getc(uart1);
            putchar_raw(ch);
            hex_feed(ch);
            if ((++activity & 0x3ff) == 0) gpio_xor_mask(1u << PICO_DEFAULT_LED_PIN);
        }
        if (done) gpio_put(PICO_DEFAULT_LED_PIN, 1);
    }
}
