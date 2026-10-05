#!/bin/bash
# regress_full.sh -- the complete regression on the final RTL, every log into logs/ (drop 0.14).
# Runs sequentially (one CPU in the development sandbox); each test's log is logs/<name>.log and the
# summary line is appended to logs/regress_full.log. Launch detached: bash sim/launch_regress.sh
cd "$(dirname "$0")/.."
mkdir -p logs
run() {
  name=$1; shift
  echo "=== $name: $* ==="
  start=$(date +%s)
  "$@" > logs/$name.log 2>&1
  rc=$?
  end=$(date +%s)
  echo "$name: rc=$rc $(grep -E '^RESULT' logs/$name.log | tail -1) ($((end - start)) s)"
}
run model       python3 model/neo_feasibility.py
run sysref      python3 model/systolic_ref.py
run mac_core    python3 sim/run_mac_core.py --vectors vectors
# (the 8x16 configuration runs inside run_mac_core.py itself)
run conv_v02    python3 sim/run_conv_v02.py
run core_c1_c8  python3 sim/run_conv_core.py
run core_c9_c10 python3 sim/run_conv_core.py --late
run noc         python3 sim/run_noc.py
run requant     python3 sim/run_requant.py
run ksplit      python3 sim/run_ksplit.py
run ecc39       python3 sim/run_ecc39.py
run mbist       python3 sim/run_mbist.py
run compiler    python3 compiler/neo_compile.py --verify
run backend     python3 compiler/neo_backend.py
run cdriver     bash -c "mkdir -p build && gcc -std=c99 -Wall -Wextra -O2 -o build/test_backend sw/neo_host.c sw/test_backend.c && ./build/test_backend > build/c_backend.txt && python3 sim/check_c_backend.py"
for t in M3 M9 M11 M8 M1 M2 M4 M5 M6 M7; do run tiles_$t python3 sim/run_tile_test.py $t; done
run two_layer   python3 sim/run_two_layer.py
echo "=== DONE ==="
