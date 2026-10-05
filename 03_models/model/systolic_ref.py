#!/usr/bin/env python3
"""
neo_mac_core -- cycle-accurate Python reference model (v0.3, 2026-10-04)

Mirrors rtl/neo_mac_core.sv register for register:
  skew_in  -> systolic_array (ROWS x (COLS+1), weight-stationary, 2-stage pipelined PE,
              double-buffered weights with a swap token travelling with the data)
           -> deskew_out -> abft_checker, plus a valid delay line.

Column COLS is the ABFT check column: its weight in row i is sum_j W[i][j], so
for every activation row the check output must equal the sum of the data
outputs. Any single-point fault in a MAC, a stored weight, or the psum chain
breaks that equality and is flagged within the same output row.

Three things this script does:
  1. proves the dataflow: Y == X @ W for random INT8 data, and measures the
     latency (expected ROWS + COLS cycles from unskewed input to aligned output)
  2. proves the ABFT checker catches (a) a MAC fault in a data PE, (b) a MAC
     fault in the check column, (c) a corrupted stored weight
  3. writes the hex vectors tb/tb_neo_mac_core.sv reads with $readmemh

Usage: python3 systolic_ref.py --rows 32 --cols 32 --m 64 --seed 1 --out ../vectors
"""
import argparse
import os
import numpy as np

PW = 32      # partial-sum width in the RTL
PE_LAT = 2   # psum latency of mac_pe (stage 1 product, stage 2 add); row skew = PE_LAT per row


def wrap(v, bits):
    """Two's-complement wrap to `bits` bits (mirrors RTL truncation)."""
    v = np.asarray(v, dtype=np.int64)
    mask = (1 << bits) - 1
    v = v & mask
    return np.where(v >= (1 << (bits - 1)), v - (1 << bits), v)


class MacCore:
    """Register-level model of neo_mac_core. One call to step() is one clock."""

    def __init__(self, rows, cols, fault_row=None, fault_col=None):
        self.R, self.C = rows, cols
        self.CT = cols + 1                      # data columns + ABFT check column
        self.LAT = PE_LAT * rows + self.CT - 1  # unskewed input -> aligned output
        self.fault_row, self.fault_col = fault_row, fault_col
        # systolic array state
        self.W = np.zeros((rows, self.CT), dtype=np.int64)   # active (stationary) weights
        self.Wsh = np.zeros((rows, self.CT), dtype=np.int64) # shadow weights (shift chain)
        self.Xq = np.zeros((rows, self.CT), dtype=np.int64)  # registered x (flows right)
        self.Sq = np.zeros((rows, self.CT), dtype=np.int64)  # registered swap token (flows right)
        self.Pq = np.zeros((rows, self.CT), dtype=np.int64)  # registered psum (flows down), stage 2
        self.ProdQ = np.zeros((rows, self.CT), dtype=np.int64)  # stage 1: product
        self.PinQ = np.zeros((rows, self.CT), dtype=np.int64)   # stage 1: delayed incoming psum
        self.InjQ = np.zeros((rows, self.CT), dtype=np.int64)   # stage 1: delayed fault hook
        # skew_in: row i delayed by i*PE_LAT cycles (data and token)
        self.skew = [np.zeros(i * PE_LAT, dtype=np.int64) for i in range(rows)]
        self.skew_s = [np.zeros(i * PE_LAT, dtype=np.int64) for i in range(rows)]
        # deskew_out: column j delayed by CT-1-j cycles
        self.deskew = [np.zeros(self.CT - 1 - j, dtype=np.int64) for j in range(self.CT)]
        self.valid_pipe = np.zeros(self.LAT, dtype=np.int64)
        self.err_sticky = 0

    def step(self, w_load, w_in, x_vec, valid_in, fault, token=0):
        """w_in: CT weights entering the top of each column's shadow chain (used when w_load)
           x_vec: ROWS unskewed activations; token: swap token (one cycle before a tile's row 0);
           fault: assert the fault_inject pin."""
        R, CT = self.R, self.CT
        # ---- skew_in (registers): data and token ----
        x_row = np.zeros(R, dtype=np.int64)
        s_row = np.zeros(R, dtype=np.int64)
        for i in range(R):
            if i == 0:
                x_row[i] = x_vec[0]
                s_row[i] = token
            else:
                x_row[i] = self.skew[i][-1]
                self.skew[i] = np.concatenate(([x_vec[i]], self.skew[i][:-1]))
                s_row[i] = self.skew_s[i][-1]
                self.skew_s[i] = np.concatenate(([token], self.skew_s[i][:-1]))
        # ---- bottom-of-array register outputs as visible THIS cycle (pre-edge) ----
        bottom = self.Pq[R - 1, :].copy()
        # ---- systolic array: compute next state from current state ----
        x_pe = np.empty((R, CT), dtype=np.int64)
        x_pe[:, 0] = x_row
        x_pe[:, 1:] = self.Xq[:, :-1]
        s_pe = np.empty((R, CT), dtype=np.int64)
        s_pe[:, 0] = s_row
        s_pe[:, 1:] = self.Sq[:, :-1]
        p_pe = np.zeros((R, CT), dtype=np.int64)
        p_pe[1:, :] = self.Pq[:-1, :]
        w_src = np.empty((R, CT), dtype=np.int64)
        w_src[0, :] = w_in
        w_src[1:, :] = self.Wsh[:-1, :]
        inj = np.zeros((R, CT), dtype=np.int64)
        if fault and self.fault_row is not None:
            inj[self.fault_row, self.fault_col] = 1
        P_next = wrap(self.PinQ + self.ProdQ + self.InjQ, PW)   # stage 2: add (previous stage-1 values)
        Prod_next = x_pe * self.W                                # stage 1: product uses the ACTIVE weight
        W_next = np.where(s_pe == 1, self.Wsh, self.W)           # token adopts the (pre-shift) shadow
        if w_load:
            self.Wsh = w_src.copy()
        self.W = W_next
        self.Xq = x_pe
        self.Sq = s_pe
        self.PinQ = p_pe
        self.ProdQ = Prod_next
        self.InjQ = inj
        self.Pq = P_next
        # ---- deskew_out (registers) ----
        y = np.zeros(CT, dtype=np.int64)
        for j in range(CT):
            d = self.deskew[j]
            if d.size == 0:
                y[j] = bottom[j]
            else:
                y[j] = d[-1]
                self.deskew[j] = np.concatenate(([bottom[j]], d[:-1]))
        # ---- valid delay line ----
        valid_out = self.valid_pipe[-1]
        self.valid_pipe = np.concatenate(([valid_in], self.valid_pipe[:-1]))
        # ---- abft_checker (combinational on aligned outputs) ----
        err = int(valid_out == 1 and int(wrap(y[:self.C].sum(), PW)) != int(y[self.C]))
        self.err_sticky |= err
        return y, valid_out, err

    # -- convenience drivers -------------------------------------------------
    def load_weights(self, W, Wc):
        """Shift W (ROWS x COLS) and the check column Wc (ROWS) into the SHADOW chain from the
        top: at cycle t feed row ROWS-1-t, so that after ROWS cycles shadow(i,j) holds W[i][j].
        The weights become active only where the swap token has passed (see run)."""
        Wt = np.concatenate([W, Wc[:, None]], axis=1)
        for t in range(self.R):
            self.step(1, Wt[self.R - 1 - t, :], np.zeros(self.R, dtype=np.int64), 0, 0)
        assert np.array_equal(self.Wsh, Wt), "shadow weight load failed"

    def run(self, X, fault_cycles=(), load_during=None):
        """Step 0: swap token. Steps 1..M: rows of X (unskewed). Collect aligned outputs.
        load_during = (W, Wc): shift a new shadow tile in, starting LAT steps after the
        token (the double-buffering rule), while this tile streams.
        Returns Y (M x CT), err flags per output row, and the step of the first valid output."""
        M = X.shape[0]
        total = M + 1 + self.LAT + 2
        outs, errs, first_valid = [], [], None
        Wt = None
        if load_during is not None:
            Wt = np.concatenate([load_during[0], load_during[1][:, None]], axis=1)
        for t in range(total):
            token = 1 if t == 0 else 0
            m = t - 1
            x_vec = X[m] if 0 <= m < M else np.zeros(self.R, dtype=np.int64)
            w_load, w_in = 0, np.zeros(self.CT, dtype=np.int64)
            if Wt is not None and self.LAT <= t < self.LAT + self.R:
                w_load, w_in = 1, Wt[self.R - 1 - (t - self.LAT), :]
            y, v, e = self.step(w_load, w_in, x_vec, 1 if 0 <= m < M else 0,
                                1 if t in fault_cycles else 0, token)
            if v:
                if first_valid is None:
                    first_valid = t
                outs.append(y.copy())
                errs.append(e)
        return np.array(outs), np.array(errs), first_valid


def write_hex(path, values, digits):
    mask = (1 << (4 * digits)) - 1
    with open(path, "w") as f:
        for v in np.asarray(values).ravel():
            f.write(f"{int(v) & mask:0{digits}x}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=32)
    ap.add_argument("--cols", type=int, default=32)
    ap.add_argument("--m", type=int, default=64)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--fault-row", type=int, default=5)
    ap.add_argument("--fault-col", type=int, default=7)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "vectors"))
    a = ap.parse_args()
    R, C, M = a.rows, a.cols, a.m
    rng = np.random.default_rng(a.seed)
    X = rng.integers(-128, 128, size=(M, R), dtype=np.int64)
    W = rng.integers(-128, 128, size=(R, C), dtype=np.int64)
    Wc = W.sum(axis=1)
    Y = X @ W
    Yc = X @ Wc
    wcw = 8 + int(np.ceil(np.log2(R))) + 1
    assert np.all(np.abs(Wc) < 2 ** (wcw - 1)), "check weights exceed RTL width"
    assert np.array_equal(Y.sum(axis=1), Yc), "ABFT identity broken (should be impossible)"
    assert np.all(np.abs(Y) < 2 ** 31) and np.all(np.abs(Yc) < 2 ** 31)

    print(f"neo_mac_core reference model: ROWS={R} COLS={C} (+1 ABFT column), M={M} rows, seed={a.seed}")
    fails = 0

    # Test 1: dataflow and latency
    core = MacCore(R, C, a.fault_row, a.fault_col)
    core.load_weights(W, Wc)
    Yo, errs, first = core.run(X)
    ok = Yo.shape[0] == M and np.array_equal(Yo[:, :C], Y) and np.array_equal(Yo[:, C], Yc)
    lat_ok = first == core.LAT + 1
    print(f"  T1 dataflow  : Y == X@W for {M}x{C} outputs and check column -> {'PASS' if ok else 'FAIL'}")
    print(f"  T1 latency   : first valid output at step {first} (token at 0, row 0 at 1), expected PE_LAT*ROWS+COLS+1 = {core.LAT + 1} -> {'PASS' if lat_ok else 'FAIL'}")
    print(f"  T1 ABFT      : {int(errs.sum())} rows flagged on clean data (expect 0) -> {'PASS' if errs.sum() == 0 else 'FAIL'}")
    fails += (not ok) + (not lat_ok) + (errs.sum() != 0)

    # Test 2: MAC fault in a data PE while output row m_f passes through it
    m_f = 10
    cyc = 1 + m_f + PE_LAT * a.fault_row + a.fault_col  # step in which PE(fr,fc) processes X[m_f]
    core = MacCore(R, C, a.fault_row, a.fault_col)
    core.load_weights(W, Wc)
    Yo, errs, _ = core.run(X, fault_cycles=(cyc,))
    diff = Yo[:, :C] - Y
    hit = (diff[m_f, a.fault_col] == 1) and (np.count_nonzero(diff) == 1)
    flagged = errs[m_f] == 1 and errs.sum() == 1
    print(f"  T2 data-PE fault at PE({a.fault_row},{a.fault_col}), cycle {cyc}: output ({m_f},{a.fault_col}) off by +1 only -> {'PASS' if hit else 'FAIL'}; "
          f"ABFT flags exactly row {m_f} -> {'PASS' if flagged else 'FAIL'}")
    fails += (not hit) + (not flagged)

    # Test 3: fault in the check column itself must also be flagged
    core = MacCore(R, C, a.fault_row, C)
    core.load_weights(W, Wc)
    cyc3 = 1 + m_f + PE_LAT * a.fault_row + C
    Yo, errs, _ = core.run(X, fault_cycles=(cyc3,))
    ok3 = np.array_equal(Yo[:, :C], Y) and errs[m_f] == 1 and errs.sum() == 1
    print(f"  T3 check-column fault: data untouched, ABFT flags exactly row {m_f} -> {'PASS' if ok3 else 'FAIL'}")
    fails += (not ok3)

    # Test 4: a corrupted stored weight (bit flip) must flag every row
    core = MacCore(R, C)
    Wbad = W.copy()
    Wbad[3, 4] ^= 0x10
    core.load_weights(Wbad, Wc)                 # check weights precomputed from the correct W
    Yo, errs, _ = core.run(X)
    ok4 = errs.sum() == M
    print(f"  T4 weight bit-flip at W[3][4]: ABFT flags {int(errs.sum())}/{M} rows (expect {M}) -> {'PASS' if ok4 else 'FAIL'}")
    fails += (not ok4)

    # Test 5: double buffering -- tile B's weights load while tile A streams; B follows A with no drain
    W2 = rng.integers(-128, 128, size=(R, C), dtype=np.int64)
    Wc2 = W2.sum(axis=1)
    core = MacCore(R, C)
    core.load_weights(W, Wc)
    MA = max(M, core.LAT + R + 2)              # tile A long enough to hide the shadow load
    XA = rng.integers(-128, 128, size=(MA, R), dtype=np.int64)
    # run A with B's shadow load overlapped; feed B's token+rows immediately after A's last row
    total = MA + 1
    outsA, errsA = [], []
    for t in range(total):
        token = 1 if t == 0 else 0
        m = t - 1
        w_load, w_in = 0, np.zeros(core.CT, dtype=np.int64)
        Wt2 = np.concatenate([W2, Wc2[:, None]], axis=1)
        if core.LAT <= t < core.LAT + R:
            w_load, w_in = 1, Wt2[R - 1 - (t - core.LAT), :]
        y, v, e = core.step(w_load, w_in, XA[m] if m >= 0 else np.zeros(R, dtype=np.int64), 1 if m >= 0 else 0, 0, token)
        if v:
            outsA.append(y.copy()); errsA.append(e)
    XB = X
    outs, errs = outsA, errsA
    for t in range(M + 1 + core.LAT + 2):
        token = 1 if t == 0 else 0
        m = t - 1
        y, v, e = core.step(0, np.zeros(core.CT, dtype=np.int64), XB[m] if 0 <= m < M else np.zeros(R, dtype=np.int64),
                            1 if 0 <= m < M else 0, 0, token)
        if v:
            outs.append(y.copy()); errs.append(e)
    outs = np.array(outs)
    okA = np.array_equal(outs[:MA, :C], XA @ W)
    okB = outs.shape[0] == MA + M and np.array_equal(outs[MA:, :C], XB @ W2) and np.array_equal(outs[MA:, C], XB @ Wc2)
    ok5 = okA and okB and sum(errs) == 0
    print(f"  T5 double-buffer: tile A ({MA} rows, W) then tile B ({M} rows, W2 loaded during A) back to back, "
          f"no drain -> {'PASS' if ok5 else 'FAIL'}")
    fails += (not ok5)

    # Vectors for the SystemVerilog testbench
    os.makedirs(a.out, exist_ok=True)
    write_hex(os.path.join(a.out, "x.hex"), X, 2)
    write_hex(os.path.join(a.out, "w.hex"), W, 2)
    write_hex(os.path.join(a.out, "wc.hex"), Wc, 4)
    write_hex(os.path.join(a.out, "y_exp.hex"), Y, 8)
    write_hex(os.path.join(a.out, "yc_exp.hex"), Yc, 8)
    with open(os.path.join(a.out, "params.vh"), "w") as f:
        f.write(f"`define VEC_ROWS {R}\n`define VEC_COLS {C}\n`define VEC_M {M}\n`define VEC_PE_LAT {PE_LAT}\n"
                f"`define VEC_FAULT_ROW {a.fault_row}\n`define VEC_FAULT_COL {a.fault_col}\n`define VEC_FAULT_M {m_f}\n")
    print(f"  vectors written to {os.path.abspath(a.out)} (x.hex, w.hex, wc.hex, y_exp.hex, yc_exp.hex, params.vh)")
    print(f"RESULT: {'ALL PASS' if fails == 0 else f'{fails} FAILURES'}")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
