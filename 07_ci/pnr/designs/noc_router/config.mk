# noc_router -- the mesh's 5-port router (1,077-bit flits, 2-entry input FIFOs, the one-hot arbiters of drop 0.41; about
# 110 k cells, no macro). Five ports of 1,077 bits each way are ~10.8 k I/O pins: at 55 % the die's edges would not hold
# them two tracks apart, so the die follows the pins -- 35 % utilization, pins one track apart (the tile's own floorplan,
# with the router's ports where the links leave the hard macro, is A3's)
export DESIGN_NAME = noc_router
include $(dir $(DESIGN_CONFIG))../neo_common.mk

export CORE_UTILIZATION = 35
export PLACE_PINS_ARGS = -min_distance 1 -min_distance_in_tracks
export SKIP_LAST_GASP = 1
