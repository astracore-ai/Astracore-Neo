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
reads added, injected through the model's VPI, raises its comparator's flag."""
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
                   desc=None, psums=(), words=None, vpi_flip=None):
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
    0.43, C6; the fault campaign's injection, 02_dv/dv/cocotb/faults/). Returns (rows, cycles, M, wo)."""
    cin = x.shape[0]
    cin_tiles = (cin + ROWS - 1) // ROWS
    h, w = x.shape[1], x.shape[2]
    ho = (h + 2 * p - k) // s + 1
    wo = (w + 2 * p - k) // s + 1
    M = ho * wo
    for name in ("abuf_we", "wbuf_we", "go", "err_clear", "fault_inject", "ctrl_fault_inject", "rq_tbl_we", "rq_relu", "ext_valid",
                 "seq_fault_inject", "rq_fault_inject", "rq_tbl_fault_inject", "dv_wbuf_flip", "dv_wbuf_addr", "dv_wbuf_lane", "dv_wbuf_mask",
                 "cfg_sparse"):
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
        dut.rq_tbl_mult.value = 30000 + c
        dut.rq_tbl_shift.value = 26
        dut.rq_tbl_zp.value = bits(-3, 8)
        await RisingEdge(dut.clk)
    dut.rq_tbl_we.value = 0
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
    dut.go.value = 1
    await RisingEdge(dut.clk)
    dut.go.value = 0
    rows, cycles, in_drain, rd_valid = [], 0, False, 0
    queue = list(psums)
    flip_h = handle(dut, vpi_flip[0]) if vpi_flip else None
    flip_c = handle(dut, vpi_flip[2]) if (vpi_flip and vpi_flip[2]) else None
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
        if int(dut.done.value):
            break
    await RisingEdge(dut.clk)
    for name in ("ctrl_fault_inject", "seq_fault_inject", "rq_fault_inject", "rq_tbl_fault_inject", "ext_valid"):
        getattr(dut, name).value = 0
    assert not queue, f"{len(queue)} partial-sum rows not accepted"
    assert flip_h is None, f"vpi_flip {vpi_flip}: its condition never held, nothing was injected"
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
    cocotb.start_soon(Clock(dut.clk, 500, units="ps").start())
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
    cocotb.start_soon(Clock(dut.clk, 500, units="ps").start())
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
    these runs finished silently, with wrong rows or none (a consistent wrong row passes the ABFT check and the SECDED lanes).
    Every run finishes with its rows drained."""
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
