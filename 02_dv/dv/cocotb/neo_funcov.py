"""neo_funcov.py -- functional coverage model for the cocotb tests (drop 0.16), sampled when NEO_FUNCOV=1.
Uses cocotb-coverage; absent that package the samplers are no-ops so the tests still run.
Covergroups: descriptor space (kernel, stride, channel tiles, output-row range), K-split roles, drain modes,
DMA ops executed, and every safety flag observed at least once in the fault-injection tests."""
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
    @CoverCross("neo.desc.k_x_s", items=["neo.desc.k", "neo.desc.s"])
    def sample_descriptor(d): pass

    @CoverPoint("neo.dma.op", xf=lambda op: op, bins=list(range(1, 12)))
    def sample_dma_op(op): pass

    @CoverPoint("neo.drain.mode", xf=lambda m: m, bins=["int32", "int8", "psum"])
    def sample_drain(m): pass

    @CoverPoint("neo.flag", xf=lambda f: f, bins=["parity", "crc", "array_abft", "acc_abft", "ctrl", "seq", "rq", "ecc_ue", "ecc_ce", "wbuf_ce",
                                                     "wbuf_ue", "tbl_perr", "lost", "timeout", "bist_fail", "prog_ce", "ctrl_path", "iso", "watchdog"])
    def sample_flag(f): pass

    def report(path):
        coverage_db.export_to_yaml(filename=path)
else:
    def sample_descriptor(d): pass
    def sample_dma_op(op): pass
    def sample_drain(m): pass
    def sample_flag(f): pass
    def report(path): pass
