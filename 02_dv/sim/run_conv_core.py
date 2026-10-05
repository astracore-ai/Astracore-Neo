#!/usr/bin/env python3
"""
run_conv_core.py -- whole convolutions on rtl/neo_core.sv (hardware sequencer) under neosim.

The host side is now what a DMA and a driver would do: write the activation channel tiles into
the feeder buffer, write every weight tile into the weight buffer, write the layer descriptor,
pulse go, and collect rd_data while rd_valid, until done. Everything else (run enumeration,
overlapped shadow loads, swap tokens, drain) is the sequencer RTL.

  C1  conv 40x6x6 -> 7x6x6, k3 s1 p1, 16x8 core (3 channel tiles)
  C2  conv 20x7x7 -> 10x4x4, k3 s2 p1, 16x8 core (2 channel tiles, 2 output tiles)
  C3  conv 35x5x5 -> 33x5x5, k3 s1 p1, 32x32 core (2 channel tiles, 2 output tiles)
  C4  accumulator bit flip while the sequencer waits for the drain -> acc_abft on exactly that row
  C5  feeder control fault (bit 0 of the primary address) on one valid row -> ctrl_err_sticky set
      by the duplicated generator's comparator; with no fault the comparator never fires (C1-C3)
  C6  requantized INT8 output (per-channel mult/shift/zp tables, ReLU) bit-exact vs the model
  C7  R10: primary sequencer's drain index corrupted -> lockstep comparator flags, ABFT silent
  C8  R10: primary requant input corrupted (bit 12, i.e. ~4 LSB of INT8) -> duplicated-stage comparator flags
  C9  weight-buffer ECC: flipped bits in stored codewords corrected on read, result bit-exact, ce flagged
  C10 requant table parity: a corrupted multiplier is flagged on use
      (a bit-0 fault is masked by quantization and harmless, which is why the hook uses bit 12)
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
       "abft_checker.sv", "neo_mac_core.sv", "act_feeder.sv", "acc_bank.sv", "neo_mac_core_v02.sv",
       "core_seq.sv", "ecc39.sv", "wbuf_mem.sv", "requant.sv", "neo_core.sv"]
S_WAIT = 4


def bits(v, w):
    return int(v) & ((1 << w) - 1)


def sbits(v, w):
    v = int(v) & ((1 << w) - 1)
    return v - (1 << w) if v >= (1 << (w - 1)) else v


class NeoCore:
    def __init__(self, rows, cols, acc_rows, abuf_depth, wbuf_depth):
        self.R, self.C = rows, cols
        self.WCW = 8 + (0 if rows <= 1 else (rows - 1).bit_length()) + 1
        rtl = os.path.join(HERE, "..", "rtl")
        self.d = Design(load([os.path.join(rtl, f) for f in RTL]))
        self.d.elaborate("neo_core", {"ROWS": rows, "COLS": cols, "ACC_ROWS": acc_rows,
                                      "ABUF_DEPTH": abuf_depth, "WBUF_DEPTH": wbuf_depth})
        d = self.d
        self.p = {n: d.cell_of(n) for n in (
            "rst_n", "abuf_we", "abuf_waddr", "wbuf_we", "wbuf_waddr", "wcbuf_wdata",
            "cfg_h", "cfg_w", "cfg_ho", "cfg_wo", "cfg_s", "cfg_p", "cfg_k", "cfg_ct_n", "cfg_tile_pixels",
            "cfg_ct0", "cfg_ky0", "cfg_kx0", "cfg_rn", "cfg_contrib_n", "ext_valid", "ext_idx", "ext_chk",
            "ext_ready", "reduce_ready",
            "cfg_m", "go", "done", "busy", "state_dbg", "rd_valid", "rd_chk", "err_clear",
            "array_abft_sticky", "acc_abft_err", "acc_abft_sticky", "ctrl_err_sticky",
            "fault_inject", "ctrl_fault_inject", "rq_valid", "rq_tbl_we", "rq_tbl_addr", "rq_tbl_mult",
            "rq_tbl_shift", "rq_tbl_zp", "rq_relu", "seq_fault_inject", "rq_fault_inject", "seq_err_sticky", "rq_err_sticky",
            "cfg_regions_m1", "tiles_ready", "cfg_oy0", "cfg_oy_n", "cfg_iy0",
            "wbuf_ce_sticky", "wbuf_ue_sticky", "rq_tbl_perr_sticky", "rq_tbl_fault_inject")}
        self.abuf_wdata = d.array_of("abuf_wdata")
        self.wbuf_wdata = d.array_of("wbuf_wdata")
        self.rd_data = d.array_of("rd_data")
        self.rq_q = d.array_of("rq_q")
        self.ext_y = d.array_of("ext_y")
        for n in ("abuf_we", "wbuf_we", "go", "err_clear", "fault_inject", "ctrl_fault_inject", "rq_tbl_we", "rq_relu",
                  "ext_valid", "cfg_ct0", "cfg_ky0", "cfg_kx0", "cfg_contrib_n", "seq_fault_inject", "rq_fault_inject",
                  "rq_tbl_fault_inject"):
            self.p[n].v = 0
        self.p["cfg_regions_m1"].v = 0xFFFF        # all channel tiles resident: region = ct
        self.p["tiles_ready"].v = 0x7FFF
        self.rq_tables = None
        self.clocks = 0
        self.p["rst_n"].v = 0
        self.tick(2)
        self.p["rst_n"].v = 1
        self.tick()

    def tick(self, n=1):
        for _ in range(n):
            self.d.tick()
        self.clocks += n

    def write_abuf(self, base, pixels):
        for a, row in enumerate(pixels):
            self.p["abuf_we"].v = 1
            self.p["abuf_waddr"].v = base + a
            for c in range(self.R):
                self.abuf_wdata[c].v = bits(row[c], 8)
            self.tick()
        self.p["abuf_we"].v = 0

    def write_rq_tables(self, mult, shift, zp, relu):
        for j in range(self.C):
            self.p["rq_tbl_we"].v = 1
            self.p["rq_tbl_addr"].v = j
            self.p["rq_tbl_mult"].v = int(mult[j])
            self.p["rq_tbl_shift"].v = int(shift[j])
            self.p["rq_tbl_zp"].v = int(zp[j]) & 0xFF
            self.tick()
        self.p["rq_tbl_we"].v = 0
        self.p["rq_relu"].v = relu
        self.rq_tables = (np.array(mult), np.array(shift), np.array(zp), relu)

    def write_wtile(self, tile_index, Wt):
        for r in range(self.R):
            self.p["wbuf_we"].v = 1
            self.p["wbuf_waddr"].v = tile_index * self.R + r
            for j in range(self.C):
                self.wbuf_wdata[j].v = bits(Wt[r, j], 8)
            self.p["wcbuf_wdata"].v = bits(int(Wt[r, :].sum()), self.WCW)
            self.tick()
        self.p["wbuf_we"].v = 0

    def execute(self, h, w, ho, wo, s, p, k, ct_n, tile_pixels, m, poke=None, ctrl_fault_at=None, seq_fault=False, rq_fault=False):
        """Write the descriptor, pulse go, collect drained rows until done."""
        for n, v in (("cfg_h", h), ("cfg_w", w), ("cfg_ho", ho), ("cfg_wo", wo), ("cfg_s", s), ("cfg_p", p),
                     ("cfg_oy0", 0), ("cfg_oy_n", ho), ("cfg_iy0", 0),
                     ("cfg_k", k), ("cfg_ct_n", ct_n), ("cfg_tile_pixels", tile_pixels),
                     ("cfg_rn", ct_n * k * k), ("cfg_ct0", 0), ("cfg_ky0", 0), ("cfg_kx0", 0), ("cfg_contrib_n", 0)):
            self.p[n].v = bits(v, 16)
        self.p["cfg_m"].v = m
        self.p["go"].v = 1
        self.tick()
        self.p["go"].v = 0
        out, errs, poked, cycles = [], [], False, 0
        self.rq_out = []
        f_valid = self.d.cells["top.u_dp.u_feeder.valid"]
        valid_seen = 0
        while True:
            cycles += 1
            if f_valid.v:
                valid_seen += 1
            self.p["ctrl_fault_inject"].v = 1 if (ctrl_fault_at is not None and f_valid.v and valid_seen == ctrl_fault_at) else 0
            # R10 hooks: during the drain, corrupt the primary sequencer's row index / the primary requant's input
            in_drain = self.p["state_dbg"].v == 5
            self.p["seq_fault_inject"].v = 1 if (seq_fault and in_drain) else 0
            self.p["rq_fault_inject"].v = 1 if (rq_fault and self.p["rd_valid"].v) else 0
            if poke is not None and not poked and self.p["state_dbg"].v == S_WAIT:
                self.tick(2 * self.R + self.C + 2)   # let the pipeline land (PE_LAT = 2), then corrupt memory
                self.d.cells[f"top.u_dp.u_acc.acc[{poke[0]},{poke[1]}]"].v ^= 1 << 5
                poked = True
            self.tick()
            if self.p["rd_valid"].v:
                out.append([sbits(c.v, 32) for c in self.rd_data])
                errs.append(self.p["acc_abft_err"].v)
            if self.p["rq_valid"].v:
                self.rq_out.append([sbits(c.v, 8) for c in self.rq_q])
            if self.p["done"].v:
                for _ in range(3):                 # requant pipeline tail after done
                    self.tick()
                    if self.p["rq_valid"].v:
                        self.rq_out.append([sbits(c.v, 8) for c in self.rq_q])
                break
            if cycles > 200000:
                raise RuntimeError("sequencer did not finish")
        self.p["ctrl_fault_inject"].v = 0
        return np.array(out, dtype=np.int64), errs, cycles


def conv_on_core(core: NeoCore, x, wgt, s, p, poke=None, ctrl_fault_at=None, seq_fault=False, rq_fault=False):
    cin, h, w = x.shape
    cout, _, k, _ = wgt.shape
    ho = (h + 2 * p - k) // s + 1
    wo = (w + 2 * p - k) // s + 1
    M = ho * wo
    R, C = core.R, core.C
    cin_tiles, cout_tiles = math.ceil(cin / R), math.ceil(cout / C)
    # activation channel tiles, all resident: entry = ct*h*w + y*w + x
    for ct in range(cin_tiles):
        px = np.zeros((h * w, R), dtype=np.int64)
        rr = min(R, cin - ct * R)
        for yy in range(h):
            for xx in range(w):
                px[yy * w + xx, :rr] = x[ct * R: ct * R + rr, yy, xx]
        core.write_abuf(ct * h * w, px)
    y = np.zeros((cout, ho, wo), dtype=np.int64)
    acc_errs, total_cycles = [], 0
    for nt in range(cout_tiles):
        cc = min(C, cout - nt * C)
        for ct in range(cin_tiles):
            rr = min(R, cin - ct * R)
            for ky in range(k):
                for kx in range(k):
                    Wt = np.zeros((R, C), dtype=np.int64)
                    for r in range(rr):
                        for c in range(cc):
                            Wt[r, c] = wgt[nt * C + c, ct * R + r, ky, kx]
                    core.write_wtile((ct * k + ky) * k + kx, Wt)
        out, errs, cycles = core.execute(h, w, ho, wo, s, p, k, cin_tiles, h * w, M,
                                         poke=poke if nt == 0 else None,
                                         ctrl_fault_at=ctrl_fault_at if nt == 0 else None,
                                         seq_fault=seq_fault and nt == 0, rq_fault=rq_fault and nt == 0)
        total_cycles += cycles
        acc_errs.append(errs)
        assert out.shape[0] == M, f"drained {out.shape[0]} rows, expected {M}"
        for mm in range(M):
            for c in range(cc):
                y[nt * C + c, mm // wo, mm % wo] = out[mm, c]
    return y, acc_errs, total_cycles


def check(label, core, x, wgt, s, p):
    t0 = time.time()
    got, acc_errs, cycles = conv_on_core(core, x, wgt, s, p)
    ref = direct_conv(x, wgt, s, p)
    flags = sum(sum(e) for e in acc_errs)
    ok = (np.array_equal(got, ref) and flags == 0 and core.p["array_abft_sticky"].v == 0
          and core.p["acc_abft_sticky"].v == 0 and core.p["ctrl_err_sticky"].v == 0
          and core.p["seq_err_sticky"].v == 0 and core.p["rq_err_sticky"].v == 0
          and core.p["wbuf_ce_sticky"].v == 0 and core.p["wbuf_ue_sticky"].v == 0 and core.p["rq_tbl_perr_sticky"].v == 0)
    cin, h, w = x.shape
    cout, _, k, _ = wgt.shape
    print(f"  {label}: conv {cin}x{h}x{w} -> {cout}x{ref.shape[1]}x{ref.shape[2]} k{k} s{s} p{p} on {core.R}x{core.C} core, "
          f"hardware sequencer: {math.ceil(cin / core.R) * k * k} runs x {math.ceil(cout / core.C)} output tiles, "
          f"{cycles} sequencer cycles, {core.clocks} clocks total, {time.time() - t0:.1f} s; "
          f"abft/ctrl sticky {core.p['array_abft_sticky'].v}/{core.p['acc_abft_sticky'].v}/{core.p['ctrl_err_sticky'].v} -> "
          f"{'PASS' if ok else 'FAIL'}")
    if not np.array_equal(got, ref):
        diff = np.argwhere(got != ref)
        print("     first differences:", diff[:4].tolist(), "got", [int(got[tuple(i)]) for i in diff[:4]],
              "ref", [int(ref[tuple(i)]) for i in diff[:4]])
    return ok


def dump_vectors(out_dir, rows=16, cols=8, seed=9):
    """Write the C1 configuration as hex vectors for tb/tb_neo_core.sv (Xcelium replay)."""
    rng = np.random.default_rng(seed)
    x = rng.integers(-128, 128, size=(40, 6, 6), dtype=np.int64)
    wgt = rng.integers(-128, 128, size=(7, 40, 3, 3), dtype=np.int64)
    s, p = 1, 1
    cin, h, w = x.shape
    cout, _, k, _ = wgt.shape
    ho = (h + 2 * p - k) // s + 1
    wo = (w + 2 * p - k) // s + 1
    M = ho * wo
    R, C = rows, cols
    wcw = 8 + (0 if R <= 1 else (R - 1).bit_length()) + 1
    cin_tiles = math.ceil(cin / R)
    ref = direct_conv(x, wgt, s, p)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "abuf.hex"), "w") as f:      # entry = ROWS channels, channel 0 in the low byte
        for ct in range(cin_tiles):
            rr = min(R, cin - ct * R)
            for yy in range(h):
                for xx in range(w):
                    v = 0
                    for c in range(R):
                        val = int(x[ct * R + c, yy, xx]) if c < rr else 0
                        v |= (val & 0xFF) << (8 * c)
                    f.write(f"{v:0{2 * R}x}\n")
    with open(os.path.join(out_dir, "wbuf.hex"), "w") as f:      # entry = check weight (high) then COLS weights, column 0 low
        for ct in range(cin_tiles):
            rr = min(R, cin - ct * R)
            for ky in range(k):
                for kx in range(k):
                    for r in range(R):
                        v = 0
                        tot = 0
                        for c in range(C):
                            val = int(wgt[c, ct * R + r, ky, kx]) if (r < rr and c < cout) else 0
                            tot += val
                            v |= (val & 0xFF) << (8 * c)
                        v |= (tot & ((1 << wcw) - 1)) << (8 * C)
                        f.write(f"{v:0{(8 * C + wcw + 3) // 4}x}\n")
    with open(os.path.join(out_dir, "y_exp.hex"), "w") as f:     # M rows x COLS int32
        for mm in range(M):
            for c in range(C):
                val = int(ref[c, mm // wo, mm % wo]) if c < cout else 0
                f.write(f"{val & 0xFFFFFFFF:08x}\n")
    with open(os.path.join(out_dir, "desc.vh"), "w") as f:
        for name, val in (("ROWS", R), ("COLS", C), ("H", h), ("W", w), ("HO", ho), ("WO", wo), ("S", s), ("P", p),
                          ("K", k), ("CT_N", cin_tiles), ("RN", cin_tiles * k * k), ("TILE_PIXELS", h * w), ("M", M),
                          ("N_ABUF", cin_tiles * h * w), ("N_WBUF", cin_tiles * k * k * R)):
            f.write(f"`define CORE_{name} {val}\n")
    print(f"vectors for tb_neo_core.sv written to {os.path.abspath(out_dir)}")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--dump":
        dump_vectors(sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "..", "vectors_core"))
        return 0
    rng = np.random.default_rng(9)
    fails = 0
    late_only = "--late" in sys.argv
    c6_only = "--c6" in sys.argv
    print("Convolutions executed on neo_core RTL (hardware sequencer) under neosim:" + (" (C9-C10)" if late_only else " (C1-C8)"))
    mult = rng.integers(20000, 60000, size=8)
    shift = rng.integers(16, 22, size=8)
    zp = rng.integers(-8, 9, size=8)
    x = rng.integers(-128, 128, size=(40, 6, 6), dtype=np.int64)
    wgt = rng.integers(-128, 128, size=(7, 40, 3, 3), dtype=np.int64)
    ref = direct_conv(x, wgt, 1, 1)
    if late_only:
        return late_tests(rng, fails, mult, shift, zp, x, wgt, ref)

    if not c6_only:
        fails += early_tests(rng, x, wgt)
    core = NeoCore(16, 8, acc_rows=64, abuf_depth=256, wbuf_depth=512)
    core.write_rq_tables(mult, shift, zp, relu=1)
    got, acc_errs, _ = conv_on_core(core, x, wgt, 1, 1)
    ref = direct_conv(x, wgt, 1, 1)                          # C6 runs on the C1 data
    M = ref.shape[1] * ref.shape[2]
    # C6: requantized INT8 output path
    def rq_model(acc, mult, shift, zp, relu):
        r = (int(acc) * int(mult) + ((1 << int(shift)) >> 1)) >> int(shift)
        v = r + int(zp)
        if relu and v < zp:
            v = zp
        return max(-128, min(127, v))

    core = NeoCore(16, 8, acc_rows=64, abuf_depth=256, wbuf_depth=512)
    core.write_rq_tables(mult, shift, zp, relu=1)
    got, acc_errs, _ = conv_on_core(core, x, wgt, 1, 1)
    ref = direct_conv(x, wgt, 1, 1)                          # C2-C5 reassigned ref; C6 runs on the C1 data
    M = ref.shape[1] * ref.shape[2]
    rq = np.array(core.rq_out)
    exp = np.array([[rq_model(ref[c, m // ref.shape[2], m % ref.shape[2]] if c < 7 else 0, mult[c], shift[c], zp[c], 1)
                     for c in range(8)] for m in range(M)])
    ok6 = np.array_equal(got, ref) and rq.shape == exp.shape and np.array_equal(rq, exp)
    print(f"  C6: requantized INT8 output (per-channel mult/shift/zp, ReLU) on C1: {rq.shape[0] if rq.ndim == 2 else 0}/{M} rows, "
          f"{'bit-exact vs model' if ok6 else 'MISMATCH'} -> {'PASS' if ok6 else 'FAIL'}")
    fails += not ok6

    # C7: sequencer fault (drain index bit 0 flipped in the primary) -> lockstep comparator
    core = NeoCore(16, 8, acc_rows=64, abuf_depth=256, wbuf_depth=512)
    got, acc_errs, _ = conv_on_core(core, x, wgt, 1, 1, seq_fault=True)
    ok7 = core.p["seq_err_sticky"].v == 1 and core.p["acc_abft_sticky"].v == 0 and not np.array_equal(got, ref)
    print(f"  C7: primary sequencer's drain index corrupted (R10): seq_err_sticky={core.p['seq_err_sticky'].v} (expect 1), "
          f"acc ABFT sticky={core.p['acc_abft_sticky'].v} (expect 0: rows are consistent, just in the wrong order), "
          f"output {'wrong' if not np.array_equal(got, ref) else 'unexpectedly right'} -> {'PASS' if ok7 else 'FAIL'}")
    fails += not ok7
    # C8: requantization fault (bit 0 of column 0 into the primary) -> duplicated-stage comparator
    core = NeoCore(16, 8, acc_rows=64, abuf_depth=256, wbuf_depth=512)
    core.write_rq_tables(mult, np.full(8, 25), zp, relu=0)      # scale ~1/1000: outputs span INT8, not saturated
    got, acc_errs, _ = conv_on_core(core, x, wgt, 1, 1, rq_fault=True)
    ok8 = core.p["rq_err_sticky"].v == 1 and np.array_equal(got, ref) and core.p["acc_abft_sticky"].v == 0
    print(f"  C8: primary requant input corrupted (R10): rq_err_sticky={core.p['rq_err_sticky'].v} (expect 1), "
          f"INT32 drain still {'bit-exact' if np.array_equal(got, ref) else 'WRONG'}, ABFT silent -> {'PASS' if ok8 else 'FAIL'}")
    fails += not ok8

    print(f"RESULT: {'ALL PASS' if fails == 0 else f'{fails} FAILURES'}")
    return 1 if fails else 0


def early_tests(rng, x, wgt):
    fails = 0

    core = NeoCore(16, 8, acc_rows=64, abuf_depth=256, wbuf_depth=512)
    x = rng.integers(-128, 128, size=(20, 7, 7), dtype=np.int64)
    wgt = rng.integers(-128, 128, size=(10, 20, 3, 3), dtype=np.int64)
    fails += not check("C2", core, x, wgt, 2, 1)

    core = NeoCore(32, 32, acc_rows=32, abuf_depth=64, wbuf_depth=1024)
    x = rng.integers(-128, 128, size=(35, 5, 5), dtype=np.int64)
    wgt = rng.integers(-128, 128, size=(33, 35, 3, 3), dtype=np.int64)
    fails += not check("C3", core, x, wgt, 1, 1)

    core = NeoCore(16, 8, acc_rows=64, abuf_depth=256, wbuf_depth=512)
    x = rng.integers(-128, 128, size=(16, 4, 4), dtype=np.int64)
    wgt = rng.integers(-128, 128, size=(8, 16, 3, 3), dtype=np.int64)
    got, acc_errs, _ = conv_on_core(core, x, wgt, 1, 1, poke=(3, 4))
    ref = direct_conv(x, wgt, 1, 1)
    flagged = [i for i, e in enumerate(acc_errs[0]) if e]
    wrong = np.argwhere(got != ref).tolist()
    ok4 = flagged == [3] and wrong == [[4, 0, 3]] and core.p["acc_abft_sticky"].v == 1 and core.p["ctrl_err_sticky"].v == 0
    print(f"  C4: acc[3][4] bit flipped while the sequencer waited for the drain: rows flagged {flagged} (expect [3]), "
          f"outputs wrong {wrong} (expect [[4, 0, 3]]), acc sticky={core.p['acc_abft_sticky'].v} -> {'PASS' if ok4 else 'FAIL'}")
    fails += not ok4

    core = NeoCore(16, 8, acc_rows=64, abuf_depth=256, wbuf_depth=512)
    got, acc_errs, _ = conv_on_core(core, x, wgt, 1, 1, ctrl_fault_at=5)   # 5th valid row of the first run
    ok5 = core.p["ctrl_err_sticky"].v == 1 and core.p["array_abft_sticky"].v == 0
    print(f"  C5: feeder address bit 0 corrupted on the 5th valid row: ctrl_err_sticky={core.p['ctrl_err_sticky'].v} (expect 1), "
          f"array ABFT sticky={core.p['array_abft_sticky'].v} (expect 0: a wrong input is invisible to ABFT) -> {'PASS' if ok5 else 'FAIL'}")
    fails += not ok5
    return fails


def late_tests(rng, fails, mult, shift, zp, x, wgt, ref):
    # C9: weight-buffer ECC: a bit flipped in a stored weight codeword is corrected on read
    core = NeoCore(16, 8, acc_rows=64, abuf_depth=256, wbuf_depth=512)
    got, acc_errs, _ = conv_on_core(core, x, wgt, 1, 1)         # loads the buffers
    core.d.cells["top.u_wbuf.mem[100,0]"].v ^= 1 << 9        # a data bit of entry 100, lane 0
    core.d.cells["top.u_wbuf.mem[7,1]"].v ^= 1 << 34         # a check bit of entry 7, lane 1
    core.p["err_clear"].v = 1; core.tick(); core.p["err_clear"].v = 0
    out, errs, _ = core.execute(6, 6, 6, 6, 1, 1, 3, 3, 36, 36)   # re-execute the same descriptor without rewriting the buffers
    got2 = np.zeros_like(ref)
    for mm in range(36):
        for c in range(7):
            got2[c, mm // 6, mm % 6] = out[mm, c]
    ok9 = (np.array_equal(got2, ref) and core.p["wbuf_ce_sticky"].v == 1 and core.p["wbuf_ue_sticky"].v == 0
           and core.p["array_abft_sticky"].v == 0)
    print(f"  C9: weight-buffer ECC: a data bit and a check bit flipped in stored codewords: result "
          f"{'bit-exact' if np.array_equal(got2, ref) else 'WRONG'}, wbuf ce={core.p['wbuf_ce_sticky'].v} ue={core.p['wbuf_ue_sticky'].v}, "
          f"ABFT silent={core.p['array_abft_sticky'].v == 0} -> {'PASS' if ok9 else 'FAIL'}")
    fails += not ok9
    # C10: requantization table parity
    core = NeoCore(16, 8, acc_rows=64, abuf_depth=256, wbuf_depth=512)
    core.write_rq_tables(mult, np.full(8, 25), zp, relu=0)
    core.p["rq_tbl_fault_inject"].v = 1
    got, acc_errs, _ = conv_on_core(core, x, wgt, 1, 1)
    core.p["rq_tbl_fault_inject"].v = 0
    ok10 = core.p["rq_tbl_perr_sticky"].v == 1 and np.array_equal(got, ref)
    print(f"  C10: requant table multiplier bit 0 flipped: tbl_perr_sticky={core.p['rq_tbl_perr_sticky'].v} (expect 1); "
          f"the output comparator stays silent ({core.p['rq_err_sticky'].v}) because a 1-LSB multiplier change is masked by "
          f"quantization, which is why the table needs its own parity; INT32 drain bit-exact -> {'PASS' if ok10 else 'FAIL'}")
    fails += not ok10

    print(f"RESULT: {'ALL PASS' if fails == 0 else f'{fails} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
