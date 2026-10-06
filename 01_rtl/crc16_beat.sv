// crc16_beat.sv -- CRC-16-CCITT update over a beat of up to BW words in one cycle (drop 0.29, stage 2 of the bank port).
//   BW crc16_word stages chained in word order; crc_out is the state after the first n words (n = 0 returns crc_in, so a
//   beat's unused word positions are never accumulated). With BW = 16 this is 512 serial XOR stages: fine for simulation;
//   for synthesis the chain is the long path of the tile interface and the usual remedy is the parallel (matrix) form of the
//   same polynomial per word, which is bit-identical and can replace crc16_word without touching this module.
module crc16_beat #(
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
      crc16_word u_s (.crc_in(c[i]), .word(words[32*i +: 32]), .crc_out(c[i+1]));
    end
  endgenerate
  assign crc_out = c[n];
endmodule
