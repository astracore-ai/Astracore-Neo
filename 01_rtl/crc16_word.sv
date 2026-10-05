// crc16_word.sv -- CRC-16-CCITT (polynomial 0x1021) update over one 32-bit word, MSB first.
//   Combinational: crc_out = step(crc_in, word). Used for end-to-end protection of every
//   message the tile interface sends (sender accumulates over the data words and appends a CRC
//   flit; receiver accumulates over the words it stores and compares).
module crc16_word (
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
