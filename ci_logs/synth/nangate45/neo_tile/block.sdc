# neo_tile at 0.5000 ns (09_synthesis/constraints.sdc, drop 0.16, in the library's units; drop 0.40)
create_clock -name clk -period 0.5000 [get_ports clk]
set_clock_uncertainty -setup 0.0400 [get_clocks clk]
set_clock_uncertainty -hold 0.0200 [get_clocks clk]
set_clock_transition 0.0300 [get_clocks clk]
# asynchronous reset: assertion async, deassertion synchronized outside the block
set_false_path -from [get_ports rst_n]
# the tile's coordinates are straps
set_false_path -from [get_ports {my_x* my_y*}]
# mesh links: registered on both sides; a budget of 0.4 of the cycle to and from the neighbouring tile
set_input_delay  -clock clk -max 0.2000 [get_ports {in_valid* in_flit* out_ready*}]
set_output_delay -clock clk -max 0.2000 [get_ports {out_valid* out_flit* in_ready*}]
# host register bus: relaxed (registered in the aggregator)
set_input_delay  -clock clk -max 0.2500 [get_ports {h_we h_re h_addr* h_wdata*}]
set_output_delay -clock clk -max 0.2500 [get_ports {h_rdata* err_pin}]
# status flags are sampled by the island at its own rate
set_multicycle_path 4 -setup -to [get_ports {prog_done drain_busy done parity_err crc_err *_sticky ecc_* wbuf_* rq_tbl_perr lost_err fetch_timeout}]
set_multicycle_path 3 -hold  -to [get_ports {prog_done drain_busy done parity_err crc_err *_sticky ecc_* wbuf_* rq_tbl_perr lost_err fetch_timeout}]
set_max_fanout 32 [current_design]
set_max_transition 0.0800 [current_design]
# the layer descriptor (the child instances' descriptor pins): written by the host or the DMA only between runs (before go) and held
# for the whole run, the sequencer several cycles from go to its first f_start -- four cycles for its paths (drop 0.41)
set_multicycle_path 4 -setup -through [get_pins {u_core/cfg_contrib_n u_core/cfg_ct0 u_core/cfg_ct_n u_core/cfg_h u_core/cfg_ho u_core/cfg_iy0 u_core/cfg_k u_core/cfg_kx0 u_core/cfg_ky0 u_core/cfg_m u_core/cfg_oy0 u_core/cfg_oy_n u_core/cfg_p u_core/cfg_regions_m1 u_core/cfg_rn u_core/cfg_s u_core/cfg_sparse u_core/cfg_tile_pixels u_core/cfg_w u_core/cfg_wo}]
set_multicycle_path 3 -hold  -through [get_pins {u_core/cfg_contrib_n u_core/cfg_ct0 u_core/cfg_ct_n u_core/cfg_h u_core/cfg_ho u_core/cfg_iy0 u_core/cfg_k u_core/cfg_kx0 u_core/cfg_ky0 u_core/cfg_m u_core/cfg_oy0 u_core/cfg_oy_n u_core/cfg_p u_core/cfg_regions_m1 u_core/cfg_rn u_core/cfg_s u_core/cfg_sparse u_core/cfg_tile_pixels u_core/cfg_w u_core/cfg_wo}]
