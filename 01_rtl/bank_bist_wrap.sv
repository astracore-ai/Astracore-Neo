// bank_bist_wrap.sv -- sram_bank with an MBIST engine that owns the ports while active (drop 0.13; row ports since drop 0.28).
//   Drop 0.44 (A4): the engine's broadcast registered. The engine's row, enables, mask and data (t_*) go through a
//   register (tq_*) before the port mux, and the mux's select is a registered copy of the engine's active per group of
//   four lanes (tq_act, each copy's D through its own output so the synthesis keeps them: vec_unit's idiom) -- the
//   placer puts each lane group's copy and its data flops beside the group, so the only long wires are from flops with a
//   whole cycle in front of the lane's encoder and the macro's setup. The routed bank of drop 0.43 (pnr_bank_bist_wrap.md,
//   run 194: 16 lane macros three per row in a 1.2 mm core) missed 1,250 ps by 1,589 ps register to register on
//   `u_bist/elem[1]_reg` into lane 12 -- the engine's background data behind the element decode, a millimetre of wire
//   and the lane's SECDED encoder in one cycle. The engine sees its read data two cycles after S_R (mbist.sv waits for
//   rvalid); the functional port is unchanged (the mux stays on it until the engine's first access is registered, and a
//   cycle after its last).
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
  // the broadcast register (drop 0.44) and the mux's select, a copy per group of four lanes (copy 0 for the row and the enables)
  localparam int NG = (LANES + 3) / 4;
  logic             tq_we, tq_re;
  logic [BRAW-1:0]  tq_wrow, tq_rrow;
  logic [LANES-1:0] tq_wmask;
  logic [DW-1:0]    tq_wdata;
  logic [NG-1:0]    tq_act;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      tq_we <= 1'b0; tq_re <= 1'b0; tq_wrow <= '0; tq_rrow <= '0; tq_wmask <= '0; tq_wdata <= '0; tq_act <= '0;
    end else begin
      tq_we <= t_we; tq_re <= t_re; tq_wrow <= t_wrow; tq_rrow <= t_rrow; tq_wmask <= t_wmask; tq_wdata <= t_wdata;
      for (int g = 0; g < NG; g++) tq_act[g] <= tq_act[g] ^ (tq_act[g] ^ bist_active);
    end
  end
  assign b_we    = tq_act[0] ? tq_we    : we;
  assign b_wrow  = tq_act[0] ? RAW'(tq_wrow) : wrow;
  assign b_re    = tq_act[0] ? tq_re    : re;
  assign b_rrow  = tq_act[0] ? RAW'(tq_rrow) : rrow;
  always_comb begin
    for (int l = 0; l < LANES; l++) begin
      b_wmask[l]            = tq_act[l / 4] ? tq_wmask[l] : wmask[l];
      b_wdata[32*l +: 32]   = tq_act[l / 4] ? tq_wdata[32*l +: 32] : wdata[32*l +: 32];
    end
  end
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
