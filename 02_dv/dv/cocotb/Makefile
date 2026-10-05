# cocotb + Verilator (or Icarus) for the Neo regressions. Requires: pip install cocotb numpy; verilator >= 5.0.
#   make -C dv/cocotb core      # test_neo_core.py: C1 convolution on neo_core, bit-exact vs the golden model
#   make -C dv/cocotb tile      # test_tile_mesh.py: M1 through the register bus on tile_mesh
SIM ?= verilator
TOPLEVEL_LANG = verilog
RTL := $(abspath ../../rtl)
CORE_SRCS := $(addprefix $(RTL)/, delay_line.sv mac_pe.sv skew_in.sv deskew_out.sv systolic_array.sv abft_checker.sv neo_mac_core.sv \
  act_feeder.sv acc_bank.sv neo_mac_core_v02.sv core_seq.sv ecc39.sv wbuf_mem.sv requant.sv neo_core.sv)
TILE_SRCS := $(CORE_SRCS) $(addprefix $(RTL)/, noc_router.sv sram_bank.sv mbist.sv bank_bist_wrap.sv crc16_word.sv tile_nic.sv tile_dma.sv \
  prog_mem.sv host_if.sv esm.sv neo_tile.sv tile_mesh.sv)
export PYTHONPATH := $(abspath ../../compiler):$(abspath ../../sim):$(PYTHONPATH)
EXTRA_ARGS += --timing -Wno-fatal -Wno-WIDTH -Wno-UNOPTFLAT -Wno-TIMESCALEMOD --public-flat-rw
# coverage: COVERAGE=1 adds Verilator line/toggle coverage (coverage.dat -> verilator_coverage --annotate),
# and the tests sample functional covergroups through cocotb-coverage (pip install cocotb-coverage)
ifeq ($(COVERAGE),1)
  EXTRA_ARGS += --coverage --coverage-line --coverage-toggle
  export NEO_FUNCOV=1
endif
ifeq ($(TARGET),tile)
  VERILOG_SOURCES = $(TILE_SRCS)
  TOPLEVEL = tile_mesh
  MODULE = test_tile_mesh
  EXTRA_ARGS += -GNX=2 -GNY=2 -GROWS=16 -GCOLS=8 -GACC_ROWS=64 -GABUF_DEPTH=256 -GWBUF_DEPTH=512 -GBANK_DEPTH=4096 -GFETCH_TIMEOUT=600 -GBIST_WORDS=16
else
  VERILOG_SOURCES = $(CORE_SRCS)
  TOPLEVEL = neo_core
  MODULE = test_neo_core
  EXTRA_ARGS += -GROWS=16 -GCOLS=8 -GACC_ROWS=64 -GABUF_DEPTH=256 -GWBUF_DEPTH=512
endif
include $(shell cocotb-config --makefiles)/Makefile.sim
core:
	$(MAKE) TARGET=core
tile:
	$(MAKE) TARGET=tile
coverage: 
	$(MAKE) TARGET=core COVERAGE=1
	$(MAKE) TARGET=tile COVERAGE=1
	verilator_coverage --annotate coverage_annotated sim_build/coverage.dat || true
	@echo "line/toggle coverage annotated in coverage_annotated/; functional coverage in funcov_*.yml"
