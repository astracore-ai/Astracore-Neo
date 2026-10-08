// bank_bist_wrap.sv -- sram_bank with an MBIST engine that owns the ports while active (drop 0.13; row ports since drop 0.28).
module bank_bist_wrap #(
  parameter int DEPTH = 4096, AW = $clog2(DEPTH), BIST_WORDS = DEPTH,
  parameter int LANES = 16, ROWS = DEPTH / LANES, RAW = (ROWS > 1) ? $clog2(ROWS) : 1, DW = 32 * LANES,
  parameter int DV_HOOKS = 0                               // testbench hooks of the bank (drop 0.35)
)(
  input  logic             clk,
  input  logic             rst_n,
  // functional ports (row addressed, lane write mask)
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
  // test
  input  logic             bist_start,
  output logic             bist_active,
  output logic             bist_done,
  output logic             bist_fail,
  output logic             bist_fail_ce,
  output logic [AW-1:0]    bist_fail_addr,         // word address of the first failing lane
  // testbench hooks, passed through to the bank (drop 0.35)
  input  logic             dv_bd_we,
  input  logic [AW-1:0]    dv_bd_addr,
  input  logic [38:0]      dv_bd_wdata,
  output logic [38:0]      dv_bd_rdata,
  input  logic             dv_fi_or,
  input  logic [AW-1:0]    dv_fi_addr,
  input  logic [38:0]      dv_fi_mask
);
  localparam int BROWS = BIST_WORDS / LANES;
  localparam int BRAW  = (BROWS > 1) ? $clog2(BROWS) : 1;
  logic [LANES-1:0] ce_lane;
  logic             b_we, b_re, t_we, t_re;
  logic [RAW-1:0]   b_wrow, b_rrow;
  logic [BRAW-1:0]  t_wrow, t_rrow;
  logic [LANES-1:0] b_wmask, t_wmask;
  logic [DW-1:0]    b_wdata, t_wdata;
  assign b_we    = bist_active ? t_we    : we;
  assign b_wrow  = bist_active ? RAW'(t_wrow) : wrow;
  assign b_wmask = bist_active ? t_wmask : wmask;
  assign b_wdata = bist_active ? t_wdata : wdata;
  assign b_re    = bist_active ? t_re    : re;
  assign b_rrow  = bist_active ? RAW'(t_rrow) : rrow;
  sram_bank #(.DEPTH(DEPTH), .LANES(LANES), .AW(AW), .DV_HOOKS(DV_HOOKS)) u_bank (
    .clk(clk), .rst_n(rst_n), .we(b_we), .wrow(b_wrow), .wmask(b_wmask), .wdata(b_wdata), .re(b_re), .rrow(b_rrow),
    .rdata(rdata), .rvalid(rvalid), .err_clear(err_clear), .ecc_ce_sticky(ecc_ce_sticky), .ecc_ue_sticky(ecc_ue_sticky),
    .ce_lane(ce_lane),
    .dv_bd_we(dv_bd_we), .dv_bd_addr(dv_bd_addr), .dv_bd_wdata(dv_bd_wdata), .dv_bd_rdata(dv_bd_rdata),
    .dv_fi_or(dv_fi_or), .dv_fi_addr(dv_fi_addr), .dv_fi_mask(dv_fi_mask));
  mbist #(.AW(AW), .WORDS(BIST_WORDS), .LANES(LANES)) u_bist (
    .clk(clk), .rst_n(rst_n), .start(bist_start), .active(bist_active), .done(bist_done), .fail(bist_fail), .fail_addr(bist_fail_addr),
    .we(t_we), .wrow(t_wrow), .wmask(t_wmask), .wdata(t_wdata), .re(t_re), .rrow(t_rrow), .rdata(rdata), .rvalid(rvalid),
    .ce_lane(ce_lane), .fail_ce(bist_fail_ce));
endmodule
