// ecc39_fv.sv -- formal harness for the (39,32) SECDED pair of rtl/ecc39.sv (drop 0.41): one ecc39_enc encodes a free
//   data word d into the stored word {op, p[5:0], d[31:0]} (39 bits, as sram_bank and wbuf_mem keep a lane) and three
//   ecc39_dec read it back: as written, with one bit flipped at a free position a1, and with two bits flipped at free
//   distinct positions a2 != b2 (positions 0..38, bit 38 the overall parity). Asserted for every d and every position:
//     clean      d_out == d, ce = 0, ue = 0
//     one flip   d_out == d, ce = 1, ue = 0      (a flipped check or parity bit included: the data is untouched)
//     two flips  ue = 1, ce = 0                  (detected, never reported as corrected)
//   Combinational: each assertion is one SAT problem over the 2^32 data words and the 39 (39 * 38 / 2) positions.
module ecc39_fv (
  input  logic [31:0] d,
  input  logic [5:0]  a1,                          // the single flip's position
  input  logic [5:0]  a2,                          // the double flip's positions
  input  logic [5:0]  b2
);
  logic [5:0]  p;
  logic        op;
  ecc39_enc u_enc (.d(d), .p(p), .op(op));

  logic [38:0] cw, r0, r1, r2;
  assign cw = {op, p, d};
  assign r0 = cw;
  assign r1 = cw ^ (39'd1 << a1);
  assign r2 = cw ^ (39'd1 << a2) ^ (39'd1 << b2);

  logic [31:0] d0, d1, d2;
  logic        ce0, ue0, ce1, ue1, ce2, ue2;
  ecc39_dec u_dec0 (.d(r0[31:0]), .p(r0[37:32]), .op(r0[38]), .d_out(d0), .ce(ce0), .ue(ue0));
  ecc39_dec u_dec1 (.d(r1[31:0]), .p(r1[37:32]), .op(r1[38]), .d_out(d1), .ce(ce1), .ue(ue1));
  ecc39_dec u_dec2 (.d(r2[31:0]), .p(r2[37:32]), .op(r2[38]), .d_out(d2), .ce(ce2), .ue(ue2));
  logic [31:0] unused_d2;                          // a double error's data is not corrected; not asserted
  assign unused_d2 = d2;

  // one assertion per data bit: a failing bit is then a small BDD of its own (a vector compare would OR them)
  always_comb begin
    assume (a1 < 6'd39);
    assume (a2 < 6'd39);
    assume (b2 < 6'd39);
    assume (a2 != b2);
    for (int k = 0; k < 32; k++) assert (d0[k] == d[k]);     // clean
    assert (!ce0);
    assert (!ue0);
    for (int k = 0; k < 32; k++) assert (d1[k] == d[k]);     // one flip: corrected
    assert (ce1);
    assert (!ue1);
    assert (ue2);                                  // two flips: detected
    assert (!ce2);
  end
endmodule
