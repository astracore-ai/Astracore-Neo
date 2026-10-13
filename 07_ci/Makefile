# AstraCore Neo -- MAC core v0.1 build / simulation targets (2026-10-04)
# make model      : feasibility model (Rev 1.3 claims vs arithmetic, v2.0 numbers)
# make vectors    : golden model + ABFT fault tests + hex vectors for the testbench
# make xrun       : Cadence Xcelium simulation of tb_neo_mac_core
# make iverilog   : Icarus Verilog simulation (open source)
# make verilator  : Verilator simulation (open source)
# make lint       : Verilator lint of the RTL: six -Wall passes, every warning fails (drop 0.35); the chip top's two at the silicon
#                   parameters (drop 0.44), one pre-existing link_pack.sv line waived there (tb/lint/silicon.vlt)
# make sim        : run the RTL under neosim (built-in subset simulator; no install needed)
# make compile    : compiler v1 lowering verification + YOLOv8-m performance report
# make conv       : whole convolutions on the v0.2 core RTL under neosim (feeder + accumulator + ABFT)
# make core       : neo_core C1-C8 (sequencer, faults, requant) -- run `python3 sim/run_conv_core.py --late` for C9-C10
# make noc        : 3x3 NoC mesh: random traffic, ordering, integrity, parity fault
# make requant    : requantization block vs fixed-point model and float
# make ksplit     : R8 K-split over three lockstepped neo_core instances (owner + 2 contributors)
# make tiles      : 2x2 neo_tile mesh, DMA programs: fetch/compute/writeback, K-split with RDY flow control, E2E CRC,
#                   2-region refill, bank ECC (~10 min; run M-tests individually if your shell has a time limit)
# make ecc        : (39,32) SECDED exhaustive single-flip and random double-flip test
# make perf2      : 64-core event model (add --ksplit --layout dist64 --link 128 for spec v2.1)
# make perf3      : v3 model: v2.1 fabric, K-split, dataflow across layers (spec v2.1 workload numbers)
# make backend_check: every YOLOv8-m group placed by the bank allocator, every program within the program memory (drop 0.33)
# make backend    : compiler backend: static compilation of YOLOv8-m to tile programs on the 8x8 mesh
# make cdriver    : build the C host driver and check it emits the Python backend's programs word for word
# make mbist      : March C- MBIST on an ECC bank (standalone) -- the tile-level version is M11 in run_tiles.py
# make nic        : the tile interface's bank-side paths at 16 words per cycle (writeback, serve, packer/unpacker; drop 0.29)
# make two_layer  : M10: two layers back to back on the mesh with INT8 handoff through the bank
# make xrun_tiles : Xcelium: M1 through the register bus on tile_mesh (vectors from sim/gen_tile_vectors.py)
# make verilator  : Verilator: the beat CRC's equivalence (make crc16, first), streaming core T1-T3, neo_core C1, tile_mesh M1 (SV testbench), then cocotb: M1-M13 and M10 at 16x8/8x8, M1-M13 and C1 on the 32x32 silicon configuration (drop 0.21)
# make crc16      : the matrix-form crc16_beat (drop 0.41): the RTL is gen_crc16_beat.py's output, lint-clean at BW 16 / 4 / 1, and equal to the
#                   serial chain it replaced (tb_crc16_beat.sv: 69,792 vectors, random and the exhaustive basis sweep) under Verilator and Icarus
# make cocotb_mesh8 : cocotb on Verilator: the mesh-level tests on the silicon mesh, 8x8 tiles of 32x32 cores with 2 MB banks (drop 0.24; its own CI job;
#                   hierarchical verilation since drop 0.35: the tile verilated once, 64 instances -- 0.76 GB instead of >16 GB)
# make cocotb_coverage : the coverage report (drop 0.26): 16x8 suite, M10 and C1 with Verilator line/toggle coverage + functional covergroups
# make cocotb_top : the chip top's suite (drop 0.44): neo_top on 2x2 tiles of 16x8 cores + 2 bridge nodes, T1-T5 with clk / aclk at 1.0 / 1.6 ns,
#                   then again with aclk == clk (the hdl-sim job's step after the tile suites)
# make perf2      : 64-core event model with mesh and bank contention (compiler/neo_perf_v2.py)
# make regress    : everything above that is self-checking (model, RTL v0.1/v0.2/core/NoC, compiler); hours under neosim
# make regress_ci : the subset that finishes in minutes (drop 0.24); CI's python-regress job runs this -- the whole-convolution
#                   neosim runs (conv_v02, conv_core, ksplit) exceeded the job's 90-minute limit on a runner and are covered by
#                   Verilator (C1 on neo_core at 16x8 and 32x32, the mesh suite); `make regress` stays for a workstation and the
#                   manual `tiles` job
# make xrun_core  : Cadence Xcelium replay of the C1 convolution on neo_core (vectors_core from --dump)
# make synth      : the open-PDK synthesis job (drop 0.40): Yosys (slang front end) + OpenSTA on ASAP7 (SS, 500 ps) -- synth/synth.py
#                   unit by unit up to the silicon tile, synth/report.py into ci_logs/synth/; tools from synth/tools.sh (CI's synth job);
#                   drop 0.42: the period sweep and its verdict (SYNTH_SWEEP), SYNTH_PDK=nangate45 the second library, make
#                   synth_compare the two side by side
# make formal     : the formal job (drop 0.41): SymbiYosys proofs of the protection logic, every formal/*.sby task (ecc39, crc16_beat
#                   against the frozen serial reference, the link packer/unpacker round trip, the router, the lockstep pairs, the
#                   requant table parity, the ABFT checker) -- formal/run.py, formal_out/formal_report.md and ci_logs/formal/ (CI's formal job)
VECDIR ?= vectors
ROWS   ?= 32
COLS   ?= 32
M      ?= 64
SEED   ?= 1

RTL := rtl/delay_line.sv rtl/mac_pe.sv rtl/skew_in.sv rtl/deskew_out.sv \
       rtl/systolic_array.sv rtl/abft_checker.sv rtl/neo_mem.sv rtl/neo_mac_core.sv \
       rtl/act_feeder.sv rtl/acc_bank.sv rtl/neo_mac_core_v02.sv \
       rtl/core_seq.sv rtl/ecc39.sv rtl/wbuf_mem.sv rtl/requant.sv rtl/neo_core.sv rtl/host_if.sv
NOC := rtl/noc_router.sv rtl/noc_mesh.sv
TILE := $(RTL) rtl/noc_router.sv rtl/sram_bank.sv rtl/mbist.sv rtl/bank_bist_wrap.sv rtl/crc16_word.sv rtl/crc16_beat.sv rtl/tile_nic.sv rtl/tile_dma.sv rtl/prog_mem.sv rtl/esm.sv rtl/link_pack.sv rtl/neo_tile.sv rtl/tile_mesh.sv
# the chip top (drop 0.44): the bridges, the control plane, the crossings
TOPR := rtl/cdc_bits.sv rtl/cdc_fifo.sv rtl/cdc_sync.sv rtl/noc_axi_bridge.sv rtl/top_ctrl.sv rtl/mesh_ctrl.sv rtl/neo_top.sv
TBC := tb/tb_neo_core.sv
TB  := tb/tb_neo_mac_core.sv

.PHONY: all model vectors sim compile conv core noc requant ksplit tiles ecc mbist nic two_layer backend_check xrun_tiles perf2 perf3 backend cdriver regress regress_ci iverilog xrun verilator lint clean vectors_core xrun_core verilator_core iverilog_core cocotb_tiles cocotb_mesh8 cocotb_mesh8_build cocotb_mesh8_run cocotb_mesh8s_build cocotb_mesh8s_run cocotb_coverage synth crc16 cocotb_top

all: regress model

model:
	python3 model/neo_feasibility.py

vectors:
	python3 model/systolic_ref.py --rows $(ROWS) --cols $(COLS) --m $(M) --seed $(SEED) --out $(VECDIR)

sim: vectors
	python3 sim/run_mac_core.py --vectors $(VECDIR)

compile:
	python3 compiler/neo_compile.py --verify
	python3 compiler/neo_compile.py --report --v02

conv:
	python3 sim/run_conv_v02.py --skip32

core:
	python3 sim/run_conv_core.py
	python3 sim/run_conv_core.py --late

noc:
	python3 sim/run_noc.py

requant:
	python3 sim/run_requant.py

ksplit:
	python3 sim/run_ksplit.py

tiles:
	python3 sim/run_tiles.py

ecc:
	python3 sim/run_ecc39.py

mbist:
	python3 sim/run_mbist.py

nic:
	python3 sim/run_nic.py

two_layer:
	python3 sim/run_two_layer.py

xrun_tiles:
	python3 sim/gen_tile_vectors.py
	xrun -sv -64bit -timescale 1ns/1ps $(TILE) tb/tb_tile_mesh.sv -top tb_tile_mesh -access +r

cdriver:
	mkdir -p build
	gcc -std=c99 -Wall -Wextra -O2 -o build/test_backend sw/neo_host.c sw/test_backend.c
	./build/test_backend > build/c_backend.txt
	python3 sim/check_c_backend.py

backend:
	python3 compiler/neo_backend.py
# every group of YOLOv8-m at 640x640 placed by the bank allocator and every program within the program memory (drop 0.33)
backend_check:
	python3 compiler/neo_backend.py --check

perf3:
	python3 compiler/neo_perf_v3.py --ksplit --no-affinity
	python3 compiler/neo_perf_v3.py --h 640 --w 640 --ksplit --no-affinity

perf2:
	python3 compiler/neo_perf_v2.py --h 736 --w 1280
	python3 compiler/neo_perf_v2.py --h 736 --w 1280 --ksplit --layout dist64 --banks 64 --link 128

vectors_core:
	python3 sim/run_conv_core.py --dump vectors_core

xrun_core: vectors_core
	xrun -64bit -sv -timescale 1ns/1ps +incdir+vectors_core -top tb_neo_core -access +rwc \
	     $(RTL) $(TBC) +VECDIR=vectors_core

verilator_core: vectors_core
	verilator --binary --timing -Wno-fatal +incdir+vectors_core --top-module tb_neo_core $(RTL) $(TBC) -o sim_core
	./obj_dir/sim_core +VECDIR=vectors_core

iverilog_core: vectors_core
	mkdir -p build
	iverilog -g2012 -I vectors_core -o build/sim_core.vvp $(RTL) $(TBC)
	vvp build/sim_core.vvp +VECDIR=vectors_core

regress: vectors
	python3 model/systolic_ref.py --rows 8 --cols 16 --m 20 --seed 7 --fault-row 2 --fault-col 9 --out build/v816
	python3 sim/run_mac_core.py --vectors build/v816
	python3 sim/run_mac_core.py --vectors $(VECDIR)
	python3 compiler/neo_compile.py --verify
	python3 compiler/neo_backend.py --check
	python3 sim/run_conv_v02.py --skip32
	python3 sim/run_conv_core.py
	python3 sim/run_conv_core.py --late
	python3 sim/run_noc.py
	python3 sim/run_requant.py
	python3 sim/run_ksplit.py
	python3 sim/run_mbist.py
	python3 sim/run_nic.py
	$(MAKE) cdriver

regress_ci: vectors
	python3 model/systolic_ref.py --rows 8 --cols 16 --m 20 --seed 7 --fault-row 2 --fault-col 9 --out build/v816
	python3 sim/run_mac_core.py --vectors build/v816
	python3 sim/run_mac_core.py --vectors $(VECDIR)
	python3 compiler/neo_compile.py --verify
	python3 compiler/neo_backend.py --check
	python3 sim/run_noc.py
	python3 sim/run_requant.py
	python3 sim/run_mbist.py
	python3 sim/run_nic.py
	$(MAKE) cdriver

iverilog: vectors
	mkdir -p build
	iverilog -g2012 -I $(VECDIR) -o build/sim.vvp $(RTL) $(TB)
	vvp build/sim.vvp +VECDIR=$(VECDIR)

xrun: vectors
	xrun -64bit -sv -timescale 1ns/1ps +incdir+$(VECDIR) -top tb_neo_mac_core -access +rwc \
	     $(RTL) $(TB) +VECDIR=$(VECDIR)

verilator: vectors vectors_core
	$(MAKE) crc16
	verilator --binary --timing -Wno-fatal -Wno-TIMESCALEMOD +incdir+$(VECDIR) --top-module tb_neo_mac_core \
	          $(RTL) $(TB) -o sim
	./obj_dir/sim +VECDIR=$(VECDIR)
	verilator --binary --timing -Wno-fatal -Wno-TIMESCALEMOD --Mdir obj_core +incdir+vectors_core --top-module tb_neo_core \
	          $(RTL) $(TBC) -o sim_core
	./obj_core/sim_core +VECDIR=vectors_core
	python3 sim/gen_tile_vectors.py
	verilator --binary --timing -Wno-fatal -Wno-TIMESCALEMOD -Wno-WIDTHTRUNC --Mdir obj_tiles --top-module tb_tile_mesh \
	          $(TILE) tb/tb_tile_mesh.sv -o sim_tiles
	./obj_tiles/sim_tiles
	$(MAKE) cocotb_tiles

# the beat CRC in matrix form (drop 0.41): rtl/crc16_beat.sv must be what sim/gen_crc16_beat.py generates (the script checks its
# tables against the serial algorithm first), lint-clean (-Wall) at the three widths the testbench instantiates, and equal to
# crc16_beat_serial -- the chain of crc16_word stages it replaced -- in tb_crc16_beat.sv: 60,000 random vectors and, for every n,
# every unit vector of {crc_in, words} (both modules are linear for a fixed n, so the basis sweep is exhaustive) at BW = 16, 4
# and 1, under Verilator and Icarus (CI's Icarus 12 runs it: continuous assignments only on the new side). The target fails
# unless both simulators print ALL PASS; `make verilator` runs it first.
CRC16_SRC := rtl/crc16_word.sv rtl/crc16_beat.sv tb/crc16_beat_serial.sv tb/tb_crc16_beat.sv
crc16:
	python3 sim/gen_crc16_beat.py --check
	for bw in 16 4 1; do verilator --lint-only -Wall -GBW=$$bw --top-module crc16_beat rtl/crc16_beat.sv || exit 1; done
	mkdir -p build
	verilator --binary --timing -Wall -Wno-TIMESCALEMOD --Mdir obj_crc16 --top-module tb_crc16_beat $(CRC16_SRC) -o sim_crc16
	./obj_crc16/sim_crc16 | tee build/crc16_verilator.txt
	grep -q "^RESULT: ALL PASS" build/crc16_verilator.txt
	iverilog -g2012 -o build/crc16.vvp $(CRC16_SRC)
	vvp build/crc16.vvp | tee build/crc16_iverilog.txt
	grep -q "^RESULT: ALL PASS" build/crc16_iverilog.txt

# cocotb on Verilator (drop 0.21): the mesh suite M1-M13 on 16x8 cores, M10 on the 8x8-core build, then the silicon
# configuration: M1-M13 on 32x32 cores with the silicon depths and 2 MB banks (tile32) and C1 on the 32x32 core
# (core32). Installs cocotb into the current Python if missing. Every target runs even if an earlier one fails
# (leading '-'); cocotb 1.x's make returns 0 even when tests fail, so check_results.py reads all the results files,
# prints one summary table and fails the target if any test failed or a build did not produce its results.
# build statistics (drop 0.34): every cocotb build runs under GNU time when it is installed (`apt-get install time`), which
# appends wall time, peak resident set (the largest process of the build: Verilator or one C++ compile) and exit status to
# STATS; the hdl-sim job publishes the file, and the hdl-mesh8 job's numbers (STATS=ci_logs/hdl-mesh8/build_stats.txt) size
# the 64-tile builds against the runner's 16 GB. Without GNU time the builds run as before.
TIME_BIN := $(shell command -v /usr/bin/time 2>/dev/null)
MESH8_BUILD_JOBS ?= 4
STATS ?= ci_logs/hdl-sim/build_stats.txt
timed = $(if $(TIME_BIN),mkdir -p $(dir $(STATS)) && $(TIME_BIN) -f "$(1): %e s wall; peak RSS %M KB; exit %x" -a -o $(STATS),)   # no commas: $(if) would split on them
cocotb_tiles:
	python3 -c "import cocotb" 2>/dev/null || pip install "cocotb>=1.9,<2" "cocotb-coverage<2"   # cocotb 2.x needs Verilator >= 5.036; Ubuntu ships 5.020
	$(MAKE) -C dv/cocotb clean >/dev/null 2>&1 || true
	-$(call timed,tile) $(MAKE) -j$(MESH8_BUILD_JOBS) -C dv/cocotb tile
	-$(call timed,tile8) $(MAKE) -j$(MESH8_BUILD_JOBS) -C dv/cocotb tile8
	-$(call timed,tile32) $(MAKE) -j$(MESH8_BUILD_JOBS) -C dv/cocotb tile32
	-$(call timed,core32) $(MAKE) -j$(MESH8_BUILD_JOBS) -C dv/cocotb core32
	-$(call timed,coverage) $(MAKE) -j$(MESH8_BUILD_JOBS) cocotb_coverage
	python3 dv/cocotb/check_results.py dv/cocotb/results.xml dv/cocotb/results_tile8.xml dv/cocotb/results_tile32.xml dv/cocotb/results_core32.xml

# the chip top (drop 0.44): neo_top's suite, one build run twice -- clk 1.0 ns / aclk 1.6 ns, then aclk == clk -- in its own build
# directory (no clean: the tile suites' results stay); check_results.py fails the target on any failed or missing test
cocotb_top:
	python3 -c "import cocotb" 2>/dev/null || pip install "cocotb>=1.9,<2" "cocotb-coverage<2"
	-$(call timed,top) $(MAKE) -j$(MESH8_BUILD_JOBS) -C dv/cocotb top
	-$(call timed,top_sync) $(MAKE) -C dv/cocotb top_sync
	python3 dv/cocotb/check_results.py dv/cocotb/results_top.xml dv/cocotb/results_top_sync.xml

# the coverage report (drop 0.26, item 5): the 16x8 suite, M10 and C1 rebuilt with Verilator line/toggle coverage and the
# functional covergroups on; the report and the raw data land in ci_logs/hdl-sim/, which the CI job publishes
cocotb_coverage:
	-$(MAKE) -C dv/cocotb coverage
	mkdir -p ci_logs/hdl-sim
	-cp dv/cocotb/coverage_report.md dv/cocotb/coverage_*.dat dv/cocotb/funcov_*.yml ci_logs/hdl-sim/ 2>/dev/null

# the 8x8 silicon mesh under cocotb (drop 0.24): a 64-tile Verilator build, run by the hdl-mesh8 job so the hdl-sim job stays short
cocotb_mesh8:
	python3 -c "import cocotb" 2>/dev/null || pip install "cocotb>=1.9,<2" "cocotb-coverage<2"
	$(MAKE) -C dv/cocotb clean >/dev/null 2>&1 || true
	-$(MAKE) -C dv/cocotb mesh8
	python3 dv/cocotb/check_results.py dv/cocotb/results_mesh8.xml
# the same in two halves for CI (drop 0.30): build the model, publish a heartbeat, then run on the built model (no clean)
# the C++ compile in parallel (drop 0.35): cocotb's own `make -f Vtop.mk` inherits -j from here through MAKEFLAGS, which
# --build-jobs alone never gave it (that only splits the generated C++ into more files); run 115's mesh8s compile ran one
# file at a time on the 4-core runner
cocotb_mesh8_build:
	python3 -c "import cocotb" 2>/dev/null || pip install "cocotb>=1.9,<2" "cocotb-coverage<2"
	$(MAKE) -C dv/cocotb clean >/dev/null 2>&1 || true
	$(call timed,mesh8 build) $(MAKE) -j$(MESH8_BUILD_JOBS) -C dv/cocotb mesh8_build
cocotb_mesh8_run:
	-$(call timed,mesh8 run) $(MAKE) -C dv/cocotb mesh8
	python3 dv/cocotb/check_results.py dv/cocotb/results_mesh8.xml
# the 8x8 mesh on 16x8 cores (drop 0.32; the hdl-mesh8 job's default since drop 0.34): MESH8_BUILD_JOBS and MESH8_CFLAGS
# pass through to both variants
cocotb_mesh8s_build:
	python3 -c "import cocotb" 2>/dev/null || pip install "cocotb>=1.9,<2" "cocotb-coverage<2"
	$(MAKE) -C dv/cocotb clean >/dev/null 2>&1 || true
	$(call timed,mesh8s build) $(MAKE) -j$(MESH8_BUILD_JOBS) -C dv/cocotb mesh8s_build
cocotb_mesh8s_run:
	-$(call timed,mesh8s run) $(MAKE) -C dv/cocotb mesh8s
	python3 dv/cocotb/check_results.py dv/cocotb/results_mesh8s.xml
# drop 0.35: every pass is -Wall with nothing waived and every warning fails the step (Verilator exits nonzero on any
# warning). The 102 warnings of run 111 (50 WIDTHEXPAND, 19 WIDTHTRUNC, 24 UNUSEDSIGNAL, 5 UNUSEDPARAM, 2 UNSIGNED,
# 2 DECLFILENAME) are cleared in the RTL: explicit widths on the beat-path arithmetic (tile_nic), the source index at its
# own width, coordinates as ports (the router's constant comparisons), unused-bit sinks named unused_* (Verilator's
# --unused-regexp default), and lint_off DECLFILENAME on the two files that hold two modules by design. LINTW stays for
# a local run that wants to waive something (make lint LINTW=-Wno-UNUSEDSIGNAL).
LINTW ?=
# drop 0.44, the chip top at the silicon parameters (8 x 8 tiles of 32 x 32 cores, 2 MB banks, WPF 32, a 512-bit AXI4 memory
# bridge per row, the host bridge in row 0) in two passes, because Verilator flattens every instance even with --lint-only
# and 64 such tiles need more than 16 GB (measured in the sandbox: 2 x 2 1.5 GB, 4 x 4 over 5.8 GB): (5) the tile alone at
# the chip top's tile parameters -- XW 4 (the bridge column at x = 8), NX 9 (its source index over 9 columns) -- and (6)
# neo_top over tb/lint/neo_tile.sv, a ports-only stub of the tile, so the bridges, the control plane, the bridge column's
# routers, the fences and the wiring are linted at XW 4 / YW 3. tb/lint/silicon.vlt waives the one finding of those passes,
# which is in link_pack.sv, not in the top: link_unpacker's `sh` upper half is unread at WPF 32 (see the file).
TOP_TILE_P := -GXW=4 -GYW=3 -GNX=9 -GNY=8 -GWPF=32 -GROWS=32 -GCOLS=32 -GACC_ROWS=512 -GABUF_DEPTH=2048 -GWBUF_DEPTH=1024 -GBANK_DEPTH=524288
lint:
	verilator --lint-only -Wall $(LINTW) --top-module neo_core $(RTL)
	verilator --lint-only -Wall $(LINTW) --top-module noc_mesh $(NOC)
	verilator --lint-only -Wall $(LINTW) --top-module neo_tile_axil $(TILE) rtl/host_axil.sv rtl/neo_tile_axil.sv
	verilator --lint-only -Wall $(LINTW) --top-module tile_mesh $(TILE)
	verilator --lint-only -Wall $(LINTW) --top-module neo_tile $(TOP_TILE_P) tb/lint/silicon.vlt $(filter-out rtl/tile_mesh.sv,$(TILE))
	verilator --lint-only -Wall $(LINTW) --top-module neo_top tb/lint/silicon.vlt $(filter-out rtl/neo_tile.sv rtl/tile_mesh.sv,$(TILE)) tb/lint/neo_tile.sv $(TOPR)
	@echo "lint: six -Wall passes (the chip top's two at the silicon parameters), clean; one link_pack.sv line waived in tb/lint/silicon.vlt"

# the synthesis job (drop 0.40): synth/synth.py synthesizes every unit of the tile (a module at its silicon parameters, its
# children black-boxed; the slang front end; the memory leaves priced by a macro model) with Yosys and runs OpenSTA on each
# unit with its subtree; it exits nonzero when a gate fails (a latch, a multiply-driven net, a hierarchy mismatch, a wrong
# PE count, hook logic left at DV_HOOKS = 0, a tool failure) -- the report is written either way. SYNTH_PDK asap7 |
# nangate45, SYNTH_CORNER SS | TT (asap7) / TT (nangate45), SYNTH_BLOCKS all | tile | core | core16 | names, SYNTH_JOBS
# parallel Yosys runs (2 on the 16 GB runner -- the two largest units peak near 5 GB each; 1 under 8 GB), SYNTH_PERIOD in ps
# (default the library's 500). Drop 0.42: SYNTH_SWEEP the period sweep after the timing runs (default: the library's list,
# 500..1500 ps; none to skip; a list in ps), SYNTH_OUT / SYNTH_LOGS the output and report directories (the CI job runs the
# second library into synth_out_nangate45 and ci_logs/synth/nangate45, SYNTH_REPORT_ARGS=--lite: no timing models in its
# logs), synth_compare the two libraries side by side (ci_logs/synth/synth_report_libraries.md). Tools: synth/tools.sh.
SYNTH_PDK ?= asap7
SYNTH_CORNER ?= SS
SYNTH_BLOCKS ?= all
SYNTH_JOBS ?= 2
SYNTH_PERIOD ?=
SYNTH_SWEEP ?= default
SYNTH_OUT ?= synth_out
SYNTH_LOGS ?= ci_logs/synth
SYNTH_REPORT_ARGS ?=
synth:
	python3 synth/synth.py --rtl rtl --out $(SYNTH_OUT) --pdk $(SYNTH_PDK) --corner $(SYNTH_CORNER) --blocks $(SYNTH_BLOCKS) --jobs $(SYNTH_JOBS) --sweep $(SYNTH_SWEEP) $(if $(SYNTH_PERIOD),--period $(SYNTH_PERIOD),); rc=$$?; \
	python3 synth/report.py $(SYNTH_OUT) --out $(SYNTH_LOGS) $(SYNTH_REPORT_ARGS); exit $$rc
.PHONY: synth_compare
synth_compare:
	python3 synth/report.py --compare synth_out synth_out_nangate45 --out ci_logs/synth

# the formal job (drop 0.41): formal/run.py runs every task of every formal/*.sby with SymbiYosys (sby -f -d formal_out/<task>),
# FORMAL_JOBS at a time, each under FORMAL_TIMEOUT seconds (the task's whole session -- sby and its solvers -- is killed at the
# limit: TIMEOUT), writes formal_out/formal_report.md (result, mode, depth, engine, reached induction length or bmc steps, time
# per task) and copies it, every task's sby log and every counterexample trace into ci_logs/formal/; it exits nonzero unless
# every task is PASS. A smtbmc prove task runs two solver processes: FORMAL_JOBS=1 on a 2-core machine. formal/run.py is
# 07_ci/formal/run.py, which the flat layout copies next to the harnesses (02_dv/formal); FORMAL_RUN points elsewhere (a
# worktree's 07_ci/formal/run.py). Tools: the OSS CAD Suite's sby, yosys with the slang plugin, yosys-smtbmc, yices, bitwuzla,
# yosys-abc (NEO_TOOLS_ONLY=oss bash synth/tools.sh).
FORMAL_JOBS ?= 2
FORMAL_TIMEOUT ?= 600
FORMAL_RUN ?= formal/run.py
.PHONY: formal
formal:
	python3 $(FORMAL_RUN) --dir formal --out formal_out --logs ci_logs/formal --jobs $(FORMAL_JOBS) --timeout $(FORMAL_TIMEOUT)

clean:
	rm -rf build obj_dir xcelium.d xrun.history xrun.log *.vcd synth_out synth_out_nangate45 formal_out
