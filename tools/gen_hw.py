#!/usr/bin/env python3
"""Generate the KiCad schematic and the breadboard build from ONE design table.

Outputs (in hw/):
  upd78c14-pico-reader.kicad_sch / .kicad_pro   KiCad 9 schematic + project
  upd78c14_reader.kicad_sym, sym-lib-table      project library with the uPD78C14GF symbol
  breadboard.svg                                breadboard layout (render to PNG separately)
  BREADBOARD.md                                 placement + numbered wire list

Self-checks:
  * the breadboard model (strips, rails, parts, wires) is flattened into nets and
    must give exactly the same connectivity as the design table;
  * run tools/check_hw.sh afterwards to compare KiCad's own netlist export and ERC.

Usage: python3 tools/gen_hw.py [project_dir]
"""
import os
import re
import sys
import uuid

PROJ = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), ".."))
HW = os.path.join(PROJ, "hw")
KICAD_SYM = "/usr/share/kicad/symbols"
NAME = "upd78c14-pico-reader"
os.makedirs(HW, exist_ok=True)


def uid(*parts):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "upd78c14-pico-reader/" + "/".join(map(str, parts))))


# =============================================================================
# 1. S-expression helpers (just enough for KiCad files)
# =============================================================================
class Q(str):
    """A quoted string atom."""


def parse(text):
    tok = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))')
    pos, stack, cur = 0, [], []
    while pos < len(text):
        m = tok.match(text, pos)
        if not m or m.end() == pos:
            break
        pos = m.end()
        if m.group(1):
            stack.append(cur)
            cur = []
        elif m.group(2):
            done = cur
            cur = stack.pop()
            cur.append(done)
        elif m.group(3) is not None:
            cur.append(Q(m.group(3).replace('\\"', '"').replace("\\\\", "\\")))
        else:
            cur.append(m.group(4))
    return cur[0]


def dump(node, ind=0):
    if not isinstance(node, list):
        if isinstance(node, Q):
            return '"' + node.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
        if isinstance(node, float):
            return f"{node:.4f}".rstrip("0").rstrip(".") if node != int(node) else str(int(node))
        return str(node)
    if not any(isinstance(x, list) for x in node):
        return "(" + " ".join(dump(x) for x in node) + ")"
    pad = "\t" * (ind + 1)
    head = [dump(x) for x in node if not isinstance(x, list)]
    out = "(" + " ".join(head)
    for x in node:
        if isinstance(x, list):
            out += "\n" + pad + dump(x, ind + 1)
    return out + "\n" + "\t" * ind + ")"


def find(node, key):
    return [x for x in node if isinstance(x, list) and x and x[0] == key]


def lib_symbol(libfile, name, nickname):
    """Return a flattened copy of a library symbol, renamed to nickname:name."""
    lib = parse(open(os.path.join(KICAD_SYM, libfile)).read())
    syms = {s[1]: s for s in find(lib, "symbol")}
    sym = syms[name]
    ext = find(sym, "extends")
    if ext:
        base = parse(dump(syms[ext[0][1]]))
        props = {p[1]: p for p in find(sym, "property")}
        new = [x for x in base if not (isinstance(x, list) and x[0] == "property")]
        # keep base property order, override with the derived values
        idx = next(i for i, x in enumerate(new) if isinstance(x, list) and x[0] == "symbol")
        merged = [props.get(p[1], p) for p in find(base, "property")]
        merged += [p for k, p in props.items() if k not in {q[1] for q in find(base, "property")}]
        new[idx:idx] = merged
        old = ext[0][1]
        for s in find(new, "symbol"):
            s[1] = Q(s[1].replace(old, name, 1))
        sym = new
    else:
        sym = parse(dump(sym))
    sym[1] = Q(f"{nickname}:{name}")
    return sym


def pins_of(sym):
    """{(unit, number): (x, y, angle, etype, name)}; unit 0 = all units."""
    base = sym[1].split(":")[-1]
    out = {}
    for sub in find(sym, "symbol"):
        m = re.match(re.escape(base) + r"_(\d+)_(\d+)$", sub[1])
        unit = int(m.group(1))
        for p in find(sub, "pin"):
            at = find(p, "at")[0]
            num = find(p, "number")[0][1]
            name = find(p, "name")[0][1]
            out[(unit, str(num))] = (float(at[1]), float(at[2]), int(float(at[3])), p[1], name)
    return out


# =============================================================================
# 2. The uPD78C14GF symbol (QFP-64 pin numbers, NEC datasheet)
# =============================================================================
QFP = {1: "PA6", 2: "PA7", 11: "PC0/TxD", 12: "PC1/RxD", 13: "PC2/~{SCK}", 14: "PC3/TI/~{INT2}",
       15: "PC4/TO", 16: "PC5/CI", 17: "PC6/CO0", 18: "PC7/CO1", 19: "~{NMI}", 20: "INT1",
       21: "MODE1", 22: "~{RESET}", 23: "MODE0", 24: "X2", 25: "X1", 26: "VSS", 27: "AVSS",
       36: "VAREF", 37: "AVDD", 38: "~{RD}", 39: "~{WR}", 40: "ALE", 57: "~{STOP}", 58: "VDD"}
QFP.update({3 + i: f"PB{i}" for i in range(8)})
QFP.update({28 + i: f"AN{i}" for i in range(8)})
QFP.update({41 + i: f"PF{i}" for i in range(8)})
QFP.update({49 + i: f"PD{i}" for i in range(8)})
QFP.update({59 + i: f"PA{i}" for i in range(6)})
assert sorted(QFP) == list(range(1, 65))
BYNAME = {v: k for k, v in QFP.items()}

ETYPE = {}
for n in QFP.values():
    ETYPE[n] = "bidirectional"
for n in ["~{NMI}", "INT1", "~{RESET}", "X1", "X2", "~{STOP}", "VAREF"] + [f"AN{i}" for i in range(8)]:
    ETYPE[n] = "input"
for n in ["ALE", "~{RD}", "~{WR}"]:
    ETYPE[n] = "output"
for n in ["VDD", "AVDD", "VSS", "AVSS"]:
    ETYPE[n] = "power_in"

LEFT = ([f"PA{i}" for i in range(6)] + ["PA6", "PA7"] + [f"PB{i}" for i in range(8)]
        + ["PC0/TxD", "PC1/RxD", "PC2/~{SCK}", "PC3/TI/~{INT2}", "PC4/TO", "PC5/CI", "PC6/CO0", "PC7/CO1"]
        + ["MODE0", "MODE1", "~{RESET}", "~{STOP}", "~{NMI}", "INT1", "X1", "X2"])
RIGHT = ([f"PD{i}" for i in range(8)] + [None] + [f"PF{i}" for i in range(8)] + [None]
         + ["ALE", "~{RD}", "~{WR}", None] + [f"AN{i}" for i in range(8)] + ["VAREF"])
TOPP, BOTP = ["VDD", "AVDD"], ["VSS", "AVSS"]


def font(size=1.27):
    return ["effects", ["font", ["size", size, size]]]


def hidden():
    return ["effects", ["font", ["size", 1.27, 1.27]], ["hide", "yes"]]


def prop(name, value, x=0, y=0, hide=False, justify=None):
    eff = hidden() if hide else font()
    if justify:
        eff.append(["justify", justify])
    return ["property", Q(name), Q(value), ["at", x, y, 0], eff]


def make_upd_symbol(nick):
    pins = []
    for i, n in enumerate(LEFT):
        pins.append(["pin", ETYPE[n], "line", ["at", -25.4, 40.64 - 2.54 * i, 0], ["length", 7.62],
                     ["name", Q(n), font()], ["number", Q(str(BYNAME[n])), font()]])
    for i, n in enumerate(RIGHT):
        if n:
            pins.append(["pin", ETYPE[n], "line", ["at", 25.4, 40.64 - 2.54 * i, 180], ["length", 7.62],
                         ["name", Q(n), font()], ["number", Q(str(BYNAME[n])), font()]])
    for i, n in enumerate(TOPP):
        pins.append(["pin", "power_in", "line", ["at", -2.54 + 5.08 * i, 53.34, 270], ["length", 7.62],
                     ["name", Q(n), font()], ["number", Q(str(BYNAME[n])), font()]])
    for i, n in enumerate(BOTP):
        pins.append(["pin", "power_in", "line", ["at", -2.54 + 5.08 * i, -53.34, 90], ["length", 7.62],
                     ["name", Q(n), font()], ["number", Q(str(BYNAME[n])), font()]])
    return ["symbol", Q(f"{nick}uPD78C14GF"),
            ["pin_names", ["offset", 1.016]], ["exclude_from_sim", "no"], ["in_bom", "yes"], ["on_board", "yes"],
            prop("Reference", "U", -15.24, 48.26, justify="left"),
            prop("Value", "uPD78C14GF", 2.54, 48.26, justify="left"),
            prop("Footprint", "Package_DIP:DIP-64_W22.86mm", hide=True),
            prop("Datasheet", "NEC uPD78C10/C11/C14 datasheet", hide=True),
            prop("Description", "NEC uCOM-87AD 8-bit MCU, 16 KB mask ROM, 64-pin QFP (GF-3BE); "
                 "pin numbers are QFP pins", hide=True),
            ["symbol", Q("uPD78C14GF_0_1"),
             ["rectangle", ["start", -17.78, 45.72], ["end", 17.78, -45.72],
              ["stroke", ["width", 0.254], ["type", "default"]], ["fill", ["type", "background"]]]],
            ["symbol", Q("uPD78C14GF_1_1")] + pins]


# =============================================================================
# 3. THE DESIGN TABLE  (ref -> pin -> net; None = no connect)
# =============================================================================
P5, GND = "+5V", "GND"
U1 = {}
for p, n in QFP.items():
    U1[p] = None
for i in range(8):
    U1[BYNAME[f"PD{i}"]] = f"AD{i}"
for i in range(7):
    U1[BYNAME[f"PF{i}"]] = f"A{8 + i}"
U1.update({BYNAME["PF7"]: None, BYNAME["ALE"]: "ALE", BYNAME["~{RD}"]: "~{RD}", BYNAME["~{WR}"]: None,
           BYNAME["PC0/TxD"]: "TXD", BYNAME["X1"]: "CLK", BYNAME["X2"]: "~{CLK}",
           BYNAME["~{RESET}"]: "~{RESET}", BYNAME["MODE0"]: "MODE0", BYNAME["PC7/CO1"]: "MODE0",
           BYNAME["MODE1"]: "MODE1", BYNAME["PC6/CO0"]: "MODE1",
           BYNAME["~{NMI}"]: P5, BYNAME["~{STOP}"]: P5, BYNAME["VDD"]: P5, BYNAME["AVDD"]: P5,
           BYNAME["INT1"]: GND, BYNAME["VSS"]: GND, BYNAME["AVSS"]: GND, BYNAME["VAREF"]: GND})
for i in range(8):
    U1[BYNAME[f"AN{i}"]] = GND
    U1[BYNAME[f"PA{i}"]] = f"PA{i}"
    U1[BYNAME[f"PB{i}"]] = f"PB{i}"
for i, n in enumerate(["PC1/RxD", "PC2/~{SCK}", "PC3/TI/~{INT2}", "PC4/TO", "PC5/CI"], 1):
    U1[BYNAME[n]] = f"PC{i}"

GP_PIN = {0: 1, 1: 2, 2: 4, 3: 5, 4: 6, 5: 7, 6: 9, 7: 10, 8: 11, 9: 12, 10: 14, 11: 15, 12: 16,
          13: 17, 14: 19, 15: 20, 16: 21, 17: 22, 18: 24, 19: 25, 20: 26, 21: 27, 22: 29,
          26: 31, 27: 32, 28: 34}
A1 = {p: None for p in range(1, 41)}
for i in range(8):
    A1[GP_PIN[i]] = f"AD{i}"
for i in range(7):
    A1[GP_PIN[8 + i]] = f"A{8 + i}"
A1.update({GP_PIN[16]: "ALE", GP_PIN[17]: "~{RD}", GP_PIN[19]: "RUN", GP_PIN[20]: "CLK_PWM",
           GP_PIN[21]: "TXD", 40: P5})
for g in (3, 8, 13, 18, 23, 28, 38):
    A1[g] = GND          # 33 (AGND) left open: it is a second power_out in the KiCad symbol

U2 = {1: "CLK_PWM", 2: "~{CLK}", 3: "~{CLK}", 4: "CLK", 5: "RUN", 6: "RESET_INV",
      9: "RESET_INV", 8: "~{RESET}", 11: GND, 10: None, 13: GND, 12: None, 14: P5, 7: GND}

PULLUPS = ([f"PA{i}" for i in range(8)] + [f"PB{i}" for i in range(8)]
           + [f"PC{i}" for i in range(1, 6)] + ["MODE1", "MODE0"])
PARTS = {"U1": U1, "A1": A1, "U2": U2}
VALUES = {"U1": "uPD78C14GF (QFP64->DIP64 900mil adapter)", "A1": "Raspberry Pi Pico 2", "U2": "74HCT04"}
for i, net in enumerate(PULLUPS, 1):
    PARTS[f"R{i}"] = {1: P5, 2: net}
    VALUES[f"R{i}"] = "22k"
PARTS["R24"] = {1: "RUN", 2: GND}
VALUES["R24"] = "4.7k"
for i, what in enumerate(["VDD", "AVDD", "74HCT04"], 1):
    PARTS[f"C{i}"] = {1: P5, 2: GND}
    VALUES[f"C{i}"] = "100nF"


def design_nets():
    nets = {}
    for ref, pins in PARTS.items():
        for p, n in pins.items():
            if n:
                nets.setdefault(n, set()).add(f"{ref}.{p}")
    return nets


# =============================================================================
# 4. KiCad schematic
# =============================================================================
NICK = "upd78c14_reader"
LIBS = {
    "Device:R": lib_symbol("Device.kicad_sym", "R", "Device"),
    "Device:C": lib_symbol("Device.kicad_sym", "C", "Device"),
    "74xx:74HCT04": lib_symbol("74xx.kicad_sym", "74HCT04", "74xx"),
    "MCU_Module:RaspberryPi_Pico": lib_symbol("MCU_Module.kicad_sym", "RaspberryPi_Pico", "MCU_Module"),
    "power:+5V": lib_symbol("power.kicad_sym", "+5V", "power"),
    "power:GND": lib_symbol("power.kicad_sym", "GND", "power"),
    f"{NICK}:uPD78C14GF": make_upd_symbol(NICK + ":"),
}
PINS = {k: pins_of(v) for k, v in LIBS.items()}
ROOT = uid("root")
items = []        # schematic body items
pwr_count = [0]


def pin_xy(lib, at, unit, num):
    key = (unit, str(num)) if (unit, str(num)) in PINS[lib] else (0, str(num))
    x, y, a, et, nm = PINS[lib][key]
    return at[0] + x, at[1] - y, a


def outward(a):
    return {0: (-1, 0), 180: (1, 0), 90: (0, 1), 270: (0, -1)}[a]


def place(ref, lib, at, unit=1, value=None, footprint="", ref_at=None, val_at=None, nopins=False, angle=0):
    rx, ry = ref_at or (at[0] + 3, at[1] - 2)
    vx, vy = val_at or (at[0] + 3, at[1] + 2)
    node = ["symbol", ["lib_id", Q(lib)], ["at", at[0], at[1], angle], ["unit", unit],
            ["exclude_from_sim", "no"], ["in_bom", "no" if ref.startswith("#") else "yes"],
            ["on_board", "no" if ref.startswith("#") else "yes"], ["dnp", "no"],
            ["uuid", Q(uid("sym", ref, unit))],
            prop("Reference", ref, rx, ry, hide=ref.startswith("#"), justify="left"),
            prop("Value", value or ref, vx, vy, justify="left"),
            prop("Footprint", footprint, at[0], at[1], hide=True),
            prop("Datasheet", "", at[0], at[1], hide=True)]
    for (u, num) in PINS[lib]:
        if u in (0, unit):
            node.append(["pin", Q(num), ["uuid", Q(uid("pin", ref, unit, num))]])
    node.append(["instances", ["project", Q(NAME), ["path", Q("/" + ROOT), ["reference", Q(ref)], ["unit", unit]]]])
    items.append(node)


def power(net, x, y, d=(0, -1)):
    """Power symbol at (x, y) pointing away from the pin along direction d."""
    pwr_count[0] += 1
    ref = f"#PWR{pwr_count[0]:03d}"
    lib = "power:+5V" if net == P5 else "power:GND"
    if net == P5:
        ang = {(0, -1): 0, (-1, 0): 90, (0, 1): 180, (1, 0): 270}[d]
    else:
        ang = {(0, 1): 0, (1, 0): 90, (0, -1): 180, (-1, 0): 270}[d]
    vx, vy = x + d[0] * 5.0, y + d[1] * 5.0 + (0.6 if d[1] == 0 else 0)
    if d[0] > 0:
        vx = x + 4.6
    if d[0] < 0:
        vx = x - 9.2
    place(ref, lib, (x, y), value=net, val_at=(vx, vy), angle=ang)
    if ang in (90, 270):                     # keep the value text horizontal
        for f in find(items[-1], "property"):
            f[3][3] = ang


def wire(x1, y1, x2, y2):
    items.append(["wire", ["pts", ["xy", x1, y1], ["xy", x2, y2]], ["stroke", ["width", 0], ["type", "default"]],
                  ["uuid", Q(uid("wire", x1, y1, x2, y2))]])


def label(net, x, y, a):
    just = {0: "left bottom", 180: "right bottom", 90: "left bottom", 270: "right bottom"}[a]
    items.append(["label", Q(net), ["at", x, y, a], ["fields_autoplaced", "yes"],
                  ["effects", ["font", ["size", 1.27, 1.27]], ["justify", *just.split()]],
                  ["uuid", Q(uid("label", net, x, y))]])


def noconn(x, y):
    items.append(["no_connect", ["at", x, y], ["uuid", Q(uid("nc", x, y))]])


def text(s, x, y, size=1.5):
    items.append(["text", Q(s), ["exclude_from_sim", "no"], ["at", x, y, 0],
                  ["effects", ["font", ["size", size, size]], ["justify", "left", "top"]],
                  ["uuid", Q(uid("text", x, y))]])


def hookup(ref, lib, at, unit, pinmap, stub=2.54):
    """Stub + net label (or power symbol / no-connect) on every pin of this unit."""
    for (u, num) in PINS[lib]:
        if u not in (0, unit) or int(num) not in pinmap:
            continue
        net = pinmap[int(num)]
        x, y, a = pin_xy(lib, at, unit, num)
        if net is None:
            noconn(x, y)
            continue
        dx, dy = outward(a)
        ex, ey = round(x + dx * stub, 2), round(y + dy * stub, 2)
        wire(x, y, ex, ey)
        if net in (P5, GND):
            power(net, ex, ey, (dx, dy))
        else:
            la = {(-1, 0): 180, (1, 0): 0, (0, -1): 90, (0, 1): 270}[(dx, dy)]
            label(net, ex, ey, la)


# --- placement ---------------------------------------------------------------
G = 2.54
U1_AT = (40 * G, 52 * G)
place("U1", f"{NICK}:uPD78C14GF", U1_AT, value=VALUES["U1"], footprint="Package_DIP:DIP-64_W22.86mm",
      ref_at=(U1_AT[0] - 17.78, U1_AT[1] - 50.8), val_at=(U1_AT[0] + 7.62, U1_AT[1] - 50.8))
hookup("U1", f"{NICK}:uPD78C14GF", U1_AT, 1, U1)

A1_AT = (124 * G, 44 * G)
place("A1", "MCU_Module:RaspberryPi_Pico", A1_AT, value=VALUES["A1"],
      footprint="Module:RaspberryPi_Pico_Common_THT",
      ref_at=(A1_AT[0] + 22, A1_AT[1] - 22), val_at=(A1_AT[0] + 22, A1_AT[1] - 19.5))
hookup("A1", "MCU_Module:RaspberryPi_Pico", A1_AT, 1, A1)

HCT_UNITS = {1: (80, 68), 2: (100, 68), 3: (80, 78), 4: (100, 78), 5: (80, 88), 6: (100, 88), 7: (118, 78)}
for u, (gx, gy) in HCT_UNITS.items():
    at = (gx * G, gy * G)
    place("U2", "74xx:74HCT04", at, unit=u, value="74HCT04", footprint="Package_DIP:DIP-14_W7.62mm",
          ref_at=(at[0] + 4, at[1] - 2) if u == 7 else (at[0] - 2, at[1] - 4.5),
          val_at=(at[0] + 4, at[1] + 1) if u == 7 else (at[0] - 2, at[1] + 5))
    hookup("U2", "74xx:74HCT04", at, u, U2)

for i in range(1, 24):
    at = ((10 + 4 * (i - 1)) * G, 100 * G)
    place(f"R{i}", "Device:R", at, value="22k",
          footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal",
          ref_at=(at[0] + 1.8, at[1] - 0.6), val_at=(at[0] + 1.8, at[1] + 1.4))
    hookup(f"R{i}", "Device:R", at, 1, PARTS[f"R{i}"])
at = (66 * G, 90 * G)
place("R24", "Device:R", at, value="4.7k",
      footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal",
      ref_at=(at[0] + 1.8, at[1] - 0.6), val_at=(at[0] + 1.8, at[1] + 1.4))
hookup("R24", "Device:R", at, 1, PARTS["R24"])
for i in range(1, 4):
    at = ((130 + 6 * (i - 1)) * G, 86 * G)
    place(f"C{i}", "Device:C", at, value="100nF", footprint="Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm",
          ref_at=(at[0] + 1.8, at[1] - 0.6), val_at=(at[0] + 1.8, at[1] + 1.4))
    hookup(f"C{i}", "Device:C", at, 1, PARTS[f"C{i}"])

text("C1 at U1 VDD (58), C2 at U1 AVDD (37), C3 at U2 pin 14", 128 * G, 93 * G, 1.27)
text("Pull-ups: R1-R8 PA0-7, R9-R16 PB0-7, R17-R21 PC1-5, R22 MODE1/PC6, R23 MODE0/PC7. "
     "R24 keeps the chip in reset until the Pico runs.", 8 * G, 106 * G, 1.27)
text("HOW IT WORKS: both MODE pins pulled up -> chip boots from 64 KB external ROM (served by the Pico on AD0-7/A8-14).\n"
     "The reader code jumps to its 8000h mirror and drives PC7 low; PC7 is tied to MODE0, so the internal\n"
     "16 KB mask ROM appears at 0000h and is sent as Intel HEX on PC0/TxD at f_clk/96 baud (65104 @ 6.25 MHz).\n"
     "X1/X2 and /RESET need >= 4 V, hence the 74HCT04 at 5 V. Chip pin numbers are QFP-64 (GF-3BE) pins;\n"
     "U1's footprint is the 900-mil DIP-64 of a QFP64->DIP64 adapter, pin N = QFP pin N. Pico 2 GPIOs are 5 V tolerant.",
     12 * G, 10 * G, 1.6)

lib_symbols = ["lib_symbols"] + list(LIBS.values())
sch = ["kicad_sch", ["version", 20250114], ["generator", Q("eeschema")], ["generator_version", Q("9.0")],
       ["uuid", Q(ROOT)], ["paper", Q("A3")],
       ["title_block", ["title", Q("uPD78C14 mask-ROM reader - Raspberry Pi Pico 2")], ["date", Q("2026-10-26")],
        ["rev", Q("1")], ["comment", 1, Q("Port of devinacker/upd7811-reader (firmware public domain, hardware CC BY-SA 4.0)")],
        ["comment", 2, Q("PC Engine Duo CD controller uPD78C14GF dump")]],
       lib_symbols] + items + [["sheet_instances", ["path", Q("/"), ["page", Q("1")]]], ["embedded_fonts", "no"]]
open(os.path.join(HW, NAME + ".kicad_sch"), "w").write(dump(sch) + "\n")

# project file, project symbol library and its table
open(os.path.join(HW, NAME + ".kicad_pro"), "w").write(
    '{\n  "meta": {\n    "filename": "%s.kicad_pro",\n    "version": 3\n  },\n  "schematic": {\n'
    '    "legacy_lib_dir": "",\n    "legacy_lib_list": []\n  }\n}\n' % NAME)
lib_file = ["kicad_symbol_lib", ["version", 20241209], ["generator", Q("kicad_symbol_editor")],
            ["generator_version", Q("9.0")], make_upd_symbol("")]
open(os.path.join(HW, "upd78c14_reader.kicad_sym"), "w").write(dump(lib_file) + "\n")
open(os.path.join(HW, "sym-lib-table"), "w").write(
    '(sym_lib_table\n  (version 7)\n  (lib (name "upd78c14_reader")(type "KiCad")'
    '(uri "${KIPRJMOD}/upd78c14_reader.kicad_sym")(options "")(descr "uPD78C14GF reader project symbols"))\n)\n')


# =============================================================================
# 5. Breadboard model (one full-size 830-point board, 63 rows)
# =============================================================================
COLS = "abcdefghij"
rows_used = {}              # hole -> owner text
parts_bb = {}               # "REF.pin" -> hole
wires = []                  # (n, holeA, holeB, net, color, group)


def hole(row, col):
    return (row, col)


def occupy(h, who):
    assert h not in rows_used, f"hole {h} used twice ({rows_used[h]} / {who})"
    rows_used[h] = who


# U1 on a 900-mil QFP64->DIP64 adapter: pin p<=32 at (p, a), pin p>32 at (65-p, h)
for p in range(1, 65):
    h = hole(p, "a") if p <= 32 else hole(65 - p, "h")
    parts_bb[f"U1.{p}"] = h
    occupy(h, f"U1.{p}")
# U2 74HCT04, rows 34..40: pins 1-7 in column e, pins 14..8 in column f
for p in range(1, 15):
    h = hole(33 + p, "e") if p <= 7 else hole(48 - p, "f")
    parts_bb[f"U2.{p}"] = h
    occupy(h, f"U2.{p}")
# A1 Pico 2, rows 43..62, USB toward row 63: pins 1-20 column h (pin 1 at row 62),
# pins 21-40 column c (pin 21 at row 43). The module covers columns d-g.
for p in range(1, 41):
    h = hole(63 - p, "h") if p <= 20 else hole(p + 22, "c")
    parts_bb[f"A1.{p}"] = h
    occupy(h, f"A1.{p}")

SIDE = lambda c: "L" if c in "abcde" else "R"
# holes covered by a part body (the raised U1 adapter is deliberately NOT blocked)
BLOCKED = {(r, c) for r in range(43, 63) for c in "defg"}


def strip_free(row, side, pref):
    for c in pref:
        if SIDE(c) == side and (row, c) not in rows_used and (row, c) not in BLOCKED:
            return (row, c)
    raise RuntimeError(f"strip {row}{side} full")


def rail_free(rail, row):
    for d in (0, 1, -1, 2, -2, 3, -3):
        h = (row + d, rail)
        if 1 <= row + d <= 63 and h not in rows_used:
            return h
    raise RuntimeError(f"rail {rail} full near {row}")


NEAR_RAIL = {"L": "abcde", "R": "jihgf"}
NEAR_MID = {"L": "edcba", "R": "ijghf"}


def strip_of(ref_pin):
    r, c = parts_bb[ref_pin]
    return r, SIDE(c)


def two_lead(ref, pin_strip, pin_rail, strip_pin_ref, rail):
    """Resistor/capacitor from a part pin's strip to a rail."""
    r, s = strip_of(strip_pin_ref)
    a = strip_free(r, s, NEAR_RAIL[s])
    b = rail_free(rail, r)
    occupy(a, ref)
    occupy(b, ref)
    parts_bb[f"{ref}.{pin_strip}"] = a
    parts_bb[f"{ref}.{pin_rail}"] = b


def jumper(net, color, group, a, b, rail_row=63):
    """a/b: ('pin', 'U1.5') or ('rail', 'L+'), placed in a free hole."""
    holes = []
    for kind, what in (a, b):
        if kind == "pin":
            r, s = strip_of(what)
            h = strip_free(r, s, NEAR_MID[s])
        else:
            other = b if (kind, what) == a else a
            row = strip_of(other[1])[0] if other[0] == "pin" else rail_row
            h = rail_free(what, row)
        occupy(h, f"wire {net}")
        holes.append(h)
    wires.append([len(wires) + 1, holes[0], holes[1], net, color, group])


# pull-ups: R1..R23 from the pin's strip to the + rail on that side
for i, net in enumerate(PULLUPS, 1):
    pin = {"MODE1": "PC6/CO0", "MODE0": "PC7/CO1"}.get(net, None)
    if pin is None:
        pin = {f"PC{k}": n for k, n in enumerate(["PC1/RxD", "PC2/~{SCK}", "PC3/TI/~{INT2}", "PC4/TO", "PC5/CI"], 1)}.get(net, net)
    ref_pin = f"U1.{BYNAME[pin]}"
    r, s = strip_of(ref_pin)
    two_lead(f"R{i}", 2, 1, ref_pin, s + "+")
two_lead("R24", 1, 2, f"A1.{GP_PIN[19]}", "L-")
two_lead("C1", 1, 2, f"U1.{BYNAME['VDD']}", "R-")
two_lead("C2", 1, 2, f"U1.{BYNAME['AVDD']}", "R-")
two_lead("C3", 1, 2, "U2.14", "R-")

RED, BLK, BLUE, GRN, YEL, ORG, PUR, WHT, BRN = "#d32f2f", "#212121", "#1e63d6", "#2e9d3e", "#d8b400", "#ef6c00", "#8e24aa", "#9e9e9e", "#795548"
# 1) power distribution
jumper("+5V", RED, "1 power", ("pin", "A1.40"), ("rail", "L+"))
jumper("GND", BLK, "1 power", ("pin", "A1.38"), ("rail", "L-"))
jumper("+5V", RED, "1 power", ("rail", "L+"), ("rail", "R+"))
jumper("GND", BLK, "1 power", ("rail", "L-"), ("rail", "R-"), rail_row=60)
# 2) static chip pins (these strips sit under the adapter: wire them before fitting it)
for nm in ["~{NMI}"]:
    jumper("+5V", RED, "2 under adapter", ("pin", f"U1.{BYNAME[nm]}"), ("rail", "L+"))
for nm in ["INT1", "VSS", "AVSS", "AN0", "AN1", "AN2", "AN3", "AN4"]:
    jumper("GND", BLK, "2 under adapter", ("pin", f"U1.{BYNAME[nm]}"), ("rail", "L-"))
for nm in ["~{STOP}", "VDD", "AVDD"]:
    jumper("+5V", RED, "3 chip right side", ("pin", f"U1.{BYNAME[nm]}"), ("rail", "R+"))
for nm in ["VAREF", "AN7", "AN6", "AN5"]:
    jumper("GND", BLK, "3 chip right side", ("pin", f"U1.{BYNAME[nm]}"), ("rail", "R-"))
jumper("MODE0", PUR, "2 under adapter", ("pin", f"U1.{BYNAME['MODE0']}"), ("pin", f"U1.{BYNAME['PC7/CO1']}"))
jumper("MODE1", PUR, "2 under adapter", ("pin", f"U1.{BYNAME['MODE1']}"), ("pin", f"U1.{BYNAME['PC6/CO0']}"))
# 3) 74HCT04 power and links
jumper("GND", BLK, "4 74HCT04", ("pin", "U2.7"), ("rail", "L-"))
jumper("+5V", RED, "4 74HCT04", ("pin", "U2.14"), ("rail", "R+"))
jumper("GND", BLK, "4 74HCT04", ("pin", "U2.11"), ("rail", "R-"))
jumper("GND", BLK, "4 74HCT04", ("pin", "U2.13"), ("rail", "R-"))
jumper("~{CLK}", ORG, "4 74HCT04", ("pin", "U2.2"), ("pin", "U2.3"))
jumper("RESET_INV", PUR, "4 74HCT04", ("pin", "U2.6"), ("pin", "U2.9"))
# 4) clock / reset / serial
jumper("~{CLK}", ORG, "5 clock+reset", ("pin", "U2.2"), ("pin", f"U1.{BYNAME['X2']}"))
jumper("CLK", ORG, "5 clock+reset", ("pin", "U2.4"), ("pin", f"U1.{BYNAME['X1']}"))
jumper("~{RESET}", PUR, "5 clock+reset", ("pin", "U2.8"), ("pin", f"U1.{BYNAME['~{RESET}']}"))
jumper("CLK_PWM", ORG, "5 clock+reset", ("pin", f"A1.{GP_PIN[20]}"), ("pin", "U2.1"))
jumper("RUN", PUR, "5 clock+reset", ("pin", f"A1.{GP_PIN[19]}"), ("pin", "U2.5"))
jumper("TXD", WHT, "5 clock+reset", ("pin", f"U1.{BYNAME['PC0/TxD']}"), ("pin", f"A1.{GP_PIN[21]}"))
# 5) bus
jumper("ALE", YEL, "6 bus", ("pin", f"U1.{BYNAME['ALE']}"), ("pin", f"A1.{GP_PIN[16]}"))
jumper("~{RD}", YEL, "6 bus", ("pin", f"U1.{BYNAME['~{RD}']}"), ("pin", f"A1.{GP_PIN[17]}"))
for i in range(8):
    jumper(f"AD{i}", BLUE, "6 bus", ("pin", f"U1.{BYNAME[f'PD{i}']}"), ("pin", f"A1.{GP_PIN[i]}"))
for i in range(7):
    jumper(f"A{8 + i}", GRN, "6 bus", ("pin", f"U1.{BYNAME[f'PF{i}']}"), ("pin", f"A1.{GP_PIN[8 + i]}"))


# --- verify: breadboard connectivity == design table -------------------------
def bb_nets():
    parent = {}

    def node(h):
        r, c = h
        if c in ("L+", "L-", "R+", "R-"):
            return ("rail", c)
        return ("strip", r, SIDE(c))

    def f(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def u(a, b):
        parent[f(a)] = f(b)

    for _, a, b, *_ in wires:
        u(node(a), node(b))
    # the Pico module ties all its GND pins together internally
    gnd = [parts_bb[f"A1.{g}"] for g in (3, 8, 13, 18, 23, 28, 38)]
    for h in gnd[1:]:
        u(node(gnd[0]), node(h))
    groups = {}
    for rp, h in parts_bb.items():
        groups.setdefault(f(node(h)), set()).add(rp)
    return groups


design = design_nets()
want = {frozenset(v) for v in design.values()}
got_groups = bb_nets()
connected = {rp for v in design.values() for rp in v}
got = set()
for g in got_groups.values():
    real = {rp for rp in g}
    if len(real) == 1 and next(iter(real)) not in connected:
        continue                               # a lone no-connect pin
    got.add(frozenset(real))
if got != want:
    for s in sorted(want - got, key=sorted):
        print("design net missing on breadboard:", sorted(s))
    for s in sorted(got - want, key=sorted):
        print("breadboard has unexpected net:   ", sorted(s))
    sys.exit("BREADBOARD CHECK FAILED")
print(f"breadboard check OK: {len(want)} nets, {len(parts_bb)} part pins, {len(wires)} jumpers")


# =============================================================================
# 6. Breadboard drawing (landscape: rows run left -> right)
# =============================================================================
PX, X0, Y0 = 21, 70, 120
Y = {"L+": 0, "L-": 1}
for i, c in enumerate("abcde"):
    Y[c] = 3 + i
for i, c in enumerate("fghij"):
    Y[c] = 10 + i
Y.update({"R-": 16, "R+": 17})


def hx(row):
    return X0 + (row - 1) * PX


def hy(col):
    return Y0 + Y[col] * PX


svg = []
A = svg.append
BUSW = [w for w in wires if w[5] == "6 bus" and w[1][1] in "fghij" and w[2][1] in "fghij"]
RIB0 = Y0 + 18 * PX + 6
W, H = X0 + 63 * PX + 60, RIB0 + 8 * len(BUSW) + 110
A(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
  f'font-family="DejaVu Sans, Arial, sans-serif">')
A(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')
A(f'<text x="{X0}" y="38" font-size="22" font-weight="bold" fill="#1b1f24">µPD78C14 reader — breadboard layout '
  f'(one 830-point board)</text>')
A(f'<text x="{X0}" y="62" font-size="13" fill="#5b6470">Rows 1–63 left→right, columns a–j top→bottom. '
  f'U1 = QFP64→DIP64 900-mil adapter (pins in a/h), raised on stacked headers; holes under it are wired first. '
  f'Wire numbers match BREADBOARD.md.</text>')
A(f'<rect x="{X0 - 20}" y="{Y0 - 22}" width="{63 * PX + 30}" height="{18 * PX + 18}" rx="10" fill="#f6f3ea" stroke="#c9c1a8"/>')
A(f'<rect x="{X0 - 20}" y="{hy("e") + PX * 0.6}" width="{63 * PX + 30}" height="{PX * 0.8}" fill="#e7e0cd"/>')
for rail, colr in (("L+", "#d32f2f"), ("L-", "#1e63d6"), ("R-", "#1e63d6"), ("R+", "#d32f2f")):
    A(f'<line x1="{X0 - 10}" y1="{hy(rail) + (-9 if rail in ("L+", "R-") else 9)}" x2="{hx(63) + 10}" '
      f'y2="{hy(rail) + (-9 if rail in ("L+", "R-") else 9)}" stroke="{colr}" stroke-width="2"/>')
    A(f'<text x="{X0 - 34}" y="{hy(rail) + 4}" font-size="12" font-weight="bold" fill="{colr}">{rail[1]}</text>')
for c in COLS:
    A(f'<text x="{X0 - 34}" y="{hy(c) + 4}" font-size="11" fill="#6d6a5f">{c}</text>')
for r in range(1, 64):
    if r % 5 == 0 or r == 1:
        A(f'<text x="{hx(r)}" y="{Y0 - 28}" font-size="10" text-anchor="middle" fill="#6d6a5f">{r}</text>')
    for c in list(COLS) + ["L+", "L-", "R-", "R+"]:
        A(f'<rect x="{hx(r) - 3}" y="{hy(c) - 3}" width="6" height="6" fill="#3d3a33" opacity="0.35"/>')

# parts
def body(r1, r2, c1, c2, fill, label, sub=""):
    x1, x2 = hx(r1) - 10, hx(r2) + 10
    y1, y2 = hy(c1) - 10, hy(c2) + 10
    A(f'<rect x="{x1}" y="{y1}" width="{x2 - x1}" height="{y2 - y1}" rx="6" fill="{fill}" fill-opacity="0.55" stroke="#333" stroke-width="1.4"/>')
    A(f'<text x="{(x1 + x2) / 2}" y="{(y1 + y2) / 2 - 2}" font-size="15" font-weight="bold" text-anchor="middle" fill="#111">{label}</text>')
    if sub:
        A(f'<text x="{(x1 + x2) / 2}" y="{(y1 + y2) / 2 + 15}" font-size="11" text-anchor="middle" fill="#222">{sub}</text>')


body(1, 32, "a", "h", "#b39ddb", "U1  µPD78C14GF on QFP64→DIP64 adapter", "pin 1 at row 1 col a · raise it on stacked headers")
body(34, 40, "e", "f", "#ffcc80", "U2", "74HCT04")
body(43, 62, "c", "h", "#a5d6a7", "A1  Pico 2", "USB → row 63 end")

PIN_SHORT = {k: v.replace("~{", "/").replace("}", "") for k, v in QFP.items()}
for rp, (r, c) in parts_bb.items():
    ref, p = rp.split(".")
    if ref in ("U1", "U2", "A1"):
        A(f'<circle cx="{hx(r)}" cy="{hy(c)}" r="4.5" fill="#222"/>')
    if ref == "U1":
        nm = PIN_SHORT[int(p)].split("/")[0]
        dy = -12 if c == "a" else 16
        A(f'<text x="{hx(r)}" y="{hy(c) + dy}" font-size="8" text-anchor="middle" fill="#3b2a6b" '
          f'transform="rotate(-90 {hx(r)} {hy(c) + dy})">{nm}</text>' if False else
          f'<text x="{hx(r)}" y="{hy(c) + dy}" font-size="7.5" text-anchor="middle" fill="#3b2a6b">{nm}</text>')
    if ref == "A1":
        gp = {v: k for k, v in GP_PIN.items()}.get(int(p))
        nm = f"GP{gp}" if gp is not None else {3: "GND", 8: "GND", 13: "GND", 18: "GND", 23: "GND", 28: "GND",
                                              33: "AGND", 38: "GND", 40: "VBUS", 39: "VSYS", 36: "3V3",
                                              37: "EN", 35: "VREF", 30: "RUN"}.get(int(p), str(p))
        dy = -12 if c == "c" else 16
        A(f'<text x="{hx(r)}" y="{hy(c) + dy}" font-size="7.5" text-anchor="middle" fill="#1b4d20">{nm}</text>')
    if ref == "U2":
        dy = -12 if c == "e" else 16
        A(f'<text x="{hx(r)}" y="{hy(c) + dy}" font-size="8" text-anchor="middle" fill="#7a3e00">{p}</text>')

# resistors / capacitors
for ref in [k for k in PARTS if k[0] in "RC"]:
    (r1, c1), (r2, c2) = parts_bb[f"{ref}.1"], parts_bb[f"{ref}.2"]
    x1, y1, x2, y2 = hx(r1), hy(c1), hx(r2), hy(c2)
    A(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#777" stroke-width="1.5"/>')
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    fill = "#e8c27a" if ref[0] == "R" else "#7fb3e0"
    A(f'<rect x="{mx - 5}" y="{my - 9}" width="10" height="18" rx="3" fill="{fill}" stroke="#555"/>')
    A(f'<text x="{mx + 7}" y="{my + 3}" font-size="8" fill="#5a4210">{ref}</text>')

# bus jumpers: a nested ribbon below the board, one level per wire
order = sorted(BUSW, key=lambda w: -abs(w[2][0] - w[1][0]))
for k, (n, (r1, c1), (r2, c2), net, color, grp) in enumerate(reversed(order)):
    x1, y1, x2, y2 = hx(r1), hy(c1), hx(r2), hy(c2)
    yk = RIB0 + 8 * k
    A(f'<path d="M{x1} {y1} L{x1} {yk - 4} Q{x1} {yk} {x1 + 4} {yk} L{x2 - 4} {yk} Q{x2} {yk} {x2} {yk - 4} L{x2} {y2}" '
      f'fill="none" stroke="{color}" stroke-width="2.2" stroke-opacity="0.9"/>')
    A(f'<circle cx="{x1}" cy="{y1}" r="3.5" fill="{color}"/><circle cx="{x2}" cy="{y2}" r="3.5" fill="{color}"/>')
    A(f'<text x="{x1 + 8}" y="{yk + 3}" font-size="8.5" font-weight="bold" fill="{color}" stroke="#fff" '
      f'stroke-width="2.5" paint-order="stroke">{n} {net}</text>')

# other jumpers (arcs; straight when both ends are in the same row)
for n, (r1, c1), (r2, c2), net, color, grp in wires:
    if [n, (r1, c1), (r2, c2), net, color, grp] in BUSW:
        continue
    x1, y1, x2, y2 = hx(r1), hy(c1), hx(r2), hy(c2)
    if r1 == r2:
        A(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="2.6" stroke-opacity="0.9"/>')
        A(f'<circle cx="{x1}" cy="{y1}" r="3.5" fill="{color}"/><circle cx="{x2}" cy="{y2}" r="3.5" fill="{color}"/>')
        A(f'<text x="{x1 + 6}" y="{(y1 + y2) / 2}" font-size="9" font-weight="bold" fill="{color}" stroke="#fff" '
          f'stroke-width="2.5" paint-order="stroke">{n}</text>')
        continue
    dist = abs(x2 - x1) + abs(y2 - y1)
    bow = min(80, 10 + dist * 0.18)
    side = -1 if (c1 in "abcde" or c1 in ("L+", "L-")) and (c2 in "abcde" or c2 in ("L+", "L-")) else 1
    if c1 in "fghij" or c1 in ("R+", "R-"):
        side = 1 if (c2 in "fghij" or c2 in ("R+", "R-")) else side
    cx1, cy1 = x1 + (x2 - x1) * 0.25, min(y1, y2) + side * bow if side > 0 else min(y1, y2) - bow
    cy1 = (max(y1, y2) + bow) if side > 0 else (min(y1, y2) - bow)
    cx2, cy2 = x1 + (x2 - x1) * 0.75, cy1
    A(f'<path d="M{x1} {y1} C{cx1} {cy1} {cx2} {cy2} {x2} {y2}" fill="none" stroke="{color}" stroke-width="2.6" '
      f'stroke-opacity="0.9" stroke-linecap="round"/>')
    A(f'<circle cx="{x1}" cy="{y1}" r="3.5" fill="{color}"/><circle cx="{x2}" cy="{y2}" r="3.5" fill="{color}"/>')
    tx, ty = (x1 + x2) / 2, cy1 * 0.75 + (y1 + y2) / 2 * 0.25
    A(f'<text x="{tx}" y="{ty + 3}" font-size="9" font-weight="bold" text-anchor="middle" fill="{color}" '
      f'stroke="#fff" stroke-width="2.5" paint-order="stroke">{n}</text>')

ly = H - 46
for i, (lab, col) in enumerate([("+5 V", RED), ("GND", BLK), ("AD0–7", BLUE), ("A8–14", GRN), ("ALE / RD", YEL),
                                ("clock", ORG), ("reset / MODE", PUR), ("TxD", WHT)]):
    x = X0 + i * 150
    A(f'<line x1="{x}" y1="{ly}" x2="{x + 30}" y2="{ly}" stroke="{col}" stroke-width="4"/>')
    A(f'<text x="{x + 38}" y="{ly + 4}" font-size="12" fill="#333">{lab}</text>')
A(f'<text x="{X0}" y="{ly + 28}" font-size="12" fill="#5b6470">Rails assumed continuous over all 63 rows — '
  f'if yours are split in the middle, bridge each rail. Resistors R1–R24 and C1–C3 go from the marked strip to the rail.</text>')
A("</svg>")
open(os.path.join(HW, "breadboard.svg"), "w").write("\n".join(svg))

# =============================================================================
# 7. BREADBOARD.md
# =============================================================================
def hs(h):
    r, c = h
    return f"{c} rail, row {r}" if c in ("L+", "L-", "R+", "R-") else f"{r}{c}"


def who(h):
    for rp, hh in parts_bb.items():
        if hh[0] == h[0] and SIDE(hh[1]) == SIDE(h[1]) and hh[1] not in ("L+", "L-", "R+", "R-") \
                and h[1] not in ("L+", "L-", "R+", "R-") and rp.split(".")[0] in ("U1", "U2", "A1"):
            ref, p = rp.split(".")
            nm = PIN_SHORT[int(p)] if ref == "U1" else (f"GP{ {v: k for k, v in GP_PIN.items()}.get(int(p))}"
                                                         if ref == "A1" and int(p) in GP_PIN.values() else f"pin {p}")
            return f"{ref} {nm} ({ref}.{p})"
    return ""


md = ["# Breadboard build (one 830-point board)", "",
      "Generated by `tools/gen_hw.py` from the same design table as the KiCad schematic; the script",
      "flattens this layout into nets and checks they match the schematic exactly.", "",
      "Hole names are `<row><column>` (rows 1–63, columns a–j). `L+`/`L-` are the rails next to column a,",
      "`R+`/`R-` the rails next to column j. Rails are assumed continuous (bridge split rails).", "",
      "## 1. Placement", "",
      "| Part | Where | Notes |", "|---|---|---|",
      "| U1 µPD78C14GF on a QFP-64 (14×20 mm, 1.0 mm) → DIP-64 **900 mil** adapter, pin N = QFP pin N | pins 1–32 in column a rows 1–32, pins 33–64 in column h rows 32–1 | pin 1 = row 1. Mount it on stacked/long headers: the strips under it (columns b–g) are wired first |",
      "| U2 74HCT04 | pins 1–7 in column e rows 34–40, pins 14–8 in column f rows 34–40 | notch toward row 33 |",
      "| A1 Raspberry Pi Pico 2 | pins 21–40 in column c rows 43–62, pins 1–20 in column h rows 62–43 | USB connector toward row 63; it covers columns d–g |",
      "", "## 2. Resistors and capacitors", "",
      "R7–R23 land in column b of rows 1–18, under the edge of the U1 adapter: fit them (leads bent flat)",
      "together with the group-2 jumpers **before** plugging in the adapter.", "",
      "| Ref | Value | Lead 1 | Lead 2 | Net |", "|---|---|---|---|---|"]
for ref in [k for k in PARTS if k[0] in "RC"]:
    md.append(f"| {ref} | {VALUES[ref]} | {hs(parts_bb[ref + '.1'])} | {hs(parts_bb[ref + '.2'])} | "
              f"{PARTS[ref][1]} / {PARTS[ref][2]} |")
md += ["", "## 3. Jumper wires (in build order)", "",
       "| # | From | To | Net | Connects |", "|---|---|---|---|---|"]
for n, a, b, net, color, grp in sorted(wires, key=lambda w: (w[5], w[0])):
    conn = " ↔ ".join(x for x in (who(a), who(b)) if x)
    md.append(f"| {n} | {hs(a)} | {hs(b)} | {net.replace('~{', '/').replace('}', '')} | {conn} *(group {grp})* |")
md += ["", "## 4. Before plugging in USB", "",
       "- Meter: rails + to − not shorted; U1 pin 58 (VDD) and 37 (AVDD) read +5 V rail, 26/27 GND.",
       "- MODE0 (U1 23) and PC7 (U1 18) are one net; MODE1 (21) and PC6 (17) are one net; both pulled up.",
       "- U1 pins left open on purpose: 39 /WR, 48 PF7 (A15).",
       "- Keep the clock jumpers (U2.4 → X1, U2.2 → X2) short and away from the bus wires.", ""]
open(os.path.join(HW, "BREADBOARD.md"), "w").write("\n".join(md))
print("wrote", ", ".join(sorted(os.listdir(HW))))

import json
json.dump({k: sorted(v) for k, v in design.items()}, open(os.path.join(HW, "design_nets.json"), "w"), indent=1)
