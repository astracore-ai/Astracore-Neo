#!/usr/bin/env python3
"""
run_vec_unit.py -- the vector unit (rtl/vec_unit.sv, drop 0.43) under neosim against its model (model/vec_ref.py).

  V1  every op bit-exact against the model on random rows (16x8 geometry: COLS 8, RBUF entries of 16 bytes, 64 rows):
      seven programs run in sequence on one unit -- the row statistics initialised (RED sum / max, RSST store); the
      element-wise ops (ADD_ROW from RBUF, MUL_SCALE with per-column scale and offset, CLAMP between two scalars from K,
      SQR, EMIT twice in a row); the scalar side (RSLD both slots, BSUB, LUT, RED sum, SLUT, BMUL, SMUL, SALU sub, RSST add
      and max, a 15-op program); a softmax over the row's lanes (RED max, BSUB, BMUL, LUT exp, RED sum, SLUT reciprocal,
      BMUL) and a layer norm over the lanes (RED sum, SMUL by 1/COLS, SQR, RED sum, SMUL, SALU, SLUT rsqrt, RELOAD, BSUB,
      BMUL, MUL_SCALE) -- the two passes over a row held in the unit; a 16-op program with no END (the row ends after
      slot 15); and the saturating ends of ADD_ROW, BMUL by a constant (ka) and SQR on full-range rows, each from the row
      as read (RELOAD). Rows of every magnitude, the saturation of the element-wise ops reached (ADD_ROW, MUL_SCALE, SQR,
      BSUB, BMUL: checked). The emitted rows equal the model's, the cycles
      from the sequencer's done to the unit's done equal the model's count (the documented latency per op), no flag rises.
      Faults: the primary control's row index flipped mid-program -> ctrl_err_sticky (the duplicated control's comparator);
      a reserved bit of slot 0's op word flipped as used -> perr_sticky, nothing else; a LUTV entry of one lane and a K
      word corrupted in place -> perr_sticky when an op reads them, err_clear clears both flags.
  V2  LUT accuracy against float references over each table's input range, with the tables the compiler builds
      (compiler/neo_vec.py) and the unit's interpolation (the model; the RTL's equality with it is V1's), on every input of
      the LUT's INT16 domain inside the range (x = c + u s_in):
        exp(x),     x in [-8, 0],    out Q15:  |err| <= 17 LSB (2^-15)     (softmax numerator; below -8 exp < 2^-11)
        GELU(x),    x in [-8, 8],    out Q12:  |err| <= 3 LSB (2^-12)      (MLP activation)
        SiLU(x),    x in [-8, 8],    out Q12:  |err| <= 3 LSB (2^-12)
        1/x,        x in [1, 64],    out Q15:  relative error <= 1.6 %     (the softmax denominator, one lookup)
        1/x + the Newton step neo_vec emits after it (the unit's scalars, Q14 / Q15):  relative error <= 0.12 %
        1/sqrt(x),  x in [1/8, 8],   out Q13:  relative error <= 0.6 %     (layer norm: the variance in the layer's
                                                                           calibrated units)
      Each bound is the linear interpolation's h^2 max|f''| / 8 on the table's segment h (h = 256 s_in) plus the rounding
      of the entries and of the interpolation (one LSB): exp 16 + 1, GELU 1.6 + 1, SiLU 1.0 + 1 LSB; 1/x 1.56 % at x = 1;
      1/sqrt(x) 0.59 % at x = 1/8; after the Newton step the error is the Q15 result's resolution at x = 64 (0.1 %).
Run: python3 sim/run_vec_unit.py [--v1 | --v2] [--dump DIR (tb_vec_unit.sv's vectors)]
"""
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "model")))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "compiler")))
from neosim import Design, load  # noqa: E402
import vec_ref as vr  # noqa: E402
from vec_ref import vop  # noqa: E402

RTL = ["neo_mem.sv", "vec_unit.sv"]
COLS, ROWS, ACC_ROWS = 8, 16, 64


def rng_rows(rng, m, cols, scale):
    """Random INT32 rows with a mix of magnitudes up to the full range (every lane's sign random)."""
    out = []
    for _ in range(m):
        r = []
        for _ in range(cols):
            mag = rng.choice([7, 15, 20, 26, 31])
            r.append(int(rng.integers(-(1 << mag), (1 << mag) - 1)) if mag < 31 else int(rng.integers(vr.I32_MIN, vr.I32_MAX)))
        out.append([int(v * scale) if abs(v * scale) <= vr.I32_MAX else v for v in r])
    return out


def v1_cases(cols=COLS, rows=ROWS, acc_rows=ACC_ROWS, seed=1):
    """The V1 programs as (writes, rbuf entries, accumulator rows) run in sequence on one unit."""
    import numpy as np
    import neo_vec as nv
    rng = np.random.default_rng(seed)
    M = 12
    cases = []
    T, P = vr.table_writes, vr.program_writes
    lut_exp = vr.build_lut(lambda x: math.exp(x) if x <= 0 else 1.0, 2.0 ** -11, 2.0 ** -15, vmin=0)
    lut_rcp = vr.build_lut(lambda x: 1.0 / x if x > 0.5 else 2.0, 2.0 ** -10, 2.0 ** -14)
    sc = [int(rng.integers(-32768, 32767)) for _ in range(cols)]
    bi = [int(rng.integers(-(1 << 20), 1 << 20)) for _ in range(cols)]
    kk = [int(rng.integers(-(1 << 24), 0)), int(rng.integers(0, 1 << 24)), 9000, 4096]
    # 1: the tables, the residual rows, the row statistics initialised
    w = T(vr.T_LUTV, lut_exp) + T(vr.T_LUTS, lut_rcp) + T(vr.T_SC, sc) + T(vr.T_BI, bi) + T(vr.T_K, kk)
    w += P([vop(vr.OP_RED, d=0, mode=1), vop(vr.OP_RSST, a=0, t=0, mode=0), vop(vr.OP_RED, d=1, mode=0),
            vop(vr.OP_RSST, a=1, t=1, mode=0), vop(vr.OP_EMIT)])
    rbuf = {i: [int(v) for v in rng.integers(-128, 128, size=rows)] for i in range(M)}
    cases.append((w, rbuf, rng_rows(rng, M, cols, 1)))
    # 2: element-wise: ADD_ROW, MUL_SCALE, CLAMP (S0 = K0, S1 = K1), SQR, three EMITs per row
    w = P([vop(vr.OP_ADD_ROW, sh=13), vop(vr.OP_EMIT), vop(vr.OP_MUL_SCALE, sh=9), vop(vr.OP_SLD, d=0, mode=1, k=0),
           vop(vr.OP_SLD, d=1, mode=1, k=1), vop(vr.OP_CLAMP, a=0, b=1), vop(vr.OP_EMIT), vop(vr.OP_SQR, sh=11), vop(vr.OP_EMIT)])
    cases.append((w, {}, rng_rows(rng, M, cols, 1)))
    # 3: the scalar side, 15 ops
    w = P([vop(vr.OP_SLD, d=0, mode=0, t=0), vop(vr.OP_SLD, d=1, mode=0, t=1), vop(vr.OP_BSUB, a=0), vop(vr.OP_LUT, sh=6),
           vop(vr.OP_EMIT), vop(vr.OP_RED, d=2, mode=1), vop(vr.OP_SLUT, d=3, a=2, sh=4), vop(vr.OP_BMUL, a=3, sh=10),
           vop(vr.OP_EMIT), vop(vr.OP_SMUL, d=0, a=2, b=3, sh=5), vop(vr.OP_SALU, d=1, a=0, b=2, mode=1),
           vop(vr.OP_RSST, a=1, t=0, mode=2), vop(vr.OP_RSST, a=0, t=1, mode=1), vop(vr.OP_SALU, d=2, a=1, b=0, mode=2),
           vop(vr.OP_EMIT)])
    cases.append((w, {}, rng_rows(rng, M, cols, 1)))
    # 4: softmax over the row's lanes: RED max, BSUB, BMUL by K2 (to the exp table's scale), LUT, RED sum, SLUT 1/sum, BMUL
    w = P([vop(vr.OP_RED, d=0, mode=0), vop(vr.OP_BSUB, a=0), vop(vr.OP_SLD, d=1, mode=1, k=2), vop(vr.OP_BMUL, a=1, sh=16),
           vop(vr.OP_LUT, sh=0), vop(vr.OP_RED, d=2, mode=1), vop(vr.OP_SLUT, d=3, a=2, sh=5), vop(vr.OP_BMUL, a=3, sh=14),
           vop(vr.OP_EMIT)])
    cases.append((w, {}, rng_rows(rng, M, cols, 1)))
    # 5: layer norm over the row's lanes (two passes: the statistics, then RELOAD and the normalisation)
    w = T(vr.T_LUTS, nv.lut_rsqrt(2.0 ** -12, 2.0 ** -12)) + T(vr.T_K, [kk[0], kk[1], 9000, 32768 // cols])
    w += P([vop(vr.OP_RED, d=0, mode=1), vop(vr.OP_SLD, d=1, mode=1, k=3), vop(vr.OP_SMUL, d=0, a=0, b=1, sh=15),
            vop(vr.OP_SQR, sh=6), vop(vr.OP_RED, d=2, mode=1), vop(vr.OP_SMUL, d=2, a=2, b=1, sh=15),
            vop(vr.OP_SMUL, d=3, a=0, b=0, sh=6), vop(vr.OP_SALU, d=2, a=2, b=3, mode=1), vop(vr.OP_SLUT, d=3, a=2, sh=2),
            vop(vr.OP_RELOAD), vop(vr.OP_BSUB, a=0), vop(vr.OP_BMUL, a=3, sh=12), vop(vr.OP_MUL_SCALE, sh=14), vop(vr.OP_EMIT)])
    small = [[int(v) for v in rng.integers(-3000, 3000, size=cols)] for _ in range(M)]
    cases.append((w, {}, small))
    # 6: 16 ops, no END: the row ends after slot 15
    w = P([vop(vr.OP_SLD, d=0, mode=1, k=2)] + [vop(vr.OP_BSUB, a=0), vop(vr.OP_EMIT)] * 7 + [vop(vr.OP_RED, d=1, mode=1)])
    cases.append((w, {}, rng_rows(rng, M, cols, 1)))
    # 7: the saturating ends of ADD_ROW (the residual shifted to 2^30), BMUL (by the constant K3 = 4096) and SQR, each
    #    from the row as read (RELOAD), on rows of full-range values
    w = P([vop(vr.OP_ADD_ROW, sh=23), vop(vr.OP_EMIT), vop(vr.OP_RELOAD), vop(vr.OP_BMUL, a=3, ka=1, sh=0), vop(vr.OP_EMIT),
           vop(vr.OP_RELOAD), vop(vr.OP_SQR, sh=0), vop(vr.OP_EMIT)])
    big = [[int(v) for v in rng.integers(vr.I32_MIN, vr.I32_MAX, size=cols)] for _ in range(M)]
    cases.append((w, {}, big))
    return cases


class VecDut:
    """vec_unit under neosim, driven as neo_core drives it: host writes, RBUF writes, go, the sequencer's done, the
    accumulator's registered read (the row presented the cycle after the read and held)."""

    def __init__(self, cols=COLS, rows=ROWS, acc_rows=ACC_ROWS):
        self.C, self.R, self.A = cols, rows, acc_rows
        self.d = Design(load([os.path.join(HERE, "..", "rtl", f) for f in RTL]))
        self.d.elaborate("vec_unit", {"COLS": cols, "ROWS": rows, "ACC_ROWS": acc_rows})
        d = self.d
        self.p = {n: d.cell_of(n) for n in ("rst_n", "vh_we", "vh_addr", "vh_wdata", "go", "seq_done", "cfg_m", "acc_rd_en",
                                            "acc_rd_idx", "rbuf_we", "rbuf_we2", "rbuf_waddr", "active", "busy", "done",
                                            "rd_q", "out_valid", "err_clear", "ctrl_err_sticky", "perr_sticky",
                                            "ctrl_fault_inject", "tbl_fault_inject")}
        self.acc = d.array_of("acc_rd_data")
        self.rbw = d.array_of("rbuf_wdata")
        self.rbw2 = d.array_of("rbuf_wdata2")
        self.out = d.array_of("out_row")
        for n in ("vh_we", "go", "seq_done", "rbuf_we", "rbuf_we2", "err_clear", "ctrl_fault_inject", "tbl_fault_inject"):
            self.p[n].v = 0
        self.p["rst_n"].v = 0
        self.d.tick(); self.d.tick()
        self.p["rst_n"].v = 1
        self.d.tick()

    def write(self, addr, data):
        self.p["vh_we"].v = 1; self.p["vh_addr"].v = addr; self.p["vh_wdata"].v = data & 0xFFFFFFFF
        self.d.tick()
        self.p["vh_we"].v = 0

    def write_rbuf(self, idx, ent):
        self.p["rbuf_we"].v = 1; self.p["rbuf_waddr"].v = idx
        for c in range(self.R):
            self.rbw[c].v = int(ent[c]) & 0xFF
        self.d.tick()
        self.p["rbuf_we"].v = 0

    def run(self, rows, fault_at=None, max_cycles=100000):
        """go, the sequencer's done two cycles later, then the unit's reads served until its done. Returns (emitted rows,
        cycles from the sequencer's done to the unit's done). fault_at = n: the control fault held on the n-th cycle."""
        self.p["cfg_m"].v = len(rows) & (self.A - 1)
        self.p["go"].v = 1
        self.d.tick()
        self.p["go"].v = 0
        self.d.tick()
        self.p["seq_done"].v = 1
        out, n = [], 0
        while True:
            en, idx = self.p["acc_rd_en"].v, self.p["acc_rd_idx"].v
            if self.p["out_valid"].v:
                out.append([vr.sext(c.v, 32) for c in self.out])
            done = self.p["done"].v
            self.p["ctrl_fault_inject"].v = 1 if (fault_at is not None and n == fault_at) else 0
            self.d.tick()
            self.p["seq_done"].v = 0
            if en:                                            # the accumulator's registered read: the row from this edge on
                for j in range(self.C):
                    self.acc[j].v = int(rows[idx][j]) & 0xFFFFFFFF
            if done:
                break
            n += 1
            assert n < max_cycles, "the unit did not finish"
        self.p["ctrl_fault_inject"].v = 0
        return out, n

    def flags(self):
        return self.p["ctrl_err_sticky"].v, self.p["perr_sticky"].v

    def clear(self):
        self.p["err_clear"].v = 1
        self.d.tick()
        self.p["err_clear"].v = 0


def v1():
    t0 = time.time()
    fails = 0
    dut = VecDut()
    vu = vr.VecUnit(COLS, ROWS, ACC_ROWS)
    cases = v1_cases()
    for ci, (writes, rbuf, acc) in enumerate(cases):
        for a, d in writes:
            dut.write(a, d)
            vu.write(a, d)
        for idx, ent in sorted(rbuf.items()):
            dut.write_rbuf(idx, ent)
            vu.write_rbuf(idx, ent)
        assert dut.p["active"].v == 1
        got, cyc = dut.run(acc)
        exp, ecyc = vu.run(acc)
        ok = got == exp and cyc == ecyc and dut.flags() == (0, 0)
        fails += not ok
        ops = sorted({vr.OP_NAMES[vr.decode(w)["op"]] for w in vu.prog if vr.decode(w)["op"]})
        print(f"  V1 case {ci + 1}: {len(acc)} rows, {len(exp)} rows emitted, {cyc} cycles (model {ecyc}), flags {dut.flags()}, "
              f"ops {' '.join(ops)} -> {'PASS' if ok else 'FAIL'}")
        if not ok and got != exp:
            for r, (g, e) in enumerate(zip(got, exp)):
                if g != e:
                    print(f"     first differing row {r}: got {g}\n                              exp {e}")
                    break
            if len(got) != len(exp):
                print(f"     {len(got)} rows emitted, expected {len(exp)}")
    # faults, on case 3's program (it reads LUTV and LUTS; the tables are the ones case 5 left)
    writes, rbuf, acc = cases[2]
    for a, d in writes:
        dut.write(a, d)
        vu.write(a, d)
    got, _ = dut.run(acc, fault_at=9)                          # the primary control's row index flipped once, mid-row
    f_ctrl = dut.flags()
    dut.clear()
    writes2, _, acc2 = cases[1]                                # case 2's program (no RS state): the parity fault alone
    for a, d in writes2:
        dut.write(a, d)
        vu.write(a, d)
    dut.p["tbl_fault_inject"].v = 1                            # a reserved bit of slot 0's op word, as used
    got_t, _ = dut.run(acc2)
    dut.p["tbl_fault_inject"].v = 0
    f_tbl = dut.flags()
    exp_t, _ = vu.run(acc2)
    dut.clear()
    f_clear = dut.flags()
    for a, d in writes:                                        # back to case 3's program for the LUT fault
        dut.write(a, d)
    lane = 3                                                   # a LUTV entry of one lane corrupted in its leaf (bit 7)
    cell = dut.d.cells
    leaf = [k for k in cell if k.startswith(f"top.g_lut[{lane}].u_lutv.u_mem.mem[") or k.startswith(f"top.g_lut[{lane}].u_lutv.mem[")]
    for k in leaf:
        cell[k].v ^= 1 << 7
    got_l, _ = dut.run(acc)
    f_lut = dut.flags()
    for k in leaf:
        cell[k].v ^= 1 << 7
    dut.clear()
    cell["top.kreg[2]"].v ^= 1 << 4                            # a K word corrupted in place; case 6's SLD reads K2
    writes6, _, acc6 = cases[5]
    for a, d in writes6:
        dut.write(a, d)
    dut.run(acc6)
    f_k = dut.flags()
    ok_f = (f_ctrl[0] == 1 and f_tbl == (0, 1) and got_t == exp_t and f_clear == (0, 0) and f_lut == (0, 1) and f_k == (0, 1))
    fails += not ok_f
    print(f"  V1 faults: control row index flipped -> flags {f_ctrl} (ctrl_err expected); slot 0 reserved bit as used -> {f_tbl} "
          f"(perr alone, rows {'unchanged' if got_t == exp_t else 'CHANGED'}); after err_clear {f_clear}; a lane's LUTV entry "
          f"corrupted ({len(leaf)} words of the leaf) -> {f_lut}; K2 corrupted -> {f_k} -> {'PASS' if ok_f else 'FAIL'}")
    sat_need = {"ADD_ROW", "MUL_SCALE", "SQR", "BSUB", "BMUL"}
    ok_s = sat_need <= vu.sat_ops
    fails += not ok_s
    print(f"  V1 saturation reached (some lane at an INT32 bound) by {' '.join(sorted(vu.sat_ops))} "
          f"(required {' '.join(sorted(sat_need))}) -> {'PASS' if ok_s else 'FAIL'}")
    print(f"  V1: {len(cases)} programs, {sum(len(c[2]) for c in cases)} rows, {time.time() - t0:.0f} s")
    return fails


def v2():
    """LUT accuracy of the compiler's tables (neo_vec) through the unit's interpolation (vec_ref.lut_eval)."""
    import neo_vec as nv
    fails = 0

    def measure(words, s_in, s_out, c, f, lo, hi, rel):
        worst, worst_x = 0.0, None
        u_lo, u_hi = max(-32768, int(math.ceil((lo - c) / s_in))), min(32767, int(math.floor((hi - c) / s_in)))
        for u in range(u_lo, u_hi + 1):
            x = c + u * s_in
            y = vr.lut_eval(words, u) * s_out
            ref = f(x)
            e = abs(y - ref) / abs(ref) if rel else abs(y - ref) / s_out
            if e > worst:
                worst, worst_x = e, x
        return worst, worst_x, u_hi - u_lo + 1

    cases = [
        ("exp", nv.lut_exp(), nv.EXP_S_IN, nv.EXP_S_OUT, 0.0, math.exp, -8.0, 0.0, False, 17.0),
        ("GELU", nv.lut_gelu(), nv.ACT_S_IN, nv.ACT_S_OUT, 0.0, nv.gelu, -8.0, 8.0, False, 3.0),
        ("SiLU", nv.lut_silu(), nv.ACT_S_IN, nv.ACT_S_OUT, 0.0, nv.silu, -8.0, 8.0, False, 3.0),
        ("1/x", nv.lut_recip(), nv.RCP_S_IN, nv.RCP_S_OUT, nv.RCP_C, lambda x: 1.0 / x, nv.RCP_LO, nv.RCP_HI, True, 0.016),
        ("1/sqrt(x)", nv.lut_rsqrt(), nv.RSQ_S_IN, nv.RSQ_S_OUT, nv.RSQ_C, lambda x: 1.0 / math.sqrt(x), nv.RSQ_LO, nv.RSQ_HI,
         True, 0.006),
    ]
    for name, words, s_in, s_out, c, f, lo, hi, rel, bound in cases:
        worst, wx, n = measure(words, s_in, s_out, c, f, lo, hi, rel)
        ok = worst <= bound
        fails += not ok
        unit = "relative" if rel else "LSB"
        print(f"  V2 {name:<10} x in [{lo:g}, {hi:g}] ({n} LUT inputs): max error {worst:.4g} {unit} at x = {wx:.4g} "
              f"(bound {bound:g}) -> {'PASS' if ok else 'FAIL'}")
    # the reciprocal with the Newton step neo_vec emits (prog_softmax_norm's scalars, recip_newton_int)
    worst, wx, steps = 0.0, None, 20000
    for i in range(steps + 1):
        x = nv.RCP_LO * (nv.RCP_HI / nv.RCP_LO) ** (i / steps)
        e = abs(nv.recip_newton_model(x) - 1.0 / x) * x
        if e > worst:
            worst, wx = e, x
    ok = worst <= 0.0012
    fails += not ok
    print(f"  V2 1/x + Newton x in [1, 64] ({steps + 1} points, geometric): max relative error {worst:.4g} at x = {wx:.4g} "
          f"(bound 0.0012) -> {'PASS' if ok else 'FAIL'}")
    return fails


def main():
    if "--dump" in sys.argv:
        out = sys.argv[sys.argv.index("--dump") + 1]
        n, _ = vr.write_vectors(out, v1_cases(), COLS, ROWS, ACC_ROWS)
        print(f"tb_vec_unit vectors: {n} expected rows in {os.path.abspath(out)}")
        return 0
    fails = 0
    if "--v2" not in sys.argv:
        print("V1: the vector unit's ops under neosim, bit-exact against model/vec_ref.py:")
        fails += v1()
    if "--v1" not in sys.argv:
        print("V2: LUT accuracy against float references (compiler/neo_vec.py's tables, the unit's interpolation):")
        fails += v2()
    print(f"RESULT: {'ALL PASS' if fails == 0 else f'{fails} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
