// crc16_beat_fv.sv -- formal harness (drop 0.41): rtl/crc16_beat against the frozen serial reference crc16_beat_ref
//   (crc16_ref.sv), both fed the same free crc_in and the same free beat of BW words, for every word count n = 0..BW:
//   one pair of instances per n, n a constant of the pair. Combinational: each assertion states the equivalence of
//   whatever form crc16_beat has (the serial chain of drops 0.29-0.40, the matrix form of drop 0.41) with the serial
//   definition over all 2^(16 + 32*BW) inputs. With n a constant every node of either circuit is a parity of a subset of
//   the inputs, which is what makes the proof cheap for BDDs (crc16_beat.sby) where a SAT solver faces an XOR miter of
//   up to 528 inputs. crc16_beat.sby runs it at BW = 4 and at the silicon BW = 16.
module crc16_beat_fv #(
  parameter int BW = 16,
  parameter int CW = $clog2(BW + 1)
)(
  input  logic [15:0]      crc_in,
  input  logic [32*BW-1:0] words
);
  generate
    for (genvar k = 0; k <= BW; k++) begin : g_n
      logic [15:0] crc_dut, crc_ref;
      crc16_beat     #(.BW(BW)) u_dut (.crc_in(crc_in), .words(words), .n(CW'(k)), .crc_out(crc_dut));
      crc16_beat_ref #(.BW(BW)) u_ref (.crc_in(crc_in), .words(words), .n(CW'(k)), .crc_out(crc_ref));
      // one assertion per bit: each is a single parity, so a broken form fails as fast as a good one passes (the OR of
      // sixteen differing parities -- the vector compare -- has an exponential BDD)
      always_comb for (int b = 0; b < 16; b++) assert (crc_dut[b] == crc_ref[b]);
    end
  endgenerate
endmodule
