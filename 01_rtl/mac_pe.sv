// mac_pe.sv -- weight-stationary processing element, pipelined multiply-accumulate (v0.3).
//   x flows left -> right (one register per PE); the partial-sum path is a two-stage pipeline
//   (stage 1: product and delayed psum, stage 2: add), so PE_LAT = 2 and the row skew in the
//   array is 2 cycles per row. This is the pipeline depth sized for the 2.0 GHz worst-case
//   corner in N5-class silicon (8x8 signed multiply in one stage, 32-bit add in the other).
//   Double-buffered weight: w_load shifts the SHADOW register down the column (w_out = w_sh),
//   and the swap token s_in (which travels with the data: skewed per row, +1 per column)
//   copies shadow -> active one cycle before the first row of the new tile arrives. So the
//   next weight tile loads while the current one streams, with no drain between tiles.
// fault_inject: adds +1 to this cycle's product (a single-point MAC fault) -- for safety
// verification of the ABFT checker only; tied to 0 on every PE except the one selected
// by FAULT_ROW/FAULT_COL in systolic_array. Remove or fuse off before tape-out.
module mac_pe #(
  parameter int XW = 8,    // activation width (signed)
  parameter int WW = 8,    // weight width (signed); wider for the ABFT check column
  parameter int PW = 32    // partial-sum width (signed)
)(
  input  logic                 clk,
  input  logic                 rst_n,
  input  logic                 w_load,
  input  logic signed [WW-1:0] w_in,
  output logic signed [WW-1:0] w_out,      // shadow weight, for the PE below (shift chain)
  input  logic                 s_in,       // swap token (shadow -> active)
  output logic                 s_out,
  input  logic                 v_in,       // valid, travels with x; datapath registers hold when 0 (clock gating)
  output logic                 v_out,
  input  logic signed [XW-1:0] x_in,
  output logic signed [XW-1:0] x_out,
  input  logic signed [PW-1:0] p_in,
  output logic signed [PW-1:0] p_out,
  input  logic                 fault_inject
);
  localparam int PE_LAT = 2;                        // psum latency through this PE

  logic signed [WW-1:0] w_q;      // active weight
  logic signed [WW-1:0] w_sh;     // shadow weight
  logic signed [PW-1:0] prod_q;   // stage 1: product
  logic signed [PW-1:0] p_in_q;   // stage 1: incoming psum, delayed to meet the product
  logic                 inj_q;
  logic                 v_q;    // stage 1: fault hook, delayed with the product it affects
  logic signed [PW-1:0] inj;

  assign inj   = inj_q ? PW'(1) : PW'(0);
  assign w_out = w_sh;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      w_q    <= '0;
      w_sh   <= '0;
      x_out  <= '0;
      s_out  <= 1'b0;
      prod_q <= '0;
      p_in_q <= '0;
      inj_q  <= 1'b0;
      p_out  <= '0;
      v_out  <= 1'b0;
      v_q    <= 1'b0;
    end else begin
      if (w_load) w_sh <= w_in;
      if (s_in)   w_q  <= w_sh;
      s_out  <= s_in;
      v_out  <= v_in;
      v_q    <= v_in;
      if (v_in) begin                               // product stage: enabled by the incoming valid
        x_out  <= x_in;
        prod_q <= x_in * w_q;                       // all operands signed -> sign-extended product
        p_in_q <= p_in;
        inj_q  <= fault_inject;
      end
      if (v_q) begin                                // add stage: enabled by the valid one cycle later
        p_out  <= p_in_q + prod_q + inj;
      end
    end
  end
endmodule
