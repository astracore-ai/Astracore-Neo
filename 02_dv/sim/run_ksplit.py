#!/usr/bin/env python3
"""
run_ksplit.py -- R8 cross-core K-split on neo_core RTL under neosim.

Three neo_core instances run in lockstep: the owner executes channel tile 0, two contributors
execute channel tiles 1 and 2 of the same convolution (same output tile, same rows). When a
contributor drains, the harness (playing the mesh and its DMA) forwards each INT32 partial-sum
row plus its ABFT check value to the owner's reduce port, which the owner accepts only in its
reduce state and when its own accumulator write has no priority. The owner's drain is the
full result and must equal a direct numpy convolution.

  K1  40x6x6 -> 7x6x6 k3 split over 3 cores (16x8): owner result bit-exact, no error flags
  K2  one forwarded partial-sum row has a bit flipped on the way: the owner's drain-time ABFT
      flags exactly that row (the reduction transfer is covered by the check column)
"""
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "compiler"))
from run_conv_core import NeoCore, bits, sbits  # noqa: E402
from neo_compile import direct_conv  # noqa: E402


def setup(core, x, wgt, s, p, ct_range, contrib_n):
    """Load this core's channel tiles and weight tiles (local indices) and its descriptor."""
    cin, h, w = x.shape
    cout, _, k, _ = wgt.shape
    ho = (h + 2 * p - k) // s + 1
    wo = (w + 2 * p - k) // s + 1
    M = ho * wo
    R, C = core.R, core.C
    for ct in ct_range:                       # activation region of each channel tile it runs
        px = np.zeros((h * w, R), dtype=np.int64)
        rr = min(R, cin - ct * R)
        for yy in range(h):
            for xx in range(w):
                px[yy * w + xx, :rr] = x[ct * R: ct * R + rr, yy, xx]
        core.write_abuf(ct * h * w, px)
    local = 0
    for ct in ct_range:
        rr = min(R, cin - ct * R)
        for ky in range(k):
            for kx in range(k):
                Wt = np.zeros((R, C), dtype=np.int64)
                for r in range(rr):
                    for c in range(min(C, cout)):
                        Wt[r, c] = wgt[c, ct * R + r, ky, kx]
                core.write_wtile(local, Wt)
                local += 1
    cin_tiles = math.ceil(cin / R)
    for n, v in (("cfg_h", h), ("cfg_w", w), ("cfg_ho", ho), ("cfg_wo", wo), ("cfg_s", s), ("cfg_p", p),
                 ("cfg_oy0", 0), ("cfg_oy_n", ho), ("cfg_iy0", 0),
                 ("cfg_k", k), ("cfg_ct_n", cin_tiles), ("cfg_tile_pixels", h * w),
                 ("cfg_ct0", ct_range[0]), ("cfg_ky0", 0), ("cfg_kx0", 0), ("cfg_rn", len(ct_range) * k * k),
                 ("cfg_contrib_n", contrib_n)):
        core.p[n].v = bits(v, 16)
    core.p["cfg_m"].v = M
    return M


def run_split(x, wgt, s, p, corrupt=None):
    """corrupt = (contributor index, row) flips a bit of that forwarded partial-sum row."""
    cin = x.shape[0]
    R, C = 16, 8
    cin_tiles = math.ceil(cin / R)
    assert cin_tiles == 3
    owner = NeoCore(R, C, acc_rows=64, abuf_depth=256, wbuf_depth=512)
    contribs = [NeoCore(R, C, acc_rows=64, abuf_depth=256, wbuf_depth=512) for _ in range(2)]
    M = setup(owner, x, wgt, s, p, [0], contrib_n=2)
    setup(contribs[0], x, wgt, s, p, [1], contrib_n=0)
    setup(contribs[1], x, wgt, s, p, [2], contrib_n=0)
    cores = [owner] + contribs
    for c in cores:
        c.p["go"].v = 1
    for c in cores:
        c.tick()
    for c in cores:
        c.p["go"].v = 0
    fifo = []                      # partial-sum rows in flight to the owner: (idx, data, chk)
    rows_from = [0, 0]
    out, errs = [], []
    cycles = 0
    done = [False] * 3
    forwarded = 0
    while not done[0]:
        cycles += 1
        # contributors' drained rows enter the mesh (with the optional corruption)
        for i, c in enumerate(contribs):
            if c.p["rd_valid"].v and not done[i + 1]:
                data = [sbits(cell.v, 32) for cell in c.rd_data]
                chk = sbits(c.p["rd_chk"].v, 32)
                if corrupt is not None and corrupt == (i, rows_from[i]):
                    data[2] ^= 1 << 7
                fifo.append((rows_from[i], data, chk))
                rows_from[i] += 1
        # mesh delivers to the owner's reduce port, one row per cycle, only when accepted
        owner_ready = owner.p["reduce_ready"].v and owner.p["ext_ready"].v
        if fifo and owner_ready:
            idx, data, chk = fifo[0]
            owner.p["ext_valid"].v = 1
            owner.p["ext_idx"].v = idx
            for j in range(C):
                owner.ext_y[j].v = bits(data[j], 32)
            owner.p["ext_chk"].v = bits(chk, 32)
        else:
            owner.p["ext_valid"].v = 0
        for c in cores:
            c.d.settle()
        accepted = owner.p["ext_valid"].v and owner.p["ext_ready"].v
        if owner.p["rd_valid"].v:
            out.append([sbits(cell.v, 32) for cell in owner.rd_data])
            errs.append(owner.p["acc_abft_err"].v)
        for i, c in enumerate(cores):           # done pulses in the same cycle as the last drained row
            if c.p["done"].v:
                done[i] = True
        for c in cores:
            c.tick()
        if accepted:
            fifo.pop(0)
            forwarded += 1
        if cycles > 6000:
            raise RuntimeError("K-split did not finish")
    owner.p["ext_valid"].v = 0
    return np.array(out, dtype=np.int64), errs, cycles, forwarded, owner


def main():
    rng = np.random.default_rng(13)
    x = rng.integers(-128, 128, size=(40, 6, 6), dtype=np.int64)
    wgt = rng.integers(-128, 128, size=(7, 40, 3, 3), dtype=np.int64)
    ref = direct_conv(x, wgt, 1, 1)
    M = ref.shape[1] * ref.shape[2]
    fails = 0
    print("R8 K-split on neo_core RTL under neosim (3 cores in lockstep, harness as mesh):")
    t0 = time.time()
    out, errs, cycles, fwd, owner = run_split(x, wgt, 1, 1)
    got = np.zeros_like(ref)
    for m in range(M):
        for c in range(7):
            got[c, m // ref.shape[2], m % ref.shape[2]] = out[m, c]
    ok1 = (out.shape[0] == M and np.array_equal(got, ref) and sum(errs) == 0 and fwd == 2 * M
           and owner.p["acc_abft_sticky"].v == 0 and owner.p["array_abft_sticky"].v == 0)
    print(f"  K1: conv 40x6x6 -> 7x6x6, channel tiles 0/1/2 on owner/contributor A/contributor B, "
          f"{fwd} partial-sum rows reduced into the owner, {cycles} cycles, {time.time() - t0:.1f} s, "
          f"owner result {'bit-exact' if np.array_equal(got, ref) else 'MISMATCH'}, flags {sum(errs)} -> {'PASS' if ok1 else 'FAIL'}")
    fails += not ok1
    out, errs, cycles, fwd, owner = run_split(x, wgt, 1, 1, corrupt=(1, 9))
    flagged = [i for i, e in enumerate(errs) if e]
    got = np.zeros_like(ref)
    for m in range(M):
        for c in range(7):
            got[c, m // ref.shape[2], m % ref.shape[2]] = out[m, c]
    wrong_rows = sorted({int(i[1] * ref.shape[2] + i[2]) for i in np.argwhere(got != ref)})
    ok2 = flagged == [9] and wrong_rows == [9] and owner.p["acc_abft_sticky"].v == 1
    print(f"  K2: bit 7 of column 2 flipped in contributor B's row 9 on the way to the owner: "
          f"owner drain flags rows {flagged} (expect [9]), wrong rows {wrong_rows} (expect [9]), sticky={owner.p['acc_abft_sticky'].v} -> {'PASS' if ok2 else 'FAIL'}")
    fails += not ok2
    print(f"RESULT: {'ALL PASS' if fails == 0 else f'{fails} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
