# bank_bist_wrap -- the tile's 2 MB bank with its MBIST and port mux: sram_bank (16 SECDED encoders and decoders around
# the 32,768 x 624 leaf), mbist, the wrapper's mux; 6 k cells. The leaf is made as 16 lane macros of 32,768 x 39, one per
# write group, each folded by FakeRAM2.0's column mux (fakeram.py lane_plan: 64 -> 512 rows of 2,496 bit cells,
# fakeram7_32768x39_cm64_1w1r, 323.6 x 166.6 um; the one-macro leaf is a 131:1 strip of 80.9 x 10,617.6 um). The block is
# 99.9 % macro: 60 % utilization and the lanes in a regular array (macro_place.tcl) -- drop 0.43, stream A3
export DESIGN_NAME = bank_bist_wrap
include $(dir $(DESIGN_CONFIG))../neo_common.mk

export CORE_UTILIZATION = 60
export MACRO_PLACEMENT_TCL = $(dir $(DESIGN_CONFIG))macro_place.tcl
export SKIP_LAST_GASP = 1
# run 188: global placement diverged (GPL-0307: the overflow low and rising, the wire length doubled) at the iteration
# where timing-driven placement inflated the 877 um2 of cells by 57 % -- 7 k cells spread over a 1.2 mm core of macros.
# Wire-length-driven placement (timing is repaired after placement, in CTS and global routing as before) with the
# step coefficient below ORFS's 1.05, as the error message advises
export GPL_TIMING_DRIVEN = 0
export GPL_ROUTABILITY_DRIVEN = 0
export MAX_PLACE_STEP_COEF = 1.03
