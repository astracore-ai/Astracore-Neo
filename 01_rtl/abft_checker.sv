// abft_checker.sv -- algorithm-based fault tolerance check on an aligned output row:
//   err = valid && (sum_{j<COLS} y[j] != y_chk), with the sum wrapped to PW bits exactly
//   like the array's own accumulators. err_sticky latches until abft_clear.
//   Overhead for a 32-column core: one extra column (3.1 %) and a 32-input adder tree.
//   Coverage: every single-point fault in a MAC, a stored weight or the psum chain of one
//   data or check column flips exactly one side of the equality.
module abft_checker #(
  parameter int COLS = 32,
  parameter int PW   = 32
)(
  input  logic                 clk,
  input  logic                 rst_n,
  input  logic                 valid,
  input  logic signed [PW-1:0] y     [COLS],
  input  logic signed [PW-1:0] y_chk,
  input  logic                 abft_clear,
  output logic                 err,
  output logic                 err_sticky
);
  logic signed [PW-1:0] sum;
  always_comb begin
    sum = '0;
    for (int j = 0; j < COLS; j++) sum = sum + y[j];
  end
  assign err = valid && (sum != y_chk);

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n)          err_sticky <= 1'b0;
    else if (abft_clear) err_sticky <= 1'b0;
    else if (err)        err_sticky <= 1'b1;
  end
endmodule
