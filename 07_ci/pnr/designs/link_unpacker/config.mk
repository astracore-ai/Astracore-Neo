# link_unpacker -- 32-word link flits into 16-word beats (2.9 k cells, 336 um2, no macro) with 1,081 inputs and 567
# outputs: the pins set the die, not the cells. At 35 % the core is ~31 um on a side; 1,648 pins one track apart on its
# four edges need ~80 um of the ~124 um (drop 0.43, stream A3)
export DESIGN_NAME = link_unpacker
include $(dir $(DESIGN_CONFIG))../neo_common.mk

export CORE_UTILIZATION = 35
export PLACE_PINS_ARGS = -min_distance 1 -min_distance_in_tracks
