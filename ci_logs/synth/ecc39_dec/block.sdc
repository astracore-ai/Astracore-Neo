# ecc39_dec: combinational -- a virtual clock of 500.0 ps, inputs and outputs at its edges (drop 0.40)
create_clock -name vclk -period 500.0
set_input_delay  -clock vclk 0 [all_inputs]
set_output_delay -clock vclk 0 [all_outputs]
set_max_fanout 32 [current_design]
set_max_transition 80.0 [current_design]
