// sram_bank.sv -- one tile's share of the shared SRAM (spec v2.1: 64 banks, one per core node).
//   Drop 0.28: a 512-bit port, 64 B per cycle each way -- the bandwidth the performance model assumes
//   (neo_perf_v2.BANK_BYTES). The bank is addressed by row; a row holds LANES = 16 words, each stored as its
//   own (39,32) SECDED codeword (16 ECC lanes, 624 bits per row): every write encodes the lanes it writes
//   (wmask), every read decodes all 16, corrects a single-bit error per lane (ecc_ce) and detects a double-bit
//   error (ecc_ue), both sticky; ce_lane says which lanes corrected, for the MBIST. One write port and one
//   read port with a one-cycle registered read. Word address = row * LANES + lane; the layout is lane-major
//   (mem[lane][row]) so a backdoor reaches word a at mem[a % LANES][a / LANES].
module sram_bank #(
  parameter int DEPTH = 65536,                  // words
  parameter int LANES = 16,
  parameter int AW    = $clog2(DEPTH),          // word address width (for reference; the ports take rows)
  parameter int ROWS  = DEPTH / LANES,
  parameter int RAW   = (ROWS > 1) ? $clog2(ROWS) : 1,
  parameter int DW    = 32 * LANES
)(
  input  logic             clk,
  input  logic             rst_n,
  input  logic             we,
  input  logic [RAW-1:0]   wrow,
  input  logic [LANES-1:0] wmask,
  input  logic [DW-1:0]    wdata,
  input  logic             re,
  input  logic [RAW-1:0]   rrow,
  output logic [DW-1:0]    rdata,
  output logic             rvalid,
  input  logic             err_clear,
  output logic             ecc_ce_sticky,
  output logic             ecc_ue_sticky,
  output logic [LANES-1:0] ce_lane             // which lanes of this read were corrected (for the MBIST)
);
  logic [38:0] mem [LANES][ROWS];
  logic [38:0] rword [LANES];
  logic [5:0]  wp  [LANES];
  logic        wop [LANES];
  logic [31:0] rfix [LANES];
  logic        ce  [LANES];
  logic        ue  [LANES];
  logic        any_ce, any_ue;

  generate
    for (genvar l = 0; l < LANES; l++) begin : g_lane
      ecc39_enc u_enc (.d(wdata[32*l +: 32]), .p(wp[l]), .op(wop[l]));
      ecc39_dec u_dec (.d(rword[l][31:0]), .p(rword[l][37:32]), .op(rword[l][38]), .d_out(rfix[l]), .ce(ce[l]), .ue(ue[l]));
      always_ff @(posedge clk) begin
        if (we && wmask[l]) mem[l][wrow] <= {wop[l], wp[l], wdata[32*l +: 32]};
        if (re) rword[l] <= mem[l][rrow];
      end
      assign rdata[32*l +: 32] = rfix[l];
      assign ce_lane[l] = rvalid && ce[l];
    end
  endgenerate
  always_comb begin
    any_ce = 1'b0; any_ue = 1'b0;
    for (int l = 0; l < LANES; l++) begin any_ce = any_ce | ce[l]; any_ue = any_ue | ue[l]; end
  end
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rvalid <= 1'b0; ecc_ce_sticky <= 1'b0; ecc_ue_sticky <= 1'b0;
    end else begin
      rvalid <= re;
      if (err_clear) begin ecc_ce_sticky <= 1'b0; ecc_ue_sticky <= 1'b0; end
      if (rvalid && any_ce) ecc_ce_sticky <= 1'b1;
      if (rvalid && any_ue) ecc_ue_sticky <= 1'b1;
    end
  end
endmodule
