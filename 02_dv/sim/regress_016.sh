#!/bin/bash
# drop 0.16 regression: the suites touched by the clock-gated PE and the control-path changes
cd "$(dirname "$0")/.."
mkdir -p logs
run() { name=$1; shift; echo "=== $name ==="; s=$(date +%s); "$@" > logs/$name.log 2>&1; rc=$?; echo "$name: rc=$rc $(grep -E '^RESULT' logs/$name.log | tail -1) ($(( $(date +%s) - s )) s)"; }
run mac_core     python3 sim/run_mac_core.py --vectors vectors
run core_c1_c8   python3 sim/run_conv_core.py
run core_c9_c10  python3 sim/run_conv_core.py --late
run ksplit       python3 sim/run_ksplit.py
run tiles_M3     python3 sim/run_tile_test.py M3
run tiles_M9     python3 sim/run_tile_test.py M9
run tiles_M8     python3 sim/run_tile_test.py M8
run tiles_M1     python3 sim/run_tile_test.py M1
run two_layer    python3 sim/run_two_layer.py
run tiles_M2     python3 sim/run_tile_test.py M2
run tiles_M4     python3 sim/run_tile_test.py M4
run tiles_M6     python3 sim/run_tile_test.py M6
run tiles_M7     python3 sim/run_tile_test.py M7
run tiles_M5     python3 sim/run_tile_test.py M5
run tiles_M11    python3 sim/run_tile_test.py M11
echo "=== DONE ==="
