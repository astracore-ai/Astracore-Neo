// ecc39.sv -- (39,32) SECDED: Hamming (38,32) with six check bits at codeword positions
//   1, 2, 4, 8, 16, 32 and data bits at the other positions 3..38, plus one overall parity bit.
//   ecc39_enc: data -> {op, p[5:0]}.  ecc39_dec: data', p', op' -> corrected data, ce (single
//   error corrected), ue (double error detected). Data bit k sits at the k-th non-power-of-two
//   position; position j holds data bit j - floor(log2 j) - 2.
module ecc39_enc (
  input  logic [31:0] d,
  output logic [5:0]  p,
  output logic        op
);
  logic cw [39];                       // data-only codeword by position (check positions 0)
  logic pb [6];
  generate
    for (genvar j = 0; j < 39; j++) begin : g_cw
      if (j == 0 || (j & (j - 1)) == 0) begin : g_chk
        assign cw[j] = 1'b0;
      end else begin : g_dat
        assign cw[j] = d[j - ($clog2(j + 1) - 1) - 2];
      end
    end
    for (genvar i = 0; i < 6; i++) begin : g_p
      logic b;
      always_comb begin
        b = 1'b0;
        for (int j = 3; j <= 38; j++) begin
          if (((j & (1 << i)) != 0) && ((j & (j - 1)) != 0)) b = b ^ cw[j];
        end
      end
      assign pb[i] = b;
    end
  endgenerate
  assign p  = {pb[5], pb[4], pb[3], pb[2], pb[1], pb[0]};
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
  logic cw [39];                       // received codeword by position
  logic sb [6];
  logic dob [32];
  logic [5:0] s;                       // syndrome = erroneous position (0 = none)
  logic       pe;                      // overall parity mismatch
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
      logic b;
      always_comb begin
        b = 1'b0;
        for (int j = 1; j <= 38; j++) begin
          if ((j & (1 << i)) != 0) b = b ^ cw[j];
        end
      end
      assign sb[i] = b;
    end
  endgenerate
  assign s  = {sb[5], sb[4], sb[3], sb[2], sb[1], sb[0]};
  assign pe = ((^d) ^ (^p) ^ op) != 1'b0;
  assign ce = pe;                                 // any single error: data, check bit, or the overall parity bit itself
  assign ue = (s != 6'd0) && !pe;                // two errors: syndrome set, overall parity consistent
  generate
    for (genvar j = 3; j <= 38; j++) begin : g_fix
      if ((j & (j - 1)) != 0) begin : g_d
        localparam int K = j - ($clog2(j + 1) - 1) - 2;   // data bit held at position j
        assign dob[K] = d[K] ^ (ce & (s == j));
      end
    end
  endgenerate
  assign d_out = {dob[31], dob[30], dob[29], dob[28], dob[27], dob[26], dob[25], dob[24], dob[23], dob[22], dob[21], dob[20], dob[19], dob[18], dob[17], dob[16], dob[15], dob[14], dob[13], dob[12], dob[11], dob[10], dob[9], dob[8], dob[7], dob[6], dob[5], dob[4], dob[3], dob[2], dob[1], dob[0]};
endmodule
