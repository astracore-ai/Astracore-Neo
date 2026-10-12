// abft_checker.sv -- algorithm-based fault tolerance check on an aligned output row:
//   err = valid && (sum_{j<COLS} y[j] != y_chk), with the sum wrapped to PW bits exactly
//   like the array's own accumulators. err_sticky latches until abft_clear.
//   Overhead for a 32-column core: one extra column (3.1 %) and a 32-input adder tree.
//   Coverage: every single-point fault in a MAC, a stored weight or the psum chain of one
//   data or check column flips exactly one side of the equality.
//   Drop 0.42: the sum in two stages (the 33-operand sum and compare were one combinational path from the row's registers,
//   1.45 ns behind the array and 1.64 ns behind the accumulator's drain read on the ASAP7 SS proxy). Stage 1 registers
//   NG = 4 partial sums of ceil(COLS / 4) consecutive columns each, y_chk and valid; stage 2 adds the four, compares and
//   registers err. Alignment: the row presented with valid in cycle t has its verdict on err in cycle t + 2, together with
//   err_valid (valid delayed by two), so a consumer pairs err with its row by counting err_valid pulses (rows keep their
//   order); err_sticky rises in cycle t + 3. The sums wrap at PW bits as before, so the verdict is the same per row.
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
  output logic                 err_valid,
  output logic                 err_sticky
);
  localparam int NG  = 4;                           // partial sums in stage 1
  localparam int CPG = (COLS + NG - 1) / NG;        // columns per partial sum

  // stage 1: the partial sums of the row (column j goes to partial sum j / CPG)
  logic signed [PW-1:0] ps   [NG];
  logic signed [PW-1:0] ps_q [NG];
  logic signed [PW-1:0] chk_q;
  logic                 v_q;
  always_comb begin
    for (int g = 0; g < NG; g++) ps[g] = '0;
    for (int j = 0; j < COLS; j++) ps[j / CPG] = ps[j / CPG] + y[j];
  end

  // stage 2: the four added and compared
  logic signed [PW-1:0] sum;
  always_comb begin
    sum = '0;
    for (int g = 0; g < NG; g++) sum = sum + ps_q[g];
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int g = 0; g < NG; g++) ps_q[g] <= '0;
      chk_q <= '0; v_q <= 1'b0;
      err <= 1'b0; err_valid <= 1'b0;
    end else begin
      for (int g = 0; g < NG; g++) ps_q[g] <= ps[g];
      chk_q     <= y_chk;
      v_q       <= valid;
      err       <= v_q && (sum != chk_q);
      err_valid <= v_q;
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n)          err_sticky <= 1'b0;
    else if (abft_clear) err_sticky <= 1'b0;
    else if (err)        err_sticky <= 1'b1;
  end
endmodule
