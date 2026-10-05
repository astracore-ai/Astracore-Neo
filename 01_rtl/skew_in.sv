// skew_in.sv -- delays activation row i (and the swap token) by i*PE_LAT cycles so that partial
// sums line up in a weight-stationary array whose PEs have a PE_LAT-cycle psum path
// (row 0 is a pass-through).
module skew_in #(
  parameter int ROWS   = 32,
  parameter int XW     = 8,
  parameter int PE_LAT = 2
)(
  input  logic                 clk,
  input  logic                 rst_n,
  input  logic signed [XW-1:0] x_vec [ROWS],   // unskewed: X[m][0..ROWS-1] in one cycle
  input  logic                 s_in,           // swap token, one cycle before row 0 of a tile
  output logic signed [XW-1:0] x_row [ROWS],   // skewed: row i delayed by i cycles
  output logic                 s_row [ROWS]
);
  generate
    for (genvar i = 0; i < ROWS; i++) begin : g_row
      delay_line #(.W(XW), .DEPTH(i*PE_LAT)) u_dl (.clk(clk), .rst_n(rst_n), .d(x_vec[i]), .q(x_row[i]));
      delay_line #(.W(1),  .DEPTH(i*PE_LAT)) u_ds (.clk(clk), .rst_n(rst_n), .d(s_in),     .q(s_row[i]));
    end
  endgenerate
endmodule
