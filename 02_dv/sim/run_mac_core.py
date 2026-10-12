#!/usr/bin/env python3
"""
run_mac_core.py -- execute rtl/neo_mac_core.sv under neosim with the same stimulus
and checks as tb/tb_neo_mac_core.sv, against the golden vectors from
model/systolic_ref.py.

  T1  clean dataflow: every Y[m][j] and the check column equal the golden values,
      first valid_out exactly PE_LAT*ROWS+COLS cycles after the token, zero ABFT flags
  T2  single-MAC fault in PE(FAULT_ROW, FAULT_COL) while row FAULT_M passes through:
      exactly one output off by +1, abft_err on exactly that row, sticky set (drop 0.41: the verdict of a row arrives
      two cycles after its valid_out, with abft_err_valid; the k-th abft_err_valid is the k-th row)
  T3  abft_clear clears the sticky flag
  T4  double buffering: tile B's shadow weights load while tile A streams, B follows A with
      no drain, both tiles' outputs correct, zero ABFT flags
  NCHK = 4 (drop 0.46: the sparse-capable core; VEC_NCHK in params.vh): T1-T4 present the dense rows in bytes
  0..ROWS-1 of the 2*ROWS-byte row, with index 0 and the check weight in column 0; then
  T6  a 2:4 sparse tile (64 input channels at 32 rows) with sparse = 1: every output and the summed check columns
      equal the model's dense arithmetic on the pruned weights, zero ABFT flags
  T7  (a) the MAC fault of T2 while PE(FAULT_ROW, FAULT_COL) holds index 0, 1, 2, 3 (the model's four weight sets):
      one output off by +1, exactly that row flagged; (b) check column k's weight in row FAULT_ROW off by one, k = 0..3:
      data untouched, exactly the rows whose channel 4*(FAULT_ROW/2) + k is nonzero flagged

Usage: python3 sim/run_mac_core.py [--vectors vectors]
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from neosim import Design, load  # noqa: E402

RTL = ["delay_line.sv", "mac_pe.sv", "skew_in.sv", "deskew_out.sv",
       "systolic_array.sv", "abft_checker.sv", "neo_mac_core.sv"]


def read_hex(path, bits):
    vals = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                v = int(line, 16) & ((1 << bits) - 1)
                vals.append(v - (1 << bits) if v >= (1 << (bits - 1)) else v)
    return vals


def read_params(path):
    p = {}
    with open(path) as f:
        for line in f:
            parts = line.split()
            if len(parts) == 3 and parts[0] == "`define":
                p[parts[1]] = int(parts[2])
    return p


def to_bits(v, w):
    return v & ((1 << w) - 1)


def from_bits(v, w):
    return v - (1 << w) if v >= (1 << (w - 1)) else v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vectors", default=os.path.join(os.path.dirname(__file__), "..", "vectors"))
    a = ap.parse_args()
    vec = a.vectors
    prm = read_params(os.path.join(vec, "params.vh"))
    R, C, M = prm["VEC_ROWS"], prm["VEC_COLS"], prm["VEC_M"]
    FR, FC, MF = prm["VEC_FAULT_ROW"], prm["VEC_FAULT_COL"], prm["VEC_FAULT_M"]
    XW, WW, PW = 8, 8, 32
    WCW = WW + (0 if R <= 1 else (R - 1).bit_length()) + 1
    PE_LAT = prm.get("VEC_PE_LAT", 2)
    LAT = PE_LAT * R + C
    NCHK = prm.get("VEC_NCHK", 1)
    XB = 2 * R if NCHK > 1 else R

    X = read_hex(os.path.join(vec, "x.hex"), 8)
    W = read_hex(os.path.join(vec, "w.hex"), 8)
    WC = read_hex(os.path.join(vec, "wc.hex"), 16)
    Y = read_hex(os.path.join(vec, "y_exp.hex"), 32)
    YC = read_hex(os.path.join(vec, "yc_exp.hex"), 32)
    if NCHK > 1:
        XS = read_hex(os.path.join(vec, "x_sp.hex"), 8)
        WS = read_hex(os.path.join(vec, "w_sp.hex"), 8)
        IS = [v & 3 for v in read_hex(os.path.join(vec, "i_sp.hex"), 4)]
        WCS = read_hex(os.path.join(vec, "wc_sp.hex"), 16)
        YS = read_hex(os.path.join(vec, "y_sp_exp.hex"), 32)
        YCS = read_hex(os.path.join(vec, "yc_sp_exp.hex"), 32)
        W7 = read_hex(os.path.join(vec, "w_t7.hex"), 8)
        I7 = [v & 3 for v in read_hex(os.path.join(vec, "i_t7.hex"), 4)]
        WC7 = read_hex(os.path.join(vec, "wc_t7.hex"), 16)
        Y7 = read_hex(os.path.join(vec, "y_t7_exp.hex"), 32)

    rtl_dir = os.path.join(os.path.dirname(__file__), "..", "rtl")
    t0 = time.time()
    design = Design(load([os.path.join(rtl_dir, f) for f in RTL]))
    design.elaborate("neo_mac_core", {"ROWS": R, "COLS": C, "XW": XW, "WW": WW, "PW": PW, "NCHK": NCHK,
                                      "FAULT_ROW": FR, "FAULT_COL": FC})
    t_elab = time.time() - t0
    print(f"neosim: elaborated neo_mac_core ROWS={R} COLS={C} (+{NCHK} ABFT column{'s' if NCHK > 1 else ''}) M={M}: "
          f"{len(design.cells)} cells, {len(design.comb_nodes)} comb nodes, {len(design.ff_nodes)} always_ff "
          f"blocks, {t_elab:.1f} s")

    # port cells
    clk = design.cell_of("clk")
    rst_n = design.cell_of("rst_n")
    w_load = design.cell_of("w_load")
    w_in = design.array_of("w_in")
    wc_in = design.cell_of("wc_in")
    x_in = design.cell_of("x_in")                 # drop 0.46: the packed row, byte c at [8c +: 8]
    valid_in = design.cell_of("valid_in")
    swap_in = design.cell_of("swap_in")
    sparse = design.cell_of("sparse")
    y = design.array_of("y")
    y_chk = design.cell_of("y_chk")
    valid_out = design.cell_of("valid_out")
    abft_clear = design.cell_of("abft_clear")
    abft_err = design.cell_of("abft_err")
    abft_err_valid = design.cell_of("abft_err_valid")
    abft_err_sticky = design.cell_of("abft_err_sticky")
    fault_inject = design.cell_of("fault_inject")

    def idle():
        w_load.v = 0
        wc_in.v = 0
        valid_in.v = 0
        swap_in.v = 0
        sparse.v = 0
        fault_inject.v = 0
        abft_clear.v = 0
        for c in w_in:
            c.v = 0
        x_in.v = 0

    def drive_weight_row(Wflat, WCflat, t, Iflat=None, nwc=1):
        """Present shadow-chain row ROWS-1-t of a tile (flat W [R*C], indices I [R*C] (default 0), WC [R*nwc]: nwc check
        weights per row, column k of row r at r*nwc + k; the check columns above them get zeros)."""
        r = R - 1 - t
        w_load.v = 1
        for j in range(C):
            idx = Iflat[r * C + j] if Iflat is not None else 0
            w_in[j].v = to_bits(Wflat[r * C + j], WW) | ((idx << WW) if NCHK > 1 else 0)
        wc_in.v = sum(to_bits(WCflat[r * nwc + k], WCW) << (k * WCW) for k in range(nwc))

    def load_weights(Wflat=None, WCflat=None, Iflat=None, nwc=1):
        Wflat = W if Wflat is None else Wflat
        WCflat = WC if WCflat is None else WCflat
        for t in range(R):
            drive_weight_row(Wflat, WCflat, t, Iflat, nwc)
            design.tick()
        w_load.v = 0
        for c in w_in:
            c.v = 0
        wc_in.v = 0
        design.tick()

    def stream(Xrows=None, fault_cycle=-1, load_during=None, drain=True, sp=0):
        """Tick 0: swap token. Ticks 1..M: unskewed rows. load_during=(Wflat, WCflat) shifts a new
        shadow tile in from tick LAT (the double-buffering rule). sp = 1: Xrows are 2*ROWS-byte sparse rows presented
        with sparse = 1 (dense rows fill bytes 0..ROWS-1). Returns (outputs, err flags, first-valid tick)."""
        Xrows = X if Xrows is None else Xrows
        rb = 2 * R if sp else R                       # bytes per row of Xrows
        Mr = len(Xrows) // rb
        outs, errs, first = [], [], None
        ticks = Mr + 1 + (LAT + 2 if drain else 0)
        sparse.v = sp
        for s in range(ticks):
            m = s - 1
            swap_in.v = 1 if s == 0 else 0
            valid_in.v = 1 if 0 <= m < Mr else 0
            fault_inject.v = 1 if s == fault_cycle else 0
            x_in.v = sum(to_bits(Xrows[m * rb + i], XW) << (XW * i) for i in range(rb)) if 0 <= m < Mr else 0
            if load_during is not None and LAT <= s < LAT + R:
                drive_weight_row(load_during[0], load_during[1], s - LAT)
            else:
                w_load.v = 0
            design.tick()
            if valid_out.v:
                if first is None:
                    first = s
                outs.append([from_bits(c.v, PW) for c in y] + [from_bits(y_chk.v, PW)])
            if abft_err_valid.v:                  # the verdict of the row valid_out showed two cycles before
                errs.append(abft_err.v)
        swap_in.v = 0
        valid_in.v = 0
        fault_inject.v = 0
        w_load.v = 0
        if drain:
            design.tick()
        return outs, errs, first

    def compare(outs, Yx=None, YCx=None):
        """Mismatches against Yx (default the dense Y) and the check sums YCx (default YC; False: not compared)."""
        Yx = Y if Yx is None else Yx
        YCx = YC if YCx is None else YCx
        mism = []
        for m, row in enumerate(outs):
            if m >= M:
                mism.append((m, -1, None))
                continue
            for j in range(C):
                if row[j] != Yx[m * C + j]:
                    mism.append((m, j, row[j] - Yx[m * C + j]))
            if YCx is not False and row[C] != YCx[m]:
                mism.append((m, C, row[C] - YCx[m]))
        return mism

    fails = 0
    idle()
    rst_n.v = 0
    for _ in range(3):
        design.tick()
    rst_n.v = 1
    design.tick()
    load_weights()

    # ---- T1 ----
    t1 = time.time()
    outs, errs, first = stream()
    mism = compare(outs)
    ok = len(outs) == M and len(errs) == M and not mism and sum(errs) == 0 and abft_err_sticky.v == 0
    lat_ok = first == LAT  # token at tick 0, row 0 at tick 1, visible after tick LAT
    print(f"  T1 dataflow  : {len(outs)} rows x {C} outputs + check column vs golden, "
          f"{len(mism)} mismatches, {sum(errs)} ABFT flags, sticky={abft_err_sticky.v} -> {'PASS' if ok else 'FAIL'}")
    print(f"  T1 latency   : first valid_out after tick {first} (token at 0, row 0 at 1), expected PE_LAT*ROWS+COLS = {LAT} -> {'PASS' if lat_ok else 'FAIL'}")
    if mism:
        print("     first mismatches:", mism[:5])
    fails += (not ok) + (not lat_ok)

    # ---- T2 ----
    cyc = 1 + MF + PE_LAT * FR + FC
    outs, errs, _ = stream(fault_cycle=cyc)
    mism = compare(outs)
    hit = len(mism) == 1 and mism[0] == (MF, FC, 1)
    flagged = len(errs) == M and sum(errs) == 1 and errs[MF] == 1
    sticky = abft_err_sticky.v == 1
    print(f"  T2 MAC fault : PE({FR},{FC}) fault at stream cycle {cyc}: mismatches={mism}, "
          f"flags on rows {[m for m, e in enumerate(errs) if e]}, sticky={abft_err_sticky.v} -> "
          f"{'PASS' if hit and flagged and sticky else 'FAIL'}")
    fails += not (hit and flagged and sticky)

    # ---- T3 ----
    abft_clear.v = 1
    design.tick()
    abft_clear.v = 0
    design.tick()
    print(f"  T3 clear     : abft_err_sticky after clear = {abft_err_sticky.v} -> {'PASS' if abft_err_sticky.v == 0 else 'FAIL'}")
    fails += abft_err_sticky.v != 0

    # ---- T4: double buffering on the RTL ----
    import numpy as np
    rng = np.random.default_rng(11)
    MA = max(M, LAT + R + 2)
    XA = rng.integers(-128, 128, size=(MA * R,), dtype=np.int64).tolist()
    W2 = rng.integers(-128, 128, size=(R, C), dtype=np.int64)
    WC2 = W2.sum(axis=1).tolist()
    W2flat = W2.ravel().tolist()
    outsA, errsA, _ = stream(XA, load_during=(W2flat, WC2), drain=False)   # A streams, B's shadow loads
    outsB, errsB, _ = stream(X, drain=True)                                # B's token right after A's last row
    outs = outsA + outsB
    XAm = np.array(XA).reshape(MA, R)
    Wm = np.array(W).reshape(R, C)
    Xm = np.array(X).reshape(M, R)
    expA = XAm @ Wm
    expB = Xm @ W2
    okA = len(outs) >= MA and all(outs[m][:C] == expA[m].tolist() for m in range(MA))
    okB = len(outs) == MA + M and all(outs[MA + m][:C] == expB[m].tolist() and outs[MA + m][C] == int(Xm[m] @ np.array(WC2)) for m in range(M))
    ok4 = okA and okB and len(errsA) + len(errsB) == len(outs) and sum(errsA) + sum(errsB) == 0
    print(f"  T4 double-buf: tile A ({MA} rows, W) with W2 loading from tick {LAT}, tile B ({M} rows, W2) with no drain: "
          f"{len(outs)} rows out, A {'ok' if okA else 'BAD'}, B {'ok' if okB else 'BAD'}, flags {sum(errsA) + sum(errsB)} -> {'PASS' if ok4 else 'FAIL'}")
    fails += not ok4

    nclk = 2 * (M + LAT + 4) + MA + M + LAT + 6 + R + 7
    if NCHK > 1:
        # ---- T6: the 2:4 sparse dataflow ----
        load_weights(WS, WCS, IS, nwc=4)
        outs, errs, first = stream(XS, sp=1)
        mism = compare(outs, YS, YCS)
        ok6 = len(outs) == M and len(errs) == M and not mism and sum(errs) == 0 and first == LAT
        print(f"  T6 sparse    : {len(outs)} rows of {2 * R} channels (2:4 on {R} rows) x {C} outputs + summed check columns vs golden, "
              f"{len(mism)} mismatches, {sum(errs)} ABFT flags, first valid after tick {first} -> {'PASS' if ok6 else 'FAIL'}")
        if mism:
            print("     first mismatches:", mism[:5])
        fails += not ok6
        # ---- T7a: the MAC fault while PE(FR, FC) holds index 0..3 ----
        for k in range(4):
            abft_clear.v = 1
            design.tick()
            abft_clear.v = 0
            load_weights(W7[k * R * C:(k + 1) * R * C], WC7[k * R * 4:(k + 1) * R * 4], I7[k * R * C:(k + 1) * R * C], nwc=4)
            Yk = Y7[k * M * C:(k + 1) * M * C]
            YCk = [from_bits(sum(Yk[m * C:(m + 1) * C]), PW) for m in range(M)]
            outs, errs, _ = stream(XS, fault_cycle=cyc, sp=1)
            mism = compare(outs, Yk, YCk)
            hit = len(mism) == 1 and mism[0] == (MF, FC, 1)
            flagged = len(errs) == M and sum(errs) == 1 and errs[MF] == 1
            print(f"  T7 index {k}   : PE({FR},{FC}) holding index {k} (check column {k} covers it), MAC fault: mismatches={mism}, "
                  f"flags on rows {[m for m, e in enumerate(errs) if e]} -> {'PASS' if hit and flagged else 'FAIL'}")
            fails += not (hit and flagged)
        # ---- T7b: check column k's weight in row FR off by one ----
        for k in range(4):
            abft_clear.v = 1
            design.tick()
            abft_clear.v = 0
            wcb = list(WCS)
            wcb[FR * 4 + k] += 1
            load_weights(WS, wcb, IS, nwc=4)
            outs, errs, _ = stream(XS, sp=1)
            mism = compare(outs, YS, False)
            expect = [1 if XS[m * 2 * R + 4 * (FR // 2) + k] != 0 else 0 for m in range(M)]
            ok = len(outs) == M and not mism and errs == expect
            print(f"  T7 check {k}   : check weight of column {k} in row {FR} off by one: {len(mism)} data mismatches, "
                  f"{sum(errs)} rows flagged (expect the {sum(expect)} whose channel {4 * (FR // 2) + k} is nonzero) -> {'PASS' if ok else 'FAIL'}")
            fails += not ok
        nclk += 9 * (R + 2 + M + LAT + 3)
    print(f"  simulated {nclk} clocks in {time.time() - t1:.1f} s")
    print(f"RESULT: {'ALL PASS' if fails == 0 else f'{fails} FAILURES'}")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
