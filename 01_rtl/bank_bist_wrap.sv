// bank_bist_wrap.sv -- sram_bank with an MBIST engine that owns the ports while active (drop 0.13).
module bank_bist_wrap #(parameter int DEPTH = 4096, AW = $clog2(DEPTH), BIST_WORDS = DEPTH) (
  input  logic          clk,
  input  logic          rst_n,
  // functional ports
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
  // test
  input  logic          bist_start,
  output logic          bist_active,
  output logic          bist_done,
  output logic          bist_fail,
  output logic          bist_fail_ce,
  output logic [AW-1:0] bist_fail_addr
);
  logic ce_now, ue_now;
  logic          b_we, b_re, t_we, t_re;
  logic [AW-1:0] b_waddr, b_raddr, t_waddr, t_raddr;
  logic [31:0]   b_wdata, t_wdata;
  assign b_we    = bist_active ? t_we    : we;
  assign b_waddr = bist_active ? t_waddr : waddr;
  assign b_wdata = bist_active ? t_wdata : wdata;
  assign b_re    = bist_active ? t_re    : re;
  assign b_raddr = bist_active ? t_raddr : raddr;
  sram_bank #(.DEPTH(DEPTH), .AW(AW)) u_bank (
    .clk(clk), .rst_n(rst_n), .we(b_we), .waddr(b_waddr), .wdata(b_wdata), .re(b_re), .raddr(b_raddr),
    .rdata(rdata), .rvalid(rvalid), .err_clear(err_clear), .ecc_ce_sticky(ecc_ce_sticky), .ecc_ue_sticky(ecc_ue_sticky),
    .ecc_ce_now(ce_now), .ecc_ue_now(ue_now));
  mbist #(.AW(AW), .WORDS(BIST_WORDS)) u_bist (
    .clk(clk), .rst_n(rst_n), .start(bist_start), .active(bist_active), .done(bist_done), .fail(bist_fail), .fail_addr(bist_fail_addr),
    .we(t_we), .waddr(t_waddr), .wdata(t_wdata), .re(t_re), .raddr(t_raddr), .rdata(rdata), .rvalid(rvalid),
    .ce_now(ce_now), .fail_ce(bist_fail_ce));
endmodule
