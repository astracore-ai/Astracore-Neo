// prog_mem.sv -- the DMA program memory with SECDED (drop 0.16; 32 entries since drop 0.22): DEPTH x 64-bit instructions stored as two
//   (39,32) lanes; every fetch is corrected (prog_ce) or flagged uncorrectable (prog_ue), both sticky.
//   Shared by the lockstep pair of DMA engines in neo_tile.
//   Drop 0.43 -- the decoded instruction registered: rdata is the corrected word at the address of the previous cycle (the
//   32:1 read mux and the two decoders, 770 ps from raddr, were a combinational read into the DMA's instruction register;
//   now they end in rdata's 64 flops). The DMA presents the address of the instruction it will hold next (tile_dma's
//   pc_out = the next value of its pc), so the registered word is always the one at its pc: no cycle is added. The sticky
//   flags are set from the decode of the address presented, as before.
//   Drop 0.44 (S1, review finding 1): the flags only on a real fetch. The decode of the idle address (pc 0 between
//   programs) set the stickies every cycle, and in silicon the array powers up random, so prog_ue (ctrl_path_err, the
//   error pin) was set after every reset. The decoders' verdicts are registered with the word (ce_q / ue_q) and set the
//   stickies in the cycle the DMA takes the word (rd_q: tile_dma's fetch strobe, either engine of the pair).
module prog_mem #(parameter int DEPTH = 32, AW = $clog2(DEPTH), DV_HOOKS = 0) (
  input  logic          clk,
  input  logic          rst_n,
  input  logic          we,
  input  logic [AW-1:0] waddr,
  input  logic [63:0]   wdata,
  input  logic [AW-1:0] raddr,
  input  logic          rd_q,           // drop 0.44: the word on rdata is taken this cycle (the flags' qualifier)
  output logic [63:0]   rdata,
  input  logic          err_clear,
  output logic          prog_ce_sticky,
  output logic          prog_ue_sticky,
  // testbench hook (drop 0.35; DV_HOOKS = 1): lane 0 of entry dv_fi_idx XOR dv_fi_mask at the falling edge while dv_fi_flip
  input  logic          dv_fi_flip,
  input  logic [AW-1:0] dv_fi_idx,
  input  logic [38:0]   dv_fi_mask
);
  logic [38:0] mem [DEPTH][2];
  logic [5:0]  wp [2];
  logic        wop [2];
  logic [31:0] rfix [2];
  logic        ce [2], ue [2];
  logic        ce_q, ue_q;        // the verdicts of the word on rdata (drop 0.44)
  ecc39_enc u_e0 (.d(wdata[31:0]),  .p(wp[0]), .op(wop[0]));
  ecc39_enc u_e1 (.d(wdata[63:32]), .p(wp[1]), .op(wop[1]));
  ecc39_dec u_d0 (.d(mem[raddr][0][31:0]), .p(mem[raddr][0][37:32]), .op(mem[raddr][0][38]), .d_out(rfix[0]), .ce(ce[0]), .ue(ue[0]));
  ecc39_dec u_d1 (.d(mem[raddr][1][31:0]), .p(mem[raddr][1][37:32]), .op(mem[raddr][1][38]), .d_out(rfix[1]), .ce(ce[1]), .ue(ue[1]));
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin rdata <= '0; ce_q <= 1'b0; ue_q <= 1'b0; end
    else        begin rdata <= {rfix[1], rfix[0]}; ce_q <= ce[0] || ce[1]; ue_q <= ue[0] || ue[1]; end
  end
  always_ff @(posedge clk) begin
    if (we) begin
      mem[waddr][0] <= {wop[0], wp[0], wdata[31:0]};
      mem[waddr][1] <= {wop[1], wp[1], wdata[63:32]};
    end
  end
  generate
    if (DV_HOOKS != 0) begin : g_dv
      always_ff @(negedge clk) if (dv_fi_flip) mem[dv_fi_idx][0] <= mem[dv_fi_idx][0] ^ dv_fi_mask;
    end else begin : g_nodv
      logic unused_dv;
      assign unused_dv = ^{dv_fi_flip, dv_fi_idx, dv_fi_mask};
    end
  endgenerate
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin prog_ce_sticky <= 1'b0; prog_ue_sticky <= 1'b0; end
    else begin
      if (err_clear) begin prog_ce_sticky <= 1'b0; prog_ue_sticky <= 1'b0; end
      if (rd_q && ce_q) prog_ce_sticky <= 1'b1;
      if (rd_q && ue_q) prog_ue_sticky <= 1'b1;
    end
  end
endmodule
