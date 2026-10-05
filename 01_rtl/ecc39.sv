// ecc39.sv -- (39,32) SECDED: Hamming (38,32) with six check bits at codeword positions
//   1, 2, 4, 8, 16, 32 and data bits at the other positions 3..38, plus one overall parity bit.
//   ecc39_enc: data -> {op, p[5:0]}.  ecc39_dec: data', p', op' -> corrected data, ce (single
//   error corrected), ue (double error detected). Data bit k sits at the k-th non-power-of-two
//   position; position j holds data bit j - floor(log2 j) - 2.
//   v2 (drop 0.17): every parity and syndrome bit is an XOR-reduction of the codeword masked by a
//   constant, with no loop-carried variables, so every simulator and synthesis tool sees plain logic.
module ecc39_enc (
  input  logic [31:0] d,
  output logic [5:0]  p,
  output logic        op
);
  logic [38:0] cw;                                   // data-only codeword by position (check positions 0)
  logic [38:0] mask [6];                             // positions contributing to check bit i
  generate
    for (genvar j = 0; j < 39; j++) begin : g_cw
      if (j == 0 || (j & (j - 1)) == 0) begin : g_chk
        assign cw[j] = 1'b0;
      end else begin : g_dat
        assign cw[j] = d[j - ($clog2(j + 1) - 1) - 2];
      end
    end
    for (genvar i = 0; i < 6; i++) begin : g_p
      for (genvar j = 0; j < 39; j++) begin : g_m
        assign mask[i][j] = (j >= 3) && ((j & (1 << i)) != 0) && ((j & (j - 1)) != 0);
      end
      assign p[i] = ^(cw & mask[i]);
    end
  endgenerate
  assign op = (^d) ^ (^p);
endmodule

module ecc39_dec (
  input  logic [31:0] d,
  input  logic [5:0]  p,
  input  logic        op,
  output logic [31:0] d_out,
  output logic        ce,
  output logic        ue
);
  logic [38:0] cw;                                   // received codeword by position
  logic [38:0] mask [6];
  logic [5:0]  s;                                    // syndrome = erroneous position (0 = none)
  logic        pe;                                   // overall parity mismatch
  generate
    for (genvar j = 0; j < 39; j++) begin : g_cw
      if (j == 0) begin : g_0
        assign cw[j] = 1'b0;
      end else if ((j & (j - 1)) == 0) begin : g_chk
        assign cw[j] = p[$clog2(j + 1) - 1];
      end else begin : g_dat
        assign cw[j] = d[j - ($clog2(j + 1) - 1) - 2];
      end
    end
    for (genvar i = 0; i < 6; i++) begin : g_s
      for (genvar j = 0; j < 39; j++) begin : g_m
        assign mask[i][j] = (j >= 1) && ((j & (1 << i)) != 0);
      end
      assign s[i] = ^(cw & mask[i]);
    end
    for (genvar j = 3; j <= 38; j++) begin : g_fix
      if ((j & (j - 1)) != 0) begin : g_d
        localparam int K = j - ($clog2(j + 1) - 1) - 2;   // data bit held at position j
        assign d_out[K] = d[K] ^ (ce & (s == 6'(j)));
      end
    end
  endgenerate
  assign pe = ((^d) ^ (^p) ^ op) != 1'b0;
  assign ce = pe;                                    // any single error: data, check bit, or the overall parity bit itself
  assign ue = (s != 6'd0) && !pe;                    // two errors: syndrome set, overall parity consistent
endmodule
