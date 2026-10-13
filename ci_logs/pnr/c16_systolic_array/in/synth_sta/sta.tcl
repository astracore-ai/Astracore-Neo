# c16_systolic_array: prep.py's netlist flat in OpenSTA, block.sdc, ideal clock, no wire load -- the synthesized estimate on the
# netlist ORFS places (07_ci/pnr/run.py)
read_liberty /home/runner/neo-synth/pdk/lib/asap7/asap7sc7p5t_AO_RVT_SS_nldm_211120.lib
read_liberty /home/runner/neo-synth/pdk/lib/asap7/asap7sc7p5t_INVBUF_RVT_SS_nldm_220122.lib
read_liberty /home/runner/neo-synth/pdk/lib/asap7/asap7sc7p5t_OA_RVT_SS_nldm_211120.lib
read_liberty /home/runner/neo-synth/pdk/lib/asap7/asap7sc7p5t_SIMPLE_RVT_SS_nldm_211120.lib
read_liberty /home/runner/neo-synth/pdk/lib/asap7/asap7sc7p5t_SEQ_RVT_SS_nldm_220123.lib
read_verilog /home/runner/work/Astracore-Neo/Astracore-Neo/pnr_out/c16_systolic_array/in/1_netlist.v
link_design c16_systolic_array
read_sdc /home/runner/work/Astracore-Neo/Astracore-Neo/pnr_out/c16_systolic_array/in/1_block.sdc
set f [open summary.txt w]
puts $f "wns_max [worst_slack -max]"
puts $f "tns_max [total_negative_slack -max]"
puts $f "wns_min [worst_slack -min]"
puts $f "instances [llength [get_cells -hier *]]"
close $f
report_clock_min_period > min_period.txt
report_clock_min_period -include_port_paths > min_period_ports.txt
report_checks -path_delay max -group_path_count 10 -endpoint_path_count 1 -unique_paths_to_endpoint -fields {slew cap fanout} -digits 1 -format full_clock_expanded > paths_max.txt
report_checks -path_delay max -from [all_registers] -to [all_registers] -group_path_count 5 -endpoint_path_count 1 -digits 1 -format full_clock_expanded > paths_reg2reg.txt
exit
