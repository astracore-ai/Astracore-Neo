# genus_tile.tcl -- Cadence Genus reference synthesis of neo_tile_axil (drop 0.16).
#   genus -f syn/genus_tile.tcl   (set LIB_DIR to the N5A library release: SS 0.675 V 125 C / 150 C, aged)
set LIB_DIR  $::env(NEO_LIB_DIR)
set CORNER   [expr {[info exists ::env(NEO_CORNER)] ? $::env(NEO_CORNER) : "ss0p675v125c"}]
set_db init_lib_search_path $LIB_DIR
set_db library [glob $LIB_DIR/*${CORNER}*.lib]
set_db hdl_track_filename_row_col true
set_db syn_generic_effort high
set_db syn_map_effort high
set_db syn_opt_effort high
set_db lp_insert_clock_gating true          ;# the PE's valid enables and the FIFO enables become ICG cells
set_db lp_clock_gating_min_flops 4
set RTL [list delay_line mac_pe skew_in deskew_out systolic_array abft_checker neo_mac_core act_feeder acc_bank \
  neo_mac_core_v02 core_seq ecc39 wbuf_mem requant neo_core noc_router sram_bank mbist bank_bist_wrap crc16_word \
  tile_nic tile_dma prog_mem host_if esm neo_tile host_axil neo_tile_axil]
foreach f $RTL { read_hdl -sv rtl/$f.sv }
# silicon configuration of one tile on an 8x8 mesh
elaborate neo_tile_axil -parameters {{XW 3} {YW 3} {NX 8} {NY 8} {ROWS 32} {COLS 32} {ACC_ROWS 512} {ABUF_DEPTH 2048} {WBUF_DEPTH 1024} {BANK_DEPTH 524288}}
read_sdc syn/constraints.sdc
# memories: the bank, accumulator, buffers and program memory are inferred as flops here; replace by the
# memory-compiler macros (see syn/memories.md) before reporting area
syn_generic
syn_map
syn_opt
report_timing -max_paths 20 > syn/reports/${CORNER}_timing.rpt
report_area -hierarchical > syn/reports/${CORNER}_area.rpt
report_power -hierarchical > syn/reports/${CORNER}_power.rpt
report_clock_gating > syn/reports/${CORNER}_clockgating.rpt
write_hdl > syn/out/neo_tile_axil_${CORNER}.v
write_sdc > syn/out/neo_tile_axil_${CORNER}.sdc
