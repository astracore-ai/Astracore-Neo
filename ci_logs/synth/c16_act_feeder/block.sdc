# c16_act_feeder at 500.0 ps: the clock and 0.2 of the period on every input and output (drop 0.40)
create_clock -name clk -period 500.0 [get_ports clk]
set_clock_uncertainty -setup 40.0 [get_clocks clk]
set_input_delay  -clock clk -max 100.0 [delete_from_list [all_inputs] [get_ports {clk rst_n}]]
set_output_delay -clock clk -max 100.0 [all_outputs]
set_false_path -from [get_ports rst_n]
set_max_fanout 32 [current_design]
set_max_transition 80.0 [current_design]
# the layer descriptor (the descriptor inputs): written by the host or the DMA only between runs (before go) and held
# for the whole run, the sequencer several cycles from go to its first f_start -- four cycles for its paths (drop 0.41)
set_multicycle_path 4 -setup -from [get_ports {cfg_h cfg_w cfg_ho cfg_wo cfg_oy0 cfg_oy_n cfg_iy0 cfg_s cfg_p cfg_tile_pixels cfg_regions_m1 cfg_sparse}]
set_multicycle_path 3 -hold  -from [get_ports {cfg_h cfg_w cfg_ho cfg_wo cfg_oy0 cfg_oy_n cfg_iy0 cfg_s cfg_p cfg_tile_pixels cfg_regions_m1 cfg_sparse}]
