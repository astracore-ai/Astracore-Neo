# neo_mac_core at 0.5000 ns: the clock and 0.2 of the period on every input and output (drop 0.40)
create_clock -name clk -period 0.5000 [get_ports clk]
set_clock_uncertainty -setup 0.0400 [get_clocks clk]
set_input_delay  -clock clk -max 0.1000 [delete_from_list [all_inputs] [get_ports {clk rst_n}]]
set_output_delay -clock clk -max 0.1000 [all_outputs]
set_false_path -from [get_ports rst_n]
set_max_fanout 32 [current_design]
set_max_transition 0.0800 [current_design]
