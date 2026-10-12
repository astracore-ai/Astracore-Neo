// abft_checker_fv.sv -- formal harness for rtl/abft_checker (drop 0.41, the two-stage checker) at COLS = 4, PW = 8, every
//   input free: the verdict of the row presented with valid in cycle t appears in cycle t + 2 as err with err_valid, and
//   equals (sum of that row != its y_chk), the sum wrapped to PW bits and formed here as a two-level tree
//   ((y0 + y1) + (y2 + y3)) rather than the RTL's carry-save rows and carry-free compare; err_valid is valid delayed by
//   two; err is never set without err_valid; err_sticky rises the cycle after err, holds, and falls only on abft_clear
//   (clear has priority).
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
  logic err, err_valid, err_sticky;
  abft_checker #(.COLS(COLS), .PW(PW)) u_dut (.clk(clk), .rst_n(rst_n), .valid(valid), .y(y), .y_chk(y_chk),
                                             .abft_clear(abft_clear), .err(err), .err_valid(err_valid), .err_sticky(err_sticky));

  // the reference: the row's verdict computed on entry and delayed two cycles beside its valid
  logic signed [PW-1:0] sum;
  assign sum = (y0 + y1) + (y2 + y3);
  logic exp_err0, exp_err1, exp_err2;               // the verdict, two stages behind
  logic exp_v1, exp_v2;
  logic exp_sticky;                                 // the sticky's one-line model
  assign exp_err0 = valid && (sum != y_chk);
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      exp_err1 <= 1'b0; exp_err2 <= 1'b0; exp_v1 <= 1'b0; exp_v2 <= 1'b0;
    end else begin
      exp_err1 <= exp_err0; exp_err2 <= exp_err1;
      exp_v1   <= valid;    exp_v2   <= exp_v1;
    end
  end
  always_ff @(posedge clk) begin
    if (!rst_n)          exp_sticky <= 1'b0;
    else if (abft_clear) exp_sticky <= 1'b0;
    else if (err)        exp_sticky <= 1'b1;
  end
  always_comb begin
    if (rst_n) begin
      assert (err_valid == exp_v2);
      assert (err == exp_err2);
      assert (!err || err_valid);
      assert (err_sticky == exp_sticky);
    end
  end
endmodule
