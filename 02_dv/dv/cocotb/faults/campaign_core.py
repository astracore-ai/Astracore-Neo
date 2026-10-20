"""campaign_core.py -- the fault campaign's core driver (drop 0.43, stream D2): faults injected into the flops of neo_core
at 16 x 8 (the cocotb `core` build: the 2:4 sparse-capable core, NCHK = 4, --public-flat-rw) during a C1-class run.

The workload is C1 of test_neo_core.py: the 3 x 3 convolution of 40 channels (three channel tiles) into 7 on 6 x 6, 27
runs, the buffers written through the DMA ports, the requantization tables loaded (multiplier 30000 + c, shift 26, zero
point -3, so the INT8 outputs do not saturate), the 36 rows drained as INT32 (rd_data) and requantized INT8 (rq_q). Per
run: every flop is put back to its value at the test's start and the three arrays (activation, weight, accumulator
leaves) are cleared through the model's VPI (common.StateKeeper), the core is reset and loaded, go is pulsed, and after every rising edge the fault is applied and the eight sticky flags read; the run ends at
done plus 6 cycles (the last row's verdict and the requant's three stages) or at three times the fault-free run (a hang:
at core level no watchdog exists -- the tile's ESM watchdog is the mechanism, measured by the tile campaign). The result is
every drained INT32 and INT8 word in order, and the row count.

Environment: as campaign_tile.py (FAULTS_LIST, FAULTS_CSV, FAULTS_BUDGET_S, FAULTS_GOLDEN)."""
import json
import os
import time

import cocotb
import numpy as np
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadWrite

import test_neo_core as tc
from neo_golden import direct_conv, bits, sbits
from faults.common import Injector, Fault, StateKeeper, classify, CsvOut, Budget, load_state_items

ROWS, COLS, WCW, CIN = tc.ROWS, tc.COLS, tc.WCW, tc.CIN
FLAGS = ["array_abft", "acc_abft", "ctrl_err", "seq_err", "rq_err", "rq_tbl_perr", "wbuf_ce", "wbuf_ue",
         "abuf_ce", "abuf_ue", "act_perr"]                                                     # drop 0.44 (S1)
LEAVES = ["u_dp.u_feeder.u_mem.mem", "u_wbuf.u_mem.mem", "u_dp.u_acc.u_mem.mem"]


class CoreRig:
    def __init__(self, dut, x, wgt):
        self.dut = dut
        self.inj = Injector(dut)
        self.flags = [getattr(dut, n + "_sticky") for n in FLAGS]
        self.leaves = [self.inj.handle(p) for p in LEAVES]
        self.x, self.wgt = x, wgt
        self.cin_tiles = (x.shape[0] + ROWS - 1) // ROWS
        self.act = tc.packed_act(x, self.cin_tiles, 6, 6)
        self.wts = tc.packed_w(wgt, self.cin_tiles, 3, 0)
        self.keeper = StateKeeper(self.inj, load_state_items())     # every flop back to the test's start (common.py)
        self.keeper.snapshot()

    def causes(self):
        return [n for n, h in zip(FLAGS, self.flags) if int(h.value)]

    async def run(self, fault=None, bound=20000):
        dut = self.dut
        self.keeper.restore()
        for h in self.leaves:
            for i in range(len(h)):
                h[i].value = 0
        for name in ("abuf_we", "wbuf_we", "go", "err_clear", "fault_inject", "ctrl_fault_inject", "rq_tbl_we", "rq_relu", "ext_valid",
                     "seq_fault_inject", "rq_fault_inject", "rq_tbl_fault_inject", "dv_wbuf_flip", "dv_wbuf_addr", "dv_wbuf_lane",
                     "dv_wbuf_mask", "abuf_we2", "wbuf_we2", "cfg_sparse", "dv_abuf_flip", "dv_abuf_addr", "dv_abuf_lane", "dv_abuf_mask"):
            getattr(dut, name).value = 0
        dut.rst_n.value = 0
        for _ in range(3):
            await RisingEdge(dut.clk)
        dut.rst_n.value = 1
        await RisingEdge(dut.clk)
        for c in range(COLS):
            dut.rq_tbl_we.value = 1
            dut.rq_tbl_addr.value = c
            dut.rq_tbl_mult.value = 30000 + c
            dut.rq_tbl_shift.value = 26
            dut.rq_tbl_zp.value = bits(-3, 8)
            await RisingEdge(dut.clk)
        dut.rq_tbl_we.value = 0
        await tc.write_abuf(dut, self.act)
        await tc.write_wbuf(dut, self.wts)
        h, w, k = 6, 6, 3
        for name, v in (("cfg_h", h), ("cfg_w", w), ("cfg_ho", 6), ("cfg_wo", 6), ("cfg_s", 1), ("cfg_p", 1), ("cfg_oy0", 0),
                        ("cfg_oy_n", 6), ("cfg_iy0", 0), ("cfg_k", k), ("cfg_ct_n", self.cin_tiles), ("cfg_tile_pixels", h * w),
                        ("cfg_ct0", 0), ("cfg_ky0", 0), ("cfg_kx0", 0), ("cfg_rn", self.cin_tiles * k * k), ("cfg_contrib_n", 0),
                        ("cfg_regions_m1", 0xFFFF), ("tiles_ready", 0x7FFF)):
            getattr(dut, name).value = bits(v, 16)
        dut.cfg_m.value = 36
        dut.go.value = 1
        await RisingEdge(dut.clk)
        dut.go.value = 0
        if fault is not None:
            fault.arm(self.inj)
        words, first_det, first, done_at, cyc = [], None, [], None, 0
        for cyc in range(bound):
            await RisingEdge(dut.clk)
            # the reads first, before the edge is evaluated (one phase for all of them: each cycle's rows once, a flag
            # raised at the edge ending cycle t seen at cycle t + 1), then the fault in the ReadWrite phase
            if int(dut.rd_valid.value):
                words += [("d", int(dut.rd_data[c].value)) for c in range(COLS)]
            if int(dut.rq_valid.value):
                words += [("q", int(dut.rq_q[c].value)) for c in range(COLS)]
            if first_det is None and any(int(fh.value) for fh in self.flags):
                first_det, first = cyc, self.causes()
            if done_at is None:
                if int(dut.done.value):
                    done_at = cyc
            elif cyc - done_at >= 6:
                break
            if fault is not None and fault.active(cyc):
                await ReadWrite()
                fault.apply(self.inj, cyc)
        finished = done_at is not None and cyc - done_at >= 6
        return dict(finished=finished, done_at=done_at, cycles=cyc, first_det=first_det, first=first, causes=self.causes(), words=words)


def diff_words(a, b):
    n = sum(1 for p, q in zip(a, b) if p != q)
    return n + abs(len(a) - len(b))


@cocotb.test()
async def faults_core(dut):
    cocotb.start_soon(Clock(dut.clk, 500, units="ps").start())
    rng = np.random.default_rng(9)
    x = rng.integers(-128, 128, size=(CIN, 6, 6), dtype=np.int64)
    wgt = rng.integers(-128, 128, size=(7, CIN, 3, 3), dtype=np.int64)
    ref = direct_conv(x, wgt, 1, 1)
    t0 = time.time()
    rig = CoreRig(dut, x, wgt)
    t1 = time.time()
    rig.keeper.restore()
    dut._log.info(f"faults_core: {len(rig.keeper.hs)} flops kept for the runs ({rig.keeper.missed} not indexable), "
                  f"handles and snapshot {t1 - t0:.2f} s, a restore pass {time.time() - t1:.3f} s")
    t0 = time.time()
    g = await rig.run()
    gwall = time.time() - t0
    assert g["finished"] and not g["causes"], f"the fault-free run: finished {g['finished']}, causes {g['causes']}"
    drows = [w for t, w in g["words"] if t == "d"]
    got = np.array([sbits(v, 32) for v in drows]).reshape(-1, COLS)
    assert got.shape[0] == 36 and all(got[m, c] == ref[c, m // 6, m % 6] for m in range(36) for c in range(7)), \
        "the fault-free run differs from the golden convolution"
    G = g["done_at"]
    gfile = os.environ.get("FAULTS_GOLDEN")
    if gfile:
        facts = dict(workload="c1", run_cycles=G, wall_s=round(gwall, 3))
        if os.environ.get("FAULTS_PROBE"):               # the population's arrays the injector cannot reach (common.py)
            with open(os.environ["FAULTS_PROBE"]) as f:
                facts["unreachable"] = rig.inj.unreachable(json.load(f))
        with open(gfile, "w") as f:
            json.dump(facts, f)
    dut._log.info(f"faults_core: fault-free C1 in {G} cycles ({gwall:.2f} s)")
    flist = os.environ.get("FAULTS_LIST")
    if not flist:
        return
    with open(flist) as f:
        faults = json.load(f)
    out = CsvOut(os.environ.get("FAULTS_CSV", "faults_core.csv"))
    budget = Budget()
    done = 0
    for fd in faults:
        if not budget.left():
            break
        f = Fault(fd)
        t1 = time.time()
        try:
            f.arm(rig.inj)
        except Exception as e:
            dut._log.warning(f"faults_core: fault {fd['id']} {f.name()}: not resolved ({e!r})")
            out.row(id=fd["id"], config="core", workload="c1", block=fd["block"], flop=f.name(), bit=f.bit, type=f.type,
                    cycle=f.cycle, outcome="unresolved")
            continue
        r = await rig.run(fault=f, bound=3 * G + 50)
        diff = diff_words(r["words"], g["words"])
        outcome, latency = classify(r["finished"], r["causes"], r["first_det"], f.cycle, diff)
        out.row(id=fd["id"], config="core", workload="c1", block=fd["block"], flop=f.name(), bit=f.bit, type=f.type, cycle=f.cycle,
                outcome=outcome, causes="+".join(r["causes"]), first_cause="+".join(r["first"]),
                latency=latency, diff=diff, run_cycles=r["cycles"], wall_ms=int(1000 * (time.time() - t1)))
        done += 1
    out.close()
    dut._log.info(f"faults_core: {done} of {len(faults)} faults run in {time.time() - budget.t0:.0f} s")
