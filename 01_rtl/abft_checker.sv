// abft_checker.sv -- algorithm-based fault tolerance check on an aligned output row:
//   err = valid && (sum_{j<COLS} y[j] != y_chk), with the sum wrapped to PW bits exactly
//   like the array's own accumulators. err_sticky latches until abft_clear.
//   Overhead for a 32-column core: one extra column (3.1 %) and a 32-input adder tree.
//   Coverage: every single-point fault in a MAC, a stored weight or the psum chain of one
//   data or check column flips exactly one side of the equality.
//   Drop 0.42: two stages, no carry-propagate adder (the 33-operand sum and compare were one combinational path from the
//   row's registers, 1.45 ns behind the array and 1.64 ns behind the accumulator's drain read on the ASAP7 SS proxy).
//   Stage 1 reduces each of NG = 2 groups of ceil(COLS / 2) consecutive columns to a sum row and a carry row (3:2
//   compressors, rows taken three at a time in order) and registers the 2 NG rows with y_chk and valid. Stage 2 reduces
//   them to two rows S and C and compares S + C with y_chk without adding: S + C = K (mod 2^PW) exactly when
//   S ^ C ^ K == G << 1 with G = (S & C) | ((S | C) & ~K) -- bit i's carry-in must be S ^ C ^ K at bit i for the sum bit
//   to be K's, and G is then bit i's carry-out (constant depth, no carry chain); err is registered. Alignment: the row
//   presented with valid in cycle t has its verdict on err in cycle t + 2, together with err_valid (valid delayed by two),
//   so a consumer pairs err with its row by counting err_valid pulses (rows keep their order); err_sticky rises in cycle
//   t + 3. The sums wrap at PW bits as before, so the verdict is the same per row.
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
  localparam int NG  = 2;                           // column groups in stage 1
  localparam int CPG = (COLS + NG - 1) / NG;        // columns per group
  localparam int R1  = (CPG > 2) ? CPG : 2;         // a group's rows (zeros past its columns)
  localparam int N1  = R1 + 2 * (R1 - 2);           // with the rows its R1 - 2 compressors make
  localparam int N2  = 2 * NG + 2 * (2 * NG - 2);   // stage 2: the 2 NG registered rows and the compressors' rows

  // stage 1: group g's rows are its columns; compressor i takes rows 3i, 3i + 1, 3i + 2 and makes rows R1 + 2i (sum)
  // and R1 + 2i + 1 (carry); the last two rows are the group's sum and carry
  logic [PW-1:0] t1 [NG][N1];
  always_comb begin
    for (int g = 0; g < NG; g++) begin
      for (int k = 0; k < R1; k++)
        t1[g][k] = ((k < CPG) && (g * CPG + k < COLS)) ? y[(g * CPG + k) % COLS] : '0;
      for (int i = 0; i < R1 - 2; i++) begin
        t1[g][R1 + 2*i]     = t1[g][3*i] ^ t1[g][3*i + 1] ^ t1[g][3*i + 2];
        t1[g][R1 + 2*i + 1] = ((t1[g][3*i] & t1[g][3*i + 1]) | (t1[g][3*i] & t1[g][3*i + 2]) |
                               (t1[g][3*i + 1] & t1[g][3*i + 2])) << 1;
      end
    end
  end
  logic [PW-1:0] r_q [2*NG];                        // the groups' sum and carry rows
  logic [PW-1:0] chk_q;
  logic          v_q;

  // stage 2: the 2 NG rows reduced to S and C the same way, then the carry-free compare with y_chk
  logic [PW-1:0] t2 [N2];
  logic [PW-1:0] g2, e2;                            // the carries the equality implies, and the bits that disagree
  always_comb begin
    for (int k = 0; k < 2 * NG; k++) t2[k] = r_q[k];
    for (int i = 0; i < 2 * NG - 2; i++) begin
      t2[2*NG + 2*i]     = t2[3*i] ^ t2[3*i + 1] ^ t2[3*i + 2];
      t2[2*NG + 2*i + 1] = ((t2[3*i] & t2[3*i + 1]) | (t2[3*i] & t2[3*i + 2]) | (t2[3*i + 1] & t2[3*i + 2])) << 1;
    end
    g2 = (t2[N2-2] & t2[N2-1]) | ((t2[N2-2] | t2[N2-1]) & (chk_q ^ {PW{1'b1}}));
    e2 = t2[N2-2] ^ t2[N2-1] ^ chk_q ^ (g2 << 1);
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int k = 0; k < 2 * NG; k++) r_q[k] <= '0;
      chk_q <= '0; v_q <= 1'b0;
      err <= 1'b0; err_valid <= 1'b0;
    end else begin
      for (int g = 0; g < NG; g++) begin
        r_q[2*g]     <= t1[g][N1-2];
        r_q[2*g + 1] <= t1[g][N1-1];
      end
      chk_q     <= y_chk;
      v_q       <= valid;
      err       <= v_q && (e2 != '0);
      err_valid <= v_q;
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n)          err_sticky <= 1'b0;
    else if (abft_clear) err_sticky <= 1'b0;
    else if (err)        err_sticky <= 1'b1;
  end
endmodule
