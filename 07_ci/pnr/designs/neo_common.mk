# neo_common.mk -- what every block of the pnr job shares (drop 0.42, stream A2b); each designs/<block>/config.mk sets
# DESIGN_NAME (the synth.py unit) and includes this file. run.py passes NEO_PNR_IN, the directory prep.py and
# fakeram.py filled for the block, on the make command line.
export PLATFORM            = asap7

# synth.py's netlist of the unit's subtree and its block.sdc (prep.py): ORFS skips its own synthesis and copies the
# netlist to 1_2_yosys.v (scripts/synth_preamble.tcl), then links it flat (scripts/synth_odb.tcl)
export SYNTH_NETLIST_FILES = $(NEO_PNR_IN)/1_netlist.v
export SDC_FILE            = $(NEO_PNR_IN)/1_block.sdc

# the memory leaves as FakeRAM2.0 macros (fakeram.py); empty for a block without a leaf
export ADDITIONAL_LEFS     = $(sort $(wildcard $(NEO_PNR_IN)/macros/*.lef))
export ADDITIONAL_LIBS     = $(sort $(wildcard $(NEO_PNR_IN)/macros/*.lib))

# the synthesis flow's corner: ASAP7 SS, 0.63 V / 100 C (the platform's WC NLDM files are the ones synth.py times with;
# the platform's own default is BC)
export CORNER              = WC

# the floorplan from the utilization alone (no manual floorplan; macros by ORFS's macro placer): 55 % of the core,
# so that ASAP7's default placement density (PLACE_DENSITY 0.60, platforms/asap7/config.mk) stays above the uniform
# density global placement starts from once the tap cells, the port buffers and the floorplan's timing repair are in
export CORE_UTILIZATION    = 55
export CORE_ASPECT_RATIO   = 1

# the final report without PDNSim's IR-drop analysis of the whole grid (not a figure of this job)
export PWR_NETS_VOLTAGES   =
export GND_NETS_VOLTAGES   =
