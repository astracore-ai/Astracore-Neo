# dc_tile.tcl -- Synopsys Design Compiler reference synthesis of neo_tile_axil (drop 0.16).
#   dc_shell -f syn/dc_tile.tcl   (NEO_LIB_DIR -> N5A .db libraries; NEO_CORNER as in the Genus script)
set LIB_DIR $::env(NEO_LIB_DIR)
set CORNER  [expr {[info exists ::env(NEO_CORNER)] ? $::env(NEO_CORNER) : "ss0p675v125c"}]
set search_path [concat $search_path $LIB_DIR]
set target_library [glob $LIB_DIR/*${CORNER}*.db]
set link_library   [concat "*" $target_library]
set RTL [list delay_line mac_pe skew_in deskew_out systolic_array abft_checker neo_mac_core act_feeder acc_bank \
  neo_mac_core_v02 core_seq ecc39 wbuf_mem requant neo_core noc_router sram_bank mbist bank_bist_wrap crc16_word crc16_beat link_pack \
  tile_nic tile_dma prog_mem host_if esm neo_tile host_axil neo_tile_axil]
foreach f $RTL { analyze -format sverilog rtl/$f.sv }
elaborate neo_tile_axil -parameters "XW=3,YW=3,NX=8,NY=8,ROWS=32,COLS=32,ACC_ROWS=512,ABUF_DEPTH=2048,WBUF_DEPTH=1024,BANK_DEPTH=524288"
current_design neo_tile_axil
link
source syn/constraints.sdc
set_clock_gating_style -minimum_bitwidth 4 -positive_edge_logic integrated
insert_clock_gating
compile_ultra -gate_clock -no_autoungroup
report_timing -max_paths 20 > syn/reports/${CORNER}_timing.rpt
report_area -hierarchy > syn/reports/${CORNER}_area.rpt
report_power -hierarchy > syn/reports/${CORNER}_power.rpt
report_clock_gating > syn/reports/${CORNER}_clockgating.rpt
write -format verilog -hierarchy -output syn/out/neo_tile_axil_${CORNER}.v
write_sdc syn/out/neo_tile_axil_${CORNER}.sdc
