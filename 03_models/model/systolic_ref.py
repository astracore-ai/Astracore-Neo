#!/usr/bin/env python3
"""
neo_mac_core -- cycle-accurate Python reference model (v0.4, drop 0.42: 2:4 structured sparsity)

Mirrors rtl/neo_mac_core.sv register for register:
  skew_in  -> systolic_array (ROWS x (COLS + NCHK), weight-stationary, 2-stage pipelined PE,
              double-buffered weights with a swap token travelling with the data)
           -> deskew_out -> abft_checker, plus a valid delay line.

NCHK = 1 is the dense-only core: one byte per row bus and one ABFT check column whose weight in
row i is sum_j W[i][j], so for every activation row the check output equals the sum of the data
outputs. NCHK = 4 is the sparse-capable silicon core (drop 0.42): every row bus is four bytes,
every data PE multiplies the byte its 2-bit index selects (the index shifts down the shadow chain
with its weight), and four check columns with hard-wired indices k = 0..3 hold, per row, the sum
of the row's weights whose index is k; the core compares the data sum with the sum of the four
check outputs. A dense layer on the NCHK = 4 core stores index 0 everywhere, today's check weight
in column 0 and zeros in columns 1..3; a 2:4 sparse layer (2 nonzeros in every group of four
consecutive input channels) maps group g to the PE pair (2g, 2g + 1) -- both rows see channels
4g .. 4g + 3 of a 64-channel tile, each PE holds one of the two nonzero weights and its position
-- so the 32 rows cover 64 input channels per pass. Any single-point fault in a MAC, a stored
weight or index, or the psum chain breaks the equality and is flagged within the same output row.

What this script does:
  1. proves the dataflow: Y == X @ W for random INT8 data, and measures the latency (expected
     PE_LAT*ROWS + COLS cycles from unskewed input to aligned output) -- T1
  2. proves the ABFT checker catches (a) a MAC fault in a data PE, (b) a MAC fault in a check
     column, (c) a corrupted stored weight -- T2-T4; double buffering -- T5
  3. NCHK = 4: the 2:4 sparse dataflow bit-exact against dense arithmetic on the pruned weights
     (T6), and faults under sparse mode (T7): a MAC fault in a data PE whose index is 0, 1, 2, 3,
     a MAC fault in each check column, a corrupted check weight of each check column, a corrupted
     stored index
  4. writes the hex vectors tb/tb_neo_mac_core.sv and sim/run_mac_core.py read

Usage: python3 systolic_ref.py --rows 32 --cols 32 --m 64 --seed 1 [--nchk 4] --out ../vectors
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


def wcw_of(rows):
    """Check-weight width of the RTL: WW + $clog2(ROWS) + 1."""
    return 8 + int(np.ceil(np.log2(rows))) + 1


# ---------------------------------------------------------------------------------------------------------------------
# 2:4 weights (the golden model's own compressor; neo_backend.py has the compiler's, checked against the same arithmetic)
# ---------------------------------------------------------------------------------------------------------------------
def random_2_4(rng, k, n, density=1.0):
    """A K x N weight matrix that is 2:4 along K: in every group of four consecutive rows, two random positions per
    column hold nonzero INT8 values (with probability `density` each), the other two are zero."""
    w = np.zeros((k, n), dtype=np.int64)
    for g in range(0, k, 4):
        for j in range(n):
            pos = rng.choice(min(4, k - g), size=min(2, k - g), replace=False)
            for p in pos:
                if rng.random() < density:
                    v = 0
                    while v == 0:
                        v = int(rng.integers(-128, 128))
                    w[g + p, j] = v
    return w


def compress_2_4(wd, rows):
    """Dense pruned weights wd [2*rows, cols] (one 64-channel tile at rows = 32) -> the array's stored form:
    W [rows, cols] and I [rows, cols]: group g (channels 4g .. 4g + 3) maps to PE rows 2g and 2g + 1; row 2g holds the
    group's nonzero weight at the lower position, row 2g + 1 the one at the higher position, each with its position in
    the group as the index; a missing weight is (0, index 0)."""
    k, n = wd.shape
    assert k == 2 * rows
    W = np.zeros((rows, n), dtype=np.int64)
    I = np.zeros((rows, n), dtype=np.int64)
    for g in range(rows // 2):
        for j in range(n):
            nz = [p for p in range(4) if wd[4 * g + p, j] != 0]
            if len(nz) > 2:
                raise ValueError(f"not 2:4: group {g} column {j} has {len(nz)} nonzero weights")
            for slot, p in enumerate(nz):
                W[2 * g + slot, j] = wd[4 * g + p, j]
                I[2 * g + slot, j] = p
    return W, I


def decompress_2_4(W, I):
    """The array's stored form (W, I) [rows, cols] -> the dense weights [2*rows, cols] it computes with: PE row r of
    pair g = r // 2 multiplies channel 4g + I[r][j] by W[r][j]."""
    rows, n = W.shape
    wd = np.zeros((2 * rows, n), dtype=np.int64)
    for r in range(rows):
        for j in range(n):
            wd[4 * (r // 2) + I[r, j], j] += W[r, j]
    return wd


def check_weights(W, I, nchk):
    """The check columns' weights: Wc[i][k] = sum of row i's weights whose index is k (nchk = 1: the row sum)."""
    if nchk == 1:
        return W.sum(axis=1)[:, None]
    Wc = np.zeros((W.shape[0], nchk), dtype=np.int64)
    for k in range(nchk):
        Wc[:, k] = np.where(I == k, W, 0).sum(axis=1)
    return Wc


class MacCore:
    """Register-level model of neo_mac_core. One call to step() is one clock."""

    def __init__(self, rows, cols, fault_row=None, fault_col=None, nchk=1):
        assert nchk in (1, 4)
        self.R, self.C, self.NCHK = rows, cols, nchk
        self.NB = nchk                           # bytes per row bus
        self.CA = cols + nchk                    # array columns: data, then the check columns
        self.CT = cols + 1                       # aligned outputs: data, then the summed check columns
        self.LAT = PE_LAT * rows + self.CT - 1   # unskewed input -> aligned output
        self.XB = 2 * rows if nchk > 1 else rows # activation bytes per row
        self.fault_row, self.fault_col = fault_row, fault_col
        # systolic array state
        self.W = np.zeros((rows, self.CA), dtype=np.int64)       # active (stationary) weights
        self.Wsh = np.zeros((rows, self.CA), dtype=np.int64)     # shadow weights (shift chain)
        self.I = np.zeros((rows, cols), dtype=np.int64)          # active index of the data PEs
        self.Ish = np.zeros((rows, cols), dtype=np.int64)        # shadow index (shifts with the weight)
        self.Xq = np.zeros((rows, cols, self.NB), dtype=np.int64)  # registered row bus (flows right), data PEs
        self.Sq = np.zeros((rows, cols), dtype=np.int64)         # registered swap token (flows right), data PEs
        self.Pq = np.zeros((rows, self.CA), dtype=np.int64)      # registered psum (flows down), stage 2
        self.ProdQ = np.zeros((rows, self.CA), dtype=np.int64)   # stage 1: product
        self.PinQ = np.zeros((rows, self.CA), dtype=np.int64)    # stage 1: delayed incoming psum
        self.InjQ = np.zeros((rows, self.CA), dtype=np.int64)    # stage 1: delayed fault hook
        # skew_in: row i's bus delayed by i*PE_LAT cycles (data and token)
        self.skew = [np.zeros((i * PE_LAT, self.NB), dtype=np.int64) for i in range(rows)]
        self.skew_s = [np.zeros(i * PE_LAT, dtype=np.int64) for i in range(rows)]
        # deskew_out: column j delayed by CT-1-j cycles
        self.deskew = [np.zeros(self.CT - 1 - j, dtype=np.int64) for j in range(self.CT)]
        self.valid_pipe = np.zeros(self.LAT, dtype=np.int64)
        self.err_sticky = 0

    def row_bus(self, x_vec, i, sparse):
        """The bus skew_in delivers to row i (before its delay): sparse -- group i // 2, channels 4g .. 4g + 3; dense --
        channel i in byte 0 and zeros, except row 0, which carries x_vec[0..3] as presented (no PE of row 0 selects
        bytes 1..3 in dense mode)."""
        if self.NB == 1:
            return np.array([x_vec[i]], dtype=np.int64)
        if sparse:
            g = i // 2
            return np.array(x_vec[4 * g: 4 * g + 4], dtype=np.int64)
        if i == 0:
            return np.array(x_vec[0:4], dtype=np.int64)
        return np.array([x_vec[i], 0, 0, 0], dtype=np.int64)

    def step(self, w_load, w_in, x_vec, valid_in, fault, token=0, i_in=None, sparse=0):
        """w_in: CA weights entering the top of each column's shadow chain (data columns, then the check columns), with
           i_in: the data columns' indices (used when w_load); x_vec: XB unskewed activation bytes; token: swap token
           (one cycle before a tile's row 0); fault: assert the fault_inject pin; sparse: the layer's mode bit."""
        R, C, CA, NB = self.R, self.C, self.CA, self.NB
        if i_in is None:
            i_in = np.zeros(C, dtype=np.int64)
        # ---- skew_in (registers): data and token ----
        x_row = np.zeros((R, NB), dtype=np.int64)
        s_row = np.zeros(R, dtype=np.int64)
        for i in range(R):
            bus = self.row_bus(x_vec, i, sparse)
            if i == 0:
                x_row[i] = bus
                s_row[i] = token
            else:
                x_row[i] = self.skew[i][-1]
                self.skew[i] = np.concatenate((bus[None, :], self.skew[i][:-1]), axis=0)
                s_row[i] = self.skew_s[i][-1]
                self.skew_s[i] = np.concatenate(([token], self.skew_s[i][:-1]))
        # ---- bottom-of-array register outputs as visible THIS cycle (pre-edge) ----
        bottom = self.Pq[R - 1, :].copy()
        # ---- systolic array: compute next state from current state ----
        x_pe = np.empty((R, C, NB), dtype=np.int64)              # the data PEs' row-bus inputs
        x_pe[:, 0, :] = x_row
        x_pe[:, 1:, :] = self.Xq[:, :-1, :]
        s_pe = np.empty((R, C), dtype=np.int64)
        s_pe[:, 0] = s_row
        s_pe[:, 1:] = self.Sq[:, :-1]
        x_sel = np.take_along_axis(x_pe, self.I[:, :, None], axis=2)[:, :, 0] if NB > 1 else x_pe[:, :, 0]
        x_chk = self.Xq[:, C - 1, :]                             # the check PEs: byte k of the last data PE's register
        s_all = np.concatenate([s_pe, np.repeat(self.Sq[:, C - 1:C], self.NCHK, axis=1)], axis=1)
        p_pe = np.zeros((R, CA), dtype=np.int64)
        p_pe[1:, :] = self.Pq[:-1, :]
        w_src = np.empty((R, CA), dtype=np.int64)
        w_src[0, :] = w_in
        w_src[1:, :] = self.Wsh[:-1, :]
        i_src = np.empty((R, C), dtype=np.int64)
        i_src[0, :] = i_in
        i_src[1:, :] = self.Ish[:-1, :]
        inj = np.zeros((R, CA), dtype=np.int64)
        if fault and self.fault_row is not None:
            inj[self.fault_row, self.fault_col] = 1
        P_next = wrap(self.PinQ + self.ProdQ + self.InjQ, PW)   # stage 2: add (previous stage-1 values)
        Prod_next = np.empty((R, CA), dtype=np.int64)            # stage 1: product uses the ACTIVE weight and index
        Prod_next[:, :C] = x_sel * self.W[:, :C]
        Prod_next[:, C:] = x_chk[:, :self.NCHK] * self.W[:, C:]
        W_next = np.where(s_all == 1, self.Wsh, self.W)          # token adopts the (pre-shift) shadow
        I_next = np.where(s_pe == 1, self.Ish, self.I)
        if w_load:
            self.Wsh = w_src.copy()
            self.Ish = i_src.copy()
        self.W = W_next
        self.I = I_next
        self.Xq = x_pe
        self.Sq = s_pe
        self.PinQ = p_pe
        self.ProdQ = Prod_next
        self.InjQ = inj
        self.Pq = P_next
        # ---- the check side: the sum of the check columns (combinational, before the deskew) ----
        bot = np.concatenate([bottom[:C], [int(wrap(bottom[C:].sum(), PW))]])
        # ---- deskew_out (registers) ----
        y = np.zeros(self.CT, dtype=np.int64)
        for j in range(self.CT):
            d = self.deskew[j]
            if d.size == 0:
                y[j] = bot[j]
            else:
                y[j] = d[-1]
                self.deskew[j] = np.concatenate(([bot[j]], d[:-1]))
        # ---- valid delay line ----
        valid_out = self.valid_pipe[-1]
        self.valid_pipe = np.concatenate(([valid_in], self.valid_pipe[:-1]))
        # ---- abft_checker (combinational on aligned outputs) ----
        err = int(valid_out == 1 and int(wrap(y[:C].sum(), PW)) != int(y[C]))
        self.err_sticky |= err
        return y, valid_out, err

    # -- convenience drivers -------------------------------------------------
    def _tile(self, W, Wc, I):
        Wc = np.asarray(Wc, dtype=np.int64).reshape(self.R, -1)
        assert Wc.shape[1] == self.NCHK
        I = np.zeros(W.shape, dtype=np.int64) if I is None else np.asarray(I, dtype=np.int64)
        return np.concatenate([W, Wc], axis=1), I

    def load_weights(self, W, Wc, I=None):
        """Shift W (ROWS x COLS), the indices I (ROWS x COLS, default 0) and the check weights Wc (ROWS x NCHK) into the
        SHADOW chain from the top: at cycle t feed row ROWS-1-t, so that after ROWS cycles shadow(i,j) holds W[i][j].
        The weights become active only where the swap token has passed (see run)."""
        Wt, It = self._tile(W, Wc, I)
        for t in range(self.R):
            self.step(1, Wt[self.R - 1 - t, :], np.zeros(self.XB, dtype=np.int64), 0, 0, i_in=It[self.R - 1 - t, :])
        assert np.array_equal(self.Wsh, Wt) and np.array_equal(self.Ish, It), "shadow weight load failed"

    def run(self, X, fault_cycles=(), load_during=None, sparse=0):
        """Step 0: swap token. Steps 1..M: rows of X (unskewed, XB bytes each). Collect aligned outputs.
        load_during = (W, Wc[, I]): shift a new shadow tile in, starting LAT steps after the
        token (the double-buffering rule), while this tile streams.
        Returns Y (M x CT), err flags per output row, and the step of the first valid output."""
        M = X.shape[0]
        total = M + 1 + self.LAT + 2
        outs, errs, first_valid = [], [], None
        Wt = It = None
        if load_during is not None:
            Wt, It = self._tile(load_during[0], load_during[1], load_during[2] if len(load_during) > 2 else None)
        for t in range(total):
            token = 1 if t == 0 else 0
            m = t - 1
            x_vec = X[m] if 0 <= m < M else np.zeros(self.XB, dtype=np.int64)
            w_load, w_in, i_in = 0, np.zeros(self.CA, dtype=np.int64), None
            if Wt is not None and self.LAT <= t < self.LAT + self.R:
                w_load, w_in, i_in = 1, Wt[self.R - 1 - (t - self.LAT), :], It[self.R - 1 - (t - self.LAT), :]
            y, v, e = self.step(w_load, w_in, x_vec, 1 if 0 <= m < M else 0,
                                1 if t in fault_cycles else 0, token, i_in=i_in, sparse=sparse)
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


def pad_x(X, xb):
    """Dense activation rows (ROWS bytes) presented on an NCHK = 4 core: the upper ROWS bytes are zero."""
    out = np.zeros((X.shape[0], xb), dtype=np.int64)
    out[:, :X.shape[1]] = X
    return out


def sparse_tests(R, C, M, rng, fault_row, fault_col, m_f, out):
    """T6 / T7 on the NCHK = 4 core; returns (failures, vectors to write)."""
    fails = 0
    nchk = 4
    wcw = wcw_of(R)
    # T6: a 64-channel 2:4 tile (2*ROWS input channels), dense ground truth on the pruned weights
    Xs = rng.integers(-128, 128, size=(M, 2 * R), dtype=np.int64)
    Wd = random_2_4(rng, 2 * R, C, density=0.95)               # some groups with fewer than two nonzeros
    W, I = compress_2_4(Wd, R)
    Wc = check_weights(W, I, nchk)
    Y = Xs @ Wd
    Yc = Y.sum(axis=1)
    assert np.all(np.abs(Wc) < 2 ** (wcw - 1)), "check weights exceed RTL width"
    assert np.all(np.abs(Y) < 2 ** 31)
    core = MacCore(R, C, fault_row, fault_col, nchk=nchk)
    core.load_weights(W, Wc, I)
    Yo, errs, first = core.run(Xs, sparse=1)
    ok = Yo.shape[0] == M and np.array_equal(Yo[:, :C], Y) and np.array_equal(Yo[:, C], Yc) and errs.sum() == 0
    lat_ok = first == core.LAT + 1
    print(f"  T6 sparse    : 2:4 tile of {2 * R} input channels on {R} rows, Y == X @ W_pruned for {M}x{C} outputs, check sum, "
          f"0 ABFT flags, latency {first} (expect {core.LAT + 1}) -> {'PASS' if ok and lat_ok else 'FAIL'}")
    fails += not (ok and lat_ok)
    # T6b: the same tile in dense mode on the NCHK = 4 core must equal the NCHK = 1 core (index 0, check column 0)
    Xd = rng.integers(-128, 128, size=(M, R), dtype=np.int64)
    Wdd = rng.integers(-128, 128, size=(R, C), dtype=np.int64)
    c1 = MacCore(R, C, nchk=1)
    c1.load_weights(Wdd, Wdd.sum(axis=1))
    Y1, e1, _ = c1.run(Xd)
    c4 = MacCore(R, C, nchk=4)
    c4.load_weights(Wdd, check_weights(Wdd, np.zeros_like(Wdd), 4))
    Y4, e4, _ = c4.run(pad_x(Xd, 2 * R), sparse=0)
    ok6b = np.array_equal(Y1, Y4) and e1.sum() == 0 and e4.sum() == 0 and np.array_equal(Y1[:, :C], Xd @ Wdd)
    print(f"  T6 dense@4   : a dense tile on the NCHK = 4 core (index 0, check column 0) equals the NCHK = 1 core row for row -> "
          f"{'PASS' if ok6b else 'FAIL'}")
    fails += not ok6b
    # T7a: a MAC fault in the data PE (fault_row, fault_col) while it holds index k = 0..3 -- check column k covers its
    # product. The stored forms are built directly (the hardware takes either order of a pair's two positions; the
    # compressor's lower-position-first rule is the compiler's convention only), the ground truth from their expansion.
    sets = []
    g, j = fault_row // 2, fault_col
    for k in range(4):
        Wk_c, Ik = W.copy(), I.copy()
        p2 = int(rng.choice([p for p in range(4) if p != k]))  # the partner PE's position
        Wk_c[fault_row, j], Ik[fault_row, j] = int(rng.integers(1, 128)), k
        Wk_c[fault_row ^ 1, j], Ik[fault_row ^ 1, j] = int(rng.integers(-128, 0)), p2
        Wk = decompress_2_4(Wk_c, Ik)
        Wck = check_weights(Wk_c, Ik, nchk)
        Yk = Xs @ Wk
        cyc = 1 + m_f + PE_LAT * fault_row + fault_col
        core = MacCore(R, C, fault_row, fault_col, nchk=nchk)
        core.load_weights(Wk_c, Wck, Ik)
        Yo, errs, _ = core.run(Xs, fault_cycles=(cyc,), sparse=1)
        diff = Yo[:, :C] - Yk
        hit = diff[m_f, j] == 1 and np.count_nonzero(diff) == 1
        flagged = errs[m_f] == 1 and errs.sum() == 1
        print(f"  T7 index {k}   : MAC fault at PE({fault_row},{j}) holding index {k} (check column {k} covers it): output "
              f"({m_f},{j}) off by +1 only -> {'PASS' if hit else 'FAIL'}; ABFT flags exactly row {m_f} -> {'PASS' if flagged else 'FAIL'}")
        fails += (not hit) + (not flagged)
        sets.append((Wk_c, Ik, Wck, Yk))
    # T7b: a corrupted check weight in each check column k: the rows whose byte k of row fault_row's bus is nonzero
    for k in range(4):
        Wcb = Wc.copy()
        Wcb[fault_row, k] += 1
        core = MacCore(R, C, nchk=nchk)
        core.load_weights(W, Wcb, I)
        Yo, errs, _ = core.run(Xs, sparse=1)
        expect = (Xs[:, 4 * g + k] != 0).astype(np.int64)
        ok = np.array_equal(Yo[:, :C], Y) and np.array_equal(errs, expect)
        print(f"  T7 check {k}   : check weight of column {k} in row {fault_row} off by one: data untouched, ABFT flags the "
              f"{int(expect.sum())} rows whose channel {4 * g + k} is nonzero -> {'PASS' if ok else 'FAIL'}")
        fails += not ok
    # T7c (model only: the RTL's hook is one PE per build): a MAC fault in each check column's PE
    for k in range(4):
        core = MacCore(R, C, fault_row, C + k, nchk=nchk)
        core.load_weights(W, Wc, I)
        cyc = 1 + m_f + PE_LAT * fault_row + C
        Yo, errs, _ = core.run(Xs, fault_cycles=(cyc,), sparse=1)
        ok = np.array_equal(Yo[:, :C], Y) and errs[m_f] == 1 and errs.sum() == 1
        print(f"  T7 chk-PE {k}  : MAC fault in check column {k}'s PE: data untouched, ABFT flags exactly row {m_f} -> "
              f"{'PASS' if ok else 'FAIL'}")
        fails += not ok
    # T7d: a stored index flipped: the first PE of row fault_row from column fault_col on that holds a nonzero weight
    # multiplies another byte of its group
    jf = next(jj for jj in list(range(j, C)) + list(range(j)) if W[fault_row, jj] != 0)
    Ib = I.copy()
    Ib[fault_row, jf] ^= 1
    core = MacCore(R, C, nchk=nchk)
    core.load_weights(W, Wc, Ib)
    Yo, errs, _ = core.run(Xs, sparse=1)
    wrong = Yo[:, jf] != Y[:, jf]
    ok = wrong.any() and np.array_equal(errs.astype(bool), wrong) and np.array_equal(np.delete(Yo[:, :C], jf, axis=1), np.delete(Y, jf, axis=1))
    print(f"  T7 index flip: PE({fault_row},{jf})'s stored index {I[fault_row, jf]} -> {Ib[fault_row, jf]}: ABFT flags exactly the "
          f"{int(wrong.sum())} rows whose output changed -> {'PASS' if ok else 'FAIL'}")
    fails += not ok
    vec = dict(Xs=Xs, W=W, I=I, Wc=Wc, Y=Y, Yc=Yc, sets=sets)
    return fails, vec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=32)
    ap.add_argument("--cols", type=int, default=32)
    ap.add_argument("--m", type=int, default=64)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--fault-row", type=int, default=5)
    ap.add_argument("--fault-col", type=int, default=7)
    ap.add_argument("--nchk", type=int, default=1, choices=(1, 4),
                    help="check columns of the core the vectors are for: 1 (dense-only), 4 (2:4 sparse-capable: adds T6 / T7)")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "vectors"))
    a = ap.parse_args()
    R, C, M = a.rows, a.cols, a.m
    rng = np.random.default_rng(a.seed)
    X = rng.integers(-128, 128, size=(M, R), dtype=np.int64)
    W = rng.integers(-128, 128, size=(R, C), dtype=np.int64)
    Wc = W.sum(axis=1)
    Y = X @ W
    Yc = X @ Wc
    wcw = wcw_of(R)
    assert np.all(np.abs(Wc) < 2 ** (wcw - 1)), "check weights exceed RTL width"
    assert np.array_equal(Y.sum(axis=1), Yc), "ABFT identity broken (should be impossible)"
    assert np.all(np.abs(Y) < 2 ** 31) and np.all(np.abs(Yc) < 2 ** 31)
    nchk = a.nchk
    Wc4 = check_weights(W, np.zeros_like(W), nchk)              # dense: column 0 the row sum, columns 1..3 zero
    XB = 2 * R if nchk > 1 else R
    Xp = pad_x(X, XB)

    print(f"neo_mac_core reference model: ROWS={R} COLS={C} (+{nchk} ABFT column{'s' if nchk > 1 else ''}), M={M} rows, seed={a.seed}")
    fails = 0

    # Test 1: dataflow and latency
    core = MacCore(R, C, a.fault_row, a.fault_col, nchk=nchk)
    core.load_weights(W, Wc4)
    Yo, errs, first = core.run(Xp)
    ok = Yo.shape[0] == M and np.array_equal(Yo[:, :C], Y) and np.array_equal(Yo[:, C], Yc)
    lat_ok = first == core.LAT + 1
    print(f"  T1 dataflow  : Y == X@W for {M}x{C} outputs and check column -> {'PASS' if ok else 'FAIL'}")
    print(f"  T1 latency   : first valid output at step {first} (token at 0, row 0 at 1), expected PE_LAT*ROWS+COLS+1 = {core.LAT + 1} -> {'PASS' if lat_ok else 'FAIL'}")
    print(f"  T1 ABFT      : {int(errs.sum())} rows flagged on clean data (expect 0) -> {'PASS' if errs.sum() == 0 else 'FAIL'}")
    fails += (not ok) + (not lat_ok) + (errs.sum() != 0)

    # Test 2: MAC fault in a data PE while output row m_f passes through it
    m_f = 10
    cyc = 1 + m_f + PE_LAT * a.fault_row + a.fault_col  # step in which PE(fr,fc) processes X[m_f]
    core = MacCore(R, C, a.fault_row, a.fault_col, nchk=nchk)
    core.load_weights(W, Wc4)
    Yo, errs, _ = core.run(Xp, fault_cycles=(cyc,))
    diff = Yo[:, :C] - Y
    hit = (diff[m_f, a.fault_col] == 1) and (np.count_nonzero(diff) == 1)
    flagged = errs[m_f] == 1 and errs.sum() == 1
    print(f"  T2 data-PE fault at PE({a.fault_row},{a.fault_col}), cycle {cyc}: output ({m_f},{a.fault_col}) off by +1 only -> {'PASS' if hit else 'FAIL'}; "
          f"ABFT flags exactly row {m_f} -> {'PASS' if flagged else 'FAIL'}")
    fails += (not hit) + (not flagged)

    # Test 3: fault in the check column itself must also be flagged
    core = MacCore(R, C, a.fault_row, C, nchk=nchk)
    core.load_weights(W, Wc4)
    cyc3 = 1 + m_f + PE_LAT * a.fault_row + C
    Yo, errs, _ = core.run(Xp, fault_cycles=(cyc3,))
    ok3 = np.array_equal(Yo[:, :C], Y) and errs[m_f] == 1 and errs.sum() == 1
    print(f"  T3 check-column fault: data untouched, ABFT flags exactly row {m_f} -> {'PASS' if ok3 else 'FAIL'}")
    fails += (not ok3)

    # Test 4: a corrupted stored weight (bit flip) must flag every row
    core = MacCore(R, C, nchk=nchk)
    Wbad = W.copy()
    Wbad[3, 4] ^= 0x10
    core.load_weights(Wbad, Wc4)                # check weights precomputed from the correct W
    Yo, errs, _ = core.run(Xp)
    ok4 = errs.sum() == M
    print(f"  T4 weight bit-flip at W[3][4]: ABFT flags {int(errs.sum())}/{M} rows (expect {M}) -> {'PASS' if ok4 else 'FAIL'}")
    fails += (not ok4)

    # Test 5: double buffering -- tile B's weights load while tile A streams; B follows A with no drain
    W2 = rng.integers(-128, 128, size=(R, C), dtype=np.int64)
    Wc2 = W2.sum(axis=1)
    Wc2t = check_weights(W2, np.zeros_like(W2), nchk)
    core = MacCore(R, C, nchk=nchk)
    core.load_weights(W, Wc4)
    MA = max(M, core.LAT + R + 2)              # tile A long enough to hide the shadow load
    XA = rng.integers(-128, 128, size=(MA, R), dtype=np.int64)
    XAp = pad_x(XA, XB)
    # run A with B's shadow load overlapped; feed B's token+rows immediately after A's last row
    total = MA + 1
    outsA, errsA = [], []
    Wt2, It2 = core._tile(W2, Wc2t, None)
    for t in range(total):
        token = 1 if t == 0 else 0
        m = t - 1
        w_load, w_in, i_in = 0, np.zeros(core.CA, dtype=np.int64), None
        if core.LAT <= t < core.LAT + R:
            w_load, w_in, i_in = 1, Wt2[R - 1 - (t - core.LAT), :], It2[R - 1 - (t - core.LAT), :]
        y, v, e = core.step(w_load, w_in, XAp[m] if m >= 0 else np.zeros(XB, dtype=np.int64), 1 if m >= 0 else 0, 0, token, i_in=i_in)
        if v:
            outsA.append(y.copy()); errsA.append(e)
    outs, errs = outsA, errsA
    for t in range(M + 1 + core.LAT + 2):
        token = 1 if t == 0 else 0
        m = t - 1
        y, v, e = core.step(0, np.zeros(core.CA, dtype=np.int64), Xp[m] if 0 <= m < M else np.zeros(XB, dtype=np.int64),
                            1 if 0 <= m < M else 0, 0, token)
        if v:
            outs.append(y.copy()); errs.append(e)
    outs = np.array(outs)
    okA = np.array_equal(outs[:MA, :C], XA @ W)
    okB = outs.shape[0] == MA + M and np.array_equal(outs[MA:, :C], X @ W2) and np.array_equal(outs[MA:, C], X @ Wc2)
    ok5 = okA and okB and sum(errs) == 0
    print(f"  T5 double-buffer: tile A ({MA} rows, W) then tile B ({M} rows, W2 loaded during A) back to back, "
          f"no drain -> {'PASS' if ok5 else 'FAIL'}")
    fails += (not ok5)

    # Tests 6 / 7: the 2:4 sparse mode (NCHK = 4)
    vec = None
    if nchk > 1:
        f67, vec = sparse_tests(R, C, M, rng, a.fault_row, a.fault_col, m_f, a.out)
        fails += f67

    # Vectors for the SystemVerilog testbench (the dense files are those of drop 0.41 for the same seed)
    os.makedirs(a.out, exist_ok=True)
    write_hex(os.path.join(a.out, "x.hex"), X, 2)
    write_hex(os.path.join(a.out, "w.hex"), W, 2)
    write_hex(os.path.join(a.out, "wc.hex"), Wc, 4)
    write_hex(os.path.join(a.out, "y_exp.hex"), Y, 8)
    write_hex(os.path.join(a.out, "yc_exp.hex"), Yc, 8)
    files = "x.hex, w.hex, wc.hex, y_exp.hex, yc_exp.hex"
    if vec is not None:
        # sparse: the 64-channel rows, the stored weights / indices / four check weights per row, the expected outputs;
        # T7's four weight sets (the faulty PE holding index 0..3) with their expected outputs
        write_hex(os.path.join(a.out, "x_sp.hex"), vec["Xs"], 2)
        write_hex(os.path.join(a.out, "w_sp.hex"), vec["W"], 2)
        write_hex(os.path.join(a.out, "i_sp.hex"), vec["I"], 1)
        write_hex(os.path.join(a.out, "wc_sp.hex"), vec["Wc"], 4)
        write_hex(os.path.join(a.out, "y_sp_exp.hex"), vec["Y"], 8)
        write_hex(os.path.join(a.out, "yc_sp_exp.hex"), vec["Yc"], 8)
        write_hex(os.path.join(a.out, "w_t7.hex"), np.stack([s[0] for s in vec["sets"]]), 2)
        write_hex(os.path.join(a.out, "i_t7.hex"), np.stack([s[1] for s in vec["sets"]]), 1)
        write_hex(os.path.join(a.out, "wc_t7.hex"), np.stack([s[2] for s in vec["sets"]]), 4)
        write_hex(os.path.join(a.out, "y_t7_exp.hex"), np.stack([s[3] for s in vec["sets"]]), 8)
        files += ", x_sp.hex, w_sp.hex, i_sp.hex, wc_sp.hex, y_sp_exp.hex, yc_sp_exp.hex, w_t7.hex, i_t7.hex, wc_t7.hex, y_t7_exp.hex"
    with open(os.path.join(a.out, "params.vh"), "w") as f:
        f.write(f"`define VEC_ROWS {R}\n`define VEC_COLS {C}\n`define VEC_M {M}\n`define VEC_PE_LAT {PE_LAT}\n"
                f"`define VEC_FAULT_ROW {a.fault_row}\n`define VEC_FAULT_COL {a.fault_col}\n`define VEC_FAULT_M {m_f}\n"
                f"`define VEC_NCHK {nchk}\n")
    print(f"  vectors written to {os.path.abspath(a.out)} ({files}, params.vh)")
    print(f"RESULT: {'ALL PASS' if fails == 0 else f'{fails} FAILURES'}")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
