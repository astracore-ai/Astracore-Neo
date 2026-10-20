`timescale 1ns/1ps
module tb_gls_ecc39_dec;
  integer seed;
  reg clk = 1'b0;
  always #0.5 clk = ~clk;
  integer cyc, mism, xonly, xfirst, xlast, first_mism;
  reg  [31:0] i_d;
  reg  [5:0] i_p;
  reg  [0:0] i_op;
  wire [31:0] r_d_out, g_d_out;
  wire [0:0] r_ce, g_ce;
  wire [0:0] r_ue, g_ue;
  ecc39_dec u_rtl (.d(i_d), .p(i_p), .op(i_op), .d_out(r_d_out), .ce(r_ce), .ue(r_ue));
  ecc39_dec_gl u_gl (.d(i_d), .p(i_p), .op(i_op), .d_out(g_d_out), .ce(g_ce), .ue(g_ue));
  task drive; begin
    i_d = $random(seed);
    i_p = $random(seed);
    i_op = $random(seed);
  end endtask
  task compare; begin
    if (r_d_out !== g_d_out) begin
      if (known_diff(r_d_out, g_d_out)) begin mism = mism + 1; if (first_mism < 0) first_mism = cyc;
        if (mism <= 5) $display("MISMATCH cycle %0d d_out: rtl %h gl %h", cyc, r_d_out, g_d_out); end
      else begin xonly = xonly + 1; if (xfirst < 0) xfirst = cyc; xlast = cyc; end
    end
    if (r_ce !== g_ce) begin
      if (known_diff(r_ce, g_ce)) begin mism = mism + 1; if (first_mism < 0) first_mism = cyc;
        if (mism <= 5) $display("MISMATCH cycle %0d ce: rtl %h gl %h", cyc, r_ce, g_ce); end
      else begin xonly = xonly + 1; if (xfirst < 0) xfirst = cyc; xlast = cyc; end
    end
    if (r_ue !== g_ue) begin
      if (known_diff(r_ue, g_ue)) begin mism = mism + 1; if (first_mism < 0) first_mism = cyc;
        if (mism <= 5) $display("MISMATCH cycle %0d ue: rtl %h gl %h", cyc, r_ue, g_ue); end
      else begin xonly = xonly + 1; if (xfirst < 0) xfirst = cyc; xlast = cyc; end
    end
  end endtask
  function automatic known_diff(input [31:0] a, input [31:0] b);
    integer k; begin known_diff = 1'b0;
      for (k = 0; k < 32; k = k + 1) if ((a[k] !== 1'bx) && (b[k] !== 1'bx) && (a[k] !== b[k])) known_diff = 1'b1;
    end endfunction
  initial begin
    seed = 178455934; mism = 0; xonly = 0; xfirst = -1; xlast = -1; first_mism = -1;
    cyc = 0; drive;
    for (cyc = 0; cyc < 3000; cyc = cyc + 1) begin
      #1;
      if (cyc >= 8) compare;
      drive;
    end
    $display("GLS ecc39_dec: %0d cycles compared from cycle 8, %0d known-bit mismatches (first %0d), %0d netlist-X-only compares (cycles %0d..%0d)", 3000 - 8, mism, first_mism, xonly, xfirst, xlast);
    if (mism == 0 && (xlast < 1500)) $display("RESULT: PASS"); else $display("RESULT: FAIL");
    $finish;
  end
endmodule
