# cocotb + Verilator (or Icarus) for the Neo regressions (drop 0.28). Requires: pip install cocotb numpy; verilator >= 5.0.
#   make -C dv/cocotb core      # test_neo_core.py: C1 convolution on neo_core (16x8), bit-exact vs the golden model; C2 the core's fault hooks;
#                               # since drop 0.42 the 2:4 sparse-capable core (NCHK = 4) with C5, a sparse convolution
#   make -C dv/cocotb core32    # the same on the silicon core: 32x32, ACC_ROWS 512, ABUF 2048, WBUF 1024
#   make -C dv/cocotb tile_sp   # drop 0.42: M20 (a 2:4 sparse program through the compiler and the DMA) and M1 / M6 on the 2x2 mesh of
#                               # 16x8 NCHK = 4 cores
#   make -C dv/cocotb tile      # test_tile_mesh.py: M1-M13 and M15-M19 through the register bus on tile_mesh (2x2, 16x8 cores)
#   make -C dv/cocotb tile8     # test_two_layer.py: M10, two-layer INT8 handoff on 8x8 cores (own build directory)
#   make -C dv/cocotb tile32    # M1-M13 on the silicon core and depths: 2x2 mesh of 32x32 cores, 2 MB (512K-word) banks, 1024-bit links,
#                               # FETCH_TIMEOUT 65535 (two 4,095-word fetches queued at one bank exceed 8,192; the silicon value is 2^20-1)
#   make -C dv/cocotb coverage  # core, tile and tile8 again with Verilator line/toggle coverage and the functional covergroups
#                               # on -> coverage_report.md (drop 0.26)
#   make -C dv/cocotb mesh8     # the silicon mesh: 8x8 tiles of 32x32 cores with the silicon depths and 2 MB banks; the mesh-level
#                               # tests M1-M3, M5-M7, M9, M12-M14 (M4, M8, M11 are tile-local and proven on tile32); own build directory;
#                               # hierarchical verilation (drop 0.35): the tile once as a library, 64 instances -- 36 s / 0.76 GB
#   make -C dv/cocotb mesh8s    # the 8x8 mesh on 16x8 cores with the silicon depths and links, the same tests (drop 0.32; the CI
#                               # default of drop 0.34, when the 32x32 mesh did not verilate flat in 16 GB; 21 s / 0.44 GB hierarchically)
#   make -C dv/cocotb top       # test_neo_top.py: the chip top (drop 0.42) -- 2x2 tiles of 16x8 cores + 2 bridge nodes (row 0: memory
#                               # and host bridge, row 1: an unused position), the AXI4-Lite control plane, clk 1.0 ns and aclk 1.6 ns
#   make -C dv/cocotb top_sync  # the same build again with aclk == clk (both 1.0 ns, the same edges): the design does not depend on the ratio
#   make -C dv/cocotb faults_tile / faults_core   # drop 0.43: the fault campaign's drivers (faults/; run by faults/run_faults.py)
SIM ?= verilator
TOPLEVEL_LANG = verilog
RTL := $(abspath ../../rtl)
CORE_SRCS := $(addprefix $(RTL)/, neo_icg.sv delay_line.sv mac_pe.sv skew_in.sv deskew_out.sv systolic_array.sv abft_checker.sv neo_mem.sv neo_mac_core.sv \
  act_feeder.sv acc_bank.sv neo_mac_core_v02.sv core_seq.sv ecc39.sv wbuf_mem.sv requant.sv neo_core.sv)
TILE_SRCS := $(CORE_SRCS) $(addprefix $(RTL)/, tile_telem.sv neo_iso.sv noc_router.sv sram_bank.sv mbist.sv bank_bist_wrap.sv crc16_word.sv crc16_beat.sv tile_nic.sv tile_dma.sv \
  prog_mem.sv host_if.sv esm.sv link_pack.sv neo_tile.sv tile_mesh.sv)
TOP_SRCS := $(TILE_SRCS) $(addprefix $(RTL)/, cdc_bits.sv cdc_fifo.sv cdc_sync.sv noc_axi_bridge.sv top_ctrl.sv mesh_ctrl.sv neo_top.sv)
export PYTHONPATH := $(abspath ../../compiler):$(abspath ../../sim):$(PYTHONPATH)
# the wrapper writes design state at the falling edge for fault injection, hence MULTIDRIVEN is expected.
# --unroll-count 1024 (drop 0.27) did not get the 8x8 build past the NIC's reset loop over NX*NY sources (BLKLOOPINIT, run 68);
# drop 0.29 keeps each source's receive state in its own generate block instead, so no procedural loop scales with the mesh.
# The raised limit stays: it is harmless and covers the COLS-wide loops of the 32x32 builds with margin.
# --public-flat-rw is cocotb's own (Makefile.verilator adds `--vpi --public-flat-rw` to every build, for VPI access): the
# drop-0.34 A/B that built the 16x8 suite with and without it measured two identical builds (890,656 vs 890,648 KB), and the
# knob is gone (drop 0.35). The flag is not what the 64-tile build needs anyway: Verilator flattens every instance in
# V3Scope whatever the flags, so the way to a 64-tile model is HIER_ARGS below.
EXTRA_ARGS += --timing -Wno-fatal -Wno-WIDTH -Wno-UNOPTFLAT -Wno-TIMESCALEMOD -Wno-MULTIDRIVEN --unroll-count 1024
# BUILD_JOBS_ARG: set by a target that wants Verilator's C++ compile on several cores (the mesh8 build, drop 0.30)
BUILD_JOBS_ARG ?=
EXTRA_ARGS += $(BUILD_JOBS_ARG)
# HIER_ARGS (drop 0.35): --hierarchical verilates neo_tile (marked /*verilator hier_block*/) once as its own library and
# instantiates it NX*NY times; the flat build clones and flattens every tile (Verilator's V3Scope copies each block per
# instance), which is why 64 tiles did not fit 16 GB: measured, the 8x8 mesh of 32x32 cores verilates in 36 s / 0.76 GB
# hierarchically against >16 GB flat, the 16x8-core variant in 21 s / 0.44 GB against 14 GB. The mesh targets set it;
# the 2x2 builds stay flat (0.87 GB, and the flat model simulates faster). The tests see no difference: everything they
# touch goes through the wrapper's ports (the hooks are ports of the tile since drop 0.35, so no hierarchical reference
# crosses the block boundary)
HIER_ARGS ?=
EXTRA_ARGS += $(HIER_ARGS)
# VERILATOR_ROOT for the hierarchical builds (drop 0.37): every hier block is verilated by a relaunch of the `verilator`
# wrapper from the generated Vtop_hier.mk, with VERILATOR_ROOT in the environment (verilator_bin exports its compiled-in
# default), and the wrapper then takes verilator_bin from $VERILATOR_ROOT/bin. A packaged Verilator splits the install --
# Ubuntu's 5.020: /usr/bin/verilator_bin, include/ under /usr/share/verilator, the compiled-in root -- and the relaunch
# died with "exec: /usr/share/verilator/verilator_bin: not found" (run 126, the first hierarchical build in CI). vroot.sh
# prints a root that holds both (the environment's or the compiled-in one when complete, else a directory of symbolic
# links, sim_build_vroot); it is exported for the hierarchical builds only, a flat build never consults it.
ifneq ($(findstring --hierarchical,$(EXTRA_ARGS)),)
VROOT := $(shell sh $(abspath $(dir $(firstword $(MAKEFILE_LIST)))vroot.sh))
ifneq ($(VROOT),)
export VERILATOR_ROOT := $(VROOT)
endif
endif
# geometry, depths and fetch watchdog; the tests read the geometry from the environment
NX ?= 2
NY ?= 2
ROWS ?= 16
COLS ?= 8
ACC_ROWS ?= 64
ABUF_DEPTH ?= 256
WBUF_DEPTH ?= 512
BANK_DEPTH ?= 4096
FETCH_TIMEOUT ?= 8192
BIST_WORDS ?= 16
WPF ?= 1
# NCHK (drop 0.42): the cores' check columns -- 1 the dense-only core of the tile suites, 4 the 2:4 sparse-capable silicon
# core (the core / core32 builds and tile_sp, where C5 and M20 run sparse programs)
NCHK ?= 1
export NEO_NX := $(NX)
export NEO_NY := $(NY)
export NEO_ROWS := $(ROWS)
export NEO_COLS := $(COLS)
export NEO_FETCH_TIMEOUT := $(FETCH_TIMEOUT)
export NEO_WPF := $(WPF)
# the chip top's build (drop 0.42): bridges, their AXI width and host-read timeout, and the two clock periods the suite runs
AXI_DW ?= 512
N_MEM_BRIDGES ?= 1
N_HOST_BRIDGES ?= 1
HOST_TIMEOUT ?= $(FETCH_TIMEOUT)
CLK_PS ?= 1000
ACLK_PS ?= 1600
export NEO_BANK_DEPTH := $(BANK_DEPTH)
export NEO_AXI_DW := $(AXI_DW)
export NEO_N_MEM := $(N_MEM_BRIDGES)
export NEO_N_HOST := $(N_HOST_BRIDGES)
export NEO_HOST_TIMEOUT := $(HOST_TIMEOUT)
export NEO_CLK_PS := $(CLK_PS)
export NEO_ACLK_PS := $(ACLK_PS)
export NEO_NCHK := $(NCHK)
# coverage: COVERAGE=1 adds Verilator line/toggle coverage (coverage.dat -> verilator_coverage --annotate),
# and the tests sample functional covergroups through cocotb-coverage (pip install cocotb-coverage)
# VCOV=1: Verilator line/toggle coverage plus the functional covergroups (not COVERAGE, which cocotb reads as Python coverage)
# TRACE_VCD=<file> (drop 0.43, stream A3: the switching activity of the power figures, 07_ci/pnr/activity.sh): the model
# with Verilator's VCD tracing, every signal up to 4,096 bits wide (the default 256 would leave out the 288-bit and wider
# rows), and the run dumped into <file> -- a named pipe that 07_ci/pnr/activity.py reads as the simulation writes it.
# Give the build its own SIM_BUILD: the traced model is not the plain one
TRACE_VCD ?=
ifneq ($(TRACE_VCD),)
  EXTRA_ARGS += --trace --trace-max-width 4096
  SIM_ARGS += --trace --trace-file $(TRACE_VCD)
endif
ifeq ($(VCOV),1)
  EXTRA_ARGS += --coverage --coverage-line --coverage-toggle
  export NEO_FUNCOV=1
  # the functional-coverage export of a tile build goes to its own file (drop 0.33): tile_cov and tile32_cov both run
  # test_tile_mesh.py, and the second would otherwise overwrite the first's funcov_tile_mesh.yml
  NEO_FUNCOV_FILE ?=
  export NEO_FUNCOV_FILE
endif
ifeq ($(TARGET),tile)
  VERILOG_SOURCES = $(TILE_SRCS) $(abspath ../../tb/tile_mesh_cocotb.sv)
  TOPLEVEL = tile_mesh_cocotb
  MODULE ?= test_tile_mesh
  EXTRA_ARGS += -GNX=$(NX) -GNY=$(NY) -GROWS=$(ROWS) -GCOLS=$(COLS) -GACC_ROWS=$(ACC_ROWS) -GABUF_DEPTH=$(ABUF_DEPTH) \
    -GWBUF_DEPTH=$(WBUF_DEPTH) -GBANK_DEPTH=$(BANK_DEPTH) -GFETCH_TIMEOUT=$(FETCH_TIMEOUT) -GBIST_WORDS=$(BIST_WORDS) -GWPF=$(WPF) \
    -GNCHK=$(NCHK)
else ifeq ($(TARGET),top)
  # the chip top (drop 0.42): neo_top itself is the toplevel -- its AXI ports are packed vectors, the tiles' hooks are off
  # (DV_HOOKS = 0 inside neo_top: the suite reaches everything through the control plane and the bridges, as a host does)
  VERILOG_SOURCES = $(TOP_SRCS)
  TOPLEVEL = neo_top
  MODULE ?= test_neo_top
  EXTRA_ARGS += -GNX=$(NX) -GNY=$(NY) -GROWS=$(ROWS) -GCOLS=$(COLS) -GACC_ROWS=$(ACC_ROWS) -GABUF_DEPTH=$(ABUF_DEPTH) \
    -GWBUF_DEPTH=$(WBUF_DEPTH) -GBANK_DEPTH=$(BANK_DEPTH) -GFETCH_TIMEOUT=$(FETCH_TIMEOUT) -GBIST_WORDS=$(BIST_WORDS) -GWPF=$(WPF) \
    -GAXI_DW=$(AXI_DW) -GN_MEM_BRIDGES=$(N_MEM_BRIDGES) -GN_HOST_BRIDGES=$(N_HOST_BRIDGES) -GHOST_TIMEOUT=$(HOST_TIMEOUT) -GNCHK=$(NCHK)
else
  VERILOG_SOURCES = $(CORE_SRCS)
  TOPLEVEL = neo_core
  MODULE ?= test_neo_core
  # DV_HOOKS=1: the core's weight-buffer fault hook exists in the core-level build (drop 0.37; the tile builds set it in the wrapper)
  EXTRA_ARGS += -GROWS=$(ROWS) -GCOLS=$(COLS) -GACC_ROWS=$(ACC_ROWS) -GABUF_DEPTH=$(ABUF_DEPTH) -GWBUF_DEPTH=$(WBUF_DEPTH) -GDV_HOOKS=1 \
    -GNCHK=$(NCHK)
endif
include $(shell cocotb-config --makefiles)/Makefile.sim
SILICON := ROWS=32 COLS=32 ACC_ROWS=512 ABUF_DEPTH=2048 WBUF_DEPTH=1024
LINKS := WPF=32                             # the 1024-bit links (drop 0.25): 32 words per flit on the tile32 and mesh8 builds
# the core builds are the 2:4 sparse-capable core (NCHK = 4, drop 0.42): C1-C4 dense as before (the same results and end
# times: the dense entry is the NCHK = 1 entry with zeros above, the core's latency is unchanged), C5 a sparse convolution
core:
	$(MAKE) TARGET=core NCHK=4 SIM_BUILD=sim_build_core COCOTB_RESULTS_FILE=results_core.xml
core32:
	$(MAKE) TARGET=core $(SILICON) NCHK=4 SIM_BUILD=sim_build_core32 COCOTB_RESULTS_FILE=results_core32.xml
tile:
	$(MAKE) TARGET=tile
# tile_sp (drop 0.42): the 2x2 mesh of 16x8 sparse-capable cores (NCHK = 4) -- M20, a 2:4 sparse program compiled by
# neo_backend and run through the DMA with a K-split, and M1 / M6 dense on the same build (the weight entries of the
# NCHK = 4 format: five words at 16x8 instead of three, so the fetches and the end times are longer by design)
TILE_SP_TESTS ?= m20_sparse_program,m1_fetch_compute_writeback,m6_compiler_program
tile_sp:
	$(MAKE) TARGET=tile NCHK=4 TESTCASE=$(TILE_SP_TESTS) SIM_BUILD=sim_build_tile_sp COCOTB_RESULTS_FILE=results_tile_sp.xml
tile8:
	$(MAKE) TARGET=tile ROWS=8 COLS=8 MODULE=test_two_layer SIM_BUILD=sim_build_tile8 COCOTB_RESULTS_FILE=results_tile8.xml
tile32:
	$(MAKE) TARGET=tile $(SILICON) $(LINKS) BANK_DEPTH=524288 FETCH_TIMEOUT=65535 SIM_BUILD=sim_build_tile32 COCOTB_RESULTS_FILE=results_tile32.xml
# the chip top (drop 0.42): one build, run twice -- unrelated clocks (clk 1.0 ns, aclk 1.6 ns), then aclk == clk. The 1024-bit
# links (WPF 32) on 16x8 cores, as mesh8s: the bridges see the silicon's beats (up to 16 words, merged into 32-word flits on
# the link), not one-word flits
top:
	$(MAKE) TARGET=top $(LINKS) SIM_BUILD=sim_build_top COCOTB_RESULTS_FILE=results_top.xml
top_sync:
	$(MAKE) TARGET=top $(LINKS) SIM_BUILD=sim_build_top COCOTB_RESULTS_FILE=results_top_sync.xml ACLK_PS=$(CLK_PS)
MESH8_TESTS := m1_fetch_compute_writeback,m2_ksplit_rdy,m3_end_to_end_crc,m5_bank_ecc_in_flight,m6_compiler_program,m7_mchunks_partial_fetch,m9_lost_flit_and_timeout,m12_control_path_protection,m13_partitions,m14_bank_contention,m16_link_parity
# --build-jobs: the model's C++ compiled on several cores (drop 0.30). MESH8_BUILD_JOBS / MESH8_CFLAGS (drop 0.32) pass through
# from the workflow. Both mesh variants verilate hierarchically (drop 0.35, HIER_ARGS): the flat 64-tile model of 32x32 cores
# never verilated in the runner's 16 GB (runs 74-111 died in the verilation, exit 143), the 16x8-core variant reached
# 14.0 GB and 791 MB of C++ (run 115); hierarchically the two take 0.76 GB / 105 MB and 0.44 GB / 56 MB.
MESH8_BUILD_JOBS ?= 4
MESH8_CFLAGS ?=
MESH8_HIER ?= --hierarchical       # MESH8_HIER= builds a mesh variant flat (the drop-0.34 probe of where the flat 32x32 model dies)
MESH8_MAKE := $(MAKE) TARGET=tile $(SILICON) $(LINKS) BANK_DEPTH=524288 FETCH_TIMEOUT=65535 NX=8 NY=8 TESTCASE=$(MESH8_TESTS) SIM_BUILD=sim_build_mesh8 COCOTB_RESULTS_FILE=results_mesh8.xml BUILD_JOBS_ARG="--build-jobs $(MESH8_BUILD_JOBS) $(MESH8_CFLAGS)" HIER_ARGS="$(MESH8_HIER)"
# the '+' marks the recipe as a recursive make, so a parent's -j (the jobserver) reaches the C++ compile (drop 0.35): make
# does not see $(MAKE) inside the := variable and would run the sub-make with -j1
mesh8:
	+$(MESH8_MAKE)
mesh8_build:                       # the model only (sim_build_mesh8/Vtop), so CI can publish a heartbeat between build and run
	+$(MESH8_MAKE) sim_build_mesh8/Vtop
# the small-core 8x8 mesh: 64 tiles of 16x8 cores, silicon depths and links, the mesh tests (the model is ~16x smaller)
SMALL_SILICON := ROWS=16 COLS=8 ACC_ROWS=512 ABUF_DEPTH=2048 WBUF_DEPTH=1024
MESH8S_MAKE := $(MAKE) TARGET=tile $(SMALL_SILICON) $(LINKS) BANK_DEPTH=524288 FETCH_TIMEOUT=65535 NX=8 NY=8 TESTCASE=$(MESH8_TESTS) SIM_BUILD=sim_build_mesh8s COCOTB_RESULTS_FILE=results_mesh8s.xml BUILD_JOBS_ARG="--build-jobs $(MESH8_BUILD_JOBS) $(MESH8_CFLAGS)" HIER_ARGS="$(MESH8_HIER)"
mesh8s:
	+$(MESH8S_MAKE)
mesh8s_build:
	+$(MESH8S_MAKE) sim_build_mesh8s/Vtop
# the fault campaign (drop 0.43, stream D2): faults/campaign_tile.py drives the tile build (M1 on the 2x2 mesh of 16x8 tiles),
# faults/campaign_core.py the core build (C1 on neo_core 16x8, NCHK = 4), each in a build directory of its own; both inject
# through the VPI that cocotb's --public-flat-rw opens. faults/run_faults.py runs the fault-free run once (the build), then
# several runs of the same model at once, each with its own fault list and results file (FAULTS_WORKER), FAULTS_LIST /
# FAULTS_CSV / FAULTS_BUDGET_S in the environment. `make faults` (07_ci/Makefile) is the job's entry.
FAULTS_WORKER ?=
faults_tile:
	$(MAKE) TARGET=tile MODULE=faults.campaign_tile SIM_BUILD=sim_build_faults_tile COCOTB_RESULTS_FILE=results_faults_tile$(FAULTS_WORKER).xml
faults_core:
	$(MAKE) TARGET=core NCHK=4 MODULE=faults.campaign_core SIM_BUILD=sim_build_faults_core COCOTB_RESULTS_FILE=results_faults_core$(FAULTS_WORKER).xml
# coverage builds (drop 0.26, item 5): the same tests with Verilator line/toggle coverage and the functional covergroups on,
# in their own build directories; each run's coverage.dat is renamed after it so the three merge into one report
define keep_cov
	@for f in coverage.dat $(1)/coverage.dat; do if [ -f $$f ]; then mv -f $$f $(2); fi; done
endef
core_cov:
	$(MAKE) TARGET=core NCHK=4 VCOV=1 SIM_BUILD=sim_build_cov_core COCOTB_RESULTS_FILE=results_cov_core.xml
	$(call keep_cov,sim_build_cov_core,coverage_core.dat)
tile_cov:
	$(MAKE) TARGET=tile VCOV=1 SIM_BUILD=sim_build_cov_tile COCOTB_RESULTS_FILE=results_cov_tile.xml
	$(call keep_cov,sim_build_cov_tile,coverage_tile.dat)
tile8_cov:
	$(MAKE) TARGET=tile ROWS=8 COLS=8 MODULE=test_two_layer VCOV=1 SIM_BUILD=sim_build_cov_tile8 COCOTB_RESULTS_FILE=results_cov_tile8.xml
	$(call keep_cov,sim_build_cov_tile8,coverage_tile8.dat)
# tile32_cov (drop 0.32): the 32x32 silicon configuration at 32 words per flit, so the beat paths of the tile interface
# (two entries per cycle, 16-column PSUM beats, 16 + 16 + 1 drains, merged flits) are measured, not only the one-word
# builds; a subset of the suite keeps the coverage job within its time (the whole tile32 suite is in `tile32`). Drop 0.36
# adds M4 (WAIT_FREE, DMA op 6, two regions) and M19 (the INT8 drain: an 8-word row as one beat) to the subset.
TILE32_COV_TESTS ?= m1_fetch_compute_writeback,m2_ksplit_rdy,m3_end_to_end_crc,m4_region_refill,m7_mchunks_partial_fetch,m9_lost_flit_and_timeout,m13_partitions,m19_int8_drain
tile32_cov:
	$(MAKE) TARGET=tile $(SILICON) $(LINKS) BANK_DEPTH=524288 FETCH_TIMEOUT=65535 TESTCASE=$(TILE32_COV_TESTS) VCOV=1 SIM_BUILD=sim_build_cov_tile32 COCOTB_RESULTS_FILE=results_cov_tile32.xml NEO_FUNCOV_FILE=funcov_tile_mesh32.yml
	$(call keep_cov,sim_build_cov_tile32,coverage_tile32.dat)
coverage: core_cov tile_cov tile8_cov tile32_cov
	python3 coverage_report.py --dat "coverage_*.dat" --funcov "funcov_*.yml" --out coverage_report.md
	-verilator_coverage --annotate coverage_annotated --annotate-min 1 coverage_*.dat > /dev/null 2>&1
	@echo "coverage_report.md written; annotated sources in coverage_annotated/"
clean::
	rm -rf sim_build_faults_tile sim_build_faults_core results_faults_*.xml
	rm -rf sim_build_core sim_build_core32 sim_build_tile8 sim_build_tile32 sim_build_tile_sp sim_build_mesh8 sim_build_mesh8s sim_build_vroot sim_build_cov_* results_core.xml results_core32.xml results_tile8.xml results_tile32.xml results_tile_sp.xml results_mesh8.xml results_mesh8s.xml results_cov_*.xml funcov_*.yml coverage_*.dat coverage.dat coverage_report.md coverage_annotated sim_build_top results_top.xml results_top_sync.xml
