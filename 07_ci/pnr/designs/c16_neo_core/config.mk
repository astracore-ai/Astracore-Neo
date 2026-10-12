# c16_neo_core -- the 16x8 core (ROADMAP's phase-7 first target): the datapath with the array, feeder and accumulator,
# the weight buffer, two sequencers, two requantizers; the activation, weight and accumulator arrays as three
# FakeRAM2.0 macros placed by ORFS's macro placer (rtl_macro_placer)
export DESIGN_NAME = c16_neo_core
include $(dir $(DESIGN_CONFIG))../neo_common.mk

# runtime: no last-gasp pass in the timing repairs of CTS and global route (the floorplan's repair skips it anyway)
export SKIP_LAST_GASP = 1

# the fallback if the macro placer fails: NEO_MACRO_PLACEMENT=1 on the make line (run.py --flow-args) places the macros
# on the core's left edge, mirrored so that FakeRAM's left-edge pins face the core (macro_place.tcl)
ifeq ($(NEO_MACRO_PLACEMENT),1)
export MACRO_PLACEMENT_TCL = $(dir $(DESIGN_CONFIG))macro_place.tcl
endif
