"""test_tile_mesh.py -- cocotb: M1 on the 2x2 tile mesh through the register bus, bit-exact (vectors from gen_tile_vectors.py's
construction, generated here directly). Backdoor-loads tile (1,1)'s bank as (39,32) codewords and reads tile (1,0)'s bank back.
Run: make -C dv/cocotb tile."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly
import numpy as np
from neo_golden import pack_words, direct_conv, ecc_encode, ins, bits, sbits, OP_FETCH_A, OP_FETCH_W, OP_DRAIN_WR, OP_GO, OP_WAIT_DONE, OP_END
import neo_funcov as fc

NX, NY, ROWS, COLS = 2, 2, 16, 8
WPA, WPW = ROWS * 8 // 32, (COLS * 8 + 13 + 31) // 32
CFG_ORDER = ["cfg_h", "cfg_w", "cfg_ho", "cfg_wo", "cfg_oy0", "cfg_oy_n", "cfg_iy0", "cfg_s", "cfg_p", "cfg_k", "cfg_ct_n", "cfg_ct0",
             "cfg_ky0", "cfg_kx0", "cfg_rn", "cfg_contrib_n", "cfg_tile_pixels", "cfg_regions_m1"]


def tile(dut, x, y):
    return getattr(getattr(getattr(dut, f"g_y[{y}]"), f"g_x[{x}]"), "u_t")


async def reg_write(dut, node, addr, val):
    dut.h_we[node].value = 1; dut.h_addr[node].value = addr; dut.h_wdata[node].value = val & 0xFFFFFFFF
    await RisingEdge(dut.clk)
    dut.h_we[node].value = 0


@cocotb.test()
async def m1_register_bus(dut):
    cocotb.start_soon(Clock(dut.clk, 500, units="ps").start())
    for n in range(NX * NY):
        dut.h_we[n].value = 0; dut.h_re[n].value = 0; dut.h_addr[n].value = 0; dut.h_wdata[n].value = 0
    dut.rst_n.value = 0
    for _ in range(4): await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)
    rng = np.random.default_rng(17)
    x = rng.integers(-128, 128, size=(40, 6, 6), dtype=np.int64)
    wgt = rng.integers(-128, 128, size=(7, 40, 3, 3), dtype=np.int64)
    ref = direct_conv(x, wgt, 1, 1)
    act, wts = pack_words(x, wgt, 3, 3, 7)
    A0, W0, R0, M = 0, 512, 2048, 36
    mem11 = tile(dut, 1, 1).u_bank.u_bank.mem
    for i, wd in enumerate(act + [None] * (W0 - len(act)) + wts):
        if wd is None: continue
        p, op = ecc_encode(wd)
        mem11[i].value = (op << 38) | (p << 32) | (wd & 0xFFFFFFFF)
    desc = dict(cfg_h=6, cfg_w=6, cfg_ho=6, cfg_wo=6, cfg_oy0=0, cfg_oy_n=6, cfg_iy0=0, cfg_s=1, cfg_p=1, cfg_k=3, cfg_ct_n=3, cfg_ct0=0,
                cfg_ky0=0, cfg_kx0=0, cfg_rn=27, cfg_contrib_n=0, cfg_tile_pixels=36, cfg_regions_m1=0xFFFF)
    fc.sample_descriptor(desc)
    for i, name in enumerate(CFG_ORDER):
        await reg_write(dut, 0, 0x10 + i, bits(desc[name], 16))
    await reg_write(dut, 0, 0x24, M)
    APT, WPT = 36 * WPA, 9 * ROWS * WPW
    prog = [ins(OP_FETCH_A, 1, 1, A0 + ct * APT, APT, ct * 36) for ct in range(3)]
    prog += [ins(OP_FETCH_W, 1, 1, W0, 3 * WPT, 0), ins(OP_DRAIN_WR, 1, 0, R0, M), ins(OP_GO), ins(OP_WAIT_DONE), ins(OP_END)]
    for a, wd in enumerate(prog):
        fc.sample_dma_op((wd >> 60) & 0xF)
        await reg_write(dut, 0, 0x02, a); await reg_write(dut, 0, 0x03, wd & 0xFFFFFFFF); await reg_write(dut, 0, 0x04, wd >> 32)
    fc.sample_drain("int32")
    await reg_write(dut, 0, 0x00, 1)
    for _ in range(3): await RisingEdge(dut.clk)
    for n in range(200000):
        await RisingEdge(dut.clk)
        await ReadOnly()
        if int(dut.prog_done[0].value) and not int(dut.drain_busy[0].value):
            break
    for _ in range(40): await RisingEdge(dut.clk)
    mem10 = tile(dut, 1, 0).u_bank.u_bank.mem
    got = np.zeros_like(ref)
    for m in range(M):
        for c in range(7):
            got[c, m // 6, m % 6] = sbits(int(mem10[R0 + m * (COLS + 1) + c].value) & 0xFFFFFFFF, 32)
    assert np.array_equal(got, ref), "mismatch against the golden convolution"
    assert int(dut.crc_err[0].value) == 0 and int(dut.parity_err[0].value) == 0 and int(dut.err_pin[0].value) == 0
    dut._log.info(f"M1 through the register bus under cocotb: {M} rows bit-exact in {n} clocks, flags clean")
    fc.report("funcov_tile_mesh.yml")
