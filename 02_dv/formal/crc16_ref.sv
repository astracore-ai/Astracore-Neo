/* verilator lint_off DECLFILENAME */        // two modules, crc16_word_ref and crc16_beat_ref, in one file by design
// crc16_ref.sv -- the golden serial CRC-16-CCITT of the formal job (drop 0.41): rtl/crc16_word.sv and rtl/crc16_beat.sv
//   as they stood from drop 0.29 to drop 0.40, renamed crc16_word_ref / crc16_beat_ref, the bodies verbatim. This file
//   never changes: crc16_beat.sby proves rtl/crc16_beat equal to crc16_beat_ref for every crc_in, every beat and every
//   word count n <= BW, so whatever form crc16_beat takes (the parallel matrix form of drop 0.41 and after) is checked
//   against the serial definition on every push.
//   crc16_word_ref: CRC-16-CCITT (polynomial 0x1021) update over one 32-bit word, MSB first.
//   crc16_beat_ref: BW crc16_word_ref stages chained in word order; crc_out is the state after the first n words (n = 0
//   returns crc_in, so a beat's unused word positions are never accumulated).
module crc16_word_ref (
  input  logic [15:0] crc_in,
  input  logic [31:0] word,
  output logic [15:0] crc_out
);
  logic [15:0] c;
  logic        fb;
  always_comb begin
    c = crc_in;
    for (int b = 31; b >= 0; b--) begin
      fb = c[15] ^ word[b];
      c  = {c[14:0], 1'b0} ^ (fb ? 16'h1021 : 16'h0000);
    end
    crc_out = c;
  end
endmodule

module crc16_beat_ref #(
  parameter int BW = 16,
  parameter int CW = $clog2(BW + 1)            // word count 0..BW
)(
  input  logic [15:0]      crc_in,
  input  logic [32*BW-1:0] words,              // word i at [32*i +: 32]
  input  logic [CW-1:0]    n,                  // words to accumulate, 0..BW
  output logic [15:0]      crc_out
);
  logic [15:0] c [BW+1];
  assign c[0] = crc_in;
  generate
    for (genvar i = 0; i < BW; i++) begin : g_s
      crc16_word_ref u_s (.crc_in(c[i]), .word(words[32*i +: 32]), .crc_out(c[i+1]));
    end
  endgenerate
  assign crc_out = c[n];
endmodule
