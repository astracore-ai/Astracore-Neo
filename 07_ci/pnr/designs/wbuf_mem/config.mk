# wbuf_mem -- the weight buffer: 12 SECDED lanes around the 512 x 936 pair leaf (the NCHK = 4 entry of drop 0.42), here
# fakeram7_512x936_g2_1w1r (121.4 x 166.6 um); 6.5 k cells -- drop 0.43, stream A3
export DESIGN_NAME = wbuf_mem
include $(dir $(DESIGN_CONFIG))../neo_common.mk

# the macro against the right edge with its pin edge inward (macro_place.tcl), and room around it: run 183's 55 % core
# (195 um square) left the 1,872 data pins 11 um from the die edge and global routing overflowed
export CORE_UTILIZATION = 45
export MACRO_PLACEMENT_TCL = $(dir $(DESIGN_CONFIG))macro_place.tcl
# drop 0.44 (A4): the read data's output budget in sdc_extra.sdc (0.1 of the period: the array's shadow register's mux).
# With it ORFS's Kepler equivalence check of CTS's repaired netlist (LEC_CHECK, the flow's own test of its resizer, not a
# figure of this job) stops in its liberty-to-truth-table step on the repaired decoders ("getTruthTable: uninitialized
# Table node", runs 226 and 232 -- with the resizer's pin swaps and gate cloning off as well). The check is skipped for
# this block; the synthesized netlist's equivalence to the RTL is the synth job's GLS, the repair's is OpenROAD's
# (ORFS: "if this fails, report an issue to OpenROAD")
export LEC_CHECK = 0
