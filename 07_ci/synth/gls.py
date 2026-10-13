#!/usr/bin/env python3
"""gls.py -- gate-level simulation of synthesized units against their RTL (drop 0.43, stream D2).

The synthesis job's netlist of a unit (synth.py's <out>/<unit>/netlist.v: the unit's module mapped onto the ASAP7 cells,
the netlist OpenSTA times and the pnr job places) is simulated beside the RTL module at the same parameters, both driven
by the same random inputs every cycle, every output compared every cycle -- the check that the netlist is the RTL (no
synthesis bug: a wrong ABC mapping, a lost bit, a constant folded across a black box). The ASAP7 cells are the PDK's own
behavioural models (OpenROAD-flow-scripts platforms/asap7/verilog/stdcell, asap7sc7p5t_*_RVT_TT_*.v, unmodified; the
pinned ORFS commit of synth/tools.sh fetches them): UDP flops with timing checks, which Icarus 14 (the OSS CAD Suite's
iverilog) simulates in zero delay -- Icarus 12 does not drive the checks' delayed nets and Verilator takes no UDP tables.

The testbench is generated per unit from the RTL's ports (neosim elaborates the module at the unit's parameters): every
input random each cycle (seeded $random, drawn at the falling edge), rst_n low for the first 4 cycles and high after,
the outputs compared just before each falling edge from cycle `warmup` on. An unpacked RTL port is the netlist's flat
vector with element 0 in the top bits (slang's flattening); the testbench packs and unpacks. Per-unit input constraints
keep to the port contracts where an out-of-contract value is undefined behaviour of the RTL (crc16_beat's word count
n <= BW). Two kinds of difference are counted apart: a known bit that differs (a mismatch: the unit fails) and a netlist
X where the RTL has a known value (X pessimism of the cell models, or an unreset flop: reported with its first and last
cycle, not a failure once both settle).

  python3 synth/gls.py --synth synth_out --rtl rtl --cells <asap7 verilog dir> --out ci_logs/synth/gls [--units ...]
Writes <out>/gls_report.md, <out>/<unit>.log, <out>/tb_gls_<unit>.sv, <out>/gls.json; the renamed netlists and the
compiled simulations go to --work (default <synth>/gls), outside the published logs: requant's compiled image is over
GitHub's 100 MB file limit, which failed the synth job's push to ci-logs-synth. Exits 1 if a unit mismatches or does
not run."""
import argparse
import json
import os
import random
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_UNITS = ["mac_pe", "mac_pe_chk", "requant", "crc16_beat", "ecc39_enc", "ecc39_dec", "link_packer"]
CYCLES = 3000
WARMUP = 8


def rtl_ports(rtl, module, params, files):
    """([(name, dir, width, dims)], {names of the signed ports}) of the module at the parameters, from neosim's
    elaboration (dims: [size] or [])."""
    sim = os.path.normpath(os.path.join(rtl, "..", "sim"))
    for p in (sim, os.path.join(HERE, "..", "..", "02_dv", "sim")):
        if os.path.isfile(os.path.join(p, "neosim.py")) and p not in sys.path:
            sys.path.insert(0, p)
    import neosim
    d = neosim.Design(neosim.load([os.path.join(rtl, f) for f in files]))
    d.top_ports = {}
    d._elab_module(module, dict(params), "top", None, is_top=True)
    out, signed = [], set()
    for name, hier in d.top_ports.items():
        s = d.sigs[hier]
        out.append((name, s.is_port, s.width, [n for _, n in s.dims]))
        if getattr(s, "signed", False):
            signed.add(name)
    return out, signed


def netlist_ports(path):
    """{name: (dir, width)} from the netlist module's declarations"""
    ports = {}
    with open(path) as f:
        for line in f:
            m = re.match(r"\s*(input|output)\s+(?:\[(\d+):(\d+)\]\s+)?(\\?\S+?)\s*;", line)
            if m:
                w = abs(int(m.group(2)) - int(m.group(3))) + 1 if m.group(2) else 1
                ports[m.group(4)] = (m.group(1), w)
            elif re.match(r"\s*(wire|reg|assign|[A-Za-z0-9_]+_ASAP7)", line) and ports:
                pass
    return ports


def rand_expr(w, constraint=None):
    n = (w + 31) // 32
    e = "{" + ", ".join(["$random(seed)"] * n) + "}" if n > 1 else "$random(seed)"
    if constraint:
        return constraint
    return e


def gen_tb(unit, module, params, ports, nports, cycles, warmup, constraints, signed=()):
    """The testbench text, and the list of compared outputs. `signed`: the RTL ports declared signed -- an unpacked
    port's testbench array must have the port's element type, signedness included (Icarus: "Element types are not
    compatible in array assignment"; requant's in_acc / out_q)."""
    L = ["`timescale 1ns/1ps", f"module tb_gls_{unit};", "  integer seed;", "  reg clk = 1'b0;", "  always #0.5 clk = ~clk;",
         "  integer cyc, mism, xonly, xfirst, xlast, first_mism;"]
    ins = [p for p in ports if p[1] == "input"]
    outs = [p for p in ports if p[1] == "output"]
    has_clk = any(p[0] == "clk" for p in ins)
    for name, d, w, dims in ins:
        flat = w * (dims[0] if dims else 1)
        if name == "clk":
            L.append("  wire [0:0] i_clk = clk;")                 # the testbench's clock, to both instances
        else:
            L.append(f"  reg  [{flat - 1}:0] i_{name};")
    for name, d, w, dims in outs:
        flat = w * (dims[0] if dims else 1)
        L.append(f"  wire [{flat - 1}:0] r_{name}, g_{name};")
    # the RTL instance: unpacked ports through TB arrays, element 0 in the flat vector's top bits
    conns = []
    for name, d, w, dims in ins + outs:
        if dims:
            n = dims[0]
            L.append(f"  wire {'signed ' if name in signed else ''}[{w - 1}:0] a_{name} [{n}];")
            for i in range(n):
                lo = (n - 1 - i) * w
                if d == "input":
                    L.append(f"  assign a_{name}[{i}] = i_{name}[{lo + w - 1}:{lo}];")
                else:
                    L.append(f"  assign r_{name}[{lo + w - 1}:{lo}] = a_{name}[{i}];")
            conns.append(f".{name}(a_{name})")
        else:
            conns.append(f".{name}({'i_' if d == 'input' else 'r_'}{name})")
    pstr = ", ".join(f".{k}({v})" for k, v in params.items())
    L.append(f"  {module} {'#(' + pstr + ') ' if pstr else ''}u_rtl ({', '.join(conns)});")
    gconns = [f".{name}({'i_' if d == 'input' else 'g_'}{name})" for name, d, w, dims in ins + outs]
    L.append(f"  {unit}_gl u_gl ({', '.join(gconns)});")
    for name, d, w, dims in ins + outs:
        flat = w * (dims[0] if dims else 1)
        if name not in nports or nports[name][1] != flat:
            raise ValueError(f"{unit}: port {name} is {flat} bits in the RTL, {nports.get(name)} in the netlist")
    L.append("  task drive; begin")
    for name, d, w, dims in ins:
        if name == "clk":
            continue
        flat = w * (dims[0] if dims else 1)
        if name == "rst_n":
            L.append("    i_rst_n = (cyc >= 4);")
        else:
            L.append(f"    i_{name} = {rand_expr(flat, constraints.get(name))};")
    L.append("  end endtask")
    L.append("  task compare; begin")
    for name, d, w, dims in outs:
        L.append(f"    if (r_{name} !== g_{name}) begin")
        L.append(f"      if (known_diff(r_{name}, g_{name})) begin mism = mism + 1; if (first_mism < 0) first_mism = cyc;")
        L.append(f"        if (mism <= 5) $display(\"MISMATCH cycle %0d {name}: rtl %h gl %h\", cyc, r_{name}, g_{name}); end")
        L.append(f"      else begin xonly = xonly + 1; if (xfirst < 0) xfirst = cyc; xlast = cyc; end")
        L.append("    end")
    L.append("  end endtask")
    # a difference in a bit both sides know (the netlist's X where the RTL is known is X pessimism, counted apart)
    wmax = max(w * (dims[0] if dims else 1) for _, _, w, dims in outs)
    L.append(f"  function automatic known_diff(input [{wmax - 1}:0] a, input [{wmax - 1}:0] b);")
    L.append("    integer k; begin known_diff = 1'b0;")
    L.append(f"      for (k = 0; k < {wmax}; k = k + 1) if ((a[k] !== 1'bx) && (b[k] !== 1'bx) && (a[k] !== b[k])) known_diff = 1'b1;")
    L.append("    end endfunction")
    L.append("  initial begin")
    L.append(f"    seed = {random.Random(unit).randrange(1, 1 << 30)}; mism = 0; xonly = 0; xfirst = -1; xlast = -1; first_mism = -1;")
    L.append("    cyc = 0; drive;")
    L.append(f"    for (cyc = 0; cyc < {cycles}; cyc = cyc + 1) begin")
    if has_clk:
        L.append("      @(posedge clk); #0.4;")
    else:
        L.append("      #1;")
    L.append(f"      if (cyc >= {warmup}) compare;")
    if has_clk:
        L.append("      @(negedge clk);")
    L.append("      drive;")
    L.append("    end")
    L.append(f"    $display(\"GLS {unit}: %0d cycles compared from cycle {warmup}, %0d known-bit mismatches (first %0d), %0d netlist-X-only compares (cycles %0d..%0d)\", "
             f"{cycles} - {warmup}, mism, first_mism, xonly, xfirst, xlast);")
    # a pass needs no known-bit mismatch and the X-only compares over before the last half of the run (outputs that never
    # leave X compare as "equal" bit for bit and would pass vacuously)
    L.append(f"    if (mism == 0 && (xlast < {cycles // 2})) $display(\"RESULT: PASS\"); else $display(\"RESULT: FAIL\");")
    L.append("    $finish;")
    L.append("  end")
    L.append("endmodule")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synth", required=True, help="synth.py's output directory")
    ap.add_argument("--rtl", required=True)
    ap.add_argument("--cells", required=True, help="the directory of the ASAP7 behavioural models (asap7sc7p5t_*.v)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", default=None, help="the renamed netlists and the compiled simulations (default <synth>/gls)")
    ap.add_argument("--units", default=",".join(DEFAULT_UNITS))
    ap.add_argument("--cycles", type=int, default=CYCLES)
    ap.add_argument("--iverilog", default=os.environ.get("NEO_IVERILOG", "iverilog"))
    args = ap.parse_args()
    sys.path.insert(0, HERE)
    import synth as S
    with open(os.path.join(args.synth, "synth_results.json")) as f:
        results = json.load(f)["units"]
    units = {u["name"]: u for u in S.UNITS}
    cells = sorted(os.path.join(args.cells, f) for f in os.listdir(args.cells) if f.startswith("asap7sc7p5t_") and f.endswith(".v"))
    work = args.work or os.path.join(args.synth, "gls")
    os.makedirs(args.out, exist_ok=True)
    os.makedirs(work, exist_ok=True)
    vvp = os.path.join(os.path.dirname(args.iverilog), "vvp") if os.path.dirname(args.iverilog) else "vvp"
    rows, ok = [], True
    for unit in [u for u in args.units.split(",") if u]:
        r = results.get(unit)
        if not r or r.get("failed") or not os.path.isfile(os.path.join(args.synth, unit, "netlist.v")):
            rows.append(dict(unit=unit, result="NO NETLIST"))
            ok = False
            continue
        module, params = r.get("module", unit), r.get("params", {})
        if unit in units:
            module, params = units[unit]["module"], units[unit]["params"]
        # the leaf units' RTL: the one file that defines the module (they have no children)
        files = [f for f in S.RTL_FILES if os.path.isfile(os.path.join(args.rtl, f))
                 and re.search(rf"^\s*module\s+{re.escape(module)}\b", open(os.path.join(args.rtl, f)).read(), flags=re.M)]
        ports, signed = rtl_ports(args.rtl, module, params, files)
        nl = os.path.join(args.synth, unit, "netlist.v")
        with open(nl) as f:
            text = f.read()
        text = re.sub(rf"^module {re.escape(unit)}\(", f"module {unit}_gl(", text, flags=re.M)
        glv = os.path.join(work, f"{unit}_gl.v")
        with open(glv, "w") as f:
            f.write(text)
        cons = {}
        if module == "crc16_beat":
            bw = int(params.get("BW", 16))
            cons["n"] = f"($unsigned($random(seed)) % {bw + 1})"
        tb = gen_tb(unit, module, params, ports, netlist_ports(glv), args.cycles, WARMUP, cons, signed)
        tbv = os.path.join(args.out, f"tb_gls_{unit}.sv")
        with open(tbv, "w") as f:
            f.write(tb)
        log = os.path.join(args.out, f"{unit}.log")
        t0 = time.time()
        simv = os.path.join(work, f"{unit}.vvp")
        with open(log, "w") as lf:
            rc = subprocess.call([args.iverilog, "-g2012", "-o", simv, "-s", f"tb_gls_{unit}", tbv, glv]
                                 + [os.path.join(args.rtl, f) for f in files] + cells, stdout=lf, stderr=subprocess.STDOUT)
            if rc == 0:
                rc = subprocess.call([vvp, "-n", simv], stdout=lf, stderr=subprocess.STDOUT)
        out = open(log).read()
        m = re.search(r"GLS \S+: (\d+) cycles compared from cycle (\d+), (\d+) known-bit mismatches \(first (-?\d+)\), (\d+) netlist-X-only compares \(cycles (-?\d+)\.\.(-?\d+)\)", out)
        res = "PASS" if (rc == 0 and "RESULT: PASS" in out) else "FAIL"
        cellsn = r.get("own_cells") or r.get("cells")
        row = dict(unit=unit, module=module, params=params, cells=cellsn, result=res, seconds=round(time.time() - t0, 1))
        if m:
            row.update(compared=int(m.group(1)), mismatches=int(m.group(3)), first_mismatch=int(m.group(4)), x_only=int(m.group(5)),
                       x_first=int(m.group(6)), x_last=int(m.group(7)))
        rows.append(row)
        ok = ok and res == "PASS"
        print(f"gls {unit}: {res} {row.get('compared', '-')} cycles, {row.get('mismatches', '-')} mismatches, "
              f"{row.get('x_only', '-')} X-only compares, {row['seconds']} s")
    L = ["# Gate-level simulation of synthesized units against the RTL (`make gls`, the synth job)", "",
         "Each unit's netlist (ASAP7 cells, the PDK's behavioural models, Icarus 14 in zero delay) beside its RTL at the same "
         "parameters, the same random inputs each cycle, every output compared each cycle from cycle 8 (rst_n low in cycles "
         "0-3). A mismatch is a known bit that differs; an X-only compare is a netlist X where the RTL is known.", "",
         "| Unit | Module | Cells | Cycles compared | Mismatches | X-only compares (cycles) | Result |",
         "| --- | --- | ---: | ---: | ---: | --- | --- |"]
    for r in rows:
        xs = f"{r.get('x_only', '-')}" + (f" ({r['x_first']}..{r['x_last']})" if r.get("x_only") else "")
        L.append(f"| {r['unit']} | {r.get('module', '')} | {r.get('cells', '')} | {r.get('compared', '-')} | {r.get('mismatches', '-')} | {xs} | {r['result']} |")
    with open(os.path.join(args.out, "gls_report.md"), "w") as f:
        f.write("\n".join(L) + "\n")
    with open(os.path.join(args.out, "gls.json"), "w") as f:
        json.dump(rows, f, indent=1)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
