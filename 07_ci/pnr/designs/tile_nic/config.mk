# tile_nic -- the tile's network interface (about 105 k cells after drop 0.41) with its two memory leaves as FakeRAM2.0
# register files with an asynchronous read: the drain FIFO (512 x 1,065) and the partial-sum rows (64 x 1,024, 32
# write groups; pin-limited: 2,093 pins on one edge). The local router port, the core's drain and the bank's rows make
# several thousand I/O pins: 45 % utilization, pins one track apart
export DESIGN_NAME = tile_nic
include $(dir $(DESIGN_CONFIG))../neo_common.mk

export CORE_UTILIZATION = 45
export PLACE_PINS_ARGS = -min_distance 1 -min_distance_in_tracks
export SKIP_LAST_GASP = 1
