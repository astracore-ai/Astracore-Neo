# AstraCore Neo -- MAC core v0.1 build / simulation targets (2026-10-04)
# make model      : feasibility model (Rev 1.3 claims vs arithmetic, v2.0 numbers)
# make vectors    : golden model + ABFT fault tests + hex vectors for the testbench
# make xrun       : Cadence Xcelium simulation of tb_neo_mac_core
# make iverilog   : Icarus Verilog simulation (open source)
# make verilator  : Verilator simulation (open source)
# make lint       : Verilator lint of the RTL only
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
# make backend    : compiler backend: static compilation of YOLOv8-m to tile programs on the 8x8 mesh
# make cdriver    : build the C host driver and check it emits the Python backend's programs word for word
# make mbist      : March C- MBIST on an ECC bank (standalone) -- the tile-level version is M11 in run_tiles.py
# make two_layer  : M10: two layers back to back on the mesh with INT8 handoff through the bank
# make xrun_tiles : Xcelium: M1 through the register bus on tile_mesh (vectors from sim/gen_tile_vectors.py)
# make perf2      : 64-core event model with mesh and bank contention (compiler/neo_perf_v2.py)
# make regress    : everything above that is self-checking (model, RTL v0.1/v0.2/core/NoC, compiler)
# make xrun_core  : Cadence Xcelium replay of the C1 convolution on neo_core (vectors_core from --dump)
VECDIR ?= vectors
ROWS   ?= 32
COLS   ?= 32
M      ?= 64
SEED   ?= 1

RTL := rtl/delay_line.sv rtl/mac_pe.sv rtl/skew_in.sv rtl/deskew_out.sv \
       rtl/systolic_array.sv rtl/abft_checker.sv rtl/neo_mac_core.sv \
       rtl/act_feeder.sv rtl/acc_bank.sv rtl/neo_mac_core_v02.sv \
       rtl/core_seq.sv rtl/ecc39.sv rtl/wbuf_mem.sv rtl/requant.sv rtl/neo_core.sv rtl/host_if.sv
NOC := rtl/noc_router.sv rtl/noc_mesh.sv
TILE := $(RTL) rtl/noc_router.sv rtl/sram_bank.sv rtl/mbist.sv rtl/bank_bist_wrap.sv rtl/crc16_word.sv rtl/tile_nic.sv rtl/tile_dma.sv rtl/esm.sv rtl/neo_tile.sv rtl/tile_mesh.sv
TBC := tb/tb_neo_core.sv
TB  := tb/tb_neo_mac_core.sv

.PHONY: all model vectors sim compile conv core noc requant ksplit tiles ecc mbist two_layer xrun_tiles perf2 perf3 backend cdriver regress iverilog xrun verilator lint clean vectors_core xrun_core verilator_core iverilog_core

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
	python3 sim/run_conv_v02.py

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
	python3 sim/run_conv_v02.py
	python3 sim/run_conv_core.py
	python3 sim/run_conv_core.py --late
	python3 sim/run_noc.py
	python3 sim/run_requant.py
	python3 sim/run_ksplit.py
	python3 sim/run_mbist.py
	$(MAKE) cdriver

iverilog: vectors
	mkdir -p build
	iverilog -g2012 -I $(VECDIR) -o build/sim.vvp $(RTL) $(TB)
	vvp build/sim.vvp +VECDIR=$(VECDIR)

xrun: vectors
	xrun -64bit -sv -timescale 1ns/1ps +incdir+$(VECDIR) -top tb_neo_mac_core -access +rwc \
	     $(RTL) $(TB) +VECDIR=$(VECDIR)

verilator: vectors vectors_core
	verilator --binary --timing -Wno-fatal -Wno-TIMESCALEMOD +incdir+$(VECDIR) --top-module tb_neo_mac_core \
	          $(RTL) $(TB) -o sim
	./obj_dir/sim +VECDIR=$(VECDIR)
	verilator --binary --timing -Wno-fatal -Wno-TIMESCALEMOD --Mdir obj_core +incdir+vectors_core --top-module tb_neo_core \
	          $(RTL) $(TBC) -o sim_core
	./obj_core/sim_core +VECDIR=vectors_core

LINTW := -Wall -Wno-DECLFILENAME -Wno-UNUSEDSIGNAL -Wno-UNUSEDPARAM -Wno-WIDTHEXPAND -Wno-UNSIGNED -Wno-TIMESCALEMOD
lint:
	verilator --lint-only $(LINTW) --top-module neo_core $(RTL)
	verilator --lint-only $(LINTW) --top-module noc_mesh $(NOC)
	verilator --lint-only $(LINTW) --top-module neo_tile_axil $(TILE) rtl/host_axil.sv rtl/neo_tile_axil.sv
	verilator --lint-only -Wall --top-module tile_mesh $(TILE)

clean:
	rm -rf build obj_dir xcelium.d xrun.history xrun.log *.vcd
