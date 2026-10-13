/* verilator lint_off DECLFILENAME */        // two modules, ecc39_enc and ecc39_dec, in one file by design
// ecc39.sv -- (39,32) SECDED: Hamming (38,32) with six check bits at codeword positions
//   1, 2, 4, 8, 16, 32 and data bits at the other positions 3..38, plus one overall parity bit.
//   ecc39_enc: data -> {op, p[5:0]}.  ecc39_dec: data', p', op' -> corrected data, ce (single
//   error corrected), ue (double error detected). Data bit k sits at the k-th non-power-of-two
//   position; position j holds data bit j - floor(log2 j) - 2.
//   v3 (drop 0.17): every packed vector has exactly one driver (one always_comb), no loop-carried
//   accumulation across processes, no per-bit continuous assignments.
//   v4 (drop 0.43): the position masks are constants (localparam), no longer always_comb blocks that read no signal --
//   CI's Icarus 12 warns "always_comb process has no sensitivities" on those and runs them only at time zero; the
//   masks are the same bits: encoder mask i = the data positions j (3..38, not a power of two) with bit i of j set,
//   decoder mask i = every position 1..38 with bit i set.
module ecc39_enc (
  input  logic [31:0] d,
  output logic [5:0]  p,
  output logic        op
);
  localparam logic [38:0] EMASK0 = 39'h2aaaaaaaa8;  // positions contributing to check bit i (data positions only)
  localparam logic [38:0] EMASK1 = 39'h4cccccccc8;
  localparam logic [38:0] EMASK2 = 39'h70f0f0f0e0;
  localparam logic [38:0] EMASK3 = 39'h00ff00fe00;
  localparam logic [38:0] EMASK4 = 39'h00fffe0000;
  localparam logic [38:0] EMASK5 = 39'h7e00000000;
  logic [38:0] cw;                                   // data-only codeword by position
  always_comb begin
    int k;
    cw = '0;
    k = 0;
    for (int j = 3; j <= 38; j++) begin
      if ((j & (j - 1)) != 0) begin cw[j] = d[k]; k = k + 1; end
    end
  end
  always_comb begin
    p[0] = ^(cw & EMASK0);
    p[1] = ^(cw & EMASK1);
    p[2] = ^(cw & EMASK2);
    p[3] = ^(cw & EMASK3);
    p[4] = ^(cw & EMASK4);
    p[5] = ^(cw & EMASK5);
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
  localparam logic [38:0] DMASK0 = 39'h2aaaaaaaaa;  // positions 1..38 with bit i set (check bit i included)
  localparam logic [38:0] DMASK1 = 39'h4ccccccccc;
  localparam logic [38:0] DMASK2 = 39'h70f0f0f0f0;
  localparam logic [38:0] DMASK3 = 39'h00ff00ff00;
  localparam logic [38:0] DMASK4 = 39'h00ffff0000;
  localparam logic [38:0] DMASK5 = 39'h7f00000000;
  logic [38:0] cw;                                   // received codeword by position
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
    s[0] = ^(cw & DMASK0);
    s[1] = ^(cw & DMASK1);
    s[2] = ^(cw & DMASK2);
    s[3] = ^(cw & DMASK3);
    s[4] = ^(cw & DMASK4);
    s[5] = ^(cw & DMASK5);
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
