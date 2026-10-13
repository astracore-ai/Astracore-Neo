#!/bin/bash
# activity.sh -- the switching activity of the power figures (drop 0.43, stream A3): two cocotb runs with Verilator's VCD
# tracing (02_dv/dv/cocotb/Makefile TRACE_VCD), the dump streamed through a named pipe into activity.py (it never lands on
# disk), one <block>.json.gz per routed block in pnr/activity/ (07_ci/pnr/activity/ in the repository), which power.py
# maps onto the routed blocks' flops in the pnr job.
#   core: the 16 x 8 core (NCHK = 4, the core suite's build) running C1 (a dense 3 x 3 convolution, three channel tiles)
#         and C5 (a 2:4 sparse convolution); the window is the core busy (go to done); the blocks: c16_neo_core and its
#         array, mac_pe (the mean over the 128 data PEs), and the silicon core and its units -- neo_core (the same
#         scope as c16_neo_core), requant, acc_bank, act_feeder, wbuf_mem -- whose wider buses and larger arrays take the
#         recorded bits by folded index (power.py)
#   tile: the 2 x 2 mesh of 16 x 8 cores at 32 words per link flit (the silicon links: the router's 1,056-bit data, the
#         merging packer) running M1 (fetch, compute, write back), M18 (five convolutions) and M19 (the INT8 drain);
#         the window is tile (0,0)'s DMA running a program; the blocks: tile (0,0)'s NIC, router, packer, unpacker,
#         bank with its MBIST, program memory, DMA engine, host interface and ESM
# Usage, from the flat layout with Verilator / cocotb on the path: bash pnr/activity.sh [core|tile|all] [out dir]
set -e
WHAT=${1:-all}
OUT=$(cd "$(dirname "$0")" && pwd)/activity
[ -n "$2" ] && OUT=$2
FLAT=$(pwd)
PY=$(cd "$(dirname "$0")" && pwd)/activity.py
mkdir -p "$OUT"
run() {        # run <name> <make args...> -- <activity.py args...>
  local name=$1; shift
  local margs=() aargs=()
  while [ "$1" != "--" ]; do margs+=("$1"); shift; done; shift
  aargs=("$@")
  local fifo
  fifo=$(mktemp -u /tmp/neo_vcd_${name}_XXXX)
  mkfifo "$fifo"
  echo "== $name: $(date -u)"
  python3 "$PY" --vcd "$fifo" --out "$OUT" "${aargs[@]}" > "$OUT/$name.log" 2>&1 &
  local apid=$!
  ( cd "$FLAT/dv/cocotb" && make -j1 "${margs[@]}" TRACE_VCD="$fifo" ) > "$OUT/${name}_sim.log" 2>&1 || {
    echo "!! $name: the simulation failed (see $OUT/${name}_sim.log)"; kill $apid 2>/dev/null; rm -f "$fifo"; return 1; }
  wait $apid || { echo "!! $name: activity.py failed (see $OUT/$name.log)"; rm -f "$fifo"; return 1; }
  rm -f "$fifo"
  grep -E "TESTS=|PASS|FAIL" "$OUT/${name}_sim.log" | tail -6 || true
  cat "$OUT/$name.log"
}
C=TOP.neo_core
if [ "$WHAT" = core ] || [ "$WHAT" = all ]; then
  run core TARGET=core NCHK=4 SIM_BUILD=sim_build_core_vcd COCOTB_RESULTS_FILE=results_core_vcd.xml \
      TESTCASE=c1_conv,c5_sparse_conv -- \
      --source "cocotb core (16x8, NCHK = 4): C1 dense conv + C5 2:4 sparse conv, Verilator 5.020" \
      --enable $C.busy --clock $C.clk --util $C.u_dp.u_core.valid_in \
      --block c16_neo_core=$C --block neo_core=$C --block c16_systolic_array=$C.u_dp.u_core.u_array \
      --block "mac_pe=$C.u_dp.u_core.u_array.g_row[*].g_col[*].g_data.u_pe" \
      --block requant=$C.u_rq --block acc_bank=$C.u_dp.u_acc --block act_feeder=$C.u_dp.u_feeder --block wbuf_mem=$C.u_wbuf
fi
T=TOP.tile_mesh_cocotb.u_mesh.g_y[0].g_x[0].u_t
if [ "$WHAT" = tile ] || [ "$WHAT" = all ]; then
  run tile TARGET=tile WPF=32 SIM_BUILD=sim_build_tile_vcd COCOTB_RESULTS_FILE=results_tile_vcd.xml \
      TESTCASE=m1_fetch_compute_writeback,m18_descriptor_sweep,m19_int8_drain -- \
      --source "cocotb tile (2x2 mesh of 16x8 cores, 32 words per link flit): M1 + M18 + M19, tile (0,0), Verilator 5.020" \
      --enable "$T.u_dma.prog_busy" --clock "$T.clk" --util "$T.u_core.u_dp.u_core.valid_in" \
      --block "tile_nic=$T.u_nic" --block "noc_router=$T.u_router" --block "link_packer=$T.u_pack" \
      --block "link_unpacker=$T.u_unpack" --block "bank_bist_wrap=$T.u_bank" --block "mbist=$T.u_bank.u_bist" \
      --block "prog_mem=$T.u_prog" --block "tile_dma=$T.u_dma" --block "host_if=$T.u_host" --block "esm=$T.u_esm"
fi
echo "== done $(date -u): $(ls "$OUT"/*.json.gz | wc -l) activity files in $OUT"
