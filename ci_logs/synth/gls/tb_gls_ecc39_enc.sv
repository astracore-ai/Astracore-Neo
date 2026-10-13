`timescale 1ns/1ps
module tb_gls_ecc39_enc;
  integer seed;
  reg clk = 1'b0;
  always #0.5 clk = ~clk;
  integer cyc, mism, xonly, xfirst, xlast, first_mism;
  reg  [31:0] i_d;
  wire [5:0] r_p, g_p;
  wire [0:0] r_op, g_op;
  ecc39_enc u_rtl (.d(i_d), .p(r_p), .op(r_op));
  ecc39_enc_gl u_gl (.d(i_d), .p(g_p), .op(g_op));
  task drive; begin
    i_d = $random(seed);
  end endtask
  task compare; begin
    if (r_p !== g_p) begin
      if (known_diff(r_p, g_p)) begin mism = mism + 1; if (first_mism < 0) first_mism = cyc;
        if (mism <= 5) $display("MISMATCH cycle %0d p: rtl %h gl %h", cyc, r_p, g_p); end
      else begin xonly = xonly + 1; if (xfirst < 0) xfirst = cyc; xlast = cyc; end
    end
    if (r_op !== g_op) begin
      if (known_diff(r_op, g_op)) begin mism = mism + 1; if (first_mism < 0) first_mism = cyc;
        if (mism <= 5) $display("MISMATCH cycle %0d op: rtl %h gl %h", cyc, r_op, g_op); end
      else begin xonly = xonly + 1; if (xfirst < 0) xfirst = cyc; xlast = cyc; end
    end
  end endtask
  function automatic known_diff(input [5:0] a, input [5:0] b);
    integer k; begin known_diff = 1'b0;
      for (k = 0; k < 6; k = k + 1) if ((a[k] !== 1'bx) && (b[k] !== 1'bx) && (a[k] !== b[k])) known_diff = 1'b1;
    end endfunction
  initial begin
    seed = 417297225; mism = 0; xonly = 0; xfirst = -1; xlast = -1; first_mism = -1;
    cyc = 0; drive;
    for (cyc = 0; cyc < 3000; cyc = cyc + 1) begin
      #1;
      if (cyc >= 8) compare;
      drive;
    end
    $display("GLS ecc39_enc: %0d cycles compared from cycle 8, %0d known-bit mismatches (first %0d), %0d netlist-X-only compares (cycles %0d..%0d)", 3000 - 8, mism, first_mism, xonly, xfirst, xlast);
    if (mism == 0 && (xlast < 1500)) $display("RESULT: PASS"); else $display("RESULT: FAIL");
    $finish;
  end
endmodule
