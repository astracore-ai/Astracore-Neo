#!/usr/bin/env python3
"""prep.py -- one unit's netlist and constraints for OpenROAD-flow-scripts (drop 0.42, stream A2b).

ORFS starts here from the synthesis job's own netlist, not from the RTL: synth.py writes one netlist per unit (the
unit's module alone, its child units and memory leaves instantiated by name), so the block's netlist is the unit's
netlist.v and those of every unit in its subtree (c16_systolic_array: the array, c16_mac_pe, c16_mac_pe_chk), plus
fakeram.py's wrappers for the memory leaves. Yosys reads them hierarchically and applies what ORFS's own synthesis
script does last (scripts/synth.tcl): splitnets, opt_clean -purge, hilomap -singleton onto TIEHI / TIELO (synth.py
leaves constants on pins -- the tied-off hooks, SETN of the reset flops -- and OpenROAD wants tie cells, which its
repair_tie_fanout then spreads), insbuf on pass-through wires, check -assert -mapped; the result is SYNTH_NETLIST_FILES
for ORFS (copied to 1_2_yosys.v, linked flat by OpenROAD, hierarchy names joined with '/').

Before that, the macro boundary is checked on the synthesized netlists (netlist.json): every pin the wrapper leaves
unconnected (the hooks bd_* / fi_*, the unused read port's address) must be a constant in the parent, and no output
the wrapper drops (bd_rdata, the unused port's data) may have a load -- else the swap would cut logic away.

The constraints are synth.py's block.sdc of the unit at the same period (the clock, 0.2 of the period on the I/O, the
reset false path, max fanout 32 / transition 0.16 of the period, the descriptor multicycles); every port it names is
checked against the netlist.

Usage: prep.py --synth <synth.py --out> --unit <unit> --macros <fakeram.py --out> --libs <asap7 liberty...> --out <dir>
       [--yosys <yosys>]
Writes <out>/1_netlist.v, <out>/1_block.sdc, <out>/prep.json (the subtree, cells, the synthesis estimates of the
block's units), <out>/prep.ys and <out>/prep.log.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

TIEHI = ("TIEHIx1_ASAP7_75t_R", "H")
TIELO = ("TIELOx1_ASAP7_75t_R", "L")
BUF = ("BUFx2_ASAP7_75t_R", "A", "Y")


def subtree(results, unit):
    """The unit's subtree, children before parents, with the instance count of each unit in one block."""
    order, count = [], {unit: 1}

    def visit(n):
        r = results.get(n)
        if r is None:
            sys.exit(f"prep.py: unit {n} is not in the synthesis results (run synth.py --blocks {unit})")
        if r.get("failed"):
            sys.exit(f"prep.py: unit {n} failed in synthesis: {r['failed']}")
        for c in (r.get("children") or {}):
            if c not in order:
                visit(c)
        if n not in order:
            order.append(n)
    visit(unit)
    for n in reversed(order):                    # parents first: multiply the counts down the tree
        for c, k in (results[n].get("children") or {}).items():
            count[c] = count.get(c, 0) + count.get(n, 0) * k
    return order, count


def check_macro_boundary(synth_dir, results, units, macros):
    """The leaf pins the wrappers do not connect: constants in, no loads out. Returns findings (empty = clean).
    Drop 0.43: an input pin may also be fed straight from one of the unit's input ports when that port bit feeds nothing
    else but such pins (sram_bank passes its hook ports dv_bd_* to the leaf's bd_* as they are; the parent ties them off
    at DV_HOOKS = 0): dropping the connection leaves the port bit unread and cuts no logic."""
    by_leaf = {m["leaf"]: m for m in macros}
    problems, checked = [], 0
    for u in units:
        mine = [m for m in (results[u].get("macros") or {}) if m in by_leaf]
        if not mine:
            continue
        with open(os.path.join(synth_dir, u, "netlist.json")) as f:
            j = json.load(f)
        top = next(n for n, m in j["modules"].items() if str(m.get("attributes", {}).get("top", "")).strip("0") == "1")
        mod = j["modules"][top]
        loads = {}                              # bit -> number of loads (cell inputs, module outputs)
        for cn, c in mod["cells"].items():
            dirs = c.get("port_directions", {})
            for pn, bits in c["connections"].items():
                if dirs.get(pn, "input") == "input":
                    for b in bits:
                        if isinstance(b, int):
                            loads[b] = loads.get(b, 0) + 1
        for pn, p in mod["ports"].items():
            if p["direction"] == "output":
                for b in p["bits"]:
                    if isinstance(b, int):
                        loads[b] = loads.get(b, 0) + 1
        port_in = {b for p in mod["ports"].values() if p["direction"] == "input" for b in p["bits"] if isinstance(b, int)}
        dropped_loads = {}                      # bit -> loads on leaf pins the wrappers drop
        for cn, c in mod["cells"].items():
            if c["type"] not in mine:
                continue
            fn = {p[0] for p in by_leaf[c["type"]]["functional_pins"]}
            for pn, bits in c["connections"].items():
                if pn not in fn and c.get("port_directions", {}).get(pn, "input") == "input":
                    for b in bits:
                        if isinstance(b, int):
                            dropped_loads[b] = dropped_loads.get(b, 0) + 1
        for cn, c in mod["cells"].items():
            if c["type"] not in mine:
                continue
            m = by_leaf[c["type"]]
            fn = {p[0] for p in m["functional_pins"]}
            for pn, bits in c["connections"].items():
                if pn in fn:
                    continue
                d = c.get("port_directions", {}).get(pn, "input")
                if d == "input" and any(isinstance(b, int) and not (b in port_in and loads.get(b, 0) == dropped_loads.get(b, 0))
                                        for b in bits):
                    problems.append(f"{u}.{cn}.{pn}: driven by logic, the macro {m['macro']} has no such pin")
                if d == "output" and any(isinstance(b, int) and loads.get(b, 0) for b in bits):
                    problems.append(f"{u}.{cn}.{pn}: read by logic, the macro {m['macro']} has no such pin")
            checked += 1
    return problems, checked


def sdc_ports(sdc_text):
    names = set()
    for m in re.finditer(r"get_ports\s+(\{[^}]*\}|\S+)", sdc_text):
        s = m.group(1).strip("{}[]")
        names.update(x for x in s.split() if x)
    return names


def run(cmd, cwd, log, timeout=None):
    with open(log, "w") as f:
        f.write("$ " + " ".join(cmd) + "\n")
        f.flush()
        return subprocess.run(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT, timeout=timeout).returncode


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synth", required=True, help="synth.py's output directory")
    ap.add_argument("--unit", required=True)
    ap.add_argument("--macros", required=True, help="fakeram.py's output directory for the unit")
    ap.add_argument("--libs", nargs="+", required=True, help="the standard-cell liberty files (cell pins only are used)")
    ap.add_argument("--yosys", default=os.environ.get("NEO_YOSYS") or "yosys")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    t0 = time.time()
    synth_dir = os.path.abspath(args.synth)
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(synth_dir, "synth_results.json")) as f:
        sres = json.load(f)
    results = sres["units"]
    units, count = subtree(results, args.unit)
    missing = [u for u in units if not os.path.isfile(os.path.join(synth_dir, u, "netlist.v"))]
    if missing:
        sys.exit(f"prep.py: no netlist for {missing}")
    mfile = os.path.join(args.macros, "macros.json")
    macros = json.load(open(mfile))["macros"] if os.path.isfile(mfile) else []
    used = sorted({m for u in units for m in (results[u].get("macros") or {})})
    if sorted(m["leaf"] for m in macros) != used:
        sys.exit(f"prep.py: the subtree uses the leaves {used}, fakeram.py made {[m['leaf'] for m in macros]}")
    problems, checked = check_macro_boundary(synth_dir, results, units, macros)
    if problems:
        sys.exit("prep.py: the macro swap would cut logic:\n  " + "\n  ".join(problems[:30]))

    # the Yosys pass: the subtree hierarchically, then ORFS's synth.tcl tail
    libs = [os.path.abspath(p) for p in args.libs]
    mlibs = [os.path.join(os.path.abspath(args.macros), m["lib"]) for m in macros]
    L = [f"# {args.unit}: synth.py's netlists of the subtree ({', '.join(units)}) for ORFS (07_ci/pnr/prep.py)"]
    L += [f"read_liberty -lib {p}" for p in libs + mlibs]
    if macros:
        L.append(f"read_verilog {os.path.join(os.path.abspath(args.macros), 'wrappers.v')}")
    L += [f"read_verilog {os.path.join(synth_dir, u, 'netlist.v')}" for u in units]
    lib_args = " ".join(f"-liberty {p}" for p in libs + mlibs)
    L += [f"hierarchy -check -top {args.unit}",
          "splitnets",
          "opt_clean -purge",
          f"hilomap -singleton -hicell {TIEHI[0]} {TIEHI[1]} -locell {TIELO[0]} {TIELO[1]}",
          f"insbuf -buf {BUF[0]} {BUF[1]} {BUF[2]}",
          "tee -q -o check.txt check",
          f"tee -q -o stat.txt stat -hierarchy {lib_args}",
          f"tee -q -o stat.json stat -json -hierarchy {lib_args}",
          "check -assert -mapped",
          "write_verilog -noattr -noexpr -nohex -nodec 1_netlist.v"]
    with open(os.path.join(out, "prep.ys"), "w") as f:
        f.write("\n".join(L) + "\n")
    rc = run([args.yosys, "-q", "-l", "prep.log", "-s", "prep.ys"], out, os.path.join(out, "prep.out"))
    if rc != 0:
        tail = open(os.path.join(out, "prep.out")).read()[-3000:]
        sys.exit(f"prep.py: yosys exit {rc}\n{tail}")

    # the constraints: synth.py's block.sdc of the unit, its ports checked against the netlist's top
    src = os.path.join(synth_dir, args.unit, "block.sdc")
    if not os.path.isfile(src):
        sys.exit(f"prep.py: no {src} (synth.py writes it in its timing run; --no-sta leaves none)")
    sdc = open(src).read()
    with open(os.path.join(synth_dir, args.unit, "netlist.json")) as f:
        j = json.load(f)
    top = next(n for n, m in j["modules"].items() if str(m.get("attributes", {}).get("top", "")).strip("0") == "1")
    ports = set(j["modules"][top]["ports"])
    unknown = sorted(p for p in sdc_ports(sdc) if p not in ports and not any(ch in p for ch in "*?"))
    if unknown:
        sys.exit(f"prep.py: block.sdc names ports the netlist lacks: {unknown}")
    period = re.search(r"create_clock\s+.*-period\s+([0-9.]+)", sdc)
    with open(os.path.join(out, "1_block.sdc"), "w") as f:
        f.write(f"# {args.unit}: synth.py's block.sdc ({os.path.relpath(src, synth_dir)}), unchanged, for ORFS (07_ci/pnr/prep.py)\n")
        f.write(sdc)

    # what the report compares against: the synthesis flow's figures for the block's units
    def count_area(s):
        """Yosys's stat -json: {"num_cells": {"count", "area", "local_count", ...}} (0.69) or flat numbers (older)."""
        v = s.get("num_cells")
        if isinstance(v, dict):
            return int(float(v.get("count", 0))), float(v.get("area", 0.0)), int(float(v.get("local_count", 0)))
        return int(v or 0), float(s.get("area", 0.0) or 0.0), None
    stat, top_s = {}, {}
    try:
        sj = json.load(open(os.path.join(out, "stat.json")))
        for mname, s in sj.get("modules", {}).items():
            c, a, lc = count_area(s)
            stat[mname.lstrip("\\")] = dict(cells=c, area_um2=round(a, 3), own_cells=lc)
        c, a, _ = count_area(sj.get("design", {}))
        by_type = {k.lstrip("\\"): v for k, v in sj.get("design", {}).get("num_cells_by_type", {}).items() if isinstance(v, dict)}
        mac = {k: int(float(v.get("count", 0))) for k, v in by_type.items() if k.startswith("fakeram7_")}
        mac_area = sum(float(v.get("area", 0.0)) for k, v in by_type.items() if k.startswith("fakeram7_"))
        top_s = dict(num_cells=c, area_um2=round(a, 3), macros=mac, macro_area_um2=round(mac_area, 3),
                     stdcells=c - sum(mac.values()), stdcell_area_um2=round(a - mac_area, 3))
    except (OSError, ValueError, AttributeError):
        pass
    synth_units = {}
    for u in units:
        r = results[u]
        sta = r.get("sta") or {}
        synth_units[u] = dict(instances=count.get(u, 0), own_cells=r.get("own_cells"), own_area_um2=r.get("own_area_um2"),
                              cells=r.get("cells"), cell_area_um2=r.get("cell_area_um2"), macro_area_um2=r.get("macro_area_um2"),
                              wns_ps=sta.get("wns_max_ps"), tns_ps=sta.get("tns_max_ps"), min_period_ps=sta.get("min_period_ps"),
                              worst_start=sta.get("worst_start"), worst_end=sta.get("worst_end"),
                              worst_arrival_ps=sta.get("worst_arrival_ps"), worst_slack_ps=sta.get("worst_slack_ps"))
    info = dict(unit=args.unit, units=units, period_ps=float(period.group(1)) if period else sres.get("period_ps"),
                synth_period_ps=sres.get("period_ps"), pdk=sres.get("pdk"), corner=sres.get("corner"), tools=sres.get("tools"),
                macros=macros, macro_pins_checked=checked, yosys_stat=dict(design=top_s, modules=stat),
                synth=synth_units, seconds=round(time.time() - t0, 1))
    with open(os.path.join(out, "prep.json"), "w") as f:
        json.dump(info, f, indent=1)
    print(f"prep.py: {args.unit}: {len(units)} units, {len(macros)} macro type(s), {checked} leaf instance(s) checked, "
          f"{top_s.get('num_cells', '?')} cells (flat), {info['seconds']} s -> {out}/1_netlist.v, 1_block.sdc")
    return 0


if __name__ == "__main__":
    sys.exit(main())
