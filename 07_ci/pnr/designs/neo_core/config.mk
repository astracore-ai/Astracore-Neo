# neo_core -- the 32 x 32 core at the silicon parameters (1,024 data PEs and 128 check PEs of NCHK = 4, the feeder, the
# accumulator, the weight buffer, two sequencers, two requantizers: 1.86 M cells and 208 k flops in synth.py's figures)
# with its three arrays as FakeRAM2.0 macros. Placed and globally routed only: `stages` names the flow's targets up to
# grt (5_1_grt) -- detailed routing of the 16 x 8 core's 0.28 M cells already peaked at 15.2 GB with swap behind it
# (A2b, run 168). The settings keep the stages within the job: the floorplan removes the synthesis buffers instead of
# repairing timing at zero wire load, global placement is wire-length driven (no timing- or routability-driven
# re-runs), CTS and global routing skip their timing repairs (drop 0.43, stream A3)
export DESIGN_NAME = neo_core
include $(dir $(DESIGN_CONFIG))../neo_common.mk

export SKIP_LAST_GASP = 1
# drop 0.44 (A4): the three arrays placed with their pin edges toward the core (macro_place.tcl; run 182's feeder faced
# the accumulator's back)
export MACRO_PLACEMENT_TCL = $(dir $(DESIGN_CONFIG))macro_place.tcl
export REMOVE_ABC_BUFFERS = 1
export GPL_TIMING_DRIVEN = 0
export GPL_ROUTABILITY_DRIVEN = 0
export SKIP_CTS_REPAIR_TIMING = 1
export SKIP_INCREMENTAL_REPAIR = 1
