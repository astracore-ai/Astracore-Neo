"""test_neo_core.py -- cocotb: the C1 convolution on neo_core, bit-exact against the golden model (drop 0.21).
Mirrors sim/run_conv_core.py's NeoCore.execute() step for step, driving the DMA write ports, the descriptor and go,
and collecting the drained rows. Run: make -C dv/cocotb core (16x8) or core32 (the silicon core: 32x32, ACC_ROWS 512,
ABUF 2048, WBUF 1024); the geometry comes from NEO_ROWS/NEO_COLS, exported by the Makefile to match its -G values.
The convolution keeps three channel tiles at any core size (CIN = 3*ROWS - 8, the last tile partly filled)."""
import os
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly
import numpy as np
from neo_golden import direct_conv, bits, sbits
import neo_funcov as fc

ROWS, COLS = int(os.environ.get("NEO_ROWS", "16")), int(os.environ.get("NEO_COLS", "8"))
WCW = 8 + (ROWS - 1).bit_length() + 1                 # check-weight width
CIN = 3 * ROWS - 8                                   # 40 at 16 rows, 88 at 32


def packed_act(x, cin_tiles, h, w):
    """activation entries: per channel tile, per pixel, ROWS channel bytes (as the feeder's write port expects)"""
    entries = []
    for ct in range(cin_tiles):
        for iy in range(h):
            for ix in range(w):
                entries.append([int(x[ct * ROWS + r, iy, ix]) if ct * ROWS + r < x.shape[0] else 0 for r in range(ROWS)])
    return entries


def packed_w(wgt, cin_tiles, k, nt):
    """weight entries in run order (ct, ky, kx) then tile row r: COLS weights for output channels nt*COLS.. and the check weight"""
    entries = []
    cout = wgt.shape[0]
    for ct in range(cin_tiles):
        for ky in range(k):
            for kx in range(k):
                for r in range(ROWS):
                    ci = ct * ROWS + r
                    ws = [int(wgt[nt * COLS + c, ci, ky, kx]) if (ci < wgt.shape[1] and nt * COLS + c < cout) else 0 for c in range(COLS)]
                    entries.append((ws, sum(ws)))
    return entries


async def write_abuf(dut, entries):
    for a, e in enumerate(entries):
        dut.abuf_we.value = 1
        dut.abuf_waddr.value = a
        for r in range(ROWS):
            dut.abuf_wdata[r].value = bits(e[r], 8)
        await RisingEdge(dut.clk)
    dut.abuf_we.value = 0


async def write_wbuf(dut, entries):
    for a, (ws, chk) in enumerate(entries):
        dut.wbuf_we.value = 1
        dut.wbuf_waddr.value = a
        for c in range(COLS):
            dut.wbuf_wdata[c].value = bits(ws[c], 8)
        dut.wcbuf_wdata.value = bits(chk, WCW)
        await RisingEdge(dut.clk)
    dut.wbuf_we.value = 0


@cocotb.test()
async def c1_conv(dut):
    cocotb.start_soon(Clock(dut.clk, 500, units="ps").start())
    rng = np.random.default_rng(9)
    x = rng.integers(-128, 128, size=(CIN, 6, 6), dtype=np.int64)
    wgt = rng.integers(-128, 128, size=(7, CIN, 3, 3), dtype=np.int64)
    ref = direct_conv(x, wgt, 1, 1)
    h = w = ho = wo = 6; k = 3; s = p = 1; cin_tiles = 3; M = ho * wo
    # reset and quiet inputs
    for name in ("abuf_we", "wbuf_we", "go", "err_clear", "fault_inject", "ctrl_fault_inject", "rq_tbl_we", "rq_relu", "ext_valid",
                 "seq_fault_inject", "rq_fault_inject", "rq_tbl_fault_inject"):
        getattr(dut, name).value = 0
    dut.rst_n.value = 0
    for _ in range(3): await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)
    await write_abuf(dut, packed_act(x, cin_tiles, h, w))
    await write_wbuf(dut, packed_w(wgt, cin_tiles, k, 0))
    for name, v in (("cfg_h", h), ("cfg_w", w), ("cfg_ho", ho), ("cfg_wo", wo), ("cfg_s", s), ("cfg_p", p), ("cfg_oy0", 0), ("cfg_oy_n", ho),
                    ("cfg_iy0", 0), ("cfg_k", k), ("cfg_ct_n", cin_tiles), ("cfg_tile_pixels", h * w), ("cfg_ct0", 0), ("cfg_ky0", 0),
                    ("cfg_kx0", 0), ("cfg_rn", cin_tiles * k * k), ("cfg_contrib_n", 0), ("cfg_regions_m1", 0xFFFF), ("tiles_ready", 0x7FFF)):
        getattr(dut, name).value = bits(v, 16)
    dut.cfg_m.value = M
    dut.go.value = 1
    await RisingEdge(dut.clk)
    dut.go.value = 0
    rows = []
    for _ in range(20000):
        await RisingEdge(dut.clk)
        await ReadOnly()
        if dut.rd_valid.value:
            rows.append([sbits(int(dut.rd_data[c].value), 32) for c in range(COLS)])
        if dut.done.value:
            break
    got = np.zeros_like(ref)
    for m in range(M):
        for c in range(7):
            got[c, m // wo, m % wo] = rows[m][c]
    assert len(rows) == M, f"{len(rows)} rows drained, expected {M}"
    assert np.array_equal(got, ref), "mismatch against the golden convolution"
    assert int(dut.array_abft_sticky.value) == 0 and int(dut.acc_abft_sticky.value) == 0 and int(dut.ctrl_err_sticky.value) == 0
    assert int(dut.seq_err_sticky.value) == 0 and int(dut.rq_err_sticky.value) == 0
    fc.sample_descriptor(dict(cfg_k=k, cfg_s=s, cfg_ct_n=cin_tiles, cfg_oy_n=ho, cfg_ho=ho, cfg_contrib_n=0, cfg_ct0=0, cfg_ky0=0, cfg_kx0=0))
    dut._log.info(f"C1 on neo_core {ROWS}x{COLS} under cocotb: {M} rows bit-exact, flags clean")
    fc.report("funcov_neo_core.yml")
