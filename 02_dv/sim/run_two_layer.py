#!/usr/bin/env python3
"""run_two_layer.py -- the first two-layer execution on the RTL mesh (drop 0.13, M10).
  8x8 cores (COLS == ROWS, so an INT8 output row is one activation entry). Layer 1 (conv 8->8, k3) runs
  on tile (0,0), requantizes, and drains INT8 rows into its own bank in activation layout. Layer 2
  (conv 8->8, k3) runs on tile (1,0), fetching its activations from tile (0,0)'s bank and its weights
  from tile (1,1), and drains INT32 rows to tile (1,1). Reference: numpy conv -> the requantization
  model (bit-exact with the RTL rounding) -> numpy conv."""
import os, sys, time
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from run_tiles import (Mesh, pack_words, read_rows, direct_conv, ins, wait, bits, OP_FETCH_A, OP_FETCH_W, OP_DRAIN_WR,
                       OP_GO, OP_WAIT_DONE, OP_END, REG_RQ_TBL, REG_RQ_ADDR, REG_RQ_RELU, NX)

def rq_model(acc, mult, shift, zp, relu):
    r = (int(acc) * int(mult) + ((1 << int(shift)) >> 1)) >> int(shift)
    v = r + int(zp)
    if relu and v < zp:
        v = zp
    return max(-128, min(127, v))

def main():
    R = C = 8
    rng = np.random.default_rng(23)
    x = rng.integers(-128, 128, size=(8, 6, 6), dtype=np.int64)
    w1 = rng.integers(-128, 128, size=(8, 8, 3, 3), dtype=np.int64)
    w2 = rng.integers(-128, 128, size=(8, 8, 3, 3), dtype=np.int64)
    mult = rng.integers(20000, 60000, size=8); shift = np.full(8, 21); zp = rng.integers(-8, 9, size=8)
    y1 = direct_conv(x, w1, 1, 1)                                   # 8 x 6 x 6 INT32
    q1 = np.vectorize(lambda v, c: rq_model(v, mult[c], shift[c], zp[c], 1))(y1, np.arange(8)[:, None, None])
    y2_ref = direct_conv(q1.astype(np.int64), w2, 1, 1)
    act, wts1 = pack_words(x, w1, 1, 3, 8, rows=R, cols=C)
    _, wts2 = pack_words(x, w2, 1, 3, 8, rows=R, cols=C)
    WPA, WPW = R * 8 // 32, (C * 8 + (8 + (R - 1).bit_length() + 1) + 31) // 32
    A0, W1, W2, ACT2, R0 = 0, 256, 1024, 2048, 3072                   # bank word addresses
    t0 = time.time()
    mesh = Mesh(rows=R, cols=C)
    for i, wd in enumerate(act):
        mesh.bank_write(3, A0 + i, wd)
    for i, wd in enumerate(wts1):
        mesh.bank_write(3, W1 + i, wd)
    for i, wd in enumerate(wts2):
        mesh.bank_write(3, W2 + i, wd)
    # layer 1 on tile (0,0): requant tables through the registers, INT8 drain into its own bank at ACT2
    for c in range(8):
        mesh.reg_write(0, REG_RQ_ADDR, c)
        mesh.reg_write(0, REG_RQ_TBL, ((int(zp[c]) & 0xFF) << 24) | (int(shift[c]) << 16) | int(mult[c]))
    mesh.reg_write(0, REG_RQ_RELU, 1)
    M = 36
    mesh.descriptor(0, 6, 6, 6, 6, 1, 1, 3, 1, 0, 9, 0, M)
    mesh.program(0, [ins(OP_FETCH_A, 1, 1, A0, 36 * WPA, 0), ins(OP_FETCH_W, 1, 1, W1, 9 * R * WPW, 0),
                     ins(OP_DRAIN_WR, 0, 0, ACT2, M, 0, arg=1), ins(OP_GO), ins(OP_WAIT_DONE), ins(OP_END)])
    mesh.start([0])
    wait(mesh, lambda: mesh.prog_done[0].v)
    wait(mesh, lambda: not mesh.drain_busy[0].v)
    mesh.tick(20)
    # check the INT8 activation region against q1
    exp_act, _ = pack_words(q1.astype(np.int64), w1, 1, 3, 8, rows=R, cols=C)
    got_act = [mesh.bank_read(0, ACT2 + i) for i in range(36 * WPA)]
    act_ok = got_act == exp_act
    t1 = time.time()
    # layer 2 on tile (1,0): activations from tile (0,0) at ACT2, weights from (1,1) at W2, result to (1,1) at R0
    mesh.descriptor(1, 6, 6, 6, 6, 1, 1, 3, 1, 0, 9, 0, M)
    mesh.program(1, [ins(OP_FETCH_A, 0, 0, ACT2, 36 * WPA, 0), ins(OP_FETCH_W, 1, 1, W2, 9 * R * WPW, 0),
                     ins(OP_DRAIN_WR, 1, 1, R0, M), ins(OP_GO), ins(OP_WAIT_DONE), ins(OP_END)])
    mesh.start([1])
    wait(mesh, lambda: mesh.prog_done[1].v)
    wait(mesh, lambda: not mesh.drain_busy[1].v)
    mesh.tick(40)
    out, chk = read_rows(mesh, 3, R0, M)
    got = np.zeros_like(y2_ref)
    for m in range(M):
        for c in range(8):
            got[c, m // 6, m % 6] = out[m, c]
    fl = mesh.flags()
    ok = act_ok and np.array_equal(got, y2_ref) and not any(sum(v) for v in fl.values())
    print("Two-layer execution on the 2x2 mesh of 8x8 cores under neosim:")
    print(f"  M10: layer 1 (conv 8->8 k3) on tile(0,0): requantized rows drained INT8 into its own bank as activation entries: "
          f"{'bit-exact vs the requant model' if act_ok else 'MISMATCH'} ({t1 - t0:.0f} s); layer 2 on tile(1,0) read them as input: "
          f"result {'bit-exact' if np.array_equal(got, y2_ref) else 'MISMATCH'} vs numpy(conv(requant(conv(x)))); flags clean="
          f"{not any(sum(v) for v in fl.values())} -> {'PASS' if ok else 'FAIL'} ({mesh.clocks} clocks, {time.time() - t0:.0f} s)")
    print("RESULT:", "ALL PASS" if ok else "FAIL")
    return 0 if ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
