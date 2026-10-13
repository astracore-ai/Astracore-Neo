# host_if -- the host register interface: the descriptor (kept twice and compared), the requantization tables' write
# port, the program memory's write port, flags and causes; 3.1 k cells, 642 I/O pins (the descriptor fans out as 521
# outputs) -- drop 0.43, stream A3
export DESIGN_NAME = host_if
include $(dir $(DESIGN_CONFIG))../neo_common.mk
