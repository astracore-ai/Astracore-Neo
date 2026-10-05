#!/usr/bin/env python3
"""
run_conv_v02.py -- execute whole convolutions on rtl/neo_mac_core_v02.sv under neosim.

The Python below is the per-core SEQUENCER (its RTL is drop 0.4): for each output-channel
tile it writes the input tile into the feeder buffer once, then for every (channel tile,
ky, kx) it loads the weight tile into the shadow chain DURING the previous run, starts the
run (token + rows), and finally drains the accumulator. Results are compared with a direct
numpy convolution; the accumulator's own ABFT is exercised by flipping a bit in acc memory
(a simulator poke, the equivalent of an Xcelium force) before the drain.

  C1  conv 40x6x6 -> 7x6x6, k3 s1 p1, 16x8 core (3 channel tiles, 1 output tile)
  C2  conv 20x7x7 -> 10x4x4, k3 s2 p1, 16x8 core (2 channel tiles, 2 output tiles)
  C3  conv 35x5x5 -> 33x5x5, k3 s1 p1, 32x32 core (2 channel tiles, 2 output tiles)
  C4  accumulator bit-flip at drain -> acc_abft_err on exactly that row
"""
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "compiler"))
from neosim import Design, load  # noqa: E402
from neo_compile import direct_conv  # noqa: E402

RTL = ["delay_line.sv", "mac_pe.sv", "skew_in.sv", "deskew_out.sv", "systolic_array.sv",
       "abft_checker.sv", "neo_mac_core.sv", "act_feeder.sv", "acc_bank.sv", "neo_mac_core_v02.sv"]


def pow2_at_least(n):
    return 1 << max(1, (n - 1).bit_length())


def bits(v, w):
    return int(v) & ((1 << w) - 1)


def sbits(v, w):
    v = int(v) & ((1 << w) - 1)
    return v - (1 << w) if v >= (1 << (w - 1)) else v


class CoreV02:
    """Port-level driver for one neo_mac_core_v02 instance under neosim."""

    def __init__(self, rows, cols, acc_rows, abuf_depth, fault_row=0, fault_col=0):
        self.R, self.C = rows, cols
        self.LAT = 2 * rows + cols            # PE_LAT = 2
        self.WCW = 8 + (0 if rows <= 1 else (rows - 1).bit_length()) + 1
        self.IDXW = max(1, (acc_rows - 1).bit_length())
        self.AW = max(1, (abuf_depth - 1).bit_length())
        rtl = os.path.join(HERE, "..", "rtl")
        self.d = Design(load([os.path.join(rtl, f) for f in RTL]))
        self.d.elaborate("neo_mac_core_v02", {"ROWS": rows, "COLS": cols, "ACC_ROWS": acc_rows,
                                              "ABUF_DEPTH": abuf_depth, "FAULT_ROW": fault_row,
                                              "FAULT_COL": fault_col})
        d = self.d
        self.p = {n: d.cell_of(n) for n in (
            "rst_n", "abuf_we", "abuf_waddr", "cfg_h", "cfg_w", "cfg_ho", "cfg_wo", "cfg_s", "cfg_p",
            "cfg_ky", "cfg_kx", "acc_first", "start", "busy", "w_load", "wc_in", "rd_en", "rd_idx",
            "rd_chk", "rd_valid", "abft_clear", "array_abft_err", "array_abft_sticky",
            "acc_abft_err", "acc_abft_sticky", "fault_inject")}
        self.abuf_wdata = d.array_of("abuf_wdata")
        self.w_in = d.array_of("w_in")
        self.rd_data = d.array_of("rd_data")
        for n in ("abuf_we", "acc_first", "start", "w_load", "rd_en", "abft_clear", "fault_inject"):
            self.p[n].v = 0
        self.p["rst_n"].v = 0
        self.tick(2)
        self.p["rst_n"].v = 1
        self.tick(1)
        self.clocks = 0

    def tick(self, n=1):
        for _ in range(n):
            self.d.tick()
        self.clocks = getattr(self, "clocks", 0) + n

    def write_abuf(self, pixels):
        """pixels: [n_pixels, ROWS] channel values; address = pixel index."""
        for a, row in enumerate(pixels):
            self.p["abuf_we"].v = 1
            self.p["abuf_waddr"].v = a
            for c in range(self.R):
                self.abuf_wdata[c].v = bits(row[c], 8)
            self.tick()
        self.p["abuf_we"].v = 0

    def drive_weight_row(self, Wt, t):
        """Shadow-chain shift: on step t present tile row ROWS-1-t. Wt: [ROWS, COLS]."""
        self.p["w_load"].v = 1
        r = self.R - 1 - t
        for j in range(self.C):
            self.w_in[j].v = bits(Wt[r, j], 8)
        self.p["wc_in"].v = bits(int(Wt[r, :].sum()), self.WCW)

    def load_weights_idle(self, Wt):
        for t in range(self.R):
            self.drive_weight_row(Wt, t)
            self.tick()
        self.p["w_load"].v = 0
        self.tick()

    def run(self, ky, kx, first, next_tile=None):
        """Start a run at kernel position (ky,kx); while it streams, shift next_tile into the
        shadow chain from LAT cycles after the token (the double-buffering rule)."""
        self.p["cfg_ky"].v, self.p["cfg_kx"].v = bits(ky, 16), bits(kx, 16)
        self.p["acc_first"].v = 1 if first else 0
        self.p["start"].v = 1
        self.tick()                           # start sampled; token visible next cycle (count 0)
        self.p["start"].v = 0
        count = 0
        loaded = next_tile is None
        while True:
            count += 1
            if next_tile is not None and self.LAT + 1 <= count <= self.LAT + self.R:
                self.drive_weight_row(next_tile, count - self.LAT - 1)
            else:
                self.p["w_load"].v = 0
            self.tick()
            if next_tile is not None and count == self.LAT + self.R:
                loaded = True
            if self.p["busy"].v == 0 and loaded and count > 2:
                break
        self.p["w_load"].v = 0

    def drain(self, n_rows):
        self.tick(self.LAT + 3)               # let the last rows reach the accumulator
        out, errs = [], []
        for r in range(n_rows + 1):
            self.p["rd_en"].v = 1 if r < n_rows else 0
            self.p["rd_idx"].v = r if r < n_rows else 0
            self.tick()
            if self.p["rd_valid"].v:
                out.append([sbits(c.v, 32) for c in self.rd_data])
                errs.append(self.p["acc_abft_err"].v)
        self.p["rd_en"].v = 0
        return np.array(out, dtype=np.int64), errs


def conv_on_core(core: CoreV02, x, wgt, s, p, poke=None):
    """Lower a conv (K ordered ky, kx, channel) onto the v0.2 core and execute it.
    poke = (row, col) flips one bit of acc memory before the drain of output tile 0."""
    cin, h, w = x.shape
    cout, _, k, _ = wgt.shape
    ho = (h + 2 * p - k) // s + 1
    wo = (w + 2 * p - k) // s + 1
    M = ho * wo
    R, C = core.R, core.C
    for n, v in (("cfg_h", h), ("cfg_w", w), ("cfg_ho", ho), ("cfg_wo", wo), ("cfg_s", s), ("cfg_p", p)):
        core.p[n].v = bits(v, 16)
    cin_tiles, cout_tiles = math.ceil(cin / R), math.ceil(cout / C)
    y = np.zeros((cout, ho, wo), dtype=np.int64)
    acc_errs = []
    for nt in range(cout_tiles):
        runs = [(ct, ky, kx) for ct in range(cin_tiles) for ky in range(k) for kx in range(k)]

        def tile(ct, ky, kx):
            Wt = np.zeros((R, C), dtype=np.int64)
            rr = min(R, cin - ct * R)
            cc = min(C, cout - nt * C)
            for r in range(rr):
                for c in range(cc):
                    Wt[r, c] = wgt[nt * C + c, ct * R + r, ky, kx]
            return Wt

        def pixels(ct):
            px = np.zeros((h * w, R), dtype=np.int64)
            rr = min(R, cin - ct * R)
            for yy in range(h):
                for xx in range(w):
                    px[yy * w + xx, :rr] = x[ct * R: ct * R + rr, yy, xx]
            return px

        core.write_abuf(pixels(0))
        core.load_weights_idle(tile(*runs[0]))
        for i, (ct, ky, kx) in enumerate(runs):
            if i > 0 and ky == 0 and kx == 0:
                core.write_abuf(pixels(ct))       # feeder is idle between channel tiles
            nxt = tile(*runs[i + 1]) if i + 1 < len(runs) else None
            core.run(ky, kx, first=(i == 0), next_tile=nxt)
        if poke is not None and nt == 0:
            core.tick(core.LAT + 3)
            cell = core.d.cells[f"top.u_acc.acc[{poke[0]},{poke[1]}]"]
            cell.v ^= 1 << 5
        out, errs = core.drain(M)
        acc_errs.append(errs)
        cc = min(C, cout - nt * C)
        for m in range(M):
            for c in range(cc):
                y[nt * C + c, m // wo, m % wo] = out[m, c]
    return y, acc_errs


def check(label, core, x, wgt, s, p):
    t0 = time.time()
    got, acc_errs = conv_on_core(core, x, wgt, s, p)
    ref = direct_conv(x, wgt, s, p)
    ok = np.array_equal(got, ref) and sum(sum(e) for e in acc_errs) == 0 and core.p["array_abft_sticky"].v == 0
    cin, h, w = x.shape
    cout, _, k, _ = wgt.shape
    print(f"  {label}: conv {cin}x{h}x{w} -> {cout}x{ref.shape[1]}x{ref.shape[2]} k{k} s{s} p{p} on {core.R}x{core.C} core: "
          f"{math.ceil(cin / core.R)} channel tiles x {k * k} positions x {math.ceil(cout / core.C)} output tiles, "
          f"{core.clocks} clocks, {time.time() - t0:.1f} s, acc ABFT flags {sum(sum(e) for e in acc_errs)} -> {'PASS' if ok else 'FAIL'}")
    if not ok:
        diff = np.argwhere(got != ref)
        print("     first differences (cout, oy, ox):", diff[:5].tolist(),
              "got", [int(got[tuple(i)]) for i in diff[:5]], "ref", [int(ref[tuple(i)]) for i in diff[:5]])
    return ok


def main():
    rng = np.random.default_rng(5)
    fails = 0
    print("Convolutions executed on neo_mac_core_v02 RTL under neosim (compiler lowering, overlapped weight loads):")

    core = CoreV02(16, 8, acc_rows=64, abuf_depth=64)
    x = rng.integers(-128, 128, size=(40, 6, 6), dtype=np.int64)
    wgt = rng.integers(-128, 128, size=(7, 40, 3, 3), dtype=np.int64)
    fails += not check("C1", core, x, wgt, 1, 1)

    core = CoreV02(16, 8, acc_rows=64, abuf_depth=64)
    x = rng.integers(-128, 128, size=(20, 7, 7), dtype=np.int64)
    wgt = rng.integers(-128, 128, size=(10, 20, 3, 3), dtype=np.int64)
    fails += not check("C2", core, x, wgt, 2, 1)

    if "--skip32" in sys.argv:          # the 32x32 case takes hours under neosim; Verilator covers 32x32 in CI
        print("  C3: 32x32 datapath case skipped (--skip32); covered by the Verilator run of tb_neo_mac_core")
    else:
        core = CoreV02(32, 32, acc_rows=32, abuf_depth=32)
        x = rng.integers(-128, 128, size=(35, 5, 5), dtype=np.int64)
        wgt = rng.integers(-128, 128, size=(33, 35, 3, 3), dtype=np.int64)
        fails += not check("C3", core, x, wgt, 1, 1)

    # C4: accumulator memory fault -> caught at drain
    core = CoreV02(16, 8, acc_rows=64, abuf_depth=64)
    x = rng.integers(-128, 128, size=(16, 4, 4), dtype=np.int64)
    wgt = rng.integers(-128, 128, size=(8, 16, 3, 3), dtype=np.int64)
    got, acc_errs = conv_on_core(core, x, wgt, 1, 1, poke=(3, 4))
    ref = direct_conv(x, wgt, 1, 1)
    flagged = [i for i, e in enumerate(acc_errs[0]) if e]
    wrong = np.argwhere(got != ref).tolist()
    ok4 = flagged == [3] and wrong == [[4, 0, 3]] and core.p["acc_abft_sticky"].v == 1
    print(f"  C4: bit 5 of acc[3][4] flipped before drain: rows flagged {flagged} (expect [3]), "
          f"outputs wrong {wrong} (expect [[4, 0, 3]]), sticky={core.p['acc_abft_sticky'].v} -> {'PASS' if ok4 else 'FAIL'}")
    fails += not ok4
    print(f"RESULT: {'ALL PASS' if fails == 0 else f'{fails} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
