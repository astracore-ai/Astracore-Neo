// ecc39.sv -- (39,32) SECDED: Hamming (38,32) with six check bits at codeword positions
//   1, 2, 4, 8, 16, 32 and data bits at the other positions 3..38, plus one overall parity bit.
//   ecc39_enc: data -> {op, p[5:0]}.  ecc39_dec: data', p', op' -> corrected data, ce (single
//   error corrected), ue (double error detected). Data bit k sits at the k-th non-power-of-two
//   position; position j holds data bit j - floor(log2 j) - 2.
//   v3 (drop 0.17): every packed vector has exactly one driver (one always_comb), no loop-carried
//   accumulation across processes, no per-bit continuous assignments.
module ecc39_enc (
  input  logic [31:0] d,
  output logic [5:0]  p,
  output logic        op
);
  logic [38:0] cw;                                   // data-only codeword by position
  logic [38:0] mask [6];                             // positions contributing to check bit i
  always_comb begin
    int k;
    cw = '0;
    k = 0;
    for (int j = 3; j <= 38; j++) begin
      if ((j & (j - 1)) != 0) begin cw[j] = d[k]; k = k + 1; end
    end
  end
  always_comb begin
    for (int i = 0; i < 6; i++)
      for (int j = 0; j < 39; j++) mask[i][j] = (j >= 3) && ((j & (1 << i)) != 0) && ((j & (j - 1)) != 0);
  end
  always_comb begin
    for (int i = 0; i < 6; i++) p[i] = ^(cw & mask[i]);
  end
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
  logic        pe;
  always_comb begin
    int k;
    cw = '0;
    k = 0;
    for (int j = 1; j <= 38; j++) begin
      if ((j & (j - 1)) == 0) cw[j] = p[$clog2(j + 1) - 1];
      else begin cw[j] = d[k]; k = k + 1; end
    end
  end
  always_comb begin
    for (int i = 0; i < 6; i++)
      for (int j = 0; j < 39; j++) mask[i][j] = (j >= 1) && ((j & (1 << i)) != 0);
  end
  always_comb begin
    for (int i = 0; i < 6; i++) s[i] = ^(cw & mask[i]);
  end
  assign pe = ((^d) ^ (^p) ^ op) != 1'b0;
  assign ce = pe;                                    // any single error: data, check bit, or the overall parity bit
  assign ue = (s != 6'd0) && !pe;                    // two errors: syndrome set, overall parity consistent
  always_comb begin
    int k;
    d_out = d;
    k = 0;
    for (int j = 3; j <= 38; j++) begin
      if ((j & (j - 1)) != 0) begin
        if (ce && (s == 6'(j))) d_out[k] = ~d[k];
        k = k + 1;
      end
    end
  end
endmodule
