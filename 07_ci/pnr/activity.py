#!/usr/bin/env python3
"""activity.py -- switching activity per block from a cocotb run's VCD (drop 0.43, stream A3).

Reads the VCD Verilator writes for a cocotb run (02_dv/dv/cocotb/Makefile TRACE_VCD=<file>; activity.sh hands it a named
pipe, so the dump is never on disk) and keeps, for every signal bit under each block's scope, its toggle count (TC)
and the time it was high (T1) -- what a SAIF file carries -- counted only while an enable signal is high (the window: the
core busy, the tile's DMA running a program), together with the clock cycles of that window and the MAC utilization
(the cycles the array's input valid is high). One JSON per block (gzip): `<block>.json.gz` with

  block, scope (the VCD scope of the block's instance), source (the run), window (enable signal, cycles, clock period),
  mac_util (when the run has an array), signals {name: [tc, t1]}: name the bit's path below the scope with every level
  joined by '.' (`u_dp.u_core.valid_in`, `g_row[3].g_col[0].g_data.u_pe.prod_q[14]`), tc its toggles in the window,
  t1 the fraction of the window it was high.

A block's scope may hold one `*` per bracket ("g_row[*].g_col[*].g_data.u_pe"): every matching instance is read and
the bits are averaged over the instances (mac_pe's activity as the mean of the array's data PEs). power.py maps the
names onto the routed block's flops (`<path>/<reg>[bit]_reg`) and input ports.

Usage: activity.py --vcd <file or fifo> --enable <scope.signal> --clock <scope.signal> [--util <scope.signal>]
                   --block <name>=<scope> [--block ...] --source "<what ran>" --out <dir>
"""
import argparse
import gzip
import json
import os
import re
import sys
import time


class Var:
    """One VCD variable we keep: per bit, toggles, accumulated high time and the window time at its last rise."""
    __slots__ = ("width", "lsb", "ranged", "val", "tc", "t1", "rise", "known")

    def __init__(self, width, lsb, ranged):
        self.width, self.lsb, self.ranged = width, lsb, ranged
        self.val = 0
        self.known = False
        self.tc = [0] * width
        self.t1 = [0] * width
        self.rise = [0] * width


def parse_value(s, width):
    """A VCD vector value (MSB first, possibly shorter than the width; x / z read as 0) as an int."""
    s = s.replace("x", "0").replace("X", "0").replace("z", "0").replace("Z", "0")
    return int(s, 2) if s else 0


def scope_rx(pattern):
    """A block scope with '*' for whole bracket indices as a regex over dotted scope paths."""
    return re.compile("^" + re.escape(pattern).replace(r"\*", r"[0-9]+") + "$")


def block_instance(scope, bscope, brx):
    """The instance path under which `scope` (a list of VCD scope names) lies for the block, or None: the block's scope
    itself or, with '*', the longest prefix of the path that matches it."""
    path = ".".join(scope)
    if brx is None:
        return bscope if (path == bscope or path.startswith(bscope + ".")) else None
    for k in range(len(scope), 0, -1):
        pre = ".".join(scope[:k])
        if brx.match(pre):
            return pre
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vcd", required=True)
    ap.add_argument("--enable", required=True, help="the window: statistics are kept while this 1-bit signal is high")
    ap.add_argument("--clock", required=True, help="the clock, for the window's cycle count and period")
    ap.add_argument("--util", default="", help="the array's input valid: its high fraction of the window = MAC utilization")
    ap.add_argument("--block", action="append", default=[], help="<block>=<scope> (a '*' bracket index averages instances)")
    ap.add_argument("--source", default="", help="what ran, for the record")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    t0 = time.time()
    blocks = []
    for b in args.block:
        name, scope = b.split("=", 1)
        blocks.append((name, scope, scope_rx(scope) if "*" in scope else None))

    # ---- header: the variables under the blocks' scopes, the enable, the clock and the utilization signal ----
    f = open(args.vcd, "r", errors="replace")
    scope, ids = [], {}            # id -> list of (block index, instance path, name, Var)
    special = {}                   # id -> role ('enable', 'clock', 'util')
    want = {args.enable: "enable", args.clock: "clock"}
    if args.util:
        want[args.util] = "util"
    vars_by_id = {}
    timescale_ps = 1.0
    line = f.readline()
    header = []
    while line:
        header.append(line)
        if "$enddefinitions" in line:
            break
        line = f.readline()
    toks = " ".join(header).split()
    i = 0
    while i < len(toks):
        t = toks[i]
        if t == "$timescale":
            j = toks.index("$end", i)
            ts = "".join(toks[i + 1:j])
            m = re.match(r"([0-9]+)\s*([munpf]?s)", ts)
            if m:
                timescale_ps = int(m.group(1)) * {"s": 1e12, "ms": 1e9, "us": 1e6, "ns": 1e3, "ps": 1.0, "fs": 1e-3}[m.group(2)]
            i = j + 1
        elif t == "$scope":
            # Verilator under cocotb writes the top scope with an empty name (`$scope module  $end`): it is TOP here,
            # the name Verilator gives it elsewhere (the scopes activity.sh passes start with it)
            scope.append("TOP" if toks[i + 2] == "$end" else toks[i + 2])
            i = toks.index("$end", i) + 1
        elif t == "$upscope":
            scope.pop()
            i = toks.index("$end", i) + 1
        elif t == "$var":
            j = toks.index("$end", i)
            width, vid, name = int(toks[i + 2]), toks[i + 3], toks[i + 4]
            rng = toks[i + 5] if j > i + 5 else ""
            lsb = 0
            m = re.match(r"\[(-?\d+)(?::(-?\d+))?\]", rng)
            if m:
                hi = int(m.group(1))
                lo = int(m.group(2)) if m.group(2) is not None else hi
                lsb = min(hi, lo)
            path = ".".join(scope)
            full = path + "." + name
            if full in want:
                special[vid] = want[full]
                if vid not in vars_by_id:
                    vars_by_id[vid] = Var(width, lsb, bool(m))
            for bi, (bname, bscope, brx) in enumerate(blocks):
                inst = block_instance(scope, bscope, brx)
                if inst is None:
                    continue
                rel = path[len(inst) + 1:] if len(path) > len(inst) else ""
                nm = (rel + "." if rel else "") + name
                if vid not in vars_by_id:
                    vars_by_id[vid] = Var(width, lsb, bool(m))
                ids.setdefault(vid, []).append((bi, inst, nm))
            i = j + 1
        else:
            i += 1
    missing = [s for s, r in want.items() if r not in special.values()]
    if missing:
        sys.exit(f"activity.py: not in the VCD: {missing}")
    print(f"activity.py: {len(vars_by_id)} variables kept of the VCD header, {sum(len(v) for v in ids.values())} block names; "
          f"timescale {timescale_ps} ps", flush=True)

    # ---- the value changes ----
    en_id = next(k for k, r in special.items() if r == "enable")
    clk_id = next(k for k, r in special.items() if r == "clock")
    util_id = next((k for k, r in special.items() if r == "util"), None)
    enabled = False
    e_acc, e_t = 0, 0              # window time accumulated up to e_t (the last enable change)
    now = 0
    clk_rises = 0
    util_t = 0
    util_rise = None
    first_window = last_window = None
    vget = vars_by_id.get
    nchanges = 0

    def window_time(t):
        return e_acc + (t - e_t if enabled else 0)

    def apply(vid, newv):
        nonlocal enabled, e_acc, e_t, clk_rises, util_t, util_rise, first_window, last_window
        v = vget(vid)
        if v is None:
            return
        old = v.val
        if not v.known:
            v.val, v.known = newv, True
            w = window_time(now)
            x = newv
            while x:
                b = x & -x
                v.rise[b.bit_length() - 1] = w
                x ^= b
            if vid in special and special[vid] == "enable" and newv & 1:
                enabled, e_t = True, now
                first_window = now if first_window is None else first_window
            return
        if old == newv:
            return
        role = special.get(vid)
        if role == "enable":
            if newv & 1 and not enabled:
                e_t, enabled = now, True
                first_window = now if first_window is None else first_window
            elif not (newv & 1) and enabled:
                e_acc += now - e_t
                e_t, enabled = now, False
                last_window = now
        w = window_time(now)
        if role == "clock" and enabled and (newv & 1) and not (old & 1):
            clk_rises += 1
        if role == "util":
            if newv & 1:
                util_rise = w
            elif util_rise is not None:
                util_t += w - util_rise
                util_rise = None
        x = old ^ newv
        tc, t1, rise = v.tc, v.t1, v.rise
        while x:
            b = x & -x
            k = b.bit_length() - 1
            if enabled:
                tc[k] += 1
            if newv & b:
                rise[k] = w
            else:
                t1[k] += w - rise[k]
            x ^= b
        v.val = newv

    for line in f:
        c = line[0] if line else ""
        if c == "#":
            now = int(line[1:])
            continue
        if c == "b" or c == "B":
            sp = line.find(" ")
            vid = line[sp + 1:].strip()
            if vid in vars_by_id:
                apply(vid, parse_value(line[1:sp], vars_by_id[vid].width))
                nchanges += 1
        elif c in "01xzXZ":
            vid = line[1:].strip()
            if vid in vars_by_id:
                apply(vid, 1 if c == "1" else 0)
                nchanges += 1
        elif c == "r":
            continue
    f.close()
    end_w = window_time(now)
    if enabled:
        last_window = now
    if util_rise is not None:
        util_t += end_w - util_rise
    # the bits still high at the end
    for v in vars_by_id.values():
        x = v.val
        while x:
            b = x & -x
            k = b.bit_length() - 1
            v.t1[k] += end_w - v.rise[k]
            x ^= b
    if end_w <= 0 or clk_rises == 0:
        sys.exit(f"activity.py: empty window (window time {end_w}, {clk_rises} clock rises)")
    # the window's time over its clock rises (the window can be several intervals: the first to the last rise would
    # count the gaps between them)
    period_ps = end_w * timescale_ps / clk_rises
    meta = dict(source=args.source, vcd=os.path.basename(args.vcd), enable=args.enable, clock=args.clock,
                window_cycles=clk_rises, window_ps=end_w * timescale_ps, clock_ps=period_ps,
                window_first_ps=(first_window or 0) * timescale_ps, window_last_ps=(last_window or now) * timescale_ps,
                mac_util=(util_t / end_w) if util_id else None, util_signal=args.util or None,
                value_changes=nchanges, seconds=round(time.time() - t0, 1),
                date=time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()))
    os.makedirs(args.out, exist_ok=True)
    for bi, (bname, bscope, _) in enumerate(blocks):
        acc = {}                   # name -> [sum tc, sum t1, n]
        insts = set()
        for vid, entries in ids.items():
            v = vars_by_id[vid]
            for (b, inst, nm) in entries:
                if b != bi:
                    continue
                insts.add(inst)
                for k in range(v.width):
                    key = f"{nm}[{k + v.lsb}]" if (v.width > 1 or v.ranged) else nm
                    a = acc.setdefault(key, [0.0, 0.0, 0])
                    a[0] += v.tc[k]
                    a[1] += v.t1[k] / end_w
                    a[2] += 1
        sig = {k: [round(a[0] / a[2], 3), round(a[1] / a[2], 4)] for k, a in sorted(acc.items())}
        rec = dict(meta, block=bname, scope=bscope, instances=len(insts), bits=len(sig), signals=sig)
        p = os.path.join(args.out, f"{bname}.json.gz")
        with gzip.open(p, "wt") as g:
            json.dump(rec, g, separators=(",", ":"))
        tot_tc = sum(s[0] for s in sig.values())
        print(f"activity.py: {bname}: {len(sig)} bits over {len(insts)} instance(s) of {bscope}, "
              f"mean {tot_tc / max(1, len(sig)) / clk_rises:.4f} toggles per bit per cycle -> {p}", flush=True)
    print(f"activity.py: window {clk_rises} cycles ({end_w * timescale_ps / 1000:.1f} ns, clock {period_ps} ps), "
          f"MAC utilization {meta['mac_util']}, {nchanges} value changes kept, {meta['seconds']} s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
