#!/usr/bin/env python3
"""run_ecc39.py -- (39,32) SECDED under neosim: all 39 single-bit flips of 40 random words are
corrected (ce), 400 random double flips are detected (ue), clean words pass through untouched."""
import os, random, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from neosim import Design, load
rtl = os.path.join(HERE, "..", "rtl", "ecc39.sv")
enc = Design(load([rtl])); enc.elaborate("ecc39_enc")
dec = Design(load([rtl])); dec.elaborate("ecc39_dec")
ed, ep, eop = enc.cell_of("d"), enc.cell_of("p"), enc.cell_of("op")
dd, dp, dop, dout, ce, ue = (dec.cell_of(n) for n in ("d", "p", "op", "d_out", "ce", "ue"))
random.seed(5)
def encode(w):
    ed.v = w; enc.settle(); return ep.v, eop.v
def decode(w, p, op):
    dd.v, dp.v, dop.v = w, p, op; dec.settle(); return dout.v, ce.v, ue.v
clean = corr = det = miss = 0
for _ in range(40):
    w = random.getrandbits(32); p, op = encode(w)
    d, c, u = decode(w, p, op); clean += (d == w and c == 0 and u == 0)
    for b in range(39):
        w2, p2, op2 = w, p, op
        if b < 32: w2 ^= 1 << b
        elif b < 38: p2 ^= 1 << (b - 32)
        else: op2 ^= 1
        d, c, u = decode(w2, p2, op2); corr += (d == w and c == 1 and u == 0)
    for _ in range(10):
        a, b = random.sample(range(39), 2); w2, p2, op2 = w, p, op
        for x in (a, b):
            if x < 32: w2 ^= 1 << x
            elif x < 38: p2 ^= 1 << (x - 32)
            else: op2 ^= 1
        d, c, u = decode(w2, p2, op2); det += (u == 1 and c == 0); miss += (u == 0 and c == 0)
ok = clean == 40 and corr == 40 * 39 and det == 400
print(f"ecc39: clean {clean}/40, single flips corrected {corr}/{40 * 39}, double flips detected {det}/400 (missed {miss})")
print("RESULT:", "ALL PASS" if ok else "FAIL")
raise SystemExit(0 if ok else 1)
