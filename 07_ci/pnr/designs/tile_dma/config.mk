# tile_dma -- the DMA program engine (one of the lockstep pair): 570 cells, 80 um2, 164 I/O pins. At 55 % the core would
# be 12 um on a side, under two pitches of the platform's power straps; at 30 % it is ~16 um, as mac_pe's die
# (drop 0.43, stream A3)
export DESIGN_NAME = tile_dma
include $(dir $(DESIGN_CONFIG))../neo_common.mk

export CORE_UTILIZATION = 30
