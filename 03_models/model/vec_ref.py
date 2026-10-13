#!/usr/bin/env python3
"""
vec_ref.py -- the vector unit's reference model (drop 0.43, stream C2): rtl/vec_unit.sv operation for operation, bit-exact,
with its cycle count.

The unit sits on the core's drain path between the accumulator and the requantizer and runs a per-layer program of up to 16
micro-ops on each output row (rtl/vec_unit.sv's header has the op table). State: the row X (COLS lanes of INT32), four
scalar registers S0..S3, two row-statistics words RS0 / RS1 per accumulator row (persisting across descriptors), the
residual buffer RBUF (an INT8 entry per row), the tables PROG / LUTV / LUTS / SC / BI / K. Every arithmetic result
saturates to INT32 unless noted; a rounding shift is round half up: (v + 2^(sh-1)) >> sh (arithmetic), v at sh = 0.

  op word  [3:0] op  [5:4] d  [7:6] a  [9:8] b  [14:10] sh  [16:15] k  [17] t  [19:18] mode  [20] ka  [21] kb  (vop())
           ka / kb: operand a / b is the constant K[a] / K[b] instead of the scalar register S[a] / S[b]
  table    host address [11:8]: 0 PROG, 1 LUTV, 2 LUTS, 3 SC, 4 BI, 5 K; [7:0] the entry (host_if's VEC_ADDR / VEC_DATA)

Cycles: a row costs 2 (its load and its END) plus its ops' cycles (two for MUL_SCALE, LUT, RED, BMUL, SQR, SMUL, SLUT, one
for the others; a row also ends after slot 15 without an END), the first row one more (its read); the unit starts the
cycle after the sequencer's done and its own done pulses after the last row (VecUnit.run returns the count).

Used by run_vec_unit.py (V1 / V2 under neosim), tb/tb_vec_unit.sv's vectors (--out), compiler/neo_vec.py (the programs and
tables of the transformer operators) and the tests' end-to-end golden models.
"""
import argparse
import os

OP_END, OP_EMIT, OP_ADD_ROW, OP_MUL_SCALE, OP_CLAMP, OP_LUT, OP_RED, OP_BSUB = range(8)
OP_BMUL, OP_SQR, OP_SLD, OP_RSST, OP_SALU, OP_SMUL, OP_SLUT, OP_RELOAD = range(8, 16)
OP_NAMES = ["END", "EMIT", "ADD_ROW", "MUL_SCALE", "CLAMP", "LUT", "RED", "BSUB", "BMUL", "SQR", "SLD", "RSST", "SALU",
            "SMUL", "SLUT", "RELOAD"]
TWO_CYCLE = {OP_MUL_SCALE, OP_LUT, OP_RED, OP_BMUL, OP_SQR, OP_SMUL, OP_SLUT}
T_PROG, T_LUTV, T_LUTS, T_SC, T_BI, T_K = range(6)
I32_MIN, I32_MAX = -(1 << 31), (1 << 31) - 1


def vop(op, d=0, a=0, b=0, sh=0, k=0, t=0, mode=0, ka=0, kb=0):
    """One op word (31 bits; the unit stores its parity itself)."""
    assert 0 <= op < 16 and 0 <= d < 4 and 0 <= a < 4 and 0 <= b < 4 and 0 <= sh < 32 and 0 <= k < 4 and t in (0, 1) and 0 <= mode < 4
    assert ka in (0, 1) and kb in (0, 1)
    return op | (d << 4) | (a << 6) | (b << 8) | (sh << 10) | (k << 15) | (t << 17) | (mode << 18) | (ka << 20) | (kb << 21)


def decode(w):
    return dict(op=w & 15, d=(w >> 4) & 3, a=(w >> 6) & 3, b=(w >> 8) & 3, sh=(w >> 10) & 31, k=(w >> 15) & 3,
                t=(w >> 17) & 1, mode=(w >> 18) & 3, ka=(w >> 20) & 1, kb=(w >> 21) & 1)


def disasm(w):
    f = decode(w)
    return (f"{OP_NAMES[f['op']]}(d={f['d']},a={'K' if f['ka'] else 'S'}{f['a']},b={'K' if f['kb'] else 'S'}{f['b']},"
            f"sh={f['sh']},k={f['k']},t={f['t']},mode={f['mode']})")


def sat(v, bits=32):
    lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    return lo if v < lo else hi if v > hi else v


def s16(v):
    return sat(v, 16)


def rshr(v, sh):
    """Round half up: (v + 2^(sh-1)) >> sh, arithmetic; v at sh = 0."""
    return v if sh == 0 else (v + (1 << (sh - 1))) >> sh


def sext(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


def lut_eval(table, u):
    """A LUT entry pair {D[31:16], V[15:0]} at the index of u (INT16): V + round(D * frac / 256)."""
    idx = ((u >> 8) + 128) & 0xFF
    frac = u & 0xFF
    e = table[idx]
    v, dd = sext(e, 16), sext(e >> 16, 16)
    return v + ((dd * frac + 128) >> 8)


def lut_input(x, sh):
    return s16(x >> sh)


class VecUnit:
    """The unit's architectural state and its program execution over the rows of one descriptor."""

    def __init__(self, cols, rows=None, acc_rows=64):
        self.C, self.R, self.A = cols, (rows or cols), acc_rows
        self.prog = [0] * 16
        self.lutv = [0] * 256
        self.luts = [0] * 256
        self.sc = [0] * cols
        self.bi = [0] * cols
        self.k = [0] * 4
        self.rs = [[0, 0] for _ in range(acc_rows)]
        self.rbuf = [[0] * self.R for _ in range(acc_rows)]
        self.s = [0, 0, 0, 0]
        self.op_counts = [0] * 16
        self.sat_ops = set()                                   # ops whose result reached an INT32 bound in some lane

    # ---- the host bus
    def write(self, addr, data):
        tbl, ent, data = (addr >> 8) & 15, addr & 0xFF, data & 0xFFFFFFFF
        if tbl == T_PROG and ent < 16:
            self.prog[ent] = data & 0x7FFFFFFF
        elif tbl == T_LUTV:
            self.lutv[ent] = data
        elif tbl == T_LUTS:
            self.luts[ent] = data
        elif tbl == T_SC and ent < self.C:
            self.sc[ent] = sext(data, 16)
        elif tbl == T_BI and ent < self.C:
            self.bi[ent] = sext(data, 32)
        elif tbl == T_K and ent < 4:
            self.k[ent] = sext(data, 32)

    def load(self, writes):
        for a, d in writes:
            self.write(a, d)

    def write_rbuf(self, idx, entry):
        self.rbuf[idx] = [sext(int(v), 8) for v in entry][: self.R] + [0] * (self.R - len(entry))

    @property
    def active(self):
        return (self.prog[0] & 15) != OP_END

    # ---- execution
    def run_row(self, m, x):
        """Run the program on accumulator row m (x: COLS INT32). Returns (emitted rows, cycles of the row's ops + END)."""
        X = [int(v) for v in x]
        S = self.s
        out, cyc = [], 0
        rb = self.rbuf[m]
        for pc in range(16):
            f = decode(self.prog[pc])
            op, d, a, b, sh, k, t, mode = f["op"], f["d"], f["a"], f["b"], f["sh"], f["k"], f["t"], f["mode"]
            A = self.k[a] if f["ka"] else S[a]                 # operand a / b: a scalar register or a constant
            B = self.k[b] if f["kb"] else S[b]
            self.op_counts[op] += 1
            if op == OP_END:
                cyc += 1
                break
            cyc += 2 if op in TWO_CYCLE else 1
            if op == OP_EMIT:
                out.append(list(X))
            elif op == OP_ADD_ROW:
                X = [sat(X[j] + (rb[j] << sh)) for j in range(self.C)]
            elif op == OP_MUL_SCALE:
                X = [sat(rshr(X[j] * self.sc[j], sh) + self.bi[j]) for j in range(self.C)]
            elif op == OP_CLAMP:
                X = [min(max(X[j], A), B) for j in range(self.C)]
            elif op == OP_LUT:
                X = [lut_eval(self.lutv, lut_input(X[j], sh)) for j in range(self.C)]
            elif op == OP_RED:
                S[d] = sat(sum(X)) if mode & 1 else max(X)
            elif op == OP_BSUB:
                X = [sat(X[j] - A) for j in range(self.C)]
            elif op == OP_RELOAD:
                X = [int(v) for v in x]
            elif op == OP_BMUL:
                X = [sat(rshr(X[j] * s16(A), sh)) for j in range(self.C)]
            elif op == OP_SQR:
                X = [sat(rshr(X[j] * s16(X[j]), sh)) for j in range(self.C)]
            elif op == OP_SLD:
                S[d] = (self.rs[m][t], self.k[k], A, 0)[mode]
            elif op == OP_RSST:
                old = self.rs[m][t]
                self.rs[m][t] = (A, max(old, A), sat(old + A), A)[mode]
            elif op == OP_SALU:
                S[d] = (sat(A + B), sat(A - B), max(A, B), min(A, B))[mode]
            elif op == OP_SMUL:
                S[d] = sat(rshr(A * s16(B), sh))
            elif op == OP_SLUT:
                S[d] = lut_eval(self.luts, lut_input(A, sh))
            if any(v in (I32_MIN, I32_MAX) for v in X) and op not in (OP_EMIT, OP_RELOAD, OP_SLD, OP_RSST, OP_RED):
                self.sat_ops.add(OP_NAMES[op])
            if op in (OP_RED, OP_SALU, OP_SMUL) and S[d] in (I32_MIN, I32_MAX):
                self.sat_ops.add(OP_NAMES[op])
            if pc == 15:                                       # the row also ends after slot 15
                break
        return out, cyc

    def run(self, rows, cfg_m=None):
        """The program over the descriptor's rows (rows[m] = accumulator row m, M = len(rows)). Returns (emitted rows, the
        cycles from the sequencer's done to the unit's done)."""
        M = len(rows) if cfg_m is None else cfg_m
        if not self.active:
            return [list(r) for r in rows[:M]], 0
        out, cycles = [], 2                                   # the wait state's exit and the first row's read
        for m in range(M):
            o, c = self.run_row(m, rows[m])
            out += o
            cycles += 1 + c                                    # the row's load, its ops and END
        return out, cycles


# ---------------------------------------------------------------------------------------------------------------------
# LUT tables: 256 entries {D, V} over the INT16 input u = (i - 128) * 256 + frac, f sampled at the entry's start
# ---------------------------------------------------------------------------------------------------------------------
def build_lut(f, s_in, s_out, vmin=-32768, vmax=32767):
    """Entries for y = f(x) with x = u * s_in (u the LUT's INT16 input) and y = V * s_out: V[i] = round(f(x_i) / s_out) at
    x_i = (i - 128) * 256 * s_in, D[i] = V[i + 1] - V[i] (the last entry continues the slope of the one before; values
    clipped to INT16). Returns the 256 entry words."""
    vals = []
    for i in range(257):
        x = (i - 128) * 256 * s_in
        try:
            y = f(x) / s_out
        except (OverflowError, ZeroDivisionError, ValueError):
            y = vmax
        vals.append(max(vmin, min(vmax, int(round(y)))))
    words = []
    for i in range(256):
        v, dd = vals[i], vals[i + 1] - vals[i]
        dd = max(-32768, min(32767, dd))
        words.append(((dd & 0xFFFF) << 16) | (v & 0xFFFF))
    return words


def table_writes(tbl, values, base=0):
    """(address, data) host writes of a table's entries."""
    return [((tbl << 8) | (base + i), int(v) & 0xFFFFFFFF) for i, v in enumerate(values)]


def program_writes(ops):
    assert len(ops) <= 16
    return table_writes(T_PROG, list(ops) + ([vop(OP_END)] if len(ops) < 16 else []))


# ---------------------------------------------------------------------------------------------------------------------
# vectors for tb/tb_vec_unit.sv: a command stream and the expected rows
# ---------------------------------------------------------------------------------------------------------------------
def write_vectors(out_dir, cases, cols, rows, acc_rows):
    """cases: [(writes, rbuf {idx: entry}, acc rows (M x COLS))] run in sequence on one unit (RS and the tables persist).
    vec_cmd.hex: 32-bit words -- 1 addr data (a host write), 2 idx + ROWS/4 words (an RBUF entry), 3 M cycles + M*COLS words
    (a run: the rows and the model's cycles from the sequencer's done to the unit's done), 0 (end); vec_exp.hex: every
    emitted row (COLS words), vec_cnt.vh: the counts."""
    vu = VecUnit(cols, rows, acc_rows)
    cmd, exp, runs = [], [], 0
    for writes, rbuf, acc in cases:
        for a, d in writes:
            cmd += [1, a, d & 0xFFFFFFFF]
            vu.write(a, d)
        for idx, ent in sorted(rbuf.items()):
            words = [0] * (rows // 4)
            for c in range(rows):
                words[c // 4] |= (int(ent[c]) & 0xFF) << (8 * (c % 4))
            cmd += [2, idx] + words
            vu.write_rbuf(idx, ent)
        o, cyc = vu.run(acc)
        cmd += [3, len(acc), cyc] + [int(v) & 0xFFFFFFFF for r in acc for v in r]
        exp += [int(v) & 0xFFFFFFFF for r in o for v in r]
        runs += 1
    cmd.append(0)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "vec_cmd.hex"), "w") as f:
        f.write("".join(f"{w & 0xFFFFFFFF:08x}\n" for w in cmd))
    with open(os.path.join(out_dir, "vec_exp.hex"), "w") as f:
        f.write("".join(f"{w:08x}\n" for w in exp) or "00000000\n")
    with open(os.path.join(out_dir, "vec_cnt.vh"), "w") as f:
        f.write(f"`define VEC_COLS {cols}\n`define VEC_ROWS {rows}\n`define VEC_ACC_ROWS {acc_rows}\n"
                f"`define VEC_NCMD {len(cmd)}\n`define VEC_NEXP {max(1, len(exp))}\n`define VEC_NOUT {len(exp) // cols}\n"
                f"`define VEC_NRUNS {runs}\n")
    return len(exp) // cols, vu


if __name__ == "__main__":
    # a self-check of the model's arithmetic on a two-pass softmax over the COLS lanes of one row (the vectors for
    # tb/tb_vec_unit.sv come from sim/run_vec_unit.py --dump, which holds the V1 cases)
    import math
    ap = argparse.ArgumentParser(description="vector-unit model self-check")
    ap.add_argument("--cols", type=int, default=8)
    a = ap.parse_args()
    vu = VecUnit(a.cols, a.cols, 4)
    vu.load(table_writes(T_LUTV, build_lut(lambda x: math.exp(x) if x <= 0 else 1.0, 2.0 ** -11, 2.0 ** -15)))
    vu.load(table_writes(T_LUTS, build_lut(lambda x: 1.0 / x if x > 0 else 0.0, 2.0 ** -7, 2.0 ** -15)))
    vu.load(program_writes([vop(OP_RED, d=0, mode=0), vop(OP_BSUB, a=0), vop(OP_LUT, sh=0), vop(OP_RED, d=1, mode=1),
                            vop(OP_SLUT, d=2, a=1, sh=8), vop(OP_BMUL, a=2, sh=15), vop(OP_EMIT)]))
    row = [(-1) ** j * 1000 * j for j in range(a.cols)]
    out, cyc = vu.run([row])
    ref = [math.exp((v - max(row)) * 2.0 ** -11) for v in row]
    ref = [r / sum(ref) for r in ref]
    print("softmax over the lanes:", [round(v / 32768, 4) for v in out[0]], "float:", [round(r, 4) for r in ref], f"{cyc} cycles")
