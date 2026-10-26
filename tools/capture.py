#!/usr/bin/env python3
"""Trigger a dump on the Pico reader and save it as .hex and .bin (stdlib only).

usage: capture.py [/dev/ttyACM0] [output-prefix]
"""
import binascii
import hashlib
import os
import select
import sys
import termios
import time
import tty

port = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyACM0"
prefix = sys.argv[2] if len(sys.argv) > 2 else "upd78c14"

fd = os.open(port, os.O_RDWR | os.O_NOCTTY)
tty.setraw(fd)
termios.tcflush(fd, termios.TCIFLUSH)
os.write(fd, b"r")  # reset the chip and start a fresh dump

buf = b""
records, image, bad, top = [], bytearray(b"\xff" * 0x10000), 0, -1
deadline = time.time() + 60
finished = False
while time.time() < deadline and not finished:
    r, _, _ = select.select([fd], [], [], 1.0)
    if not r:
        continue
    buf += os.read(fd, 4096)
    while b"\n" in buf:
        raw, buf = buf.split(b"\n", 1)
        s = raw.decode("ascii", "replace").strip()
        if s.startswith("#"):
            print(s)
            finished |= s.startswith("# DONE")
            continue
        if not s.startswith(":"):
            continue
        try:
            rec = bytes.fromhex(s[1:])
        except ValueError:
            bad += 1
            continue
        if len(rec) < 5 or len(rec) != rec[0] + 5 or sum(rec) & 0xFF:
            bad += 1
            continue
        records.append(s)
        n, addr, typ = rec[0], (rec[1] << 8) | rec[2], rec[3]
        if typ == 0:
            image[addr:addr + n] = rec[4:4 + n]
            top = max(top, addr + n - 1)
            if (addr & 0x3FF) == 0:
                print(f"\r  {addr + n:5d} bytes", end="", flush=True)
os.close(fd)
print()

if not finished:
    sys.exit("timed out before the end-of-file record; check wiring / try again")

data = bytes(image[:top + 1])
with open(prefix + ".hex", "w") as f:
    f.write("\n".join(records) + "\n")
with open(prefix + ".bin", "wb") as f:
    f.write(data)
print(f"{prefix}.bin: {len(data)} bytes, bad records {bad}, "
      f"CRC32 {binascii.crc32(data):08X}, SHA1 {hashlib.sha1(data).hexdigest()}")
if bad:
    print("WARNING: bad records - dump again and compare before trusting this image")
print("Tip: dump 2-3 times and compare the SHA1s.")
