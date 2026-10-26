# Licensing per file

The MIT license in `LICENSE` covers this project's own code and documentation
(main.c, rom7810.pio, CMakeLists.txt, tools/, README.md, WIRING.md, wiring.*).
It does NOT cover:
  - upstream/reader.s, upstream/upd7811-reader.bin and reader_rom.h:
    devinacker/upd7811-reader firmware, public domain;
  - upstream/upd7811-reader.pdf: devinacker/upd7811-reader hardware, CC BY-SA 4.0;
  - hw/: adapted from that hardware design, CC BY-SA 4.0
    (https://creativecommons.org/licenses/by-sa/4.0/);
  - pico_sdk_import.cmake: Raspberry Pi pico-sdk, BSD-3-Clause.
