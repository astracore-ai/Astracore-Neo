"""campaign_tile.py -- the fault campaign's tile driver (drop 0.43, stream D2): faults injected into the flops of the 2 x 2
mesh of 16 x 8 tiles (the cocotb `tile` build, --public-flat-rw) during an M1-class run, each classified against the
fault-free run.

The workload is M1 of test_tile_mesh.py, driven as the island's firmware drives a tile: tile (0,0) fetches three
activation tiles and three weight tiles from bank (1,1), computes the 3 x 3 convolution of 40 channels into 7 (36 output
rows, 27 runs), drains the INT32 rows to bank (1,0) and ends; the watchdog is enabled with a window of twice the
fault-free run and kicked at the start (the island's FTTI-windowed schedule for one layer). Per run: the core arrays and
the NIC's leaves are cleared, the four banks put back to the initial image and every flop of the build to its value at the
test's start through the model's VPI (common.StateKeeper: the arrays and the registers without a reset keep what the last
run left; every run starts from the state the fault-free run started from), the tile is reset, programmed through the
register bus and started; after every rising edge the fault (common.Fault) is applied and err_pin sampled; the run ends
when tile (0,0) reports prog_done with its drain idle, plus 40 cycles for the last write-back to land, or at the bound
(the watchdog window + 200 cycles: a hang). The result is every word of the four banks (a write anywhere is a result)
and the four tiles' cause registers (u_esm.cause, the 18 flags and the watchdog).

Environment: FAULTS_LIST (the worker's JSON list from run_faults.py), FAULTS_OUT (its CSV), FAULTS_BUDGET_S (stop when the
wall clock has run out), FAULTS_GOLDEN (a JSON file the fault-free run's facts are written to)."""
import json
import os
import time

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ReadWrite

import test_tile_mesh as tm
from neo_golden import ecc_encode, ins, OP_FETCH_A, OP_DRAIN_WR, OP_GO, OP_WAIT_DONE, OP_END
from faults.common import Injector, Fault, StateKeeper, classify, CsvOut, Budget, load_state_items

NX, NY = tm.NX, tm.NY
N = NX * NY
LANES = 16
BANK_ROWS = int(os.environ.get("NEO_BANK_DEPTH", "4096")) // LANES
CAUSES = ["parity_err", "crc_err", "array_abft", "acc_abft", "ctrl_err", "seq_err", "rq_err", "ecc_ue", "ecc_ce", "wbuf_ce",
          "wbuf_ue", "rq_tbl_perr", "lost_err", "fetch_timeout", "bist_fail", "prog_ce", "ctrl_path_err", "iso_err", "watchdog"]
REG_WD_CTRL, REG_WD_KICK = 0x07, 0x08


def tile_path(n):
    x, y = n % NX, n // NX
    return f"u_mesh.g_y[{y}].g_x[{x}].u_t"


# the arrays that keep their contents through a reset: cleared before every run (the fault-free run started from zeros)
LEAVES = ["u_core.u_dp.u_feeder.u_mem.mem", "u_core.u_wbuf.u_mem.mem", "u_core.u_dp.u_acc.u_mem.mem", "u_nic.u_dfifo.mem",
          "u_nic.u_prow.mem"]


class TileRig:
    def __init__(self, dut):
        self.dut = dut
        self.mesh = tm.Mesh(dut)
        self.inj = Injector(dut)
        self.banks = [self.inj.handle(f"{tile_path(n)}.u_bank.u_bank.u_mem.mem") for n in range(N)]
        self.leaves = [self.inj.handle(f"{tile_path(n)}.{p}") for n in range(N) for p in LEAVES]
        self.esm = [self.inj.handle(f"{tile_path(n)}.u_esm.cause") for n in range(N)]
        self.init_image = {}                         # (node, row) -> 624-bit row, the rows the workload loads
        self.golden = None
        self.keeper = StateKeeper(self.inj, load_state_items())     # every flop back to the test's start (common.py)
        self.keeper.snapshot()

    # ---- memories through the VPI ----
    def load_words(self, node, base, words):
        rows = {}
        for i, wd in enumerate(words):
            a = base + i
            p, op = ecc_encode(wd)
            cw = (op << 38) | (p << 32) | (wd & 0xFFFFFFFF)
            r, lane = divmod(a, LANES)
            rows[r] = rows.get(r, 0) | (cw << (39 * lane))
        for r, v in rows.items():
            self.init_image[(node, r)] = v

    def restore(self):
        """every flop back to the test's start, the banks back to the initial image (only the rows that differ), the
        leaves cleared"""
        self.keeper.restore()
        for n in range(N):
            b = self.banks[n]
            for r in range(BANK_ROWS):
                want = self.init_image.get((n, r), 0)
                if int(b[r].value) != want:
                    b[r].value = want
        for h in self.leaves:
            for i in range(len(h)):
                h[i].value = 0

    def bank_image(self):
        return [[int(self.banks[n][r].value) for r in range(BANK_ROWS)] for n in range(N)]

    def causes(self):
        out = []
        for n in range(N):
            c = int(self.esm[n].value)
            out += [f"{CAUSES[i]}@{n}" for i in range(len(CAUSES)) if (c >> i) & 1]
        return out

    # ---- one run ----
    async def run(self, fault=None, window=60000, bound=None):
        m, dut = self.mesh, self.dut
        self.restore()
        await m.reset()
        await m.descriptor(0, 6, 6, 6, 6, 1, 1, 3, tm.CT_N, 0, tm.CT_N * 9, 0, 36)
        prog = [ins(OP_FETCH_A, 1, 1, tm.A0 + ct * tm.APT, tm.APT, ct * 36) for ct in range(tm.CT_N)]
        prog += tm.fetch_weights(1, 1) + [ins(OP_DRAIN_WR, 1, 0, tm.R0, 36), ins(OP_GO), ins(OP_WAIT_DONE), ins(OP_END)]
        await m.program(0, prog)
        await m.reg_write(0, REG_WD_CTRL, (window << 8) | 1)
        await m.reg_write(0, REG_WD_KICK, 1)
        await m.reg_write(0, tm.REG_CTRL, 1)          # start
        if fault is not None:
            fault.arm(self.inj)
        bound = bound or (window + 200)
        first_det, first, finished, tail, cyc = None, [], False, None, 0
        for cyc in range(bound):
            await RisingEdge(dut.clk)
            # the reads first, before the edge is evaluated: every cycle sees the state the previous cycle left (one
            # phase for all of them; a cause raised at the edge ending cycle t is seen here at cycle t + 1)
            if first_det is None and int(dut.err_pin.value):
                first_det, first = cyc, self.causes()
            if tail is None:
                if (int(dut.prog_done.value) & 1) and not (int(dut.drain_busy.value) & 1):
                    tail = cyc
            elif cyc - tail >= 40:
                finished = True
                break
            if fault is not None and fault.active(cyc):
                await ReadWrite()
                fault.apply(self.inj, cyc)
        return dict(finished=finished, done_at=tail, cycles=cyc, first_det=first_det, first=first, causes=self.causes(),
                    image=self.bank_image())


POS = [j for j in range(1, 39) if j & (j - 1)]       # ecc39: data bit k at the k-th non-power-of-two position of 1..38


def ecc_fix(cw):
    """(data, status) of a (39,32) codeword {op, p[5:0], d[31:0]} as ecc39_dec reads it: a single error corrected ('ce'), two
    detected ('ue')"""
    d, p, op = cw & 0xFFFFFFFF, (cw >> 32) & 0x3F, (cw >> 38) & 1
    s = 0
    for k, j in enumerate(POS):
        if (d >> k) & 1:
            s ^= j
    s ^= p
    pe = (bin(d).count("1") + bin(p).count("1") + op) & 1
    if pe:
        if s in POS:
            d ^= 1 << POS.index(s)
        return d, "ce"
    return d, ("ue" if s else "ok")


def diff_words(a, b):
    """the number of bank words whose data, as a reader decodes it, differs from the fault-free run's (b): a codeword with a
    single flipped bit reads back corrected (ecc_ce at the reader) and is not a wrong result; two flips or a wrongly
    written word are"""
    n = 0
    mask = (1 << 39) - 1
    for ra, rb in zip(a, b):
        for x, y in zip(ra, rb):
            if x != y:
                for l in range(LANES):
                    cx, cy = (x >> (39 * l)) & mask, (y >> (39 * l)) & mask
                    if cx != cy:
                        dx, st = ecc_fix(cx)
                        if st == "ue" or dx != (cy & 0xFFFFFFFF):
                            n += 1
    return n


@cocotb.test()
async def faults_tile(dut):
    cocotb.start_soon(Clock(dut.clk, 500, units="ps").start())
    t0 = time.time()
    rig = TileRig(dut)
    t1 = time.time()
    rig.keeper.restore()
    dut._log.info(f"faults_tile: {len(rig.keeper.hs)} flops kept for the runs ({rig.keeper.missed} not indexable), "
                  f"handles and snapshot {t1 - t0:.2f} s, a restore pass {time.time() - t1:.3f} s")
    x, wgt, ref, act, wts = tm.conv_data()
    rig.load_words(tm.N11, tm.A0, act)
    rig.load_words(tm.N11, tm.W0, wts)
    t0 = time.time()
    g = await rig.run()
    gwall = time.time() - t0
    assert g["finished"] and not g["causes"], f"the fault-free run: finished {g['finished']}, causes {g['causes']}"
    got = tm.to_image(_rows_from_image(g["image"], tm.N10, tm.R0, 36), ref, 36)
    assert (got == ref).all(), "the fault-free run's result differs from the golden convolution"
    G = g["done_at"]
    window = 2 * G
    gfile = os.environ.get("FAULTS_GOLDEN")
    if gfile:
        facts = dict(workload="m1", run_cycles=G, wall_s=round(gwall, 3), window=window)
        if os.environ.get("FAULTS_PROBE"):               # the population's arrays the injector cannot reach (common.py)
            with open(os.environ["FAULTS_PROBE"]) as f:
                facts["unreachable"] = rig.inj.unreachable(json.load(f))
        with open(gfile, "w") as f:
            json.dump(facts, f)
    dut._log.info(f"faults_tile: fault-free M1 in {G} cycles ({gwall:.2f} s), watchdog window {window}")
    flist = os.environ.get("FAULTS_LIST")
    if not flist:
        return
    with open(flist) as f:
        faults = json.load(f)
    out = CsvOut(os.environ.get("FAULTS_OUT", "faults_tile.csv"))
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
            dut._log.warning(f"faults_tile: fault {fd['id']} {f.name()}: not resolved ({e!r})")
            out.row(id=fd["id"], config="tile", workload="m1", node=fd.get("node", ""), block=fd["block"], flop=f.name(),
                    bit=f.bit, type=f.type, cycle=f.cycle, outcome="unresolved")
            continue
        r = await rig.run(fault=f, window=window)
        diff = diff_words(r["image"], g["image"])
        outcome, latency = classify(r["finished"], r["causes"], r["first_det"], f.cycle, diff)
        out.row(id=fd["id"], config="tile", workload="m1", node=fd.get("node", ""), block=fd["block"], flop=f.name(), bit=f.bit,
                type=f.type, cycle=f.cycle, outcome=outcome, causes="+".join(r["causes"]),
                first_cause="+".join(r["first"]), latency=latency, diff=diff, run_cycles=r["cycles"],
                wall_ms=int(1000 * (time.time() - t1)))
        done += 1
    out.close()
    dut._log.info(f"faults_tile: {done} of {len(faults)} faults run in {time.time() - budget.t0:.0f} s")


def _rows_from_image(image, node, base, M):
    import numpy as np
    out = np.zeros((M, tm.COLS), dtype=np.int64)
    for m in range(M):
        for j in range(tm.COLS):
            a = base + m * (tm.COLS + 1) + j
            r, lane = divmod(a, LANES)
            w = (image[node][r] >> (39 * lane)) & 0xFFFFFFFF
            out[m, j] = w - (1 << 32) if w >> 31 else w
    return out
