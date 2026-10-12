// sram_bank.sv -- one tile's share of the shared SRAM (spec v2.1: 64 banks, one per core node).
//   Drop 0.28: a 512-bit port, 64 B per cycle each way -- the bandwidth the performance model assumes
//   (neo_perf_v2.BANK_BYTES). The bank is addressed by row; a row holds LANES = 16 words, each stored as its
//   own (39,32) SECDED codeword (16 ECC lanes, 624 bits per row): every write encodes the lanes it writes
//   (wmask), every read decodes all 16, corrects a single-bit error per lane (ecc_ce) and detects a double-bit
//   error (ecc_ue), both sticky; ce_lane says which lanes corrected, for the MBIST. One write port and one
//   read port with a one-cycle registered read. Word address = row * LANES + lane: word a is lane a % LANES of row
//   a / LANES. Drop 0.40: the array is a neo_mem_sp leaf of ROWS words x LANES x 39 bits (the 624-bit row of coded lanes)
//   with one write-enable group per lane and a registered row read -- the module the synthesis flow black-boxes and a
//   memory compiler's macros replace; the ECC lanes, the sticky flags and the hooks' decode stay here. Lane l of a row
//   is bits [39*l +: 39] of the leaf's word (the testbenches' backdoor path: u_mem.mem[row][39*lane +: 39]).
module sram_bank #(
  parameter int DEPTH = 65536,                  // words
  parameter int LANES = 16,
  parameter int AW    = $clog2(DEPTH),          // word address width (for reference; the ports take rows)
  parameter int ROWS  = DEPTH / LANES,
  parameter int RAW   = (ROWS > 1) ? $clog2(ROWS) : 1,
  parameter int DW    = 32 * LANES,
  parameter int DV_HOOKS = 0                    // 1: the testbench hooks below exist (drop 0.35); 0: the silicon bank, ports tied off
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
  output logic [LANES-1:0] ce_lane,            // which lanes of this read were corrected (for the MBIST)
  // testbench hooks (drop 0.35; DV_HOOKS = 1): the backdoor on the raw (39,32) codewords by word address, and the stuck-at
  // fault -- a mask ORed into a word at the falling edge while dv_fi_or is high. Ports, so that neo_tile can be a
  // hierarchical block of the Verilator build (no hierarchical references into it); the wrapper's semantics are unchanged.
  input  logic             dv_bd_we,           // rising edge: dv_bd_wdata into word dv_bd_addr
  input  logic [AW-1:0]    dv_bd_addr,
  input  logic [38:0]      dv_bd_wdata,
  output logic [38:0]      dv_bd_rdata,        // the codeword at dv_bd_addr, combinational
  input  logic             dv_fi_or,           // falling edge: word dv_fi_addr |= dv_fi_mask
  input  logic [AW-1:0]    dv_fi_addr,
  input  logic [38:0]      dv_fi_mask
);
  localparam int CW = 39 * LANES;                  // a row of coded lanes
  logic [CW-1:0] row_wr;                           // the coded lanes to write (masked by wmask)
  logic [CW-1:0] row_rd;                           // the row read, registered in the leaf
  logic [38:0]   rword [LANES];
  logic [5:0]    wp  [LANES];
  logic          wop [LANES];
  logic [31:0]   rfix [LANES];
  logic          ce  [LANES];
  logic          ue  [LANES];
  logic          any_ce, any_ue;

  // the hooks' decode (DV_HOOKS = 1): word a is lane a % LANES of row a / LANES; the leaf takes a lane as a write group of
  // the row. At DV_HOOKS = 0 the leaf's hook ports are constants and nothing of this exists.
  localparam int LW = (LANES > 1) ? $clog2(LANES) : 1;
  logic [RAW-1:0]   bd_row, fi_row;
  logic [LANES-1:0] bd_we_lane;
  logic [CW-1:0]    bd_row_rd, fi_mask_row;
  logic             fi_en;
  generate
    if (DV_HOOKS != 0) begin : g_dvd
      logic [LW-1:0] bd_lane, fi_lane;
      logic [31:0]   fi_shift;
      assign bd_lane     = dv_bd_addr[LW-1:0];
      assign bd_row      = dv_bd_addr[AW-1:LW];
      assign fi_lane     = dv_fi_addr[LW-1:0];
      assign fi_row      = dv_fi_addr[AW-1:LW];
      assign bd_we_lane  = dv_bd_we ? (LANES'(1) << bd_lane) : '0;
      assign fi_shift    = 32'd39 * 32'(fi_lane);
      assign fi_mask_row = CW'(dv_fi_mask) << fi_shift;
      assign fi_en       = dv_fi_or;
      assign dv_bd_rdata = bd_row_rd[39*bd_lane +: 39];
    end else begin : g_nodvd
      assign bd_row = '0; assign fi_row = '0; assign bd_we_lane = '0; assign fi_mask_row = '0; assign fi_en = 1'b0;
      assign dv_bd_rdata = '0;
      logic unused_dv;
      assign unused_dv = ^{dv_bd_we, dv_bd_addr, dv_bd_wdata, dv_fi_or, dv_fi_addr, dv_fi_mask, bd_row_rd};
    end
  endgenerate

  generate
    for (genvar l = 0; l < LANES; l++) begin : g_lane
      ecc39_enc u_enc (.d(wdata[32*l +: 32]), .p(wp[l]), .op(wop[l]));
      ecc39_dec u_dec (.d(rword[l][31:0]), .p(rword[l][37:32]), .op(rword[l][38]), .d_out(rfix[l]), .ce(ce[l]), .ue(ue[l]));
      assign row_wr[39*l +: 39] = {wop[l], wp[l], wdata[32*l +: 32]};
      assign rword[l] = row_rd[39*l +: 39];
      assign rdata[32*l +: 32] = rfix[l];
      assign ce_lane[l] = rvalid && ce[l];
    end
  endgenerate
  // the array (drop 0.40): one leaf, a write-enable group per lane, the row read registered on re
  neo_mem_sp #(.W(CW), .D(ROWS), .G(LANES), .DV_HOOKS(DV_HOOKS)) u_mem (
    .clk(clk),
    .we(wmask & {LANES{we}}), .waddr(wrow), .wdata(row_wr),
    .re(re), .raddr(rrow), .rdata(row_rd),
    .bd_we(bd_we_lane), .bd_addr(bd_row), .bd_wdata({LANES{dv_bd_wdata}}), .bd_rdata(bd_row_rd),
    .fi_en(fi_en), .fi_or(1'b1), .fi_addr(fi_row), .fi_mask(fi_mask_row));
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
