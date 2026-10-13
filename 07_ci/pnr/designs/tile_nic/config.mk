# tile_nic -- the tile's network interface (about 105 k cells after drop 0.41) with its two memory leaves as FakeRAM2.0
# register files with an asynchronous read: the drain FIFO (512 x 1,065) and the partial-sum rows (64 x 1,024, 32
# write groups; pin-limited: 2,093 pins on one edge). The local router port, the core's drain and the bank's rows make
# several thousand I/O pins: 45 % utilization, pins one track apart
export DESIGN_NAME = tile_nic
include $(dir $(DESIGN_CONFIG))../neo_common.mk

export CORE_UTILIZATION = 45
export PLACE_PINS_ARGS = -min_distance 1 -min_distance_in_tracks
export SKIP_LAST_GASP = 1

# run 168: global routing ended with overflow (20, at the platform's 25 % layer adjustment: 18 GCells over by one or two
# tracks, all at the die's edges where the I/O pins crowd) and stopped the flow (GRT-0116); FastRoute's own hint
# (GRT-0704) is a lower adjustment, so more of each layer's tracks are offered. Run 174 at 0.10: no overflow (14.3 %
# usage), 0 DRC at detailed routing's 19th iteration
export ROUTING_LAYER_ADJUSTMENT = 0.10

# run 188 (drop 0.43, NCHK = 4): the macro placer put the partial-sum rows' macro at the left edge mirrored (MX), its
# 2,093-pin edge 11 um from the die boundary, and global routing overflowed on M6 (2,025 in all); macro_place.tcl stacks
# both macros against the left edge with their pin edges inward, as run 174's placement had them
export MACRO_PLACEMENT_TCL = $(dir $(DESIGN_CONFIG))macro_place.tcl
