# c16_systolic_array: synth.py's block.sdc (c16_systolic_array/block.sdc), unchanged, for ORFS (07_ci/pnr/prep.py)
# c16_systolic_array at 1000.0 ps: the clock and 0.2 of the period on every input and output (drop 0.40)
create_clock -name clk -period 1000.0 [get_ports clk]
set_clock_uncertainty -setup 80.0 [get_clocks clk]
set_input_delay  -clock clk -max 200.0 [delete_from_list [all_inputs] [get_ports {clk rst_n}]]
set_output_delay -clock clk -max 200.0 [all_outputs]
set_false_path -from [get_ports rst_n]
set_max_fanout 32 [current_design]
set_max_transition 160.0 [current_design]
