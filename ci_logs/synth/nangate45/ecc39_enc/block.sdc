# ecc39_enc: combinational -- a virtual clock of 0.5000 ns, inputs and outputs at its edges (drop 0.40)
create_clock -name vclk -period 0.5000
set_input_delay  -clock vclk 0 [all_inputs]
set_output_delay -clock vclk 0 [all_outputs]
set_max_fanout 32 [current_design]
set_max_transition 0.0800 [current_design]
