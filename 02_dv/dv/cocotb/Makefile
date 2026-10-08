# cocotb + Verilator (or Icarus) for the Neo regressions (drop 0.28). Requires: pip install cocotb numpy; verilator >= 5.0.
#   make -C dv/cocotb core      # test_neo_core.py: C1 convolution on neo_core (16x8), bit-exact vs the golden model
#   make -C dv/cocotb core32    # the same on the silicon core: 32x32, ACC_ROWS 512, ABUF 2048, WBUF 1024
#   make -C dv/cocotb tile      # test_tile_mesh.py: M1-M13 through the register bus on tile_mesh (2x2, 16x8 cores)
#   make -C dv/cocotb tile8     # test_two_layer.py: M10, two-layer INT8 handoff on 8x8 cores (own build directory)
#   make -C dv/cocotb tile32    # M1-M13 on the silicon core and depths: 2x2 mesh of 32x32 cores, 2 MB (512K-word) banks, 1024-bit links,
#                               # FETCH_TIMEOUT 65535 (two 4,095-word fetches queued at one bank exceed 8,192; the silicon value is 2^20-1)
#   make -C dv/cocotb coverage  # core, tile and tile8 again with Verilator line/toggle coverage and the functional covergroups
#                               # on -> coverage_report.md (drop 0.26)
#   make -C dv/cocotb mesh8     # the silicon mesh: 8x8 tiles of 32x32 cores with the silicon depths and 2 MB banks; the mesh-level
#                               # tests M1-M3, M5-M7, M9, M12-M14 (M4, M8, M11 are tile-local and proven on tile32); own build directory
SIM ?= verilator
TOPLEVEL_LANG = verilog
RTL := $(abspath ../../rtl)
CORE_SRCS := $(addprefix $(RTL)/, delay_line.sv mac_pe.sv skew_in.sv deskew_out.sv systolic_array.sv abft_checker.sv neo_mac_core.sv \
  act_feeder.sv acc_bank.sv neo_mac_core_v02.sv core_seq.sv ecc39.sv wbuf_mem.sv requant.sv neo_core.sv)
TILE_SRCS := $(CORE_SRCS) $(addprefix $(RTL)/, noc_router.sv sram_bank.sv mbist.sv bank_bist_wrap.sv crc16_word.sv crc16_beat.sv tile_nic.sv tile_dma.sv \
  prog_mem.sv host_if.sv esm.sv link_pack.sv neo_tile.sv tile_mesh.sv)
export PYTHONPATH := $(abspath ../../compiler):$(abspath ../../sim):$(PYTHONPATH)
# the wrapper writes design state at the falling edge for fault injection, hence MULTIDRIVEN is expected.
# --unroll-count 1024 (drop 0.27) did not get the 8x8 build past the NIC's reset loop over NX*NY sources (BLKLOOPINIT, run 68);
# drop 0.29 keeps each source's receive state in its own generate block instead, so no procedural loop scales with the mesh.
# The raised limit stays: it is harmless and covers the COLS-wide loops of the 32x32 builds with margin.
EXTRA_ARGS += --timing -Wno-fatal -Wno-WIDTH -Wno-UNOPTFLAT -Wno-TIMESCALEMOD -Wno-MULTIDRIVEN --public-flat-rw --unroll-count 1024
# BUILD_JOBS_ARG: set by a target that wants Verilator's C++ compile on several cores (the mesh8 build, drop 0.30)
BUILD_JOBS_ARG ?=
EXTRA_ARGS += $(BUILD_JOBS_ARG)
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
export NEO_NX := $(NX)
export NEO_NY := $(NY)
export NEO_ROWS := $(ROWS)
export NEO_COLS := $(COLS)
export NEO_FETCH_TIMEOUT := $(FETCH_TIMEOUT)
export NEO_WPF := $(WPF)
# coverage: COVERAGE=1 adds Verilator line/toggle coverage (coverage.dat -> verilator_coverage --annotate),
# and the tests sample functional covergroups through cocotb-coverage (pip install cocotb-coverage)
# VCOV=1: Verilator line/toggle coverage plus the functional covergroups (not COVERAGE, which cocotb reads as Python coverage)
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
    -GWBUF_DEPTH=$(WBUF_DEPTH) -GBANK_DEPTH=$(BANK_DEPTH) -GFETCH_TIMEOUT=$(FETCH_TIMEOUT) -GBIST_WORDS=$(BIST_WORDS) -GWPF=$(WPF)
else
  VERILOG_SOURCES = $(CORE_SRCS)
  TOPLEVEL = neo_core
  MODULE ?= test_neo_core
  EXTRA_ARGS += -GROWS=$(ROWS) -GCOLS=$(COLS) -GACC_ROWS=$(ACC_ROWS) -GABUF_DEPTH=$(ABUF_DEPTH) -GWBUF_DEPTH=$(WBUF_DEPTH)
endif
include $(shell cocotb-config --makefiles)/Makefile.sim
SILICON := ROWS=32 COLS=32 ACC_ROWS=512 ABUF_DEPTH=2048 WBUF_DEPTH=1024
LINKS := WPF=32                             # the 1024-bit links (drop 0.25): 32 words per flit on the tile32 and mesh8 builds
core:
	$(MAKE) TARGET=core SIM_BUILD=sim_build_core COCOTB_RESULTS_FILE=results_core.xml
core32:
	$(MAKE) TARGET=core $(SILICON) SIM_BUILD=sim_build_core32 COCOTB_RESULTS_FILE=results_core32.xml
tile:
	$(MAKE) TARGET=tile
tile8:
	$(MAKE) TARGET=tile ROWS=8 COLS=8 MODULE=test_two_layer SIM_BUILD=sim_build_tile8 COCOTB_RESULTS_FILE=results_tile8.xml
tile32:
	$(MAKE) TARGET=tile $(SILICON) $(LINKS) BANK_DEPTH=524288 FETCH_TIMEOUT=65535 SIM_BUILD=sim_build_tile32 COCOTB_RESULTS_FILE=results_tile32.xml
MESH8_TESTS := m1_fetch_compute_writeback,m2_ksplit_rdy,m3_end_to_end_crc,m5_bank_ecc_in_flight,m6_compiler_program,m7_mchunks_partial_fetch,m9_lost_flit_and_timeout,m12_control_path_protection,m13_partitions,m14_bank_contention
# --build-jobs 4: the 64-tile model's C++ is compiled on four cores (drop 0.30: run 85's mesh8 job had not reported after an
# hour, so the single-core compile of 64 tiles of 32x32 cores is the suspect; the hdl-sim job's 2x2 builds take ~50 min)
# MESH8_BUILD_JOBS / MESH8_CFLAGS (drop 0.32): the fallbacks for a build that kills the runner are a one-line change in the
# workflow -- `make cocotb_mesh8_build MESH8_BUILD_JOBS=2 MESH8_CFLAGS="-CFLAGS -O1"` -- and, below, a mesh8 variant on
# 16x8 cores with the silicon depths (mesh-level tests only; the tile32 suite proves the 32x32 core)
MESH8_BUILD_JOBS ?= 4
MESH8_CFLAGS ?=
MESH8_MAKE := $(MAKE) TARGET=tile $(SILICON) $(LINKS) BANK_DEPTH=524288 FETCH_TIMEOUT=65535 NX=8 NY=8 TESTCASE=$(MESH8_TESTS) SIM_BUILD=sim_build_mesh8 COCOTB_RESULTS_FILE=results_mesh8.xml BUILD_JOBS_ARG="--build-jobs $(MESH8_BUILD_JOBS) $(MESH8_CFLAGS)"
mesh8:
	$(MESH8_MAKE)
mesh8_build:                       # the model only (sim_build_mesh8/Vtop), so CI can publish a heartbeat between build and run
	$(MESH8_MAKE) sim_build_mesh8/Vtop
# the small-core 8x8 mesh: 64 tiles of 16x8 cores, silicon depths and links, the mesh tests (the model is ~16x smaller)
SMALL_SILICON := ROWS=16 COLS=8 ACC_ROWS=512 ABUF_DEPTH=2048 WBUF_DEPTH=1024
MESH8S_MAKE := $(MAKE) TARGET=tile $(SMALL_SILICON) $(LINKS) BANK_DEPTH=524288 FETCH_TIMEOUT=65535 NX=8 NY=8 TESTCASE=$(MESH8_TESTS) SIM_BUILD=sim_build_mesh8s COCOTB_RESULTS_FILE=results_mesh8s.xml BUILD_JOBS_ARG="--build-jobs $(MESH8_BUILD_JOBS) $(MESH8_CFLAGS)"
mesh8s:
	$(MESH8S_MAKE)
mesh8s_build:
	$(MESH8S_MAKE) sim_build_mesh8s/Vtop
# coverage builds (drop 0.26, item 5): the same tests with Verilator line/toggle coverage and the functional covergroups on,
# in their own build directories; each run's coverage.dat is renamed after it so the three merge into one report
define keep_cov
	@for f in coverage.dat $(1)/coverage.dat; do if [ -f $$f ]; then mv -f $$f $(2); fi; done
endef
core_cov:
	$(MAKE) TARGET=core VCOV=1 SIM_BUILD=sim_build_cov_core COCOTB_RESULTS_FILE=results_cov_core.xml
	$(call keep_cov,sim_build_cov_core,coverage_core.dat)
tile_cov:
	$(MAKE) TARGET=tile VCOV=1 SIM_BUILD=sim_build_cov_tile COCOTB_RESULTS_FILE=results_cov_tile.xml
	$(call keep_cov,sim_build_cov_tile,coverage_tile.dat)
tile8_cov:
	$(MAKE) TARGET=tile ROWS=8 COLS=8 MODULE=test_two_layer VCOV=1 SIM_BUILD=sim_build_cov_tile8 COCOTB_RESULTS_FILE=results_cov_tile8.xml
	$(call keep_cov,sim_build_cov_tile8,coverage_tile8.dat)
# tile32_cov (drop 0.32): the 32x32 silicon configuration at 32 words per flit, so the beat paths of the tile interface
# (two entries per cycle, 16-column PSUM beats, 16 + 16 + 1 drains, merged flits) are measured, not only the one-word
# builds; a subset of the suite keeps the coverage job within its time (the whole tile32 suite is in `tile32`)
TILE32_COV_TESTS ?= m1_fetch_compute_writeback,m2_ksplit_rdy,m3_end_to_end_crc,m7_mchunks_partial_fetch,m9_lost_flit_and_timeout,m13_partitions
tile32_cov:
	$(MAKE) TARGET=tile $(SILICON) $(LINKS) BANK_DEPTH=524288 FETCH_TIMEOUT=65535 TESTCASE=$(TILE32_COV_TESTS) VCOV=1 SIM_BUILD=sim_build_cov_tile32 COCOTB_RESULTS_FILE=results_cov_tile32.xml NEO_FUNCOV_FILE=funcov_tile_mesh32.yml
	$(call keep_cov,sim_build_cov_tile32,coverage_tile32.dat)
coverage: core_cov tile_cov tile8_cov tile32_cov
	python3 coverage_report.py --dat "coverage_*.dat" --funcov "funcov_*.yml" --out coverage_report.md
	-verilator_coverage --annotate coverage_annotated --annotate-min 1 coverage_*.dat > /dev/null 2>&1
	@echo "coverage_report.md written; annotated sources in coverage_annotated/"
clean::
	rm -rf sim_build_core sim_build_core32 sim_build_tile8 sim_build_tile32 sim_build_mesh8 sim_build_mesh8s sim_build_cov_* results_core.xml results_core32.xml results_tile8.xml results_tile32.xml results_mesh8.xml results_mesh8s.xml results_cov_*.xml funcov_*.yml coverage_*.dat coverage.dat coverage_report.md coverage_annotated
