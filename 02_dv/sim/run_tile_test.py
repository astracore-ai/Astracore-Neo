#!/usr/bin/env python3
"""run_tile_test.py <Mn> -- run one M-test of sim/run_tiles.py in isolation (each fits a shell time limit,
and they can run in parallel): python3 sim/run_tile_test.py M4"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
name = sys.argv[1]
src = open(os.path.join(HERE, "run_tiles.py")).read()
a = src.index('    # ---------------- M1:')
markers = sorted([(src.index(f'    # ---------------- {m}:'), m) for m in ("M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "M11", "M12", "M13")])
d = src.index('    print(f"RESULT:')
start = src.index(f'    # ---------------- {name}:')
following = [p for p, m in markers if p > start]
end = min(following) if following else d
prelude = "    src = 3\n    sys.path.insert(0, os.path.join(HERE, '..', 'compiler'))\n    from neo_backend import compile_layer\n"
src2 = src[:a] + prelude + src[start:end] + src[d:]
ns = {'__file__': os.path.join(HERE, "run_tiles.py"), '__name__': name}
exec(compile(src2, f'run_tiles_{name}', 'exec'), ns)
raise SystemExit(ns['main']())
