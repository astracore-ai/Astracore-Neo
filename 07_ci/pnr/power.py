#!/usr/bin/env python3
"""power.py -- the recorded switching activity onto a block's flops and inputs (drop 0.43, stream A3).

activity.py records, from a cocotb run's VCD, every RTL signal bit's toggle count and high time under a block's scope
(07_ci/pnr/activity/<block>.json.gz). OpenSTA's report_power propagates activity from the inputs and the register
outputs through the logic, so those are the pins to annotate. The synthesis flow names every flop after the register
bit it holds (`prod_q[14]_reg`), and the routed block keeps the names: the flat instance path is the unit hierarchy's
instance names joined by '/' (`g_row[3].g_col[0].g_data.u_pe/prod_q[14]_reg`). This script walks the block's subtree
in synth.py's netlists (netlist.json per unit), takes every sequential cell (the liberty's ff / latch cells: its output
pins), turns its path into the RTL name -- '/' -> '.', the `_reg` suffix dropped -- and looks it up:

  exact    the name is in the recording (the block was simulated at these parameters: the 16 x 8 core's blocks);
  folded   the routed unit is wider than the simulated one (the silicon requant's 32 columns against the 16 x 8 core's
           8, the NIC's 64 sources against the 2 x 2 mesh's 4): every bracket index n becomes n mod (the largest index
           the recording has at that position + 1), so column 13 takes column 5's activity -- a recorded activity of the
           same signal, not a default; also counted here, an unpacked array the synthesis flow flattened
           (host_if's `cfg [20]` of 16-bit entries: `cfg[100]_reg`) where the recording keeps the entries
           (`cfg[6][4]`): the last index split by the recorded entry width, then folded as above;
  default  no recorded name: the flop keeps OpenSTA's propagated activity (from the default input activity). Verilator
           leaves an unpacked array above its --trace-max-array (32 elements) out of the trace (prog_mem's 32 x 2
           instruction words): those flops stay at the default.

Writes <out>/activity.tcl -- set_power_activity commands (-activity: toggles per clock cycle of the window, applied at
the SDC's period; -duty: the high fraction, inverted for a QN output), the annotated pins grouped by value -- and
<out>/activity.json (the counts of each kind per unit, examples of the unmatched names, the recording's window and MAC
utilization). neo_report.tcl sources the Tcl file after the power report at the flow's default activity.

Usage: power.py --synth <synth.py --out> --unit <block> --activity <block>.json.gz --libs <liberty...> --out <dir>
"""
import argparse
import gzip
import json
import os
import re
import sys
import time


def seq_cells(libs):
    """{cell: {output pin: inverted?}} for the liberty's sequential cells (ff / latch groups)."""
    out = {}
    for lib in libs:
        txt = open(lib, errors="replace").read()
        for m in re.finditer(r"\n\s*cell\s*\(\s*\"?([A-Za-z0-9_]+)\"?\s*\)\s*\{", txt):
            name = m.group(1)
            start = m.end()
            depth, i = 1, start
            while depth and i < len(txt):
                c = txt[i]
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                i += 1
            body = txt[start:i]
            if not re.search(r"\n\s*(ff|latch|ff_bank)\s*\(", body):
                continue
            pins = {}
            for pm in re.finditer(r"\n\s*pin\s*\(\s*\"?([A-Za-z0-9_]+)\"?\s*\)\s*\{", body):
                pstart = pm.end()
                d, j = 1, pstart
                while d and j < len(body):
                    if body[j] == "{":
                        d += 1
                    elif body[j] == "}":
                        d -= 1
                    j += 1
                pb = body[pstart:j]
                if re.search(r"direction\s*:\s*\"?output", pb):
                    fn = re.search(r"function\s*:\s*\"([^\"]*)\"", pb)
                    pins[pm.group(1)] = bool(fn and ("!" in fn.group(1) or "'" in fn.group(1)))
            if pins:
                out[name] = pins
    return out


def top_module(j):
    return next(n for n, m in j["modules"].items() if str(m.get("attributes", {}).get("top", "")).strip("0") == "1")


def walk(synth_dir, results, unit, seq, prefix="", acc=None):
    """[(flat instance path, cell type, unit)] of every sequential cell under the unit."""
    acc = [] if acc is None else acc
    with open(os.path.join(synth_dir, unit, "netlist.json")) as f:
        j = json.load(f)
    mod = j["modules"][top_module(j)]
    kids = results[unit].get("children") or {}
    for cn, c in mod["cells"].items():
        ty = c["type"]
        name = cn.lstrip("\\")
        if ty in seq:
            acc.append((prefix + name, ty, unit))
        elif ty in kids:
            walk(synth_dir, results, ty, seq, prefix + name + "/", acc)
    return acc


def input_ports(synth_dir, unit):
    with open(os.path.join(synth_dir, unit, "netlist.json")) as f:
        j = json.load(f)
    out = []
    for pn, p in j["modules"][top_module(j)]["ports"].items():
        if p["direction"] == "input":
            w = len(p["bits"])
            out += [f"{pn}[{k}]" for k in range(w)] if w > 1 else [pn]
    return out


IDX = re.compile(r"\[(\d+)\]")


def shape(name):
    return IDX.sub("[#]", name)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synth", required=True)
    ap.add_argument("--unit", required=True)
    ap.add_argument("--activity", required=True)
    ap.add_argument("--libs", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    t0 = time.time()
    with gzip.open(args.activity, "rt") as f:
        rec = json.load(f)
    sig = rec["signals"]
    cycles = float(rec["window_cycles"])
    # the folding table: per name shape, the largest index at each bracket position
    maxi = {}
    for k in sig:
        idx = [int(x) for x in IDX.findall(k)]
        if not idx:
            continue
        sh = shape(k)
        cur = maxi.get(sh)
        maxi[sh] = idx if cur is None else [max(a, b) for a, b in zip(cur, idx)]

    def fold(name, idx, mx):
        it = iter(n % (m + 1) for n, m in zip(idx, mx))
        folded = IDX.sub(lambda _: f"[{next(it)}]", name)
        return sig.get(folded)

    def lookup(name):
        if name in sig:
            return sig[name], "exact"
        idx = [int(x) for x in IDX.findall(name)]
        if not idx:
            return None, "default"
        mx = maxi.get(shape(name))
        if mx is not None and len(mx) == len(idx):
            v = fold(name, idx, mx)
            if v is not None:
                return v, "folded"
        # a flattened unpacked array: cfg[100] against the recorded cfg[#][#] -> cfg[100 // w][100 % w], w the entry width
        k = name.rfind("[")
        base = name[:k]
        mx2 = maxi.get(shape(base) + "[#][#]")
        if mx2 is not None and len(mx2) == len(idx) + 1:
            w = mx2[-1] + 1
            split = f"{base}[{idx[-1] // w}][{idx[-1] % w}]"
            v = sig.get(split) or fold(split, idx[:-1] + [idx[-1] // w, idx[-1] % w], mx2)
            if v is not None:
                return v, "folded"
        return None, "default"

    with open(os.path.join(args.synth, "synth_results.json")) as f:
        results = json.load(f)["units"]
    seq = seq_cells(args.libs)
    flops = walk(args.synth, results, args.unit, seq)
    groups = {}                    # (activity, duty) -> [pins]
    count = {"exact": 0, "folded": 0, "default": 0}
    per_unit = {}
    unmatched = []
    for path, ty, unit in flops:
        rtl = path.replace("/", ".")
        if rtl.endswith("_reg"):
            rtl = rtl[:-4]
        v, kind = lookup(rtl)
        count[kind] += 1
        pu = per_unit.setdefault(unit, {"exact": 0, "folded": 0, "default": 0})
        pu[kind] += 1
        if v is None:
            if len(unmatched) < 40:
                unmatched.append(rtl)
            continue
        act = round(v[0] / cycles, 4)
        for pin, inv in seq[ty].items():
            duty = round(1.0 - v[1] if inv else v[1], 3)
            groups.setdefault((act, duty), []).append(f"{path}/{pin}")
    ins = input_ports(args.synth, args.unit)
    in_groups = {}
    in_count = {"exact": 0, "folded": 0, "default": 0}
    for pn in ins:
        if pn in ("clk", "rst_n"):
            continue
        v, kind = lookup(pn)
        in_count[kind] += 1
        if v is not None:
            in_groups.setdefault((round(v[0] / cycles, 4), round(v[1], 3)), []).append(pn)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "activity.tcl"), "w") as f:
        f.write(f"# {args.unit}: the recorded activity ({os.path.basename(args.activity)}: {rec.get('source', '')}; "
                f"{int(cycles)} cycles) on {count['exact'] + count['folded']} of {len(flops)} flops and "
                f"{in_count['exact'] + in_count['folded']} input bits (07_ci/pnr/power.py)\n")
        f.write("set neo_act_pins 0\nset neo_act_found 0\nset neo_act_ports 0\n")
        f.write("proc neo_act { pins act duty } {\n  global neo_act_pins neo_act_found\n  incr neo_act_pins [llength $pins]\n"
                "  set p [get_pins -quiet $pins]\n  incr neo_act_found [llength $p]\n"
                "  if { [llength $p] } { set_power_activity -pins $p -activity $act -duty $duty }\n}\n")
        f.write("proc neo_act_in { ports act duty } {\n  global neo_act_ports\n  set p [get_ports -quiet $ports]\n"
                "  incr neo_act_ports [llength $p]\n"
                "  if { [llength $p] } { set_power_activity -input_ports $p -activity $act -duty $duty }\n}\n")
        for (act, duty), pins in sorted(groups.items()):
            for k in range(0, len(pins), 500):
                f.write(f"neo_act {{{' '.join(pins[k:k + 500])}}} {act} {duty}\n")
        for (act, duty), ports in sorted(in_groups.items()):
            for k in range(0, len(ports), 500):
                f.write(f"neo_act_in {{{' '.join(ports[k:k + 500])}}} {act} {duty}\n")
    info = dict(unit=args.unit, activity=os.path.basename(args.activity), source=rec.get("source"), scope=rec.get("scope"),
                window_cycles=rec.get("window_cycles"), clock_ps=rec.get("clock_ps"), mac_util=rec.get("mac_util"),
                recorded_bits=len(sig), flops=len(flops), flops_by_kind=count, flops_per_unit=per_unit,
                input_bits=len(ins), inputs_by_kind=in_count, unmatched_examples=unmatched,
                pin_groups=len(groups), seconds=round(time.time() - t0, 1))
    with open(os.path.join(args.out, "activity.json"), "w") as f:
        json.dump(info, f, indent=1)
    print(f"power.py: {args.unit}: {len(flops)} flops -- {count['exact']} exact, {count['folded']} folded, "
          f"{count['default']} default; {len(ins)} input bits -- {in_count}; {info['seconds']} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
