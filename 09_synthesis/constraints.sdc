# constraints.sdc -- Neo tile / mesh timing constraints (drop 0.16). 2.0 GHz worst-case corner.
create_clock -name clk -period 0.500 [get_ports clk]
set_clock_uncertainty -setup 0.040 [get_clocks clk]
set_clock_uncertainty -hold  0.020 [get_clocks clk]
set_clock_transition 0.030 [get_clocks clk]
# asynchronous reset: assertion async, deassertion synchronized outside the block
set_false_path -from [get_ports rst_n]
# mesh links: registered on both sides; budget half a cycle to the neighbouring tile's input FIFO
set_input_delay  -clock clk -max 0.200 [get_ports {in_valid* in_flit* out_ready*}]
set_output_delay -clock clk -max 0.200 [get_ports {out_valid* out_flit* in_ready*}]
# host register bus (AXI-Lite side is registered inside host_axil): relaxed
set_input_delay  -clock clk -max 0.250 [get_ports {s_aw* s_w* s_b* s_ar* s_r*}]
set_output_delay -clock clk -max 0.250 [get_ports {s_awready s_wready s_bvalid s_bresp s_arready s_rdata s_rvalid s_rresp err_pin}]
# status flags are sampled by the island at its own rate
set_multicycle_path 4 -setup -to [get_ports {prog_done drain_busy done *_err* *_sticky ecc_* wbuf_* rq_tbl_perr lost_err fetch_timeout}]
set_multicycle_path 3 -hold  -to [get_ports {prog_done drain_busy done *_err* *_sticky ecc_* wbuf_* rq_tbl_perr lost_err fetch_timeout}]
# the DV fault-injection hooks are tied off in silicon
set_case_analysis 0 [get_ports {*fault_inject*}]
set_max_fanout 32 [current_design]
set_max_transition 0.080 [current_design]
