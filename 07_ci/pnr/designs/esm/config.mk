# esm -- the error-signalling module: 18 causes, the mask, the windowed watchdog; 305 cells, 43 um2. At 15 % the core is
# ~17 um on a side (mac_pe's die), the smallest the power straps cover comfortably (drop 0.43, stream A3)
export DESIGN_NAME = esm
include $(dir $(DESIGN_CONFIG))../neo_common.mk

export CORE_UTILIZATION = 15
