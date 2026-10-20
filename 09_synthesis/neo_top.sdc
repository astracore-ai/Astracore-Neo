# written by 07_ci/synth/synth.py --write-sdc at 1250 ps (drop 0.44): the template the synthesis job's unit flow times with, in nanoseconds; regenerate here rather than edit
# neo_top at 1.2500 ns: the mesh clock clk and the AXI clock aclk (drop 0.44, A4; 08_ip_product/neo_top_integration.md section 6)
create_clock -name clk  -period 1.2500 [get_ports clk]
create_clock -name aclk -period 1.2500 [get_ports aclk]
set_clock_uncertainty -setup 0.1000 [get_clocks {clk aclk}]
set_clock_uncertainty -hold 0.0500 [get_clocks {clk aclk}]
set_clock_transition 0.0750 [get_clocks {clk aclk}]
# the two resets: assertion asynchronous, deassertion synchronized outside (neo_top.sv: aresetn released before rst_n)
set_false_path -from [get_ports {rst_n aresetn}]
# the AXI4-Lite control port and the AXI4 ports (every input and output but the mesh-clock ones): 0.2 of the period in aclk
set_input_delay  -clock aclk -max 0.2500 [delete_from_list [all_inputs] [get_ports {clk aclk rst_n aresetn}]]
set_output_delay -clock aclk -max 0.2500 [delete_from_list [all_outputs] [get_ports {tile_pwr_en*}]]
# the tiles' power-switch enables: mesh-clock levels, each held for the switch's ramp (mesh_ctrl's sequencer, >= 4 clk)
set_output_delay -clock clk -max 0.2500 [get_ports {tile_pwr_en*}]
set_multicycle_path 4 -setup -to [get_ports {tile_pwr_en*}]
set_multicycle_path 3 -hold  -to [get_ports {tile_pwr_en*}]
# crossing 1 -- the bridges' data path, a cdc_fifo per direction (u_rxq: clk -> aclk, u_txq: aclk -> clk): one source
# period from each gray pointer register to its first synchronizer flop (the code's bits must arrive within a cycle of
# each other), one destination period from the entries to the read side's consumer (the bridge's rx_flit, the packer's
# core_flit)
set_max_delay -ignore_clock_latency 1.2500 -from [get_cells {*u_rxq.wgray*_reg *u_txq.wgray*_reg}] -to [get_cells {*u_rxq.u_wsync.sync1*_reg *u_txq.u_wsync.sync1*_reg}]
set_max_delay -ignore_clock_latency 1.2500 -from [get_cells {*u_rxq.rgray*_reg *u_txq.rgray*_reg}] -to [get_cells {*u_rxq.u_rsync.sync1*_reg *u_txq.u_rsync.sync1*_reg}]
set_max_delay -ignore_clock_latency 1.2500 -from [get_cells {*u_rxq.mem*_reg}] -to [get_pins {*u_br/rx_flit*}]
set_max_delay -ignore_clock_latency 1.2500 -from [get_cells {*u_txq.mem*_reg}] -to [get_pins {*u_pack/core_flit*}]
# crossing 2 -- the register bus (cdc_sync u_rq): the held request into mesh_ctrl, the held response into a_rsp_data, one
# destination period each (the payload is stable two edges before it is sampled, by the handshake)
set_max_delay -ignore_clock_latency 1.2500 -from [get_cells {u_rq.a_hold*_reg}] -to [get_pins {u_mctrl/rq_data*}]
set_max_delay -ignore_clock_latency 1.2500 -from [get_cells {u_rq.b_hold*_reg}] -to [get_cells {u_rq.a_rsp_data*_reg}]
# crossing 3 -- the levels and the handshake's toggles (cdc_bits u_*_sync, u_rq.u_ack_sync / u_req_sync): a false path into
# every first synchronizer flop (the FIFOs' u_rsync / u_wsync are the max-delay paths above, not these)
set_false_path -to [get_cells {*_sync.sync1*_reg}]
set_max_fanout 32 [current_design]
set_max_transition 0.2000 [current_design]
