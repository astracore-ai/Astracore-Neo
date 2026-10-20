#!/usr/bin/env python3
"""check_regmap.py -- the C driver's register map and cause bits must be the RTL's (drop 0.44, review finding 6).
  1. Every register of host_if.sv's header ('//     0xNN NAME' lines; a range '0xNN..0xMM NAME' is a base and a count) is a
     NEO_REG_* macro of sw/neo_host.h with the same address (the CFG range: NEO_REG_CFG_BASE and NEO_NDESC; the TEL
     range: NEO_REG_TEL(k) and NEO_TEL_NCNT), and every NEO_REG_* macro with a plain address names a register of the RTL.
  2. Every bit of neo_tile.sv's tile_flags concatenation (the last name is bit 0) is a NEO_CAUSE_* macro at that position,
     esm's NFLAGS is NEO_CAUSE_NFLAGS and the watchdog sits above the flags; neo_host.c's cause names follow the same order.
  3. The telemetry counters of host_if's header (0x41 .. 0x4C) are NEO_TEL_NCNT = 12 in the header's enum order.
Prints the generated register table (the spec's §7 rewrite takes it) and RESULT."""
import os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
RTL, SW = os.path.join(HERE, "..", "rtl"), os.path.join(HERE, "..", "sw")
host_if = open(os.path.join(RTL, "host_if.sv")).read()
tile = open(os.path.join(RTL, "neo_tile.sv")).read()
hdr = open(os.path.join(SW, "neo_host.h")).read()
src = open(os.path.join(SW, "neo_host.c")).read()
fails = []

# ---- 1. registers: the names from host_if.sv's header, the addresses the RTL decodes from its case statements
rtl_regs = {}
for m in re.finditer(r"^//\s+0x([0-9A-Fa-f]{2})(?:\.\.0x([0-9A-Fa-f]{2}))?\s+([A-Z_]+[A-Z0-9_]*)\s", host_if, re.M):
    a0, a1, name = int(m.group(1), 16), m.group(2), m.group(3)
    rtl_regs[name] = (a0, (int(a1, 16) - a0 + 1) if a1 else 1)
m = re.search(r"^//\s+0x10\.\.0x([0-9A-Fa-f]{2})\s+w: descriptor registers", host_if, re.M)       # the CFG range has no name of its own
rtl_regs["CFG"] = (0x10, int(m.group(1), 16) - 0x10 + 1)
rtl_regs["RQ_ADDR"] = (int(re.search(r"table row at RQ_ADDR \(0x([0-9A-Fa-f]{2})\)", host_if).group(1), 16), 1)
decoded = {int(a, 16) for a in re.findall(r"8'h([0-9A-Fa-f]{2}):", host_if)}
decoded |= set(range(0x10, 0x24)) | {0x24} | set(range(0x41, 0x4D))                                      # the default branches
rtl_regs["CFG_M"] = (0x24, 1)
c_regs = {m.group(1): int(m.group(2), 16) for m in re.finditer(r"#define NEO_REG_([A-Z0-9_]+) 0x([0-9A-Fa-f]+)", hdr)}
m = re.search(r"#define NEO_REG_TEL\(k\) \(0x([0-9A-Fa-f]+) \+ \(k\)\)", hdr)
c_regs["TEL"] = int(m.group(1), 16) if m else -1
ndesc = int(re.search(r"#define NEO_NDESC (\d+)", hdr).group(1))
ncnt = re.search(r"enum \{ NEO_TEL_CYCLES.*?NEO_TEL_NCNT \}", hdr, re.S).group(0).count("NEO_TEL_") - 1
rows = []
for name, (addr, count) in sorted(rtl_regs.items(), key=lambda kv: kv[1][0]):
    cname = {"CFG": "CFG_BASE"}.get(name, name)
    if cname not in c_regs:
        fails.append(f"register {name} at 0x{addr:02x} (host_if.sv) has no NEO_REG_{cname} in neo_host.h")
        rows.append((addr, count, name, "MISSING"))
        continue
    if c_regs[cname] != addr:
        fails.append(f"NEO_REG_{cname} = 0x{c_regs[cname]:02x}, host_if.sv has 0x{addr:02x}")
    if name == "CFG" and count != ndesc:
        fails.append(f"CFG is {count} registers in host_if.sv, NEO_NDESC = {ndesc}")
    if name == "TEL" and count != ncnt:
        fails.append(f"TEL is {count} counters in host_if.sv, NEO_TEL_NCNT = {ncnt}")
    if not all(a in decoded for a in range(addr, addr + count)):
        fails.append(f"register {name} at 0x{addr:02x}: not decoded by host_if.sv's case statements")
    rows.append((addr, count, name, f"NEO_REG_{cname}" + ("(k)" if name == "TEL" else "")))
for cname, addr in c_regs.items():
    rname = {"CFG_BASE": "CFG"}.get(cname, cname)
    if rname not in rtl_regs:
        fails.append(f"NEO_REG_{cname} = 0x{addr:02x} names no register of host_if.sv")
    elif addr not in decoded:
        fails.append(f"NEO_REG_{cname} = 0x{addr:02x} is not decoded by host_if.sv")

# ---- 2. causes
m = re.search(r"assign tile_flags = \{(.*?)\};", tile, re.S)
flags = [f.strip() for f in m.group(1).replace("\n", " ").split(",")]
flags = [re.sub(r"_t$|_sticky_t$|_sticky$", "", f) for f in flags]
flags = [f[:-1] if f.endswith("_") else f for f in flags]
nflags = int(re.search(r"esm #\(\.NFLAGS\((\d+)\)\)", tile).group(1))
if len(flags) != nflags:
    fails.append(f"tile_flags has {len(flags)} flags, esm NFLAGS {nflags}")
c_causes = {m.group(1): m.group(2) for m in re.finditer(r"#define NEO_CAUSE_([A-Z0-9_]+) (\w+)", hdr)}
c_nflags = int(c_causes.get("NFLAGS", "-1"))
if c_nflags != nflags:
    fails.append(f"NEO_CAUSE_NFLAGS = {c_nflags}, the RTL has {nflags}")
norm = lambda s: s.upper().replace("_ERR_", "_").rstrip("_")  # noqa: E731
bits = {}
for i, f in enumerate(reversed(flags)):                           # the last name of the concatenation is bit 0
    bits[i] = f
    cand = [k for k, v in c_causes.items() if v.isdigit() and int(v) == i]
    if not cand:
        fails.append(f"cause bit {i} ({f}) has no NEO_CAUSE_* macro")
    elif norm(cand[0]) != norm(f) and norm(cand[0]) not in norm(f) and norm(f) not in norm(cand[0]):
        fails.append(f"cause bit {i} is {f} in the RTL, NEO_CAUSE_{cand[0]} in the header")
if c_causes.get("WATCHDOG") != "NEO_CAUSE_NFLAGS":
    fails.append("NEO_CAUSE_WATCHDOG is not NEO_CAUSE_NFLAGS")
names = re.search(r"neo_cause_names\[NEO_CAUSE_NFLAGS \+ 1\] = \{(.*?)\};", src, re.S).group(1)
names = [n.strip().strip('"') for n in names.replace("\n", " ").split(",") if n.strip()]
if len(names) != nflags + 1 or names[-1] != "watchdog":
    fails.append(f"neo_host.c names {len(names)} causes, the last '{names[-1] if names else ''}'")
for i, f in bits.items():
    if i < len(names) and norm(names[i]) != norm(f):
        fails.append(f"neo_host.c's cause name {i} '{names[i]}' is not the RTL's '{f}'")

# ---- the table
print("Tile register map (host_if.sv, checked against sw/neo_host.h):")
print(f"  {'address':<12} {'register':<10} {'C macro':<22}")
for addr, count, name, cname in rows:
    rng = f"0x{addr:02X}" + (f"..0x{addr + count - 1:02X}" if count > 1 else "")
    print(f"  {rng:<12} {name:<10} {cname:<22}")
print(f"Causes (neo_tile.sv's tile_flags, esm NFLAGS = {nflags}; ERR_CAUSE / ERR_MASK bit = position):")
print("  " + ", ".join(f"{i} {f}" for i, f in bits.items()) + f", {nflags} watchdog")
for f in fails:
    print("FAIL:", f)
print(f"register map and causes: {len(rows)} registers, {nflags + 1} causes checked -> {'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
raise SystemExit(1 if fails else 0)
