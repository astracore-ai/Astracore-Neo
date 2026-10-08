"""test_neo_core.py -- cocotb: the C1 convolution on neo_core, bit-exact against the golden model (drop 0.21), and C2, the
core's own fault hooks (drop 0.36): the duplicated feeder control, the lockstep sequencer, the duplicated requantization
and the requantization-table parity each flag their fault -- the flags the mesh suite cannot raise, since neo_tile ties
these hooks off.
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


S_DRAIN = 5                                          # core_seq state while the accumulator rows are read out


async def run_conv(dut, x, wgt, k=3, s=1, p=1, ctrl_fault=False, seq_fault=False, rq_fault=False, tbl_fault=False, wbuf_flips=()):
    """C1's run as a helper: reset, load the buffers, descriptor, go, collect the drained rows until done. The fault
    hooks (drop 0.36) mirror sim/run_conv_core.py: the feeder-control fault held for the first 40 cycles of the run
    (it hits valid rows), the sequencer fault while the drain runs, the requant-input fault while rows drain, the
    table fault (bit 0 of column 0's multiplier) for the whole run; wbuf_flips = [(entry, lane, mask)] XORs stored weight
    codewords through the buffer's hook before the run (drop 0.37). Returns (rows, cycles, M, wo)."""
    cin = x.shape[0]
    cin_tiles = (cin + ROWS - 1) // ROWS
    h, w = x.shape[1], x.shape[2]
    ho = (h + 2 * p - k) // s + 1
    wo = (w + 2 * p - k) // s + 1
    M = ho * wo
    for name in ("abuf_we", "wbuf_we", "go", "err_clear", "fault_inject", "ctrl_fault_inject", "rq_tbl_we", "rq_relu", "ext_valid",
                 "seq_fault_inject", "rq_fault_inject", "rq_tbl_fault_inject", "dv_wbuf_flip", "dv_wbuf_addr", "dv_wbuf_lane", "dv_wbuf_mask"):
        getattr(dut, name).value = 0
    dut.rst_n.value = 0
    for _ in range(3): await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)
    # a requantization table for every column (odd parity stored with each entry), so the table check has something to check
    for c in range(COLS):
        dut.rq_tbl_we.value = 1
        dut.rq_tbl_addr.value = c
        dut.rq_tbl_mult.value = 30000 + c
        dut.rq_tbl_shift.value = 20
        dut.rq_tbl_zp.value = bits(-3, 8)
        await RisingEdge(dut.clk)
    dut.rq_tbl_we.value = 0
    await write_abuf(dut, packed_act(x, cin_tiles, h, w))
    await write_wbuf(dut, packed_w(wgt, cin_tiles, k, 0))
    for entry, lane, mask in wbuf_flips:                             # the stored codewords, one falling edge each
        dut.dv_wbuf_addr.value = entry; dut.dv_wbuf_lane.value = lane; dut.dv_wbuf_mask.value = mask
        dut.dv_wbuf_flip.value = 1
        await RisingEdge(dut.clk)
        dut.dv_wbuf_flip.value = 0
    for name, v in (("cfg_h", h), ("cfg_w", w), ("cfg_ho", ho), ("cfg_wo", wo), ("cfg_s", s), ("cfg_p", p), ("cfg_oy0", 0), ("cfg_oy_n", ho),
                    ("cfg_iy0", 0), ("cfg_k", k), ("cfg_ct_n", cin_tiles), ("cfg_tile_pixels", h * w), ("cfg_ct0", 0), ("cfg_ky0", 0),
                    ("cfg_kx0", 0), ("cfg_rn", cin_tiles * k * k), ("cfg_contrib_n", 0), ("cfg_regions_m1", 0xFFFF), ("tiles_ready", 0x7FFF)):
        getattr(dut, name).value = bits(v, 16)
    dut.cfg_m.value = M
    dut.rq_tbl_fault_inject.value = 1 if tbl_fault else 0
    dut.go.value = 1
    await RisingEdge(dut.clk)
    dut.go.value = 0
    rows, cycles, in_drain, rd_valid = [], 0, False, 0
    for cycles in range(1, 20000):
        await RisingEdge(dut.clk)
        # the hooks for the coming edge, from what the last read-only phase saw (one cycle behind the state: the sequencer
        # fault is held through the drain and the requant fault through the row burst, so the lag changes nothing)
        dut.ctrl_fault_inject.value = 1 if (ctrl_fault and cycles < 40) else 0
        dut.seq_fault_inject.value = 1 if (seq_fault and in_drain) else 0
        dut.rq_fault_inject.value = 1 if (rq_fault and rd_valid) else 0
        await ReadOnly()
        in_drain = int(dut.state_dbg.value) == S_DRAIN
        rd_valid = int(dut.rd_valid.value)
        if rd_valid:
            rows.append([sbits(int(dut.rd_data[c].value), 32) for c in range(COLS)])
        if int(dut.done.value):
            break
    await RisingEdge(dut.clk)
    for name in ("ctrl_fault_inject", "seq_fault_inject", "rq_fault_inject", "rq_tbl_fault_inject"):
        getattr(dut, name).value = 0
    for _ in range(4):                                               # the requant pipeline and the sticky flags settle
        await RisingEdge(dut.clk)
    return rows, cycles, M, wo


def flags(dut):
    return {n: int(getattr(dut, n + "_sticky").value) for n in ("array_abft", "acc_abft", "ctrl_err", "seq_err", "rq_err", "rq_tbl_perr",
                                                                "wbuf_ce", "wbuf_ue")}


@cocotb.test()
async def c1_conv(dut):
    cocotb.start_soon(Clock(dut.clk, 500, units="ps").start())
    rng = np.random.default_rng(9)
    x = rng.integers(-128, 128, size=(CIN, 6, 6), dtype=np.int64)
    wgt = rng.integers(-128, 128, size=(7, CIN, 3, 3), dtype=np.int64)
    ref = direct_conv(x, wgt, 1, 1)
    rows, cycles, M, wo = await run_conv(dut, x, wgt)
    got = np.zeros_like(ref)
    for m in range(M):
        for c in range(7):
            got[c, m // wo, m % wo] = rows[m][c]
    assert len(rows) == M, f"{len(rows)} rows drained, expected {M}"
    assert np.array_equal(got, ref), "mismatch against the golden convolution"
    f = flags(dut)
    assert not any(f.values()), f"C1 flags {f}"
    fc.sample_descriptor(dict(cfg_k=3, cfg_s=1, cfg_ct_n=3, cfg_oy_n=6, cfg_ho=6, cfg_contrib_n=0, cfg_ct0=0, cfg_ky0=0, cfg_kx0=0))
    dut._log.info(f"C1 on neo_core {ROWS}x{COLS} under cocotb: {M} rows bit-exact, flags clean ({cycles} cycles)")


@cocotb.test()
async def c2_fault_hooks(dut):
    """The core's own fault hooks (drop 0.36), one run each: ctrl_fault_inject (the duplicated feeder control disagrees on a
    valid row), seq_fault_inject (the primary sequencer's drain index corrupted: the lockstep comparator flags, the ABFT
    stays silent as in neosim's C7), rq_fault_inject (the primary requant's input corrupted: the duplicated-stage
    comparator flags, C8), rq_tbl_fault_inject (a multiplier bit flipped against the stored parity). Each flag is
    checked alone, and every run still finishes."""
    cocotb.start_soon(Clock(dut.clk, 500, units="ps").start())
    rng = np.random.default_rng(11)
    x = rng.integers(-128, 128, size=(CIN, 6, 6), dtype=np.int64)
    wgt = rng.integers(-128, 128, size=(7, CIN, 3, 3), dtype=np.int64)
    expect = {"ctrl_fault": "ctrl_err", "seq_fault": "seq_err", "rq_fault": "rq_err", "tbl_fault": "rq_tbl_perr"}
    seen = {}
    for hook, flag in expect.items():
        rows, cycles, M, wo = await run_conv(dut, x, wgt, **{hook: True})
        f = flags(dut)
        assert f[flag] == 1, f"C2 {hook}: {flag} not raised ({f})"
        others = {n: v for n, v in f.items() if n != flag and v}
        if hook == "seq_fault":
            assert "acc_abft" not in others and "array_abft" not in others, f"C2 seq fault: the ABFT must stay silent ({f})"
        assert len(rows) == M, f"C2 {hook}: {len(rows)} rows drained, expected {M}"
        seen[hook] = (cycles, others)
        fc.sample_flag({"ctrl_err": "ctrl", "seq_err": "seq", "rq_err": "rq", "rq_tbl_perr": "tbl_perr"}[flag])
    dut._log.info("C2: feeder-control, lockstep-sequencer, duplicated-requant and table-parity faults each flagged, every run finished: "
                  + "; ".join(f"{h}: {c} cycles, also {o or 'nothing'}" for h, (c, o) in seen.items()))
    fc.report("funcov_neo_core.yml")


@cocotb.test()
async def c3_wbuf_ecc(dut):
    """The weight buffer's SECDED through its hook (drop 0.37): one bit of a stored codeword flipped (a data bit of entry 5's
    lane 0, a check bit of entry 40's last lane) is corrected on read -- wbuf_ce, result bit-exact, nothing else; two bits
    of one lane (entry 7) are uncorrectable -- wbuf_ue, the run completes (neosim's C9 pokes the same cells directly)."""
    cocotb.start_soon(Clock(dut.clk, 500, units="ps").start())
    rng = np.random.default_rng(13)
    x = rng.integers(-128, 128, size=(CIN, 6, 6), dtype=np.int64)
    wgt = rng.integers(-128, 128, size=(7, CIN, 3, 3), dtype=np.int64)
    ref = direct_conv(x, wgt, 1, 1)
    lanes = (COLS * 8 + WCW + 31) // 32
    rows, cycles, M, wo = await run_conv(dut, x, wgt, wbuf_flips=[(5, 0, 1 << 3), (40, lanes - 1, 1 << 35)])
    got = np.zeros_like(ref)
    for m in range(M):
        for c in range(7):
            got[c, m // wo, m % wo] = rows[m][c]
    assert np.array_equal(got, ref), "C3: single-bit errors in the weight buffer were not corrected"
    f = flags(dut)
    assert f["wbuf_ce"] == 1 and f["wbuf_ue"] == 0 and not any(v for n, v in f.items() if n not in ("wbuf_ce",)), f"C3 single-bit flags {f}"
    fc.sample_flag("wbuf_ce")
    rows, cycles2, M, wo = await run_conv(dut, x, wgt, wbuf_flips=[(7, 0, (1 << 3) | (1 << 9))])
    f = flags(dut)
    assert f["wbuf_ue"] == 1 and len(rows) == M, f"C3 double-bit flags {f}, {len(rows)} rows"
    fc.sample_flag("wbuf_ue")
    dut._log.info(f"C3: weight-buffer SECDED: a data bit and a check bit corrected on read (result bit-exact, wbuf_ce, {cycles} cycles); "
                  f"a double-bit lane error flagged uncorrectable (wbuf_ue, run complete, {cycles2} cycles)")
    fc.report("funcov_neo_core.yml")
