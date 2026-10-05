#!/usr/bin/env python3
"""
run_requant.py -- requant.sv under neosim: bit-exact against the fixed-point model and within one
LSB of the floating-point requantization it approximates.

  Q1  256 random accumulator rows, per-column random (mult, shift, zp): every INT8 output equals the
      Python fixed-point model (round-half-up, saturation) bit for bit
  Q2  same rows with ReLU: outputs below zp are clamped to zp
  Q3  float sanity: |q - clip(round(acc * mult / 2^shift) + zp)| <= 1 for every element
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from neosim import Design, load  # noqa: E402

COLS, PW, MW = 8, 32, 16


def model(acc, mult, shift, zp, relu):
    bias = (1 << shift) >> 1
    r = (int(acc) * int(mult) + bias) >> shift          # Python >> is arithmetic (floor) like >>>
    v = r + int(zp)
    if relu and v < zp:
        v = zp
    return max(-128, min(127, v))


def main():
    rng = np.random.default_rng(21)
    d = Design(load([os.path.join(HERE, "..", "rtl", "requant.sv")]))
    d.elaborate("requant", {"COLS": COLS, "PW": PW, "MW": MW})
    p = {n: d.cell_of(n) for n in ("rst_n", "tbl_we", "tbl_addr", "tbl_mult", "tbl_shift", "tbl_zp", "relu",
                                    "in_valid", "out_valid")}
    in_acc, out_q = d.array_of("in_acc"), d.array_of("out_q")
    for n in ("tbl_we", "in_valid", "relu"):
        p[n].v = 0
    p["rst_n"].v = 0
    d.tick(); d.tick()
    p["rst_n"].v = 1
    mult = rng.integers(16384, 65536, size=COLS)
    shift = rng.integers(14, 24, size=COLS)
    zp = rng.integers(-12, 13, size=COLS)
    for j in range(COLS):
        p["tbl_we"].v = 1
        p["tbl_addr"].v = j
        p["tbl_mult"].v = int(mult[j])
        p["tbl_shift"].v = int(shift[j])
        p["tbl_zp"].v = int(zp[j]) & 0xFF
        d.tick()
    p["tbl_we"].v = 0

    def run(rows, relu):
        p["relu"].v = relu
        got = []
        for s in range(len(rows) + 3):
            p["in_valid"].v = 1 if s < len(rows) else 0
            for j in range(COLS):
                in_acc[j].v = int(rows[s][j]) & 0xFFFFFFFF if s < len(rows) else 0
            d.tick()
            if p["out_valid"].v:
                got.append([(c.v - 256) if c.v >= 128 else c.v for c in out_q])
        return np.array(got)

    N = 256
    rows = rng.integers(-(1 << 20), 1 << 20, size=(N, COLS))
    rows[:8, :] = [[(1 << 31) - 1, -(1 << 31), 0, 1, -1, 123456, -123456, 7] * (COLS // 8)] * 8  # extremes
    fails = 0
    for relu in (0, 1):
        got = run(rows, relu)
        ref = np.array([[model(rows[i][j], mult[j], shift[j], zp[j], relu) for j in range(COLS)] for i in range(N)])
        ok = got.shape == ref.shape and np.array_equal(got, ref)
        print(f"  Q{relu + 1} {'relu' if relu else 'linear'}: {N} rows x {COLS} columns vs fixed-point model -> {'PASS' if ok else 'FAIL'}")
        if not ok:
            idx = np.argwhere(got != ref)[:3]
            print("     first differences:", idx.tolist(), got[tuple(idx.T)] if len(idx) else "", ref[tuple(idx.T)] if len(idx) else "")
        fails += not ok
        if relu == 0:
            fl = np.array([[max(-128, min(127, int(round(rows[i][j] * mult[j] / 2 ** shift[j])) + zp[j])) for j in range(COLS)] for i in range(N)])
            worst = int(np.max(np.abs(got - fl)))
            print(f"  Q3 float sanity: max |q - float requantization| = {worst} LSB (expect <= 1) -> {'PASS' if worst <= 1 else 'FAIL'}")
            fails += worst > 1
    print(f"RESULT: {'ALL PASS' if fails == 0 else f'{fails} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
