#!/usr/bin/env python3
"""
run_requant.py -- requant.sv under neosim: bit-exact against the fixed-point model and within one
LSB of the floating-point requantization it approximates.

  Q1  256 random accumulator rows, per-column random (mult, shift, zp): every INT8 output equals the
      Python fixed-point model (round-half-up, saturation) bit for bit
  Q2  same rows with ReLU: outputs below zp are clamped to zp
  Q3  float sanity: |q - clip(round(acc * mult / 2^shift) + zp)| <= 1 for every element
  Q4  the three-stage pipeline (drop 0.42): full-range tables (mult 0..65535, shift 0..31, zp -128..127), accumulators
      aimed at the saturation and ReLU boundaries (r + zp around -128 / 127, r around -256 / 255) plus random 32-bit
      extremes, in_valid with random gaps: every row comes out exactly LAT = 3 cycles after it went in (out_valid nowhere
      else), bit-exact against the model, with and without ReLU
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from neosim import Design, load  # noqa: E402

COLS, PW, MW = 8, 32, 16
LAT = 3                                                  # in_valid -> out_valid (drop 0.42: three stages)


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

    # Q4: full-range tables, boundary-aimed accumulators, random valid gaps, the exact latency
    mult = rng.integers(0, 65536, size=COLS)
    mult[:3] = [0, 1, 65535]
    shift = rng.integers(0, 32, size=COLS)
    shift[:3] = [0, 31, 0]
    zp = rng.integers(-128, 128, size=COLS)
    zp[:3] = [-128, 127, 0]
    for j in range(COLS):
        p["tbl_we"].v = 1
        p["tbl_addr"].v = j
        p["tbl_mult"].v = int(mult[j])
        p["tbl_shift"].v = int(shift[j])
        p["tbl_zp"].v = int(zp[j]) & 0xFF
        d.tick()
    p["tbl_we"].v = 0

    def aimed(j):
        """An accumulator whose requantized value r lands near a saturation boundary of column j (or a random extreme)."""
        k = int(rng.integers(0, 4))
        if k == 0 or mult[j] == 0:
            return int(rng.choice([(1 << 31) - 1, -(1 << 31), int(rng.integers(-(1 << 31), 1 << 31))]))
        target = int(rng.choice([127, -128, 255, -256, 256, -257, 0, -1])) - (int(zp[j]) if k == 1 else 0) + int(rng.integers(-3, 4))
        acc = (target << int(shift[j])) // int(mult[j]) + int(rng.integers(-2, 3))
        return max(-(1 << 31), min((1 << 31) - 1, acc))

    N4 = 400
    rows4 = [[aimed(j) for j in range(COLS)] for _ in range(N4)]
    for relu in (0, 1):
        p["relu"].v = relu
        sched = [int(rng.random() < 0.7) for _ in range(N4 * 2)]        # in_valid with gaps
        sent, got4, bad_lat, i = [], [], 0, 0
        for s in range(len(sched) + LAT + 2):
            v = sched[s] if s < len(sched) and i < N4 else 0
            p["in_valid"].v = v
            for j in range(COLS):
                in_acc[j].v = rows4[i][j] & 0xFFFFFFFF if v else 0
            d.tick()
            if v:
                sent.append((s, i))
                i += 1
            if p["out_valid"].v:
                # the row that entered LAT cycles earlier (tick s - LAT + 1 counts the entry tick as the first)
                src = [r for (t, r) in sent if t == s - LAT + 1]
                if not src:
                    bad_lat += 1
                    continue
                got4.append((src[0], [(c.v - 256) if c.v >= 128 else c.v for c in out_q]))
        lost = i - len(got4)
        ok = bad_lat == 0 and lost == 0 and all(
            q == [model(rows4[r][j], mult[j], shift[j], zp[j], relu) for j in range(COLS)] for r, q in got4)
        print(f"  Q4 {'relu' if relu else 'linear'}: {i} rows with gaps, full-range tables, boundary-aimed accumulators: "
              f"{len(got4)} out exactly {LAT} cycles after entry ({bad_lat} at another cycle, {lost} lost), "
              f"bit-exact vs model -> {'PASS' if ok else 'FAIL'}")
        fails += not ok
    print(f"RESULT: {'ALL PASS' if fails == 0 else f'{fails} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
