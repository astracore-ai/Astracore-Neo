# cocotb + Verilator (or Icarus) for the Neo regressions (drop 0.20). Requires: pip install cocotb numpy; verilator >= 5.0.
#   make -C dv/cocotb core      # test_neo_core.py: C1 convolution on neo_core, bit-exact vs the golden model
#   make -C dv/cocotb tile      # test_tile_mesh.py: M1-M13 through the register bus on tile_mesh (2x2, 16x8 cores)
#   make -C dv/cocotb tile8     # test_two_layer.py: M10, two-layer INT8 handoff on 8x8 cores (own build directory)
SIM ?= verilator
TOPLEVEL_LANG = verilog
RTL := $(abspath ../../rtl)
CORE_SRCS := $(addprefix $(RTL)/, delay_line.sv mac_pe.sv skew_in.sv deskew_out.sv systolic_array.sv abft_checker.sv neo_mac_core.sv \
  act_feeder.sv acc_bank.sv neo_mac_core_v02.sv core_seq.sv ecc39.sv wbuf_mem.sv requant.sv neo_core.sv)
TILE_SRCS := $(CORE_SRCS) $(addprefix $(RTL)/, noc_router.sv sram_bank.sv mbist.sv bank_bist_wrap.sv crc16_word.sv tile_nic.sv tile_dma.sv \
  prog_mem.sv host_if.sv esm.sv neo_tile.sv tile_mesh.sv)
export PYTHONPATH := $(abspath ../../compiler):$(abspath ../../sim):$(PYTHONPATH)
# the wrapper writes design state at the falling edge for fault injection, hence MULTIDRIVEN is expected
EXTRA_ARGS += --timing -Wno-fatal -Wno-WIDTH -Wno-UNOPTFLAT -Wno-TIMESCALEMOD -Wno-MULTIDRIVEN --public-flat-rw
# mesh geometry and fetch watchdog of the tile targets; the tests read the same values from the environment
NX ?= 2
NY ?= 2
ROWS ?= 16
COLS ?= 8
FETCH_TIMEOUT ?= 8192
BIST_WORDS ?= 16
export NEO_NX := $(NX)
export NEO_NY := $(NY)
export NEO_ROWS := $(ROWS)
export NEO_COLS := $(COLS)
export NEO_FETCH_TIMEOUT := $(FETCH_TIMEOUT)
# coverage: COVERAGE=1 adds Verilator line/toggle coverage (coverage.dat -> verilator_coverage --annotate),
# and the tests sample functional covergroups through cocotb-coverage (pip install cocotb-coverage)
ifeq ($(COVERAGE),1)
  EXTRA_ARGS += --coverage --coverage-line --coverage-toggle
  export NEO_FUNCOV=1
endif
ifeq ($(TARGET),tile)
  VERILOG_SOURCES = $(TILE_SRCS) $(abspath ../../tb/tile_mesh_cocotb.sv)
  TOPLEVEL = tile_mesh_cocotb
  MODULE ?= test_tile_mesh
  EXTRA_ARGS += -GNX=$(NX) -GNY=$(NY) -GROWS=$(ROWS) -GCOLS=$(COLS) -GACC_ROWS=64 -GABUF_DEPTH=256 -GWBUF_DEPTH=512 -GBANK_DEPTH=4096 \
    -GFETCH_TIMEOUT=$(FETCH_TIMEOUT) -GBIST_WORDS=$(BIST_WORDS)
else
  VERILOG_SOURCES = $(CORE_SRCS)
  TOPLEVEL = neo_core
  MODULE ?= test_neo_core
  EXTRA_ARGS += -GROWS=16 -GCOLS=8 -GACC_ROWS=64 -GABUF_DEPTH=256 -GWBUF_DEPTH=512
endif
include $(shell cocotb-config --makefiles)/Makefile.sim
core:
	$(MAKE) TARGET=core
tile:
	$(MAKE) TARGET=tile
tile8:
	$(MAKE) TARGET=tile ROWS=8 COLS=8 MODULE=test_two_layer SIM_BUILD=sim_build_tile8 COCOTB_RESULTS_FILE=results_tile8.xml
coverage:
	$(MAKE) TARGET=core COVERAGE=1
	$(MAKE) TARGET=tile COVERAGE=1
	verilator_coverage --annotate coverage_annotated sim_build/coverage.dat || true
	@echo "line/toggle coverage annotated in coverage_annotated/; functional coverage in funcov_*.yml"
clean::
	rm -rf sim_build_tile8 results_tile8.xml funcov_*.yml coverage_annotated
