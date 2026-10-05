// neo_mac_core.sv -- AstraCore Neo streaming MAC core v0.2 (2026-10-04)
//   One 32x32 weight-stationary INT8 systolic core (1024 MACs) with an ABFT check column.
//   Streaming interface: present one unskewed activation row X[m][0..ROWS-1] per cycle with
//   valid_in; the matching output row Y[m][0..COLS-1] appears on y with valid_out exactly
//   LATENCY = ROWS + COLS cycles later, together with the ABFT verdict for that row.
//   Weight load: hold w_load for ROWS cycles, feeding W[ROWS-1-t][*] (and the check weight
//   sum_j W[ROWS-1-t][j]) on cycle t into the SHADOW registers; then pulse swap_in for one
//   cycle immediately before the first row of the tile. The token travels with the data, so
//   the next tile's shadow load may run while this tile streams (start it >= LATENCY cycles
//   after the token). The check weights are precomputed by the compiler.
//   v0.2 scope: INT8 x INT8 -> INT32, 1-cycle PE, double-buffered weights. Not yet: FP8/INT4
//   modes, 2:4 sparsity, deeper PE pipeline, output requantization (v0.3+). The activation
//   feeder and the accumulator live in neo_mac_core_v02.sv.
module neo_mac_core #(
  parameter int ROWS      = 32,
  parameter int COLS      = 32,
  parameter int XW        = 8,
  parameter int WW        = 8,
  parameter int PW        = 32,
  parameter int WCW       = WW + $clog2(ROWS) + 1,
  parameter int PE_LAT    = 2,                      // psum latency of mac_pe
  parameter int FAULT_ROW = 0,
  parameter int FAULT_COL = 0
)(
  input  logic                  clk,
  input  logic                  rst_n,
  // weight load
  input  logic                  w_load,
  input  logic signed [WW-1:0]  w_in  [COLS],
  input  logic signed [WCW-1:0] wc_in,
  // activation stream in
  input  logic signed [XW-1:0]  x_in  [ROWS],
  input  logic                  valid_in,
  input  logic                  swap_in,     // one-cycle token before the first row of a tile
  // result stream out (aligned)
  output logic signed [PW-1:0]  y     [COLS],
  output logic signed [PW-1:0]  y_chk,
  output logic                  valid_out,
  // safety
  input  logic                  abft_clear,
  output logic                  abft_err,
  output logic                  abft_err_sticky,
  // DV only
  input  logic                  fault_inject
);
  localparam int CT      = COLS + 1;
  localparam int LATENCY = PE_LAT * ROWS + CT - 1;   // = PE_LAT*ROWS + COLS

  logic signed [XW-1:0] x_row  [ROWS];
  logic                 s_row  [ROWS];
  logic                 v_row  [ROWS];                 // valid skewed like the data rows
  logic signed [PW-1:0] p_bot  [CT];
  logic signed [PW-1:0] p_algn [CT];

  skew_in #(.ROWS(ROWS), .XW(XW), .PE_LAT(PE_LAT)) u_skew (
    .clk(clk), .rst_n(rst_n), .x_vec(x_in), .s_in(swap_in), .x_row(x_row), .s_row(s_row));

  systolic_array #(.ROWS(ROWS), .COLS(COLS), .XW(XW), .WW(WW), .PW(PW), .WCW(WCW),
                   .FAULT_ROW(FAULT_ROW), .FAULT_COL(FAULT_COL)) u_array (
    .clk(clk), .rst_n(rst_n), .w_load(w_load), .w_in(w_in), .wc_in(wc_in),
    .x_in(x_row), .s_in(s_row), .v_in(v_row), .fault_inject(fault_inject), .p_out(p_bot));

  generate
    for (genvar r = 0; r < ROWS; r++) begin : g_vskew
      if (r == 0) begin : g_v0
        assign v_row[r] = valid_in;
      end else begin : g_vd
        logic signed [0:0] vq;
        delay_line #(.W(1), .DEPTH(PE_LAT * r)) u_vd (.clk(clk), .rst_n(rst_n), .d(valid_in), .q(vq));
        assign v_row[r] = vq[0];
      end
    end
  endgenerate

  deskew_out #(.CT(CT), .PW(PW)) u_deskew (
    .clk(clk), .rst_n(rst_n), .p_in(p_bot), .p_out(p_algn));

  generate
    for (genvar j = 0; j < COLS; j++) begin : g_y
      assign y[j] = p_algn[j];
    end
  endgenerate
  assign y_chk = p_algn[COLS];

  // valid travels the same LATENCY as the data
  logic valid_pipe [LATENCY];
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int k = 0; k < LATENCY; k++) valid_pipe[k] <= 1'b0;
    end else begin
      valid_pipe[0] <= valid_in;
      for (int k = 1; k < LATENCY; k++) valid_pipe[k] <= valid_pipe[k-1];
    end
  end
  assign valid_out = valid_pipe[LATENCY-1];

  abft_checker #(.COLS(COLS), .PW(PW)) u_abft (
    .clk(clk), .rst_n(rst_n), .valid(valid_out), .y(y), .y_chk(y_chk),
    .abft_clear(abft_clear), .err(abft_err), .err_sticky(abft_err_sticky));
endmodule
