#!/usr/bin/env python3
"""gen_tile_vectors.py -- vectors for tb/tb_tile_mesh.sv (Xcelium replay of M1 through the register bus):
bank image of tile (1,1) as (39,32) codewords, the descriptor registers, the DMA program, and the expected
INT32 result rows at tile (1,0). No simulation involved: golden models only."""
import os, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from run_tiles import pack_words, direct_conv, ecc_encode, ins, bits, OP_FETCH_A, OP_FETCH_W, OP_DRAIN_WR, OP_GO, OP_WAIT_DONE, OP_END, ROWS, COLS, WPA, WPW, CFG_ORDER
out = os.path.join(HERE, "..", "vectors_tile"); os.makedirs(out, exist_ok=True)
rng = np.random.default_rng(17)
x = rng.integers(-128, 128, size=(40, 6, 6), dtype=np.int64)
wgt = rng.integers(-128, 128, size=(7, 40, 3, 3), dtype=np.int64)
ref = direct_conv(x, wgt, 1, 1)
cin_tiles, k, cout, ho, wo = 3, 3, 7, 6, 6
act, wts = pack_words(x, wgt, cin_tiles, k, cout)
A0, W0, R0 = 0, 512, 2048
APT, WPT = 36 * WPA, k * k * ROWS * WPW
M = ho * wo
bank = {}
for i, wd in enumerate(act): bank[A0 + i] = wd
for i, wd in enumerate(wts): bank[W0 + i] = wd
with open(os.path.join(out, "bank11.hex"), "w") as f:          # sparse: "@addr word39" per line
    for a in sorted(bank):
        p, op = ecc_encode(bank[a]); f.write(f"@{a:x} {((op << 38) | (p << 32) | (bank[a] & 0xFFFFFFFF)):010x}\n")
desc = dict(cfg_h=6, cfg_w=6, cfg_ho=ho, cfg_wo=wo, cfg_oy0=0, cfg_oy_n=ho, cfg_iy0=0, cfg_s=1, cfg_p=1, cfg_k=k, cfg_ct_n=cin_tiles,
            cfg_ct0=0, cfg_ky0=0, cfg_kx0=0, cfg_rn=cin_tiles * k * k, cfg_contrib_n=0, cfg_tile_pixels=36, cfg_regions_m1=0xFFFF)
with open(os.path.join(out, "cfg.hex"), "w") as f:
    for n in CFG_ORDER: f.write(f"{bits(desc[n], 16):04x}\n")
    f.write(f"{M:04x}\n")
prog = [ins(OP_FETCH_A, 1, 1, A0 + ct * APT, APT, ct * 36) for ct in range(cin_tiles)]
prog += [ins(OP_FETCH_W, 1, 1, W0, cin_tiles * WPT, 0), ins(OP_DRAIN_WR, 1, 0, R0, M), ins(OP_GO), ins(OP_WAIT_DONE), ins(OP_END)]
with open(os.path.join(out, "prog.hex"), "w") as f:
    for w in prog: f.write(f"{w:016x}\n")
with open(os.path.join(out, "y_exp.hex"), "w") as f:
    for m in range(M):
        for c in range(COLS):
            v = int(ref[c, m // wo, m % wo]) if c < cout else 0
            f.write(f"{v & 0xFFFFFFFF:08x}\n")
print(f"vectors_tile: {len(bank)} bank words, {len(prog)} program words, {M} expected rows x {COLS} (R0 = {R0})")
