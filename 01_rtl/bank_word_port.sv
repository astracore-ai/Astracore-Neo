// bank_word_port.sv -- one-word view of the 512-bit bank (drop 0.28, stage 1 of the wide port).
//   The tile interface still moves one 32-bit word per cycle on its bank side; this adapter turns a word write into a
//   row write with one lane selected and a word read into a row read with the lane picked when the data returns, so
//   the interface runs unchanged on the wide bank. Stage 2 replaces it with the interface's own 16-word paths.
module bank_word_port #(
  parameter int DEPTH = 4096, AW = $clog2(DEPTH), LANES = 16,
  parameter int ROWS = DEPTH / LANES, RAW = (ROWS > 1) ? $clog2(ROWS) : 1, DW = 32 * LANES, LW = $clog2(LANES)
)(
  input  logic             clk,
  input  logic             rst_n,
  // word side
  input  logic             we,
  input  logic [AW-1:0]    waddr,
  input  logic [31:0]      wdata,
  input  logic             re,
  input  logic [AW-1:0]    raddr,
  output logic [31:0]      rdata,
  // row side
  output logic             r_we,
  output logic [RAW-1:0]   r_wrow,
  output logic [LANES-1:0] r_wmask,
  output logic [DW-1:0]    r_wdata,
  output logic             r_re,
  output logic [RAW-1:0]   r_rrow,
  input  logic [DW-1:0]    r_rdata
);
  logic [LW-1:0] lane_d;
  logic [DW-1:0] shifted;
  assign r_we    = we;
  assign r_wrow  = waddr[AW-1:LW];
  assign r_wmask = LANES'(1) << waddr[LW-1:0];
  assign r_wdata = {LANES{wdata}};
  assign r_re    = re;
  assign r_rrow  = raddr[AW-1:LW];
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) lane_d <= '0;
    else if (re) lane_d <= raddr[LW-1:0];
  end
  assign shifted = r_rdata >> (32 * lane_d);
  assign rdata   = shifted[31:0];
endmodule
