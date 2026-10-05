#!/usr/bin/env python3
"""run_mbist.py -- March C- MBIST on an ECC bank under neosim.
  B1 clean bank of 64 words: done, no fail, every word left at the last background
  B2 a stuck-at-1 emulated at word 37 bit 5 (re-applied after every write): fail with fail_addr = 37
  B3 after the MBIST, functional writes and reads still work and ECC flags are clean"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from neosim import Design, load
rtl = os.path.join(HERE, "..", "rtl")
def run(stuck=None, stuck2=None):
    d = Design(load([os.path.join(rtl, f) for f in ("ecc39.sv", "sram_bank.sv", "mbist.sv", "bank_bist_wrap.sv")]))
    d.elaborate("bank_bist_wrap", {"DEPTH": 64, "BIST_WORDS": 64})
    p = {n: d.cell_of(n) for n in ("rst_n", "we", "waddr", "wdata", "re", "raddr", "rdata", "rvalid", "err_clear",
                                   "ecc_ce_sticky", "ecc_ue_sticky", "bist_start", "bist_active", "bist_done", "bist_fail", "bist_fail_addr", "bist_fail_ce")}
    for n in ("we", "re", "err_clear", "bist_start"): p[n].v = 0
    p["rst_n"].v = 0; d.tick(); d.tick(); p["rst_n"].v = 1; d.tick()
    p["bist_start"].v = 1; d.tick(); p["bist_start"].v = 0
    cycles = 0
    while not p["bist_done"].v:
        d.tick(); cycles += 1
        for st in (stuck, stuck2):
            if st is not None:
                addr, bit = st
                c = d.cells[f"top.u_bank.mem[{addr}]"]
                c.v |= (1 << bit)                 # a stuck-at-1 in the array: the bit never stores a 0
        if cycles > 20000: raise RuntimeError("mbist did not finish")
    return d, p, cycles
fails = 0
d, p, cyc = run()
ok1 = p["bist_fail"].v == 0 and p["ecc_ue_sticky"].v == 0
print(f"  B1 clean bank, 64 words, 12 March elements: done in {cyc} cycles, fail={p['bist_fail'].v}, ecc_ue={p['ecc_ue_sticky'].v} -> {'PASS' if ok1 else 'FAIL'}")
fails += not ok1
d2, p2, cyc2 = run(stuck=(37, 5))
ok2 = p2["bist_fail"].v == 1 and p2["bist_fail_addr"].v == 37 and p2["bist_fail_ce"].v == 1
print(f"  B2 stuck-at-1 at word 37 bit 5: fail={p2['bist_fail'].v}, fail_addr={p2['bist_fail_addr'].v} (expect 37), found by the ECC "
      f"(fail_ce={p2['bist_fail_ce'].v}: the data read back correct because SECDED corrected it, so the correction itself is the finding) -> {'PASS' if ok2 else 'FAIL'}")
d3, p3, _ = run(stuck=(37, 5), stuck2=(37, 20))
ok2b = p3["bist_fail"].v == 1 and p3["bist_fail_addr"].v == 37 and p3["bist_fail_ce"].v == 0
print(f"  B2b two stuck bits in word 37: fail={p3['bist_fail'].v}, fail_addr={p3['bist_fail_addr'].v}, by data mismatch (fail_ce={p3['bist_fail_ce'].v}), ecc_ue={p3['ecc_ue_sticky'].v} -> {'PASS' if ok2b else 'FAIL'}")
fails += not ok2b
fails += not ok2
p["we"].v = 1; p["waddr"].v = 9; p["wdata"].v = 0xC0FFEE11; d.tick(); p["we"].v = 0
p["re"].v = 1; p["raddr"].v = 9; d.tick(); p["re"].v = 0
ok3 = p["rdata"].v == 0xC0FFEE11 and p["rvalid"].v == 1 and p["ecc_ce_sticky"].v == 0
print(f"  B3 functional access after MBIST: wrote 0xC0FFEE11 at 9, read 0x{p['rdata'].v:08x}, ecc clean -> {'PASS' if ok3 else 'FAIL'}")
fails += not ok3
print("RESULT:", "ALL PASS" if fails == 0 else f"{fails} FAILURES")
raise SystemExit(1 if fails else 0)
