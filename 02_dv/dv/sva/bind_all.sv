// bind_all.sv -- attaches the checkers of neo_sva.sv to the RTL (drop 0.44, review #10). Compiled after the RTL and
//   neo_sva.sv by the cocotb builds (dv/cocotb/Makefile, SVA=1); a bind reaches the module's internal signals, so the RTL
//   carries no assertion and no port for them. Every instance of a bound module gets the checker (every router of a
//   mesh, every bridge, every crossing).
bind noc_router noc_router_sva #(.FW(FW)) u_sva (     // the unpacked arrays packed, element p at [p * W +: W]
  .clk(clk), .rst_n(rst_n),
  .in_valid({in_valid[4], in_valid[3], in_valid[2], in_valid[1], in_valid[0]}),
  .in_ready({in_ready[4], in_ready[3], in_ready[2], in_ready[1], in_ready[0]}),
  .out_valid({out_valid[4], out_valid[3], out_valid[2], out_valid[1], out_valid[0]}),
  .out_ready({out_ready[4], out_ready[3], out_ready[2], out_ready[1], out_ready[0]}),
  .in_flit({in_flit[4], in_flit[3], in_flit[2], in_flit[1], in_flit[0]}),
  .out_flit({out_flit[4], out_flit[3], out_flit[2], out_flit[1], out_flit[0]}),
  .gnt({gnt[4], gnt[3], gnt[2], gnt[1], gnt[0]}));
bind tile_nic tile_nic_sva #(.FW(FW), .DFW(DFW), .DFD(DFD)) u_sva (
  .clk(clk), .rst_n(rst_n), .rx_valid(rx_valid), .rx_ready(rx_ready), .tx_valid(tx_valid), .tx_ready(tx_ready),
  .rx_flit(rx_flit), .tx_flit(tx_flit), .cmd_valid(cmd_valid), .cmd_op(cmd_op), .fetch_busy(fetch_busy),
  .fx_entry(fx_entry), .rx_tag(rx_tag), .fetch_seq(fetch_seq), .drain_push(drain_push), .dfifo_np(dfifo_np), .dfifo_nq(dfifo_nq));
bind cdc_sync cdc_sync_sva u_sva (
  .a_clk(a_clk), .a_rst_n(a_rst_n), .a_req(a_req), .a_busy(a_busy), .a_done(a_done),
  .b_clk(b_clk), .b_rst_n(b_rst_n), .b_req(b_req), .b_done(b_done));
bind cdc_fifo cdc_fifo_sva #(.W(W), .PW(PW)) u_sva (
  .wclk(wclk), .wrst_n(wrst_n), .w_valid(w_valid), .w_ready(w_ready), .w_data(w_data), .wgray(wgray),
  .rclk(rclk), .rrst_n(rrst_n), .r_valid(r_valid), .r_ready(r_ready), .r_data(r_data), .rgray(rgray));
bind noc_axi_bridge noc_axi_bridge_sva #(.AXI_DW(AXI_DW), .AXI_IDW(AXI_IDW)) u_sva (
  .clk(clk), .rst_n(rst_n),
  .m_awvalid(m_awvalid), .m_awready(m_awready), .m_wvalid(m_wvalid), .m_wready(m_wready), .m_wlast(m_wlast), .m_bvalid(m_bvalid),
  .m_bready(m_bready), .m_arvalid(m_arvalid), .m_arready(m_arready), .m_rvalid(m_rvalid), .m_rready(m_rready), .m_rlast(m_rlast),
  .m_awaddr(m_awaddr), .m_araddr(m_araddr), .m_awlen(m_awlen), .m_arlen(m_arlen), .m_wdata(m_wdata), .m_wstrb(m_wstrb),
  .s_awvalid(s_awvalid), .s_awready(s_awready), .s_wvalid(s_wvalid), .s_wready(s_wready), .s_bvalid(s_bvalid), .s_bready(s_bready),
  .s_arvalid(s_arvalid), .s_arready(s_arready), .s_rvalid(s_rvalid), .s_rready(s_rready), .s_rlast(s_rlast),
  .s_bresp(s_bresp), .s_rresp(s_rresp), .s_bid(s_bid), .s_rid(s_rid), .s_rdata(s_rdata));
bind core_seq core_seq_sva u_sva (.clk(clk), .rst_n(rst_n), .done(done), .rd_en(rd_en), .state_dbg(state_dbg));
bind requant requant_sva u_sva (.clk(clk), .rst_n(rst_n), .in_valid(in_valid), .out_valid(out_valid));
bind tile_dma tile_dma_sva u_sva (.clk(clk), .rst_n(rst_n), .go(go), .prog_done(prog_done), .cmd_valid(cmd_valid));
