// bind_all.sv -- attach the assertion modules to the RTL without touching it. Compile after the RTL.
bind neo_mac_core neo_mac_core_sva #(.ROWS(ROWS), .COLS(COLS), .PE_LAT(PE_LAT)) u_sva (
  .clk(clk), .rst_n(rst_n), .w_load(w_load), .swap_in(swap_in), .valid_in(valid_in), .valid_out(valid_out),
  .abft_err(abft_err), .fault_inject(fault_inject));
bind core_seq core_seq_sva u_sva (.clk(clk), .rst_n(rst_n), .go(go), .done(done), .busy(busy), .reduce_ready(reduce_ready),
  .f_start(f_start), .rd_en(rd_en), .w_load(w_load), .state_dbg(state_dbg));
bind acc_bank acc_bank_sva u_sva (.clk(clk), .rst_n(rst_n), .wr_valid(wr_valid), .ext_valid(ext_valid), .ext_ready(ext_ready),
  .rd_en(rd_en), .rd_valid(rd_valid));
bind noc_router noc_router_sva #(.FW(FW)) u_sva (.clk(clk), .rst_n(rst_n), .in_valid(in_valid), .in_ready(in_ready),
  .out_valid(out_valid), .out_ready(out_ready), .in_flit(in_flit), .out_flit(out_flit));
bind tile_nic tile_nic_sva u_sva (.clk(clk), .rst_n(rst_n), .rx_valid(rx_valid), .rx_ready(rx_ready), .tx_valid(tx_valid),
  .tx_ready(tx_ready), .cmd_busy(cmd_busy), .drain_busy(drain_busy), .rx_type(rx_type), .serve_busy(serve_busy), .drain_open(drain_open));
bind tile_dma tile_dma_sva u_sva (.clk(clk), .rst_n(rst_n), .prog_start(prog_start), .prog_done(prog_done), .prog_busy(prog_busy),
  .cmd_valid(cmd_valid), .go(go), .state(state));
bind requant requant_sva u_sva (.clk(clk), .rst_n(rst_n), .in_valid(in_valid), .out_valid(out_valid));
