#!/usr/bin/env python3
"""Generate wiring.svg and WIRING.md for the Pico 2 <-> uPD78C14GF reader.

One table drives both outputs. Chip pin numbers are for the 64-pin QFP
(GF-3BE, 14 x 20 mm) from the NEC uPD78C10A/11A/12A datasheet, which uses the
same pinout as the 78C14. Pico pin numbers are the physical header pins.
"""
import os
import sys

out_dir = sys.argv[1] if len(sys.argv) > 1 else "."

# (Pico GPIO, Pico header pin, chip signal, chip QFP pin, note)
DIRECT = (
    [(f"GP{i}", p, f"PD{i} / AD{i}", 49 + i, "address/data bus")
     for i, p in enumerate([1, 2, 4, 5, 6, 7, 9, 10])]
    + [(f"GP{8 + i}", p, f"PF{i} / A{8 + i}", 41 + i, "address bus")
       for i, p in enumerate([11, 12, 14, 15, 16, 17, 19])]
    + [("GP16", 21, "ALE", 40, "address latch enable"),
       ("GP17", 22, "/RD", 38, "read strobe"),
       ("GP21", 27, "PC0 / TxD", 11, "serial dump out (UART1 RX)")]
)
# signals that pass through the 74HCT04 (chip needs >= 4 V on X1 and /RESET)
BUFFERED = [
    ("GP20", 26, "X2", 24, "clock, inverted (HCT04 gate 1)"),
    ("GP20", 26, "X1", 25, "clock 6.25 MHz (HCT04 gates 1+2)"),
    ("GP19", 25, "/RESET", 22, "high = run (HCT04 gates 3+4)"),
]
PLUS5 = [("VDD", 58), ("AVDD", 37), ("/STOP", 57), ("/NMI", 19)]
GND = [("VSS", 26), ("AVSS", 27), ("INT1", 20), ("VAREF", 36)] + [(f"AN{i}", 28 + i) for i in range(8)]
PULLUPS = ([(f"PA{i}", 59 + i if i < 6 else i - 5) for i in range(8)]
           + [(f"PB{i}", 3 + i) for i in range(8)]
           + [(f"PC{i}", 11 + i) for i in range(1, 6)])
MODE_LINKS = [("MODE0", 23, "PC7", 18), ("MODE1", 21, "PC6", 17)]
OPEN = [("/WR", 39), ("PF7 / A15", 48)]

# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------
W, H = 1500, 1360
FG, MUTED, BUS, CLK, PWR, GNDC, ACC = "#1b1f24", "#5b6470", "#1f6feb", "#b35900", "#c62828", "#2e7d32", "#7b1fa2"
svg = []
a = svg.append


def text(x, y, s, size=14, anchor="start", color=FG, weight="normal", family="DejaVu Sans, Arial, sans-serif"):
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    a(f'<text x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}" fill="{color}" '
      f'font-weight="{weight}" font-family="{family}">{s}</text>')


def line(x1, y1, x2, y2, color=FG, w=1.6, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    a(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{w}"{d}/>')


def rect(x, y, w, h, fill="#ffffff", stroke=FG, rx=6, sw=2):
    a(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')


def dot(x, y, color=FG):
    a(f'<circle cx="{x}" cy="{y}" r="3.5" fill="{color}"/>')


def inverter(x, y, label, below=False):
    # triangle + bubble, input at x, output at x+46
    a(f'<path d="M{x} {y - 13} L{x} {y + 13} L{x + 34} {y} Z" fill="#fff" stroke="{FG}" stroke-width="1.8"/>')
    a(f'<circle cx="{x + 39}" cy="{y}" r="5" fill="#fff" stroke="{FG}" stroke-width="1.8"/>')
    text(x + 12, y + 28 if below else y - 17, label, 11, "middle", MUTED)


def resistor(x, y, label, vertical=True):
    if vertical:
        rect(x - 6, y, 12, 30, "#fff", FG, 2, 1.6)
        text(x + 12, y + 20, label, 11, "start", MUTED)
    else:
        rect(x, y - 6, 30, 12, "#fff", FG, 2, 1.6)
        text(x + 15, y - 10, label, 11, "middle", MUTED)


a(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">')
a(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')
text(40, 42, "µPD78C14GF mask-ROM reader — Raspberry Pi Pico 2 wiring", 24, weight="bold")
text(40, 66, "Port of devinacker/upd7811-reader. Pico 2 (RP2350) replaces the 27C256, 74HCT373, crystal and USB-UART. "
     "Chip pin numbers = 64-pin QFP (GF-3BE).", 13, color=MUTED)

PX, PW = 40, 230          # Pico box
CX, CW = 1150, 300        # chip box
TOP, STEP = 140, 30
rows = DIRECT + BUFFERED
n = len(rows)
rect(PX, TOP - 30, PW, STEP * n + 40, "#f3f8f3", GNDC)
text(PX + PW / 2, TOP - 40, "Raspberry Pi Pico 2", 16, "middle", weight="bold")
rect(CX, TOP - 30, CW, STEP * n + 40, "#f4f1fa", ACC)
text(CX + CW / 2, TOP - 40, "NEC µPD78C14GF (QFP-64)", 16, "middle", weight="bold")

for i, (gp, pp, sig, cp, note) in enumerate(rows):
    y = TOP + i * STEP
    color = BUS if i < 15 else (ACC if i < len(DIRECT) else CLK)
    if sig != "X2":  # X2 and X1 share GP20; label it once
        text(PX + 14, y + 5, f"pin {pp}", 12, color=MUTED)
        text(PX + PW - 12, y + 5, gp, 14, "end", weight="bold")
    text(CX + 12, y + 5, f"{cp}", 13, color=MUTED, weight="bold")
    text(CX + 46, y + 5, sig, 14, weight="bold")
    text(CX + CW - 10, y + 5, note, 10, "end", MUTED)
    if i < len(DIRECT):
        line(PX + PW, y, CX, y, color)
        resistor(PX + PW + 70, y, "", vertical=False)

# series-resistor caption
text(PX + PW + 85, TOP - 14, "470 Ω series (optional, recommended)", 11, "middle", MUTED)

# --- 74HCT04 section --------------------------------------------------------
yx2 = TOP + len(DIRECT) * STEP          # X2 row
yx1 = yx2 + STEP                        # X1 row
yrs = yx1 + STEP                        # /RESET row
gx = 620
rect(gx - 40, yx2 - 34, 300, yrs - yx2 + 74, "#fff8ef", CLK, 8, 1.6)
text(gx - 30, yrs + 58, "U2  74HCT04  (VCC pin 14 = +5 V, GND pin 7; tie unused inputs 11, 13 to GND)", 11, "start", CLK)

# clock: GP20 -> gate1 (1A pin1 -> 1Y pin2) -> X2 ; 1Y -> gate2 (2A pin3 -> 2Y pin4) -> X1
line(PX + PW, yx1, gx - 60, yx1, CLK)
line(gx - 60, yx1, gx - 60, yx2, CLK)
line(gx - 60, yx2, gx, yx2, CLK)
inverter(gx, yx2, "1A(1)→1Y(2)")
line(gx + 44, yx2, CX, yx2, CLK)
dot(gx + 80, yx2, CLK)
line(gx + 80, yx2, gx + 80, yx1, CLK)
line(gx + 80, yx1, gx + 120, yx1, CLK)
inverter(gx + 120, yx1, "2A(3)→2Y(4)")
line(gx + 164, yx1, CX, yx1, CLK)
text(PX + PW + 8, yx1 - 6, "PWM 6.25 MHz", 11, color=CLK)

# reset: GP19 -> gate3 (5->6) -> gate4 (9? no: 4A pin 9 -> 4Y pin 8) -> /RESET
line(PX + PW, yrs, gx, yrs, CLK)
inverter(gx, yrs, "3A(5)→3Y(6)", below=True)
line(gx + 44, yrs, gx + 120, yrs, CLK)
inverter(gx + 120, yrs, "4A(9)→4Y(8)", below=True)
line(gx + 164, yrs, CX, yrs, CLK)
dot(PX + PW + 120, yrs, CLK)
line(PX + PW + 120, yrs, PX + PW + 120, yrs + 24, CLK)
resistor(PX + PW + 120, yrs + 24, "4.7 kΩ to GND")
line(PX + PW + 120, yrs + 54, PX + PW + 120, yrs + 66, GNDC)
line(PX + PW + 110, yrs + 66, PX + PW + 130, yrs + 66, GNDC, 2.4)

# --- power & static section -------------------------------------------------
y0 = yrs + 110
text(40, y0, "Power and static connections on the µPD78C14", 18, weight="bold")
# Pico power pins
rect(40, y0 + 20, 230, 74, "#f3f8f3", GNDC)
text(54, y0 + 44, "pin 40  VBUS (+5 V)", 12, color=PWR, weight="bold")
text(54, y0 + 66, "pin 38  GND", 12, color=GNDC, weight="bold")
text(54, y0 + 86, "Pico 2 — power it from USB", 10, color=MUTED)

rx0, rx1 = 300, 1450
y5 = y0 + 50
yg = y0 + 82
line(270, y0 + 40, rx0, y0 + 40, PWR, 2.4); line(rx0, y0 + 40, rx0, y5, PWR, 2.4)
line(rx0, y5, rx1, y5, PWR, 3)
text(rx1, y5 - 8, "+5 V", 13, "end", PWR, "bold")
line(270, y0 + 62, rx0 - 10, y0 + 62, GNDC, 2.4); line(rx0 - 10, y0 + 62, rx0 - 10, yg, GNDC, 2.4)
line(rx0 - 10, yg, rx1, yg, GNDC, 3)
text(rx1, yg + 18, "GND", 13, "end", GNDC, "bold")

# items hanging off the rails
x = 330
for name, pin in PLUS5:
    dot(x, y5, PWR); line(x, y5, x, y5 - 26, PWR); text(x, y5 - 30, f"{name} {pin}", 11, "middle", PWR, "bold")
    x += 64
for name, pin in GND:
    dot(x, yg, GNDC); line(x, yg, x, yg + 26, GNDC); text(x, yg + 40, f"{name}", 10, "middle", GNDC, "bold")
    text(x, yg + 52, f"{pin}", 10, "middle", GNDC)
    x += 46
# decoupling caps
cx = x + 20
for lbl in ["VDD 58", "AVDD 37", "74HCT04"]:
    line(cx, y5, cx, y5 + 10, FG); line(cx - 10, y5 + 10, cx + 10, y5 + 10, FG, 2.4)
    line(cx - 10, y5 + 16, cx + 10, y5 + 16, FG, 2.4); line(cx, y5 + 16, cx, yg, FG)
    dot(cx, y5); dot(cx, yg)
    text(cx, yg + 40, "100 nF", 10, "middle", MUTED)
    text(cx, yg + 52, lbl, 10, "middle", MUTED)
    cx += 95

# MODE links
ym = yg + 100
text(40, ym, "MODE links (this is the whole trick)", 16, weight="bold")
for k, (m, mp, pc, pcp) in enumerate(MODE_LINKS):
    yy = ym + 34 + k * 56
    rect(40, yy - 18, 340, 36, "#fff", ACC, 4, 1.4)
    text(52, yy + 5, f"{m} (pin {mp})  ⟷  {pc} (pin {pcp})", 14, color=ACC, weight="bold")
    line(380, yy, 420, yy, ACC)
    resistor(420, yy, "22 kΩ", vertical=False)
    line(450, yy, 480, yy, PWR)
    text(486, yy + 5, "+5 V", 12, color=PWR, weight="bold")
text(560, ym + 30, "Both MODE pins are pulled HIGH at reset → 64 KB external-ROM boot from the Pico.", 12)
text(560, ym + 50, "The reader code jumps to its 8000h mirror, then drives PC7 low → MODE0 = 0", 12)
text(560, ym + 70, "→ internal mask ROM appears at 0000h–3FFFh while code keeps running at 8000h+.", 12)
text(560, ym + 94, "Never wire the MODE pins straight to 5 V: they are also outputs (M1, IO/M) — use the pull-ups.", 12, color=PWR)

# pull-ups & open pins
yp = ym + 170
text(40, yp, "22 kΩ pull-up to +5 V on every unused input (or a 22 kΩ / 10 kΩ resistor network):", 14, weight="bold")
names = ", ".join(f"{nm}({p})" for nm, p in PULLUPS)
text(40, yp + 24, names, 12, color=MUTED)
text(40, yp + 50, "Leave open: " + ", ".join(f"{nm} (pin {p})" for nm, p in OPEN) +
     ".   Optional: LED + 1 kΩ from +5 V to PC5 (pin 16) — the reader blinks it while dumping, solid when done.", 12)

# QFP pin-1 inset
qx, qy = 1180, yp - 120
rect(qx, qy, 150, 200, "#fafafa", FG, 4, 1.4)
a(f'<circle cx="{qx + 20}" cy="{qy + 20}" r="6" fill="none" stroke="{FG}" stroke-width="1.4"/>')
text(qx + 75, qy + 100, "top view", 11, "middle", MUTED)
text(qx - 8, qy + 24, "1", 12, "end", weight="bold")
text(qx - 8, qy + 196, "19", 12, "end", weight="bold")
text(qx + 4, qy + 216, "20", 11)
text(qx + 146, qy + 216, "32", 11, "end")
text(qx + 158, qy + 196, "33", 12, weight="bold")
text(qx + 158, qy + 24, "51", 12, weight="bold")
text(qx + 146, qy - 6, "52", 11, "end")
text(qx + 4, qy - 6, "64", 11)
text(qx + 75, qy + 238, "QFP-64 14×20: 19 pins on long sides, 13 on short", 10, "middle", MUTED)
text(qx + 75, qy + 252, "verify against VDD 58 / VSS 26 with a meter", 10, "middle", MUTED)

a("</svg>")
open(os.path.join(out_dir, "wiring.svg"), "w").write("\n".join(svg))

# ---------------------------------------------------------------------------
# Markdown tables
# ---------------------------------------------------------------------------
md = ["# Wiring: Pico 2 ↔ µPD78C14GF (QFP-64)", "",
      "Generated by `tools/gen_wiring.py` (same table as `wiring.svg`).", "",
      "## Signals", "",
      "| Pico GPIO | Pico pin | µPD78C14 signal | QFP pin | Via | Note |",
      "|---|---|---|---|---|---|"]
for gp, pp, sig, cp, note in DIRECT:
    md.append(f"| {gp} | {pp} | {sig} | {cp} | 470 Ω (optional) | {note} |")
md += ["| GP20 | 26 | X2 | 24 | 74HCT04 1A(1)→1Y(2) | clock, inverted |",
       "| GP20 | 26 | X1 | 25 | 74HCT04 1Y(2)→2A(3), 2Y(4) | clock 6.25 MHz |",
       "| GP19 | 25 | /RESET | 22 | 74HCT04 3A(5)→3Y(6)→4A(9), 4Y(8) | high = run; 4.7 kΩ GP19→GND |",
       "", "## Power and static", "",
       "| Connection | Pins |", "|---|---|",
       "| +5 V (Pico VBUS, pin 40) | " + ", ".join(f"{n} {p}" for n, p in PLUS5) + ", 74HCT04 pin 14 |",
       "| GND (Pico pin 38) | " + ", ".join(f"{n} {p}" for n, p in GND) + ", 74HCT04 pin 7, 74HCT04 unused inputs 11, 13 |",
       "| MODE0 23 ⟷ PC7 18 | plus 22 kΩ to +5 V |",
       "| MODE1 21 ⟷ PC6 17 | plus 22 kΩ to +5 V |",
       "| 22 kΩ pull-ups to +5 V | " + ", ".join(f"{n} {p}" for n, p in PULLUPS) + " |",
       "| Leave open | " + ", ".join(f"{n} {p}" for n, p in OPEN) + " |",
       "| Decoupling | 100 nF at VDD 58, AVDD 37 and the 74HCT04 |",
       "| Optional LED | +5 V → LED → 1 kΩ → PC5 16 |", ""]
open(os.path.join(out_dir, "WIRING.md"), "w").write("\n".join(md))
print("wrote wiring.svg and WIRING.md")
