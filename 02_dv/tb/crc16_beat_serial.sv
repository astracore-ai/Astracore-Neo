// crc16_beat_serial.sv -- the reference for the matrix-form crc16_beat (drop 0.41): crc16_beat as it was from drop 0.29 to 0.40,
//   renamed, verbatim otherwise -- BW crc16_word stages chained in word order, crc_out the state after the first n words (n = 0
//   returns crc_in). tb_crc16_beat.sv (make crc16, Verilator and Icarus) and run_nic.py's I13 (neosim) compare the two.
module crc16_beat_serial #(
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
