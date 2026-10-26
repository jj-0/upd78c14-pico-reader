#!/usr/bin/env python3
"""Check the generated KiCad schematic: KiCad's netlist == design table, and ERC report."""
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

hw = os.path.join(os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "."), "hw")
sch = os.path.join(hw, "upd78c14-pico-reader.kicad_sch")
net_xml = os.path.join(hw, "kicad_netlist.xml")
subprocess.run(["kicad-cli", "sch", "export", "netlist", "--format", "kicadxml", "-o", net_xml, sch], check=True)

want = {frozenset(v) for v in json.load(open(os.path.join(hw, "design_nets.json"))).values()}
got, names = set(), {}
for n in ET.parse(net_xml).iter("net"):
    nodes = frozenset(f"{x.get('ref')}.{x.get('pin')}" for x in n.iter("node")
                      if not x.get("ref").startswith("#"))
    if len(nodes) > 1 or n.get("name", "").startswith("unconnected") is False and len(nodes) > 1:
        got.add(nodes)
        names[nodes] = n.get("name")
got = {g for g in got if len(g) > 1}
want = {w for w in want if len(w) > 1}
ok = got == want
for s in sorted(want - got, key=sorted):
    print("missing in KiCad:", sorted(s))
for s in sorted(got - want, key=sorted):
    print("extra in KiCad:  ", names.get(s), sorted(s))
print(f"KiCad netlist {'MATCHES' if ok else 'DIFFERS FROM'} the design table ({len(got)} multi-pin nets)")

erc = os.path.join(hw, "erc.rpt")
subprocess.run(["kicad-cli", "sch", "erc", "--severity-all", "-o", erc, sch], check=False)
print(open(erc).read()[-1500:])
sys.exit(0 if ok else 1)
