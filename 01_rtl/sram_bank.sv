// sram_bank.sv -- one tile's share of the shared SRAM (spec v2.1: 64 banks, one per core node).
//   32-bit words stored as (39,32) SECDED codewords (drop 0.9): every write is encoded, every read
//   corrects a single-bit error (ecc_ce) and detects a double-bit error (ecc_ue), both sticky.
//   One write port and one read port with a one-cycle registered read.
module sram_bank #(
  parameter int DEPTH = 65536,
  parameter int AW    = $clog2(DEPTH)
)(
  input  logic          clk,
  input  logic          rst_n,
  input  logic          we,
  input  logic [AW-1:0] waddr,
  input  logic [31:0]   wdata,
  input  logic          re,
  input  logic [AW-1:0] raddr,
  output logic [31:0]   rdata,
  output logic          rvalid,
  input  logic          err_clear,
  output logic          ecc_ce_sticky,
  output logic          ecc_ue_sticky,
  output logic          ecc_ce_now,        // this read was corrected (for the MBIST)
  output logic          ecc_ue_now
);
  logic [38:0] mem [DEPTH];
  logic [5:0]  wp;
  logic        wop;
  logic [38:0] rword;
  logic [31:0] rfix;
  logic        ce, ue;

  ecc39_enc u_enc (.d(wdata), .p(wp), .op(wop));
  ecc39_dec u_dec (.d(rword[31:0]), .p(rword[37:32]), .op(rword[38]), .d_out(rfix), .ce(ce), .ue(ue));

  always_ff @(posedge clk) begin
    if (we) mem[waddr] <= {wop, wp, wdata};
    if (re) rword <= mem[raddr];
  end
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rvalid <= 1'b0; ecc_ce_sticky <= 1'b0; ecc_ue_sticky <= 1'b0;
    end else begin
      rvalid <= re;
      if (err_clear) begin ecc_ce_sticky <= 1'b0; ecc_ue_sticky <= 1'b0; end
      if (rvalid && ce) ecc_ce_sticky <= 1'b1;
      if (rvalid && ue) ecc_ue_sticky <= 1'b1;
    end
  end
  assign rdata = rfix;
  assign ecc_ce_now = rvalid && ce;
  assign ecc_ue_now = rvalid && ue;
endmodule
