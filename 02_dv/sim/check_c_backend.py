#!/usr/bin/env python3
"""check_c_backend.py -- the C host driver must emit exactly the descriptors and programs of the Python backend."""
import os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, "..", "compiler"))
from neo_backend import compile_layer
ORDER = ["cfg_h", "cfg_w", "cfg_ho", "cfg_wo", "cfg_oy0", "cfg_oy_n", "cfg_iy0", "cfg_s", "cfg_p", "cfg_k", "cfg_ct_n", "cfg_ct0",
         "cfg_ky0", "cfg_kx0", "cfg_rn", "cfg_contrib_n", "cfg_tile_pixels", "cfg_regions_m1"]
out = subprocess.run([os.path.join(HERE, "..", "build", "test_backend")], capture_output=True, text=True).stdout.splitlines()
cases = [compile_layer("c1", 6, 6, 40, 7, 3, 1, 1, 16, 8, 2, 2, (1, 1), 0, (1, 1), 512, owner=(0, 0), shares=3),
         compile_layer("rows3_5", 6, 6, 40, 7, 3, 1, 1, 16, 8, 2, 2, (1, 1), 0, (1, 1), 512, owner=(1, 0), shares=1, oy0=3, oy_n=3)]
i, fails, checked = 0, 0, 0
for lp in cases:
    assert out[i].startswith("tiles "); n = int(out[i].split()[1]); i += 1
    fails += n != len(lp.tiles)
    for tp in lp.tiles:
        hdr = out[i].split(); prog = [int(w, 16) for w in out[i + 1].split()[1:]]; i += 2
        tile = (int(hdr[1]), int(hdr[2])); cfg = [int(v) for v in hdr[8:8 + 18]]; cfg_m = int(hdr[6])
        exp_cfg = [tp.descriptor[k] & 0xFFFF for k in ORDER]
        ok = tile == tp.tile and cfg == exp_cfg and cfg_m == tp.descriptor["cfg_m"] and prog == tp.program
        checked += 1; fails += not ok
        if not ok:
            print("MISMATCH", tp.tile, tp.role, cfg == exp_cfg, prog == tp.program, cfg_m == tp.descriptor["cfg_m"])
print(f"C host driver vs Python backend: {checked} tile programs compared (descriptors, cfg_m, every program word) -> {'ALL PASS' if fails == 0 else f'{fails} FAILURES'}")
raise SystemExit(1 if fails else 0)
