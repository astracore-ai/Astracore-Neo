#!/usr/bin/env python3
"""run_mbist.py -- March C- MBIST on an ECC bank under neosim (the 512-bit bank with 16 lanes since drop 0.28: 64 words = 4 rows).
  B1 clean bank of 64 words: done, no fail, every word left at the last background
  B2 a stuck-at-1 emulated at word 37 bit 5 (re-applied after every write): fail with fail_addr = 37
  B3 after the MBIST, functional writes and reads still work and ECC flags are clean
  B4 (drop 0.43: the check registered, the first failure recorded a cycle after its read) a stuck bit that appears only
     for the last element's last read (word 63, the last row of the final up(r0)): fail and fail_addr are already set in
     the first cycle done is high -- done waits for the registered check
  B5 (drop 0.44, review #11) after a failing MBIST, err_clear clears fail and fail_ce (the tile's CTRL bit 1 clears every
     cause together); fail_addr keeps the address; a start afterwards runs a clean pass"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from neosim import Design, load
rtl = os.path.join(HERE, "..", "rtl")
def run(stuck=None, stuck2=None, stuck_from=0):
    d = Design(load([os.path.join(rtl, f) for f in ("ecc39.sv", "neo_mem.sv", "sram_bank.sv", "mbist.sv", "bank_bist_wrap.sv")]))
    d.elaborate("bank_bist_wrap", {"DEPTH": 64, "BIST_WORDS": 64})
    p = {n: d.cell_of(n) for n in ("rst_n", "we", "wrow", "wmask", "wdata", "re", "rrow", "rdata", "rvalid", "err_clear",
                                   "ecc_ce_sticky", "ecc_ue_sticky", "bist_start", "bist_active", "bist_done", "bist_fail", "bist_fail_addr", "bist_fail_ce")}
    for n in ("we", "re", "err_clear", "bist_start"): p[n].v = 0
    p["rst_n"].v = 0; d.tick(); d.tick(); p["rst_n"].v = 1; d.tick()
    p["bist_start"].v = 1; d.tick(); p["bist_start"].v = 0
    cycles = 0
    while not p["bist_done"].v:
        d.tick(); cycles += 1
        for st in (stuck, stuck2):
            if st is not None and cycles >= stuck_from:
                addr, bit = st
                c = d.cells[f"top.u_bank.u_mem.mem[{addr // 16}]"]            # the row word; lane addr % 16 at bits [39 * lane +: 39] (drop 0.40)
                c.v |= (1 << (bit + 39 * (addr % 16)))   # a stuck-at-1 in the array: the bit never stores a 0
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
# B4: the fault from the start of the final element (12th: up(r0) over the 4 rows = 8 cycles of read and check), so only
# the very last check sees it; the run returns in the first cycle bist_done is high
d4, p4, cyc4 = run(stuck=(63, 1), stuck_from=cyc - 9)
ok4 = p4["bist_fail"].v == 1 and p4["bist_fail_addr"].v == 63 and p4["bist_fail_ce"].v == 1 and cyc4 == cyc
print(f"  B4 stuck-at-1 at word 63 bit 1 from cycle {cyc - 9} (the final element), seen by the last check only: in the first cycle "
      f"of done fail={p4['bist_fail'].v}, fail_addr={p4['bist_fail_addr'].v} (expect 63), fail_ce={p4['bist_fail_ce'].v}, "
      f"done after {cyc4} cycles (clean run {cyc}) -> {'PASS' if ok4 else 'FAIL'}")
fails += not ok4
# B3 through the row port: word 9 = row 0 lane 9 (one lane of the 512-bit write, the lane picked out of the 512-bit read)
p["we"].v = 1; p["wrow"].v = 0; p["wmask"].v = 1 << 9; p["wdata"].v = 0xC0FFEE11 << (32 * 9); d.tick(); p["we"].v = 0
p["re"].v = 1; p["rrow"].v = 0; d.tick(); p["re"].v = 0
got = (p["rdata"].v >> (32 * 9)) & 0xFFFFFFFF
ok3 = got == 0xC0FFEE11 and p["rvalid"].v == 1 and p["ecc_ce_sticky"].v == 0
print(f"  B3 functional access after MBIST (row port, lane 9): wrote 0xC0FFEE11 at word 9, read 0x{got:08x}, ecc clean -> {'PASS' if ok3 else 'FAIL'}")
fails += not ok3
# B5 (drop 0.44): the failing bank of B2 -- err_clear pulsed once: fail / fail_ce drop, fail_addr stays; a clean start passes
p2["err_clear"].v = 1; d2.tick(); p2["err_clear"].v = 0; d2.tick()
cleared = (p2["bist_fail"].v, p2["bist_fail_ce"].v, p2["bist_fail_addr"].v)
p2["bist_start"].v = 1; d2.tick(); p2["bist_start"].v = 0
n5 = 0
while not p2["bist_done"].v:
    d2.tick(); n5 += 1
    if n5 > 20000: raise RuntimeError("B5: mbist did not finish")
ok5 = cleared == (0, 0, 37) and p2["bist_fail"].v == 0 and p2["bist_fail_ce"].v == 0
print(f"  B5 err_clear after the failing pass: fail={cleared[0]} fail_ce={cleared[1]} (expect 0, 0), fail_addr={cleared[2]} kept (expect 37); "
      f"the next start (stuck bit gone) done in {n5} cycles with fail={p2['bist_fail'].v} -> {'PASS' if ok5 else 'FAIL'}")
fails += not ok5
print("RESULT:", "ALL PASS" if fails == 0 else f"{fails} FAILURES")
raise SystemExit(1 if fails else 0)
