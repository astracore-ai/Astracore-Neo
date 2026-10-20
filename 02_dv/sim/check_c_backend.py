#!/usr/bin/env python3
"""check_c_backend.py -- the C host driver must emit exactly the descriptors and programs of the Python backend."""
import os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, "..", "compiler"))
from neo_backend import compile_layer, cq_image
ORDER = ["cfg_h", "cfg_w", "cfg_ho", "cfg_wo", "cfg_oy0", "cfg_oy_n", "cfg_iy0", "cfg_s", "cfg_p", "cfg_k", "cfg_ct_n", "cfg_ct0",
         "cfg_ky0", "cfg_kx0", "cfg_rn", "cfg_contrib_n", "cfg_tile_pixels", "cfg_regions_m1", "cfg_flags"]   # cfg_flags: drop 0.42
out = subprocess.run([os.path.join(HERE, "..", "build", "test_backend")], capture_output=True, text=True).stdout.splitlines()
cases = [compile_layer("c1", 6, 6, 40, 7, 3, 1, 1, 16, 8, 2, 2, (1, 1), 0, (1, 1), 512, owner=(0, 0), shares=3),
         compile_layer("rows3_5", 6, 6, 40, 7, 3, 1, 1, 16, 8, 2, 2, (1, 1), 0, (1, 1), 512, owner=(1, 0), shares=1, oy0=3, oy_n=3),
         compile_layer("sparse", 6, 6, 64, 7, 3, 1, 1, 16, 8, 2, 2, (1, 1), 0, (1, 1), 512, owner=(0, 0), shares=2, nchk=4, sparse=True),
         compile_layer("runtime", 6, 6, 32, 7, 3, 1, 1, 16, 8, 2, 2, (2, 0), 0x100, (2, 0), 0x800, owner=(0, 0), shares=1, int8_out=True,
                       out_base=0x1000, drain_fmt=0x2000, int4=True, rbuf=((2, 0), 0x2000, 36 * 4))]   # drop 0.44: the runtime's form
i, fails, checked = 0, 0, 0
for ci, lp in enumerate(cases):
    if ci == 2:                      # drop 0.43: the command queue's image of the first case, after the second case
        assert out[i].startswith("cqimage "); f = out[i].split(); i += 1
        words = [int(v, 16) for v in f[2:]]
        exp = [w for e in cq_image(cases[0].tiles, 2, extra=[((1 << 14) | 0x32, 1)]) for w in e]
        checked += 1; ok = int(f[1]) == len(exp) // 2 and words == exp; fails += not ok
        if not ok:
            print("MISMATCH cq_image", int(f[1]), len(exp) // 2)
    assert out[i].startswith("tiles "); n = int(out[i].split()[1]); i += 1
    fails += n != len(lp.tiles)
    for tp in lp.tiles:
        hdr = out[i].split(); prog = [int(w, 16) for w in out[i + 1].split()[1:]]; i += 2
        tile = (int(hdr[1]), int(hdr[2])); cfg = [int(v) for v in hdr[8:8 + len(ORDER)]]; cfg_m = int(hdr[6])
        exp_cfg = [tp.descriptor.get(k, 0) & 0xFFFF for k in ORDER]          # a dense descriptor carries no cfg_flags (0)
        ok = tile == tp.tile and cfg == exp_cfg and cfg_m == tp.descriptor["cfg_m"] and prog == tp.program
        checked += 1; fails += not ok
        if not ok:
            print("MISMATCH", tp.tile, tp.role, cfg == exp_cfg, prog == tp.program, cfg_m == tp.descriptor["cfg_m"])
assert out[i].startswith("m0 "); checked += 1
if out[i].split()[1] != "-3":                 # drop 0.44 (review finding 13): a group of no output rows is refused by both
    fails += 1; print("MISMATCH: the C driver did not refuse cfg_m = 0 with -3")
try:
    compile_layer("m0", 6, 6, 32, 7, 3, 1, 1, 16, 8, 2, 2, (2, 0), 0, (2, 0), 0x800, owner=(0, 0), oy_n=0)
    fails += 1; print("MISMATCH: the Python backend did not refuse cfg_m = 0")
except (ValueError, AssertionError):
    pass
print(f"C host driver vs Python backend: {checked} tile programs and command-queue images compared (descriptors, cfg_m, every program word, every image word) -> {'ALL PASS' if fails == 0 else f'{fails} FAILURES'}")
raise SystemExit(1 if fails else 0)
