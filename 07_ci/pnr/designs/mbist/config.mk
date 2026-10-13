# mbist -- the bank's March C- engine with (drop 0.43) the registered check: 1.0 k cells, 120 um2, and 1,115 I/O pins
# (the row it writes and the row it reads, 512 bits each). At 30 % the core is ~20 um on a side; the pins one track apart
# need ~54 um of its ~80 um of edge (drop 0.43, stream A3)
export DESIGN_NAME = mbist
include $(dir $(DESIGN_CONFIG))../neo_common.mk

export CORE_UTILIZATION = 30
export PLACE_PINS_ARGS = -min_distance 1 -min_distance_in_tracks
