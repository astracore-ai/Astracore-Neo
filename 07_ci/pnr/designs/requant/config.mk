# requant -- the requantizer (drop 0.41's three stages: the 32 x 16 product, the rounding bias and zero point as
# carry-save rows and one adder, shift / fits-in-8-bits / saturate; 32 columns, the tables with parity; 127 k cells, no
# macro: the largest logic block of the core after the array, twice in it -- drop 0.43, stream A3)
export DESIGN_NAME = requant
include $(dir $(DESIGN_CONFIG))../neo_common.mk

# runtime: no last-gasp pass in the timing repairs of CTS and global route (the floorplan's repair skips it anyway)
export SKIP_LAST_GASP = 1
