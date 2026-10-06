"""test_two_layer.py -- M10, the two-layer execution on the RTL mesh under cocotb (drop 0.20).
Port of sim/run_two_layer.py on tile_mesh_cocotb built with 8x8 cores (COLS == ROWS, so an INT8 output row is one
activation entry): make -C dv/cocotb tile8 (ROWS=8 COLS=8, MODULE=test_two_layer).
  Layer 1 (conv 8->8, k3) runs on tile (0,0), requantizes through the register-loaded tables, and drains INT8 rows
  into its own bank in activation layout. Layer 2 (conv 8->8, k3) runs on tile (1,0), fetching its activations from
  tile (0,0)'s bank and its weights from tile (1,1), and drains INT32 rows to tile (1,1).
  Reference: numpy conv -> the requantization model (bit-exact with the RTL rounding) -> numpy conv.
"""
import cocotb
import numpy as np
from neo_golden import pack_words, direct_conv, ins, OP_FETCH_A, OP_FETCH_W, OP_DRAIN_WR, OP_GO, OP_WAIT_DONE, OP_END
from test_tile_mesh import (Mesh, setup, expect_clean, check_result, ROWS, COLS, WPA, WPW, REG_RQ_TBL, REG_RQ_ADDR, REG_RQ_RELU)
import neo_funcov as fc


def rq_model(acc, mult, shift, zp, relu):
    r = (int(acc) * int(mult) + ((1 << int(shift)) >> 1)) >> int(shift)
    v = r + int(zp)
    if relu and v < zp:
        v = zp
    return max(-128, min(127, v))


@cocotb.test()
async def m10_two_layer_int8_handoff(dut):
    assert ROWS == 8 and COLS == 8, f"M10 needs the 8x8-core build (make -C dv/cocotb tile8), this build is {ROWS}x{COLS}"
    mesh = await setup(dut)
    rng = np.random.default_rng(23)
    x = rng.integers(-128, 128, size=(8, 6, 6), dtype=np.int64)
    w1 = rng.integers(-128, 128, size=(8, 8, 3, 3), dtype=np.int64)
    w2 = rng.integers(-128, 128, size=(8, 8, 3, 3), dtype=np.int64)
    mult = rng.integers(20000, 60000, size=8); shift = np.full(8, 21); zp = rng.integers(-8, 9, size=8)
    y1 = direct_conv(x, w1, 1, 1)                                   # 8 x 6 x 6 INT32
    q1 = np.vectorize(lambda v, c: rq_model(v, mult[c], shift[c], zp[c], 1))(y1, np.arange(8)[:, None, None])
    y2_ref = direct_conv(q1.astype(np.int64), w2, 1, 1)
    act, wts1 = pack_words(x, w1, 1, 3, 8, rows=ROWS, cols=COLS)
    _, wts2 = pack_words(x, w2, 1, 3, 8, rows=ROWS, cols=COLS)
    A0, W1, W2, ACT2, R0 = 0, 256, 1024, 2048, 3072                   # bank word addresses
    M = 36
    await mesh.load_words(3, A0, act)
    await mesh.load_words(3, W1, wts1)
    await mesh.load_words(3, W2, wts2)
    # layer 1 on tile (0,0): requant tables through the registers, INT8 drain into its own bank at ACT2
    for c in range(8):
        await mesh.reg_write(0, REG_RQ_ADDR, c)
        await mesh.reg_write(0, REG_RQ_TBL, ((int(zp[c]) & 0xFF) << 24) | (int(shift[c]) << 16) | int(mult[c]))
    await mesh.reg_write(0, REG_RQ_RELU, 1)
    await mesh.descriptor(0, 6, 6, 6, 6, 1, 1, 3, 1, 0, 9, 0, M)
    await mesh.program(0, [ins(OP_FETCH_A, 1, 1, A0, 36 * WPA, 0), ins(OP_FETCH_W, 1, 1, W1, 9 * ROWS * WPW, 0),
                           ins(OP_DRAIN_WR, 0, 0, ACT2, M, 0, arg=1), ins(OP_GO), ins(OP_WAIT_DONE), ins(OP_END)])
    await mesh.start([0])
    n1 = await mesh.wait_prog(0)
    await mesh.tick(20)
    exp_act, _ = pack_words(q1.astype(np.int64), w1, 1, 3, 8, rows=ROWS, cols=COLS)
    got_act = np.array([await mesh.bank_read(0, ACT2 + i) for i in range(36 * WPA)], dtype=np.int64)
    await check_result(mesh, got_act, np.array(exp_act, dtype=np.int64), "M10 layer-1 INT8 activation rows")
    fc.sample_drain("int8")
    # layer 2 on tile (1,0): activations from tile (0,0) at ACT2, weights from (1,1) at W2, result to (1,1) at R0
    await mesh.descriptor(1, 6, 6, 6, 6, 1, 1, 3, 1, 0, 9, 0, M)
    await mesh.program(1, [ins(OP_FETCH_A, 0, 0, ACT2, 36 * WPA, 0), ins(OP_FETCH_W, 1, 1, W2, 9 * ROWS * WPW, 0),
                           ins(OP_DRAIN_WR, 1, 1, R0, M), ins(OP_GO), ins(OP_WAIT_DONE), ins(OP_END)])
    await mesh.start([1])
    n2 = await mesh.wait_prog(1)
    await mesh.tick(40)
    out = await mesh.read_rows(3, R0, M)
    got = np.zeros_like(y2_ref)
    for m in range(M):
        for c in range(8):
            got[c, m // 6, m % 6] = out[m, c]
    await check_result(mesh, got, y2_ref, "M10 layer-2 result")
    await expect_clean(mesh)
    fc.report("funcov_two_layer.yml")
    dut._log.info(f"M10: layer 1 drained INT8 activations bit-exact vs the requant model ({n1} clocks); layer 2 read them as input, "
                  f"result bit-exact vs numpy(conv(requant(conv(x)))) ({n2} clocks); causes clean")
