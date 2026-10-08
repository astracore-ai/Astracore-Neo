// prog_mem.sv -- the DMA program memory with SECDED (drop 0.16; 32 entries since drop 0.22): DEPTH x 64-bit instructions stored as two
//   (39,32) lanes; every fetch is corrected (prog_ce) or flagged uncorrectable (prog_ue), both sticky.
//   Shared by the lockstep pair of DMA engines in neo_tile.
module prog_mem #(parameter int DEPTH = 32, AW = $clog2(DEPTH), DV_HOOKS = 0) (
  input  logic          clk,
  input  logic          rst_n,
  input  logic          we,
  input  logic [AW-1:0] waddr,
  input  logic [63:0]   wdata,
  input  logic [AW-1:0] raddr,
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
  ecc39_enc u_e0 (.d(wdata[31:0]),  .p(wp[0]), .op(wop[0]));
  ecc39_enc u_e1 (.d(wdata[63:32]), .p(wp[1]), .op(wop[1]));
  ecc39_dec u_d0 (.d(mem[raddr][0][31:0]), .p(mem[raddr][0][37:32]), .op(mem[raddr][0][38]), .d_out(rfix[0]), .ce(ce[0]), .ue(ue[0]));
  ecc39_dec u_d1 (.d(mem[raddr][1][31:0]), .p(mem[raddr][1][37:32]), .op(mem[raddr][1][38]), .d_out(rfix[1]), .ce(ce[1]), .ue(ue[1]));
  assign rdata = {rfix[1], rfix[0]};
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
      if (ce[0] || ce[1]) prog_ce_sticky <= 1'b1;
      if (ue[0] || ue[1]) prog_ue_sticky <= 1'b1;
    end
  end
endmodule
