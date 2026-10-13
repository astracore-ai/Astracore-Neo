"""neo_funcov.py -- functional coverage model for the cocotb tests (drop 0.16), sampled when NEO_FUNCOV=1.
Uses cocotb-coverage; absent that package the samplers are no-ops so the tests still run.
Covergroups: descriptor space (kernel, stride, channel tiles, output-row range), K-split roles, drain modes,
DMA ops executed, and every safety flag observed at least once in the fault-injection tests. Drop 0.42: the layer's
mode (dense / 2:4 sparse, the descriptor's cfg_flags bit 0) and the index values a sparse layer's PEs hold (0..3: the
position of each kept weight in its group of four channels, every value -- every check column -- exercised).
Drop 0.43: the vector unit's sixteen micro-ops (each executed by a test's program at least once), the NIC's fetch
formats (INT8 / INT4 weights, activations, the unit's residual buffer), the drain layouts (rows, transposed blocks,
weight entries, padded activation entries) and the unit's two flags (its control comparator, its table parity)."""
import os
try:
    from cocotb_coverage.coverage import CoverPoint, CoverCross, coverage_db
    HAVE = True
except Exception:  # pragma: no cover
    HAVE = False
ENABLED = HAVE and os.environ.get("NEO_FUNCOV") == "1"

if ENABLED:
    @CoverPoint("neo.desc.k", xf=lambda d: d["cfg_k"], bins=[1, 3, 5])
    @CoverPoint("neo.desc.s", xf=lambda d: d["cfg_s"], bins=[1, 2])
    @CoverPoint("neo.desc.ct_n", xf=lambda d: min(d["cfg_ct_n"], 4), bins=[1, 2, 3, 4])
    @CoverPoint("neo.desc.partial_rows", xf=lambda d: d["cfg_oy_n"] < d["cfg_ho"], bins=[True, False])
    @CoverPoint("neo.desc.role", xf=lambda d: "owner" if d["cfg_contrib_n"] > 0 else ("contrib" if d["cfg_ct0"] > 0 or d["cfg_ky0"] or d["cfg_kx0"] else "solo"), bins=["solo", "owner", "contrib"])
    @CoverPoint("neo.desc.sparse", xf=lambda d: int(d.get("cfg_flags", 0)) & 1, bins=[0, 1])
    @CoverCross("neo.desc.k_x_s", items=["neo.desc.k", "neo.desc.s"])
    def sample_descriptor(d): pass

    @CoverPoint("neo.sparse.index", xf=lambda i: i, bins=[0, 1, 2, 3])
    def sample_sparse_index(i): pass

    @CoverPoint("neo.dma.op", xf=lambda op: op, bins=list(range(1, 12)))
    def sample_dma_op(op): pass

    @CoverPoint("neo.drain.mode", xf=lambda m: m, bins=["int32", "int8", "psum"])
    def sample_drain(m): pass

    @CoverPoint("neo.flag", xf=lambda f: f, bins=["parity", "crc", "array_abft", "acc_abft", "ctrl", "seq", "rq", "ecc_ue", "ecc_ce", "wbuf_ce",
                                                     "wbuf_ue", "tbl_perr", "lost", "timeout", "bist_fail", "prog_ce", "ctrl_path", "iso", "watchdog",
                                                     "vec_err", "vec_perr"])
    def sample_flag(f): pass

    @CoverPoint("neo.vec.op", xf=lambda o: o, bins=["END", "EMIT", "ADD_ROW", "MUL_SCALE", "CLAMP", "LUT", "RED", "BSUB", "BMUL", "SQR",
                                                     "SLD", "RSST", "SALU", "SMUL", "SLUT", "RELOAD"])
    def sample_vec_op(o): pass

    @CoverPoint("neo.fetch.format", xf=lambda f: f, bins=["w_int8", "w_int4", "act", "rbuf"])
    def sample_fetch(f): pass

    @CoverPoint("neo.drain.layout", xf=lambda f: f, bins=["rows", "transpose", "entry", "pad", "grouped"])
    def sample_drain_layout(f): pass

    def report(path):
        coverage_db.export_to_yaml(filename=path)
else:
    def sample_descriptor(d): pass
    def sample_sparse_index(i): pass
    def sample_dma_op(op): pass
    def sample_drain(m): pass
    def sample_flag(f): pass
    def sample_vec_op(o): pass
    def sample_fetch(f): pass
    def sample_drain_layout(f): pass
    def report(path): pass
