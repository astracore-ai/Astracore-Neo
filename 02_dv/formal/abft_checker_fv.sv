// abft_checker_fv.sv -- formal harness for rtl/abft_checker (drop 0.41) at COLS = 4, PW = 8, every input free:
//   err == valid && (sum of the row != y_chk), the sum wrapped to PW bits and formed here as a two-level tree
//   ((y0 + y1) + (y2 + y3)) rather than the RTL's chain, and err_sticky rises the cycle after err, holds, and falls
//   only on abft_clear (clear has priority). This pins the checker's interface -- err combinational from valid -- for
//   the pipelined checker of drop 0.42: a version with an err_valid output changes this harness with it.
module abft_checker_fv #(
  parameter int COLS = 4,
  parameter int PW   = 8
)(
  input  logic                 clk,
  input  logic                 valid,
  input  logic signed [PW-1:0] y0, y1, y2, y3,
  input  logic signed [PW-1:0] y_chk,
  input  logic                 abft_clear
);
  logic rst_n = 1'b0;                              // reset in the first cycle, released for good
  always_ff @(posedge clk) rst_n <= 1'b1;

  logic signed [PW-1:0] y [COLS];
  assign y[0] = y0;
  assign y[1] = y1;
  assign y[2] = y2;
  assign y[3] = y3;
  logic err, err_sticky;
  abft_checker #(.COLS(COLS), .PW(PW)) u_dut (.clk(clk), .rst_n(rst_n), .valid(valid), .y(y), .y_chk(y_chk),
                                             .abft_clear(abft_clear), .err(err), .err_sticky(err_sticky));

  logic signed [PW-1:0] sum;
  assign sum = (y0 + y1) + (y2 + y3);
  logic exp_sticky;                                 // the sticky's one-line model
  always_ff @(posedge clk) begin
    if (!rst_n)          exp_sticky <= 1'b0;
    else if (abft_clear) exp_sticky <= 1'b0;
    else if (err)        exp_sticky <= 1'b1;
  end
  always_comb begin
    assert (err == (valid && (sum != y_chk)));
    if (rst_n) assert (err_sticky == exp_sticky);
  end
endmodule
