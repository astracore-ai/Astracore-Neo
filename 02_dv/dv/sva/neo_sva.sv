// neo_sva.sv -- the simulation-bound assertion set (drop 0.44, review #10): the handshake and protocol rules the RTL
//   depends on, as concurrent properties in checker modules that bind_all.sv attaches to the RTL modules (bind: the
//   RTL is untouched, the checker sees the module's ports and internal signals). Compiled by Verilator with --assert in
//   the cocotb builds of neo_core, tile_mesh and neo_top (dv/cocotb/Makefile, SVA=1: every suite runs with them on; a
//   failing property stops the simulation, which fails the test); never in the Icarus, neosim, synthesis or formal file
//   lists -- Verilator-only material lives in its own files, as tb/tile_mesh_cocotb.sv and tb/lint/neo_tile.sv do. The
//   fault campaign and the coverage builds compile without them (SVA=0: an injected flop flip may break a handshake by
//   design, and the coverage builds are at the runner's memory limit).
//   Every property is a rule stated in a module header or a brief: a valid held with its payload until ready; the
//   two-flop crossings' one-in-flight and gray-code rules; the NIC's one fetch outstanding and its sequence tag; the
//   drain FIFO's occupancy bound; the bridge's AXI4 master and slave obligations; the router's one-hot grant; the pulse
//   signals that must be pulses. The clock of each property is the module's ungated clock; the gated blocks' registers
//   change only in cycles their enable is on, so the latencies hold in those clocks too.
//   Checked once by breaking the RTL on purpose (the brief's proof that the set fires): cdc_fifo's write pointer sent as
//   binary instead of gray fails p_wgray_one_bit in the chip top's T2 within the first host write.
//   Subset of SVA Verilator 5.020 supports: |->, |=>, $past, $stable, $onehot0, $countones, disable iff; no ## cycle
//   delays in sequences (a latency is written with $past), no repetition operators, no local variables in sequences.
/* verilator lint_off DECLFILENAME */        // several checker modules in one file by design

// ---- valid / ready: a presented transfer is held with its payload until it is taken ----------------------------------
module neo_sva_hold #(parameter int W = 8, parameter string NAME = "link") (
  input logic clk, rst_n, valid, ready, input logic [W-1:0] data);
  p_hold: assert property (@(posedge clk) disable iff (!rst_n)
    (valid && !ready) |=> (valid && $stable(data)))
    else $error("%s: valid dropped or the payload changed while not ready", NAME);
endmodule

// ---- noc_router: every link held, the grant one-hot -----------------------------------------------------------------
// (the five ports arrive as packed vectors, element p at [p * W +: W]: bind_all.sv concatenates the router's unpacked
// arrays -- an unpacked-array port on the checker made Verilator 5.020 emit a C++ copy that does not compile, run 3 of PR #16)
module noc_router_sva #(parameter int FW = 81) (
  input logic clk, rst_n,
  input logic [4:0] in_valid, in_ready, out_valid, out_ready,
  input logic [5*FW-1:0] in_flit, out_flit,
  input logic [24:0] gnt);
  genvar p;
  generate for (p = 0; p < 5; p++) begin : g_p
    neo_sva_hold #(.W(FW), .NAME("noc_router input"))  u_in  (.clk(clk), .rst_n(rst_n), .valid(in_valid[p]),  .ready(in_ready[p]),  .data(in_flit[p*FW +: FW]));
    neo_sva_hold #(.W(FW), .NAME("noc_router output")) u_out (.clk(clk), .rst_n(rst_n), .valid(out_valid[p]), .ready(out_ready[p]), .data(out_flit[p*FW +: FW]));
    p_grant_onehot: assert property (@(posedge clk) disable iff (!rst_n) $onehot0(gnt[p*5 +: 5]))
      else $error("noc_router: output %0d granted to more than one input", p);
    // (no parity property on the outputs: M3 / M16 corrupt an output register through the fault hook by design -- the
    // parity check at the next router's input, and the end-to-end CRC past the last one, are the mechanisms those tests prove)
  end endgenerate
endmodule

// ---- tile_nic: its links held, one fetch outstanding, the stored beats tagged, the drain FIFO within its depth -------
module tile_nic_sva #(parameter int FW = 593, parameter int DFW = 6, parameter int DFD = 64) (
  input logic clk, rst_n, rx_valid, rx_ready, tx_valid, tx_ready,
  input logic [FW-1:0] rx_flit, tx_flit,
  input logic cmd_valid, input logic [2:0] cmd_op, input logic fetch_busy,
  input logic fx_entry, input logic [8:0] rx_tag, input logic [3:0] fetch_seq,
  input logic drain_push, input logic [DFW:0] dfifo_np, dfifo_nq);
  neo_sva_hold #(.W(FW), .NAME("tile_nic rx")) u_rx (.clk(clk), .rst_n(rst_n), .valid(rx_valid), .ready(rx_ready), .data(rx_flit));
  neo_sva_hold #(.W(FW), .NAME("tile_nic tx")) u_tx (.clk(clk), .rst_n(rst_n), .valid(tx_valid), .ready(tx_ready), .data(tx_flit));
  // one fetch outstanding: the DMA issues a FETCH only after the previous one completed or timed out
  p_one_fetch: assert property (@(posedge clk) disable iff (!rst_n)
    (cmd_valid && ((cmd_op == 3'd1) || (cmd_op == 3'd2))) |-> !fetch_busy)
    else $error("tile_nic: a FETCH command while a fetch is outstanding");
  // a stored RDRSP beat carries the outstanding fetch's sequence tag (drop 0.44, review #2)
  p_tag_match: assert property (@(posedge clk) disable iff (!rst_n) fx_entry |-> (fetch_busy && (rx_tag[3:0] == fetch_seq)))
    else $error("tile_nic: an RDRSP beat stored without the outstanding fetch's tag");
  // the drain FIFO never overflows: a row is pushed only while fewer than DFD rows are held (sized for a full drain)
  p_dfifo_bound: assert property (@(posedge clk) disable iff (!rst_n) drain_push |-> ((dfifo_np - dfifo_nq) < (DFW + 1)'(DFD)))
    else $error("tile_nic: the drain FIFO is full and a row is pushed");
endmodule

// ---- cdc_sync: one access in flight, the pulses pulses, the caller's obligation ---------------------------------------
module cdc_sync_sva (input logic a_clk, a_rst_n, a_req, a_busy, a_done, b_clk, b_rst_n, b_req, b_done);
  p_req_only_idle: assert property (@(posedge a_clk) disable iff (!a_rst_n) a_req |-> !a_busy)
    else $error("cdc_sync: a request while an access is in flight");
  p_busy_after_req: assert property (@(posedge a_clk) disable iff (!a_rst_n) (a_req && !a_busy) |=> a_busy)
    else $error("cdc_sync: a taken request did not raise a_busy");
  p_done_pulse: assert property (@(posedge a_clk) disable iff (!a_rst_n) a_done |=> !a_done)
    else $error("cdc_sync: a_done is not a one-cycle pulse");
  p_breq_pulse: assert property (@(posedge b_clk) disable iff (!b_rst_n) b_req |=> !b_req)
    else $error("cdc_sync: b_req is not a one-cycle pulse");
  p_bdone_not_breq: assert property (@(posedge b_clk) disable iff (!b_rst_n) b_done |-> !b_req)
    else $error("cdc_sync: a response in the cycle a new request arrives");
endmodule

// ---- cdc_fifo: the gray pointers change one bit at a time, each side holds its transfer ------------------------------
module cdc_fifo_sva #(parameter int W = 8, parameter int PW = 4) (
  input logic wclk, wrst_n, w_valid, w_ready, rclk, rrst_n, r_valid, r_ready,
  input logic [W-1:0] w_data, r_data, input logic [PW-1:0] wgray, rgray);
  p_wgray_one_bit: assert property (@(posedge wclk) disable iff (!wrst_n) $countones(wgray ^ $past(wgray)) <= 1)
    else $error("cdc_fifo: the write pointer's gray code changed more than one bit");
  p_rgray_one_bit: assert property (@(posedge rclk) disable iff (!rrst_n) $countones(rgray ^ $past(rgray)) <= 1)
    else $error("cdc_fifo: the read pointer's gray code changed more than one bit");
  neo_sva_hold #(.W(W), .NAME("cdc_fifo write side")) u_w (.clk(wclk), .rst_n(wrst_n), .valid(w_valid), .ready(w_ready), .data(w_data));
  neo_sva_hold #(.W(W), .NAME("cdc_fifo read side"))  u_r (.clk(rclk), .rst_n(rrst_n), .valid(r_valid), .ready(r_ready), .data(r_data));
endmodule

// ---- noc_axi_bridge: the AXI4 master's and slave's obligations on its ports -----------------------------------------
module noc_axi_bridge_sva #(parameter int AXI_DW = 512, parameter int AXI_IDW = 4) (
  input logic clk, rst_n,
  // master (the memory role; tied 0 without it)
  input logic m_awvalid, m_awready, m_wvalid, m_wready, m_wlast, m_bvalid, m_bready, m_arvalid, m_arready, m_rvalid, m_rready, m_rlast,
  input logic [39:0] m_awaddr, m_araddr, input logic [7:0] m_awlen, m_arlen, input logic [AXI_DW-1:0] m_wdata, input logic [AXI_DW/8-1:0] m_wstrb,
  // slave (the host role; tied 0 without it)
  input logic s_awvalid, s_awready, s_wvalid, s_wready, s_bvalid, s_bready, s_arvalid, s_arready, s_rvalid, s_rready, s_rlast,
  input logic [1:0] s_bresp, s_rresp, input logic [AXI_IDW-1:0] s_bid, s_rid, input logic [AXI_DW-1:0] s_rdata);
  // the master holds AW / W / AR with their payloads until taken
  neo_sva_hold #(.W(48), .NAME("m_axi AW")) u_aw (.clk(clk), .rst_n(rst_n), .valid(m_awvalid), .ready(m_awready), .data({m_awaddr, m_awlen}));
  neo_sva_hold #(.W(AXI_DW + AXI_DW / 8 + 1), .NAME("m_axi W")) u_w (.clk(clk), .rst_n(rst_n), .valid(m_wvalid), .ready(m_wready), .data({m_wdata, m_wstrb, m_wlast}));
  neo_sva_hold #(.W(48), .NAME("m_axi AR")) u_ar (.clk(clk), .rst_n(rst_n), .valid(m_arvalid), .ready(m_arready), .data({m_araddr, m_arlen}));
  // WLAST exactly on the burst's last beat (AW before or with the last W, as the bridge issues them)
  logic [8:0] wcnt;                                  // W beats of the burst so far
  logic [7:0] awl;                                   // the burst's AWLEN
  logic       aw_seen;
  logic       aw_fire, w_fire, b_fire, ar_fire, rl_fire;
  assign aw_fire = m_awvalid && m_awready;
  assign w_fire  = m_wvalid && m_wready;
  assign b_fire  = m_bvalid && m_bready;
  assign ar_fire = m_arvalid && m_arready;
  assign rl_fire = m_rvalid && m_rready && m_rlast;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin wcnt <= '0; awl <= '0; aw_seen <= 1'b0; end
    else begin
      if (aw_fire) begin awl <= m_awlen; aw_seen <= 1'b1; end
      if (w_fire) begin
        if (m_wlast) begin wcnt <= '0; aw_seen <= aw_fire; end   // (an AW taken in the same cycle belongs to the next burst)
        else wcnt <= wcnt + 9'd1;
      end
    end
  end
  p_wlast_on_last: assert property (@(posedge clk) disable iff (!rst_n)
    (w_fire && (aw_seen || aw_fire)) |-> (m_wlast == (wcnt == 9'((aw_seen ? awl : m_awlen)))))
    else $error("m_axi: WLAST is not on the burst's last beat (beat %0d, AWLEN %0d)", wcnt, aw_seen ? awl : m_awlen);
  // no response without a request: bursts outstanding on each channel
  logic [7:0] aw_out, ar_out;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin aw_out <= '0; ar_out <= '0; end
    else begin
      aw_out <= aw_out + 8'(aw_fire) - 8'(b_fire);
      ar_out <= ar_out + 8'(ar_fire) - 8'(rl_fire);
    end
  end
  p_b_after_aw: assert property (@(posedge clk) disable iff (!rst_n) m_bvalid |-> (aw_out != 8'd0))
    else $error("m_axi: a write response with no write burst outstanding");
  p_r_after_ar: assert property (@(posedge clk) disable iff (!rst_n) m_rvalid |-> (ar_out != 8'd0))
    else $error("m_axi: read data with no read burst outstanding");
  // the slave holds B and R with their payloads until taken, and answers only what was requested
  neo_sva_hold #(.W(2 + AXI_IDW), .NAME("s_axi B")) u_b (.clk(clk), .rst_n(rst_n), .valid(s_bvalid), .ready(s_bready), .data({s_bresp, s_bid}));
  neo_sva_hold #(.W(AXI_DW + 3 + AXI_IDW), .NAME("s_axi R")) u_r (.clk(clk), .rst_n(rst_n), .valid(s_rvalid), .ready(s_rready), .data({s_rdata, s_rresp, s_rlast, s_rid}));
  logic [7:0] saw_out, sar_out;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin saw_out <= '0; sar_out <= '0; end
    else begin
      saw_out <= saw_out + 8'(s_awvalid && s_awready) - 8'(s_bvalid && s_bready);
      sar_out <= sar_out + 8'(s_arvalid && s_arready) - 8'(s_rvalid && s_rready && s_rlast);
    end
  end
  p_sb_after_aw: assert property (@(posedge clk) disable iff (!rst_n) s_bvalid |-> (saw_out != 8'd0))
    else $error("s_axi: a write response with no write transaction outstanding");
  p_sr_after_ar: assert property (@(posedge clk) disable iff (!rst_n) s_rvalid |-> (sar_out != 8'd0))
    else $error("s_axi: read data with no read transaction outstanding");
endmodule

// ---- the pulses and latencies the core blocks promise -----------------------------------------------------------------
module core_seq_sva (input logic clk, rst_n, done, rd_en, input logic [2:0] state_dbg);
  localparam logic [2:0] S_DRAIN = 3'd5;
  p_done_pulse: assert property (@(posedge clk) disable iff (!rst_n) done |=> !done)
    else $error("core_seq: done is not a one-cycle pulse");
  p_drain_only_in_drain: assert property (@(posedge clk) disable iff (!rst_n) rd_en |-> (state_dbg == S_DRAIN))
    else $error("core_seq: a drain read outside S_DRAIN");
endmodule

module requant_sva (input logic clk, rst_n, in_valid, out_valid);
  // (written with $past: Verilator 5.020 takes no ## cycle delay in a sequence -- run 228's build error; 5.053 does)
  p_latency: assert property (@(posedge clk) disable iff (!rst_n) $past(in_valid, 3) |-> out_valid)
    else $error("requant: a row did not come out three cycles after it went in");
  p_no_spurious: assert property (@(posedge clk) disable iff (!rst_n) out_valid |-> $past(in_valid, 3))
    else $error("requant: a row came out that did not go in three cycles before");
endmodule

module tile_dma_sva (input logic clk, rst_n, go, prog_done, cmd_valid);
  p_go_pulse: assert property (@(posedge clk) disable iff (!rst_n) go |=> !go)
    else $error("tile_dma: go is not a one-cycle pulse");
  p_done_quiet: assert property (@(posedge clk) disable iff (!rst_n) prog_done |-> !cmd_valid)
    else $error("tile_dma: a command issued while the program is done");
endmodule
