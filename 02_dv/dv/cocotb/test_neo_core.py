"""test_neo_core.py -- cocotb: the C1 convolution on neo_core, bit-exact against the golden model (drop 0.21), and C2, the
core's own fault hooks (drop 0.36): the duplicated feeder control, the lockstep sequencer, the duplicated requantization
and the requantization-table parity each flag their fault -- the flags the mesh suite cannot raise, since neo_tile ties
these hooks off. C3, the weight buffer's SECDED through its hook (drop 0.37); C4, the reduce port's back-to-back partial
sums on one row and the drain after the last one (drop 0.41: the accumulator's two-cycle read-modify-write).
Mirrors sim/run_conv_core.py's NeoCore.execute() step for step, driving the DMA write ports, the descriptor and go,
and collecting the drained rows. Run: make -C dv/cocotb core (16x8) or core32 (the silicon core: 32x32, ACC_ROWS 512,
ABUF 2048, WBUF 1024); the geometry comes from NEO_ROWS/NEO_COLS, exported by the Makefile to match its -G values.
The convolution keeps three channel tiles at any core size (CIN = 3*ROWS - 8, the last tile partly filled).
Drop 0.42: both builds are the 2:4 sparse-capable core (NEO_NCHK = 4): C1-C4 write the dense entry (index 0, the check
weight in column 0) and run as before; C5 is a 2:4 sparse convolution through the compiler (neo_backend's validator,
compressor, layouts and descriptor), bit-exact against the dense convolution on the pruned weights.
Drop 0.43: C6, the storage behind the R7 / R10 comparators -- a single-cycle upset of each register drop 0.41's synchronous
reads added, injected through the model's VPI, raises its comparator's flag.
Drop 0.43: C12, a convolution + residual + SiLU through the core's vector unit with the compiler's epilogue (the program,
the SiLU table, the constants and the requantizer's tables from compiler/neo_vec.py; the residual rows written into the
unit's buffer as the DMA's FETCH_A with arg 1 does), bit-exact against the convolution -> the unit's model -> the
requantizer's model; C13, the unit's two flags through its fault hooks (the duplicated control, the table parity); C14,
a layer norm over the row's own lanes (the output tile's COLS channels: both passes on the held row -- RELOAD) clipped
by CLAMP, the compiler's in-row norm epilogue, bit-exact against the unit's and the requantizer's models."""
import os
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadOnly, ReadWrite
import numpy as np
from neo_golden import direct_conv, bits, sbits
import neo_funcov as fc

ROWS, COLS = int(os.environ.get("NEO_ROWS", "16")), int(os.environ.get("NEO_COLS", "8"))
NCHK = int(os.environ.get("NEO_NCHK", "1"))           # the core's check columns (drop 0.42): must match the build's -GNCHK
WCW = 8 + (ROWS - 1).bit_length() + 1                 # check-weight width
EW = COLS * (8 + (2 if NCHK > 1 else 0)) + NCHK * WCW   # the weight entry (wbuf_mem's EW)
LANES = (EW + 31) // 32                              # its SECDED lanes
CIN = 3 * ROWS - 8                                   # 40 at 16 rows, 88 at 32
CG = ["datapath", "requant", "requant_dup"]           # drop 0.43: the core's clock enables (cg_en bits 0..2)


async def _gating(dut, tag):
    """Drop 0.43: count the cycles each of the core's three clock enables is high; report, as the test ends, the fraction
    of its cycles each gated block spent with its clock off (the gated fractions of the stream's report)."""
    hist = {}
    try:
        while True:
            await RisingEdge(dut.clk)
            v = int(dut.cg_en.value)
            hist[v] = hist.get(v, 0) + 1
    finally:
        cyc = sum(hist.values())
        if cyc:
            on = [sum(c for v, c in hist.items() if (v >> b) & 1) for b in range(len(CG))]
            dut._log.info(f"{tag} gated fractions (% of {cyc} cycles off): " +
                          " ".join(f"{n} {100.0 * (1 - on[b] / cyc):.0f}" for b, n in enumerate(CG)))


def start_clock(dut, tag):
    cocotb.start_soon(Clock(dut.clk, 500, units="ps").start())
    if hasattr(dut, "cg_en"):
        cocotb.start_soon(_gating(dut, tag))


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
    """dense entries: index 0, the check weight in column 0 (the NCHK = 4 entry's other check columns zero)"""
    for a, (ws, chk) in enumerate(entries):
        dut.wbuf_we.value = 1
        dut.wbuf_waddr.value = a
        for c in range(COLS):
            dut.wbuf_wdata[c].value = bits(ws[c], 8)
        dut.wcbuf_wdata.value = bits(chk, WCW)
        await RisingEdge(dut.clk)
    dut.wbuf_we.value = 0


async def write_abuf_words(dut, words):
    """bank words of activation entries (ROWS / 4 words each) into consecutive buffer entries, as the DMA's fetch does"""
    wpe = ROWS // 4
    for e in range(len(words) // wpe):
        dut.abuf_we.value = 1
        dut.abuf_waddr.value = e
        for r in range(ROWS):
            dut.abuf_wdata[r].value = (int(words[e * wpe + r // 4]) >> (8 * (r % 4))) & 0xFF
        await RisingEdge(dut.clk)
    dut.abuf_we.value = 0


async def write_wbuf_words(dut, words):
    """bank words of weight entries (neo_backend.pack_entry: {idx, wc[NCHK], w}) into consecutive buffer entries, unpacked
    the way the NIC's fetch assembly does: {index, weight} per column, the NCHK check weights packed"""
    wpw = (EW + 31) // 32
    for e in range(len(words) // wpw):
        v = sum(int(words[e * wpw + i]) << (32 * i) for i in range(wpw))
        dut.wbuf_we.value = 1
        dut.wbuf_waddr.value = e
        for c in range(COLS):
            idx = (v >> (8 * COLS + NCHK * WCW + 2 * c)) & 3 if NCHK > 1 else 0
            dut.wbuf_wdata[c].value = ((v >> (8 * c)) & 0xFF) | (idx << 8)
        dut.wcbuf_wdata.value = (v >> (8 * COLS)) & ((1 << (NCHK * WCW)) - 1)
        await RisingEdge(dut.clk)
    dut.wbuf_we.value = 0


S_DRAIN = 5                                          # core_seq state while the accumulator rows are read out


def handle(dut, path):
    """A signal of the core by its hierarchical path below the toplevel (C6's paths cross no generate index; an unpacked
    array below one needs the fault campaign's two-step lookup, faults/common.py Injector.resolve)."""
    return dut._id(path, extended=False)


async def run_conv(dut, x, wgt, k=3, s=1, p=1, ctrl_fault=False, seq_fault=False, rq_fault=False, tbl_fault=False, wbuf_flips=(),
                   desc=None, psums=(), words=None, vpi_flip=None, vec=None, rbuf=None, rq_tables=None, collect=None,
                   vec_fault=None):
    """C1's run as a helper: reset, load the buffers, descriptor, go, collect the drained rows until done. The fault
    hooks (drop 0.36) mirror sim/run_conv_core.py: the feeder-control fault held for the first 40 cycles of the run
    (it hits valid rows), the sequencer fault while the drain runs, the requant-input fault while rows drain, the
    table fault (bit 0 of column 0's multiplier) for the whole run; wbuf_flips = [(entry, lane, mask)] XORs stored weight
    codewords through the buffer's hook before the run (drop 0.37). desc overrides descriptor fields; psums =
    [(idx, ys, chk)] are partial-sum rows offered on the reduce port in that order, one per cycle, each until the core
    accepts it (ext_ready; drop 0.41). words = (activation words, weight words): the compiler's bank layouts written into
    the buffers instead of the dense packing (drop 0.42, C5); desc may then carry cfg_flags (the sparse mode).
    vpi_flip = (path, bit, cond): bit `bit` of the register at `path` is flipped once, through the model's VPI (cocotb's
    Verilator builds are --public-flat-rw), right after the first rising edge of the run at which the 1-bit signal
    `cond` (a path, or None for the first cycle) reads 1 -- a single-cycle upset in a cycle the register is used (drop
    0.43, C6; the fault campaign's injection, 02_dv/dv/cocotb/faults/).
    Drop 0.43 (C12-C14): vec = the vector unit's (address, data) writes, rbuf = its residual rows (INT8, written as entries
    0..), rq_tables = (mult, shift, zp) per column instead of the default tables; collect (a dict) receives the requantized
    rows ('rq') and the cycle of the sequencer's done ('seq_done'); vec_fault = 'ctrl' | 'tbl': the unit's hooks held over
    the run. Returns (rows, cycles, M, wo)."""
    cin = x.shape[0]
    cin_tiles = (cin + ROWS - 1) // ROWS
    h, w = x.shape[1], x.shape[2]
    ho = (h + 2 * p - k) // s + 1
    wo = (w + 2 * p - k) // s + 1
    M = ho * wo
    for name in ("abuf_we", "wbuf_we", "go", "err_clear", "fault_inject", "ctrl_fault_inject", "rq_tbl_we", "rq_relu", "ext_valid",
                 "seq_fault_inject", "rq_fault_inject", "rq_tbl_fault_inject", "dv_wbuf_flip", "dv_wbuf_addr", "dv_wbuf_lane", "dv_wbuf_mask",
                 "cfg_sparse", "vec_we", "rbuf_we", "rbuf_we2", "vec_ctrl_fault_inject", "vec_tbl_fault_inject"):
        getattr(dut, name).value = 0
    dut.rst_n.value = 0
    for _ in range(3): await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)
    # a requantization table for every column (odd parity stored with each entry), so the table check has something to check.
    # Shift 26 (drop 0.38; was 20): the comparator of the duplicated requantization sees the INT8 outputs, so C2's input fault
    # (bit 12 of column 0 flipped) is visible only where the output does not saturate -- at shift 20 every column-0 value of
    # the 32x32 core's run (80 input channels, accumulators in the 100,000s) saturated and run 126 saw no rq_err; at 26 the
    # outputs sit within +-128 and the flip moves each one by 4096 x 30000 / 2^26 = 1.8, at least one LSB, in every row
    for c in range(COLS):
        dut.rq_tbl_we.value = 1
        dut.rq_tbl_addr.value = c
        dut.rq_tbl_mult.value = 30000 + c if rq_tables is None else int(rq_tables[0][c])
        dut.rq_tbl_shift.value = 26 if rq_tables is None else int(rq_tables[1][c])
        dut.rq_tbl_zp.value = bits(-3 if rq_tables is None else int(rq_tables[2][c]), 8)
        await RisingEdge(dut.clk)
    dut.rq_tbl_we.value = 0
    for a, d in (vec or ()):                                         # the vector unit's tables and program (drop 0.43)
        dut.vec_we.value = 1
        dut.vec_addr.value = a
        dut.vec_wdata.value = d & 0xFFFFFFFF
        await RisingEdge(dut.clk)
    dut.vec_we.value = 0
    for m, row in enumerate(rbuf or ()):                             # its residual rows, entry m = row m
        dut.rbuf_we.value = 1
        dut.rbuf_waddr.value = m
        for c in range(ROWS):
            dut.rbuf_wdata[c].value = bits(row[c] if c < len(row) else 0, 8)
        await RisingEdge(dut.clk)
    dut.rbuf_we.value = 0
    if words is None:
        await write_abuf(dut, packed_act(x, cin_tiles, h, w))
        await write_wbuf(dut, packed_w(wgt, cin_tiles, k, 0))
    else:
        await write_abuf_words(dut, words[0])
        await write_wbuf_words(dut, words[1])
    for entry, lane, mask in wbuf_flips:                             # the stored codewords, one falling edge each
        dut.dv_wbuf_addr.value = entry; dut.dv_wbuf_lane.value = lane; dut.dv_wbuf_mask.value = mask
        dut.dv_wbuf_flip.value = 1
        await RisingEdge(dut.clk)
        dut.dv_wbuf_flip.value = 0
    for name, v in (("cfg_h", h), ("cfg_w", w), ("cfg_ho", ho), ("cfg_wo", wo), ("cfg_s", s), ("cfg_p", p), ("cfg_oy0", 0), ("cfg_oy_n", ho),
                    ("cfg_iy0", 0), ("cfg_k", k), ("cfg_ct_n", cin_tiles), ("cfg_tile_pixels", h * w), ("cfg_ct0", 0), ("cfg_ky0", 0),
                    ("cfg_kx0", 0), ("cfg_rn", cin_tiles * k * k), ("cfg_contrib_n", 0), ("cfg_regions_m1", 0xFFFF), ("tiles_ready", 0x7FFF)):
        getattr(dut, name).value = bits((desc or {}).get(name, v), 16)
    dut.cfg_sparse.value = int((desc or {}).get("cfg_flags", 0)) & 1
    dut.cfg_m.value = (desc or {}).get("cfg_m", M)
    dut.rq_tbl_fault_inject.value = 1 if tbl_fault else 0
    dut.vec_ctrl_fault_inject.value = 1 if vec_fault == "ctrl" else 0
    dut.vec_tbl_fault_inject.value = 1 if vec_fault == "tbl" else 0
    dut.go.value = 1
    await RisingEdge(dut.clk)
    dut.go.value = 0
    rows, cycles, in_drain, rd_valid = [], 0, False, 0
    queue = list(psums)
    flip_h = handle(dut, vpi_flip[0]) if vpi_flip else None
    flip_c = handle(dut, vpi_flip[2]) if (vpi_flip and vpi_flip[2]) else None
    rq_rows, seq_done_at = [], None
    for cycles in range(1, 20000):
        await RisingEdge(dut.clk)
        if flip_h is not None:
            # the read-modify-write in the ReadWrite phase: right after RisingEdge the model has not evaluated the edge yet
            # (a read returns the value before it); the write lands after the edge, and the logic behind the register
            # re-evaluates before the next edge samples it
            await ReadWrite()
            if flip_c is None or int(flip_c.value) == 1:
                flip_h.value = int(flip_h.value) ^ (1 << vpi_flip[1])      # this cycle's value: the upset
                flip_h = None
        # the hooks for the coming edge, from what the last read-only phase saw (one cycle behind the state: the sequencer
        # fault is held through the drain and the requant fault through the row burst, so the lag changes nothing)
        dut.ctrl_fault_inject.value = 1 if (ctrl_fault and cycles < 40) else 0
        dut.seq_fault_inject.value = 1 if (seq_fault and in_drain) else 0
        dut.rq_fault_inject.value = 1 if (rq_fault and rd_valid) else 0
        # the reduce port: the head row is offered every cycle (the core gates it with reduce_ready) until it is accepted
        dut.ext_valid.value = 1 if queue else 0
        if queue:
            idx, ys, chk = queue[0]
            dut.ext_idx.value = idx
            for c in range(COLS):
                dut.ext_y[c].value = bits(ys[c], 32)
            dut.ext_chk.value = bits(chk, 32)
        await ReadOnly()
        if queue and int(dut.ext_ready.value):
            queue.pop(0)                                             # taken at the coming edge
        in_drain = int(dut.state_dbg.value) == S_DRAIN
        rd_valid = int(dut.rd_valid.value)
        if rd_valid:
            rows.append([sbits(int(dut.rd_data[c].value), 32) for c in range(COLS)])
        if int(dut.rq_valid.value):
            rq_rows.append([sbits(int(dut.rq_q[c].value), 8) for c in range(COLS)])
        if seq_done_at is None and int(dut.u_seq.done.value):
            seq_done_at = cycles
        if int(dut.done.value):
            break
    await RisingEdge(dut.clk)
    for name in ("ctrl_fault_inject", "seq_fault_inject", "rq_fault_inject", "rq_tbl_fault_inject", "ext_valid",
                 "vec_ctrl_fault_inject", "vec_tbl_fault_inject"):
        getattr(dut, name).value = 0
    assert not queue, f"{len(queue)} partial-sum rows not accepted"
    assert flip_h is None, f"vpi_flip {vpi_flip}: its condition never held, nothing was injected"
    for _ in range(4):                                               # the requant pipeline and the sticky flags settle
        await ReadOnly()
        if int(dut.rq_valid.value):
            rq_rows.append([sbits(int(dut.rq_q[c].value), 8) for c in range(COLS)])
        await RisingEdge(dut.clk)
    if collect is not None:
        collect["rq"] = rq_rows
        collect["seq_done"] = seq_done_at
        collect["cycles"] = cycles
    return rows, cycles, M, wo


def flags(dut):
    return {n: int(getattr(dut, n + "_sticky").value) for n in ("array_abft", "acc_abft", "ctrl_err", "seq_err", "rq_err", "rq_tbl_perr",
                                                                "wbuf_ce", "wbuf_ue", "vec_err", "vec_perr")}


@cocotb.test()
async def c1_conv(dut):
    start_clock(dut, "C1")
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
    start_clock(dut, "C2")
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
    start_clock(dut, "C3")
    rng = np.random.default_rng(13)
    x = rng.integers(-128, 128, size=(CIN, 6, 6), dtype=np.int64)
    wgt = rng.integers(-128, 128, size=(7, CIN, 3, 3), dtype=np.int64)
    ref = direct_conv(x, wgt, 1, 1)
    lanes = LANES                                     # 3 at 16x8 with NCHK = 1, 5 with NCHK = 4
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


@cocotb.test()
async def c4_reduce_forward(dut):
    """The accumulator's two-cycle read-modify-write on the reduce port (drop 0.41). The core runs channel tile 0 as the owner
    of a K-split (cfg_rn = 9 runs, cfg_contrib_n = 2) and then takes the partial sums of channel tiles 1 and 2 (A and B,
    from the golden model) on its reduce port, one row per cycle in the order A0, A1, B1, A2, B2, ..., A35, B35, B0: every
    pair (A_r, B_r) lands back to back on one row, so B_r's registered read is taken at the edge where A_r's sum is
    written and only the forward (acc_bank's fwd1 / new_q) keeps A_r in the row; the last row goes to row 0, which the
    drain reads first, so the drain must start a cycle after that row's write (core_seq's red_last). The drained rows are
    the whole convolution, bit-exact, with every flag clean -- without the forward or without the extra cycle a row loses a
    contributor's partial sum (consistently with its check sum, so only the comparison with the golden model sees it)."""
    start_clock(dut, "C4")
    rng = np.random.default_rng(17)
    x = rng.integers(-128, 128, size=(CIN, 6, 6), dtype=np.int64)
    wgt = rng.integers(-128, 128, size=(7, CIN, 3, 3), dtype=np.int64)
    ref = direct_conv(x, wgt, 1, 1)
    wo, M = 6, 36
    parts = [direct_conv(x[t * ROWS:(t + 1) * ROWS], wgt[:, t * ROWS:(t + 1) * ROWS], 1, 1) for t in (1, 2)]

    def psum(part, m):
        ys = [int(part[c, m // wo, m % wo]) if c < 7 else 0 for c in range(COLS)]
        return (m, ys, sum(ys))
    order = [psum(parts[0], 0)] + [r for m in range(1, M) for r in (psum(parts[0], m), psum(parts[1], m))] + [psum(parts[1], 0)]
    rows, cycles, M, wo = await run_conv(dut, x, wgt, desc={"cfg_rn": 9, "cfg_contrib_n": 2}, psums=order)
    got = np.zeros_like(ref)
    for m in range(M):
        for c in range(7):
            got[c, m // wo, m % wo] = rows[m][c]
    assert len(rows) == M, f"C4: {len(rows)} rows drained, expected {M}"
    bad = sorted({int(i[1] * wo + i[2]) for i in np.argwhere(got != ref)})
    assert not bad, f"C4: rows {bad} differ from the golden convolution (a partial sum lost in the read-modify-write)"
    f = flags(dut)
    assert not any(f.values()), f"C4 flags {f}"
    fc.sample_descriptor(dict(cfg_k=3, cfg_s=1, cfg_ct_n=3, cfg_oy_n=6, cfg_ho=6, cfg_contrib_n=2, cfg_ct0=0, cfg_ky0=0, cfg_kx0=0))
    dut._log.info(f"C4: owner of a 3-way K-split on neo_core {ROWS}x{COLS}: {len(order)} partial-sum rows on the reduce port, "
                  f"{M - 1} same-row pairs back to back and the last on row 0; {M} rows bit-exact, flags clean ({cycles} cycles)")
    fc.report("funcov_neo_core.yml")


@cocotb.test(skip=(NCHK < 4))
async def c5_sparse_conv(dut):
    """A 2:4 sparse convolution on the sparse-capable core (drop 0.42, NCHK = 4): 4*ROWS input channels -- two sparse channel
    tiles of 2*ROWS (64 at 32 rows), four dense ones -- with 2:4-pruned weights, everything from the compiler: neo_backend's
    validator rejects the unpruned tensor and accepts the pruned one, its compressor and layouts give the buffers' contents
    (the activation tiles of 2*ROWS channels, two entries per pixel; the compressed entries, {index, weight} per column and
    four check weights per row), its descriptor the run (cfg_flags = sparse, cfg_ct_n = 2, cfg_rn = 18). The drained rows
    equal the dense convolution on the pruned weights, every flag clean; every index value 0..3 is held by some PE (each
    check column covers weights)."""
    import neo_backend as nb
    start_clock(dut, "C5")
    rng = np.random.default_rng(31)
    cin = 4 * ROWS
    x = rng.integers(-128, 128, size=(cin, 6, 6), dtype=np.int64)
    dense = rng.integers(-128, 128, size=(7, cin, 3, 3), dtype=np.int64)
    try:
        nb.check_2_4(dense)
        raise AssertionError("C5: the validator accepted an unpruned weight tensor")
    except ValueError:
        pass
    wgt = nb.prune_2_4(dense)
    nb.check_2_4(wgt)
    ref = direct_conv(x, wgt, 1, 1)
    lp = nb.compile_layer("c5", 6, 6, cin, 7, 3, 1, 1, ROWS, COLS, 2, 2, (1, 1), 0, (1, 1), 0, owner=(0, 0), shares=1,
                          nchk=NCHK, sparse=True)
    desc = lp.tiles[0].descriptor
    assert desc.get("cfg_flags") == 1 and desc["cfg_ct_n"] == 2 and desc["cfg_rn"] == 18, f"C5 descriptor {desc}"
    act, wts = nb.pack_words(x, wgt, desc["cfg_ct_n"], 3, 7, ROWS, COLS, nchk=NCHK, sparse=True)
    # the index values the stored entries hold (the coverage bins: every check column exercised)
    wpw = (EW + 31) // 32
    seen = set()
    for e in range(len(wts) // wpw):
        v = sum(int(wts[e * wpw + i]) << (32 * i) for i in range(wpw))
        for c in range(7):
            if (v >> (8 * c)) & 0xFF:
                seen.add((v >> (8 * COLS + NCHK * WCW + 2 * c)) & 3)
    assert seen == {0, 1, 2, 3}, f"C5: index values held {sorted(seen)}"
    rows, cycles, M, wo = await run_conv(dut, x, wgt, desc=desc, words=(act, wts))
    got = np.zeros_like(ref)
    for m in range(M):
        for c in range(7):
            got[c, m // wo, m % wo] = rows[m][c]
    assert len(rows) == M, f"C5: {len(rows)} rows drained, expected {M}"
    assert np.array_equal(got, ref), f"C5: {int((got != ref).sum())} outputs differ from the dense convolution on the pruned weights"
    f = flags(dut)
    assert not any(f.values()), f"C5 flags {f}"
    fc.sample_descriptor(dict(desc, cfg_ho=6))
    for i in sorted(seen):
        fc.sample_sparse_index(i)
    dut._log.info(f"C5: 2:4 sparse convolution {cin}x6x6 -> 7x6x6 on neo_core {ROWS}x{COLS} (NCHK = 4) through the compiler: "
                  f"2 sparse channel tiles of {2 * ROWS}, 18 runs (36 dense), {len(wts) // wpw} entries of {wpw} words, index values "
                  f"{sorted(seen)}; {M} rows bit-exact, flags clean ({cycles} cycles)")
    fc.report("funcov_neo_core.yml")


# C6 (drop 0.43): each register drop 0.41's synchronous reads put behind the comparators, its path below neo_core, the bit
# flipped, the 1-bit signal that says the register is in use (None: every cycle), and the flag its copy raises
C6_TARGETS = [
    ("u_dp.u_feeder.inb_q", 0, "u_dp.u_feeder.valid_q", "ctrl_err"),             # the row is inside the tile (a zero row)
    ("u_dp.u_feeder.a0_q", 0, "u_dp.u_feeder.valid_q", "ctrl_err"),              # the entry of the pair (the other entry)
    ("u_dp.u_feeder.first_oq", 0, "u_dp.u_feeder.valid_q", "ctrl_err"),          # overwrite instead of accumulate
    ("u_dp.u_feeder.u_mem.rb_addr_q", 0, "u_dp.u_feeder.valid_q", "ctrl_err"),   # the activation buffer's address latch
    ("w_load_q", 0, "w_load_q", "seq_err"),                                     # the shadow chain's load strobe
    ("u_wbuf.u_mem.rb_addr_q", 0, "w_load_q", "seq_err"),                       # the weight buffer's address latch
    ("u_wbuf.ra0_q", 0, "w_load_q", "seq_err"),                                 # the entry of the pair
    ("u_dp.u_acc.v1", 0, None, "seq_err"),                                      # the accumulate's stage-0 control
    ("u_dp.u_acc.first1", 0, None, "seq_err"),
    ("u_dp.u_acc.idx1", 0, "u_dp.u_acc.v1", "seq_err"),
    ("u_dp.u_acc.fwd1", 0, None, "seq_err"),
    ("u_dp.u_acc.u_mem.rb_addr_q", 0, "u_dp.u_acc.rd_valid", "seq_err"),        # the accumulator's address latch (a drained row)
]


@cocotb.test()
async def c6_comparator_storage(dut):
    """The storage behind the R7 / R10 comparators (drop 0.43, D2): a single-cycle upset of each register the synchronous
    reads of drop 0.41 added -- the feeder's inb_q, a0_q, first_oq and the activation buffer's port-B address latch, the
    core's w_load_q, the weight buffer's address latch and entry select, the accumulator's stage-0 control (v1, first1, idx1,
    fwd1) and its address latch -- flipped through the model's VPI in a cycle the register is used. Each raises its
    comparator's flag (ctrl_err for the feeder, seq_err for the rest) through the copies drop 0.43 added; before them each of
    these runs finished silently, seven of the twelve with wrong rows (a consistent wrong row passes the ABFT check and the
    SECDED lanes), five with exact rows. Every run finishes with its rows drained."""
    cocotb.start_soon(Clock(dut.clk, 500, units="ps").start())
    rng = np.random.default_rng(15)
    x = rng.integers(-128, 128, size=(CIN, 6, 6), dtype=np.int64)
    wgt = rng.integers(-128, 128, size=(7, CIN, 3, 3), dtype=np.int64)
    ref = direct_conv(x, wgt, 1, 1)
    seen = []
    for path, bit, cond, flag in C6_TARGETS:
        rows, cycles, M, wo = await run_conv(dut, x, wgt, vpi_flip=(path, bit, cond))
        f = flags(dut)
        assert f[flag] == 1, f"C6 {path}[{bit}]: {flag} not raised ({f})"
        assert len(rows) == M, f"C6 {path}: {len(rows)} rows drained, expected {M}"
        got = np.zeros_like(ref)
        for m in range(M):
            for c in range(7):
                got[c, m // wo, m % wo] = rows[m][c]
        seen.append((path, "rows wrong" if not np.array_equal(got, ref) else "rows exact",
                     ", ".join(n for n, v in f.items() if v)))
    dut._log.info("C6: every register behind the comparators flagged when upset in use -- "
                  + "; ".join(f"{p}: {r}, flags {fl}" for p, r, fl in seen))


def c12_setup(seed=41):
    """C12's layer: C1's convolution shape, a residual tensor and the compiler's SiLU epilogue (neosim's C12, the same
    scales: the conv's real values ~ N(0, 2.5^2), the residual at 1/32, the output at 1/16)."""
    import neo_vec as nv
    rng = np.random.default_rng(seed)
    x = rng.integers(-128, 128, size=(CIN, 6, 6), dtype=np.int64)
    wgt = rng.integers(-128, 128, size=(7, CIN, 3, 3), dtype=np.int64)
    ref = direct_conv(x, wgt, 1, 1)
    M = 36
    acc = [[int(ref[c, m // 6, m % 6]) if c < 7 else 0 for c in range(COLS)] for m in range(M)]
    res = [[int(v) if c < 7 else 0 for c, v in enumerate(rng.integers(-128, 128, size=COLS))] for _ in range(M)]
    s_acc, s_res, s_out = 2.5 / float(np.std(ref)), 1.0 / 32, 1.0 / 16
    ep = nv.act_epilogue("silu", s_acc, s_out, COLS, s_res=s_res, res_sh=10)
    return x, wgt, acc, res, ep, (s_acc, s_res, s_out)


@cocotb.test()
async def c12_residual_silu(dut):
    """C12 (drop 0.43): a convolution + residual + SiLU on neo_core through the compiler's epilogue: the vector unit's
    program (MUL_SCALE to the residual's scale, ADD_ROW, BMUL to the SiLU table's input, LUT, EMIT), its table and
    constants and the requantizer's tables from compiler/neo_vec.py's act_epilogue, written through the unit's host port;
    the residual rows written into its buffer. The INT8 rows equal the golden chain bit for bit (direct_conv -> the unit's
    model -> the requantizer's model), no INT32 row leaves on rd_valid (a layer with a program drains through the unit
    alone), the unit's cycles after the sequencer's done are the model's, every flag clean; the error against the float
    SiLU is reported."""
    import neo_vec as nv
    import vec_ref as vr
    start_clock(dut, "C12")
    x, wgt, acc, res, ep, (s_acc, s_res, s_out) = c12_setup()
    col = {}
    rows, cycles, M, wo = await run_conv(dut, x, wgt, vec=ep.vec.table_writes(), rbuf=res,
                                         rq_tables=(ep.rq_mult, ep.rq_shift, ep.rq_zp), collect=col)
    exp, _, _ = nv.golden_epilogue(ep, acc, res, cols=COLS)
    vu = vr.VecUnit(COLS, COLS, 64)
    ep.vec.apply(vu)
    _, ecyc = vu.run(acc)
    got = col["rq"]
    assert not rows, f"C12: {len(rows)} INT32 rows on rd_valid with a vector program loaded (expected none)"
    bad = [m for m in range(M) if m >= len(got) or got[m] != exp[m]]
    assert len(got) == M and not bad, f"C12: {len(got)} INT8 rows, rows {bad[:6]} differ from the golden chain"
    vcyc = col["cycles"] - col["seq_done"]
    assert vcyc == ecyc, f"C12: the unit took {vcyc} cycles after the sequencer's done, the model {ecyc}"
    f = flags(dut)
    assert not any(f.values()), f"C12 flags {f}"
    err = max(abs(nv.silu(acc[m][c] * s_acc + res[m][c] * s_res) / s_out - got[m][c]) for m in range(M) for c in range(7))
    for w0 in ep.vec.prog:
        fc.sample_vec_op(vr.OP_NAMES[w0 & 15])
    dut._log.info(f"C12: conv + residual + SiLU on neo_core {ROWS}x{COLS}: {M} INT8 rows bit-exact (program "
                  f"{' '.join(vr.OP_NAMES[w0 & 15] for w0 in ep.vec.prog)}), the unit {vcyc} cycles after the sequencer's done "
                  f"(model {ecyc}), {cycles} cycles in all, flags clean; max |error| against the float SiLU {err:.2f} LSB")
    fc.report("funcov_neo_core.yml")


@cocotb.test()
async def c14_row_norm(dut):
    """C14 (drop 0.43): a convolution with COLS output channels (inputs and weights in [-8, 8): accumulators within INT16,
    SQR's operand) followed by compiler/neo_vec.py's in-row layer norm -- the row's mean and variance over its COLS lanes,
    the rsqrt table, RELOAD for the second pass, the clip by CLAMP at 3 sigma -- bit-exact against the unit's and the
    requantizer's models; the error against the float layer norm (clipped) reported."""
    import neo_vec as nv
    import vec_ref as vr
    start_clock(dut, "C14")
    rng = np.random.default_rng(14)
    x = rng.integers(-8, 8, size=(CIN, 6, 6), dtype=np.int64)
    wgt = rng.integers(-8, 8, size=(COLS, CIN, 3, 3), dtype=np.int64)
    ref = direct_conv(x, wgt, 1, 1)
    M = 36
    acc = [[int(ref[c, m // 6, m % 6]) for c in range(COLS)] for m in range(M)]
    V = sorted(sum((v - sum(r) / COLS) ** 2 for v in r) for r in acc)
    ep = nv.norm_row_epilogue(COLS, V[M // 2], 4.0 / 127, 3.0)
    col = {}
    rows, cycles, M, wo = await run_conv(dut, x, wgt, vec=ep.vec.table_writes(), rq_tables=(ep.rq_mult, ep.rq_shift, ep.rq_zp),
                                         collect=col)
    exp, _, _ = nv.golden_epilogue(ep, acc, cols=COLS)
    vu = vr.VecUnit(COLS, COLS, 64)
    ep.vec.apply(vu)
    _, ecyc = vu.run(acc)
    got = col["rq"]
    bad = [m for m in range(M) if m >= len(got) or got[m] != exp[m]]
    assert len(got) == M and not bad, f"C14: {len(got)} INT8 rows, rows {bad[:6]} differ from the golden chain"
    assert col["cycles"] - col["seq_done"] == ecyc, f"C14: the unit took {col['cycles'] - col['seq_done']} cycles, the model {ecyc}"
    f = flags(dut)
    assert not any(f.values()), f"C14 flags {f}"
    err = 0.0
    for m in range(M):
        mu = sum(acc[m]) / COLS
        sd = (sum((v - mu) ** 2 for v in acc[m]) / COLS) ** 0.5
        for c in range(COLS):
            err = max(err, abs(max(-3.0, min(3.0, (acc[m][c] - mu) / sd)) - got[m][c] * 4.0 / 127) / (4.0 / 127))
    for w0 in ep.vec.prog:
        fc.sample_vec_op(vr.OP_NAMES[w0 & 15])
    dut._log.info(f"C14: conv + in-row layer norm over {COLS} channels, clip 3 sigma ({len(ep.vec.prog)} ops, "
                  f"{nv.cycles_per_row(ep.vec.prog, COLS)} cycles a row): {M} INT8 rows bit-exact, the unit {ecyc} cycles, flags clean; "
                  f"max |error| against the float norm {err:.2f} LSB")
    fc.report("funcov_neo_core.yml")


@cocotb.test()
async def c13_vec_fault_hooks(dut):
    """C13 (drop 0.43): the vector unit's own fault hooks on C12's layer, one run each: vec_ctrl_fault_inject (the primary
    control's row index flipped -- the duplicated control's comparator raises vec_err) and vec_tbl_fault_inject (a
    reserved bit of the slot-0 op word flipped as used -- the program's parity raises vec_perr, the rows unchanged). Each
    flag alone, every run complete."""
    import neo_vec as nv
    start_clock(dut, "C13")
    x, wgt, acc, res, ep, _ = c12_setup()
    exp, _, _ = nv.golden_epilogue(ep, acc, res, cols=COLS)
    seen = {}
    for hook, flag in (("ctrl", "vec_err"), ("tbl", "vec_perr")):
        col = {}
        rows, cycles, M, wo = await run_conv(dut, x, wgt, vec=ep.vec.table_writes(), rbuf=res,
                                             rq_tables=(ep.rq_mult, ep.rq_shift, ep.rq_zp), collect=col, vec_fault=hook)
        f = flags(dut)
        others = {n: v for n, v in f.items() if n != flag and v}
        assert f[flag] == 1 and not others, f"C13 {hook}: {flag} {f[flag]}, others {others}"
        if hook == "tbl":
            assert col["rq"] == exp, "C13: the parity fault (a reserved bit) changed the rows"
        seen[hook] = (cycles, len(col["rq"]))
        fc.sample_flag(flag)
    dut._log.info("C13: the vector unit's control fault -> vec_err alone, its table fault -> vec_perr alone (rows unchanged): "
                  + "; ".join(f"{h}: {c} cycles, {n} rows" for h, (c, n) in seen.items()))
    fc.report("funcov_neo_core.yml")
