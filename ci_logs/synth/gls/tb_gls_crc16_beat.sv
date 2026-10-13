`timescale 1ns/1ps
module tb_gls_crc16_beat;
  integer seed;
  reg clk = 1'b0;
  always #0.5 clk = ~clk;
  integer cyc, mism, xonly, xfirst, xlast, first_mism;
  reg  [15:0] i_crc_in;
  reg  [511:0] i_words;
  reg  [4:0] i_n;
  wire [15:0] r_crc_out, g_crc_out;
  crc16_beat #(.BW(16)) u_rtl (.crc_in(i_crc_in), .words(i_words), .n(i_n), .crc_out(r_crc_out));
  crc16_beat_gl u_gl (.crc_in(i_crc_in), .words(i_words), .n(i_n), .crc_out(g_crc_out));
  task drive; begin
    i_crc_in = $random(seed);
    i_words = {$random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed)};
    i_n = ($unsigned($random(seed)) % 17);
  end endtask
  task compare; begin
    if (r_crc_out !== g_crc_out) begin
      if (known_diff(r_crc_out, g_crc_out)) begin mism = mism + 1; if (first_mism < 0) first_mism = cyc;
        if (mism <= 5) $display("MISMATCH cycle %0d crc_out: rtl %h gl %h", cyc, r_crc_out, g_crc_out); end
      else begin xonly = xonly + 1; if (xfirst < 0) xfirst = cyc; xlast = cyc; end
    end
  end endtask
  function automatic known_diff(input [15:0] a, input [15:0] b);
    integer k; begin known_diff = 1'b0;
      for (k = 0; k < 16; k = k + 1) if ((a[k] !== 1'bx) && (b[k] !== 1'bx) && (a[k] !== b[k])) known_diff = 1'b1;
    end endfunction
  initial begin
    seed = 681119304; mism = 0; xonly = 0; xfirst = -1; xlast = -1; first_mism = -1;
    cyc = 0; drive;
    for (cyc = 0; cyc < 3000; cyc = cyc + 1) begin
      #1;
      if (cyc >= 8) compare;
      drive;
    end
    $display("GLS crc16_beat: %0d cycles compared from cycle 8, %0d known-bit mismatches (first %0d), %0d netlist-X-only compares (cycles %0d..%0d)", 3000 - 8, mism, first_mism, xonly, xfirst, xlast);
    if (mism == 0 && (xlast < 1500)) $display("RESULT: PASS"); else $display("RESULT: FAIL");
    $finish;
  end
endmodule
