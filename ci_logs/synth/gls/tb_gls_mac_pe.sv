`timescale 1ns/1ps
module tb_gls_mac_pe;
  integer seed;
  reg clk = 1'b0;
  always #0.5 clk = ~clk;
  integer cyc, mism, xonly, xfirst, xlast, first_mism;
  wire [0:0] i_clk = clk;
  reg  [0:0] i_rst_n;
  reg  [0:0] i_w_load;
  reg  [9:0] i_w_in;
  reg  [0:0] i_s_in;
  reg  [0:0] i_v_in;
  reg  [31:0] i_x_in;
  reg  [31:0] i_p_in;
  reg  [0:0] i_fault_inject;
  wire [9:0] r_w_out, g_w_out;
  wire [0:0] r_s_out, g_s_out;
  wire [0:0] r_v_out, g_v_out;
  wire [31:0] r_x_out, g_x_out;
  wire [31:0] r_p_out, g_p_out;
  mac_pe #(.XW(8), .WW(8), .PW(32), .NB(4)) u_rtl (.clk(i_clk), .rst_n(i_rst_n), .w_load(i_w_load), .w_in(i_w_in), .s_in(i_s_in), .v_in(i_v_in), .x_in(i_x_in), .p_in(i_p_in), .fault_inject(i_fault_inject), .w_out(r_w_out), .s_out(r_s_out), .v_out(r_v_out), .x_out(r_x_out), .p_out(r_p_out));
  mac_pe_gl u_gl (.clk(i_clk), .rst_n(i_rst_n), .w_load(i_w_load), .w_in(i_w_in), .s_in(i_s_in), .v_in(i_v_in), .x_in(i_x_in), .p_in(i_p_in), .fault_inject(i_fault_inject), .w_out(g_w_out), .s_out(g_s_out), .v_out(g_v_out), .x_out(g_x_out), .p_out(g_p_out));
  task drive; begin
    i_rst_n = (cyc >= 4);
    i_w_load = $random(seed);
    i_w_in = $random(seed);
    i_s_in = $random(seed);
    i_v_in = $random(seed);
    i_x_in = $random(seed);
    i_p_in = $random(seed);
    i_fault_inject = $random(seed);
  end endtask
  task compare; begin
    if (r_w_out !== g_w_out) begin
      if (known_diff(r_w_out, g_w_out)) begin mism = mism + 1; if (first_mism < 0) first_mism = cyc;
        if (mism <= 5) $display("MISMATCH cycle %0d w_out: rtl %h gl %h", cyc, r_w_out, g_w_out); end
      else begin xonly = xonly + 1; if (xfirst < 0) xfirst = cyc; xlast = cyc; end
    end
    if (r_s_out !== g_s_out) begin
      if (known_diff(r_s_out, g_s_out)) begin mism = mism + 1; if (first_mism < 0) first_mism = cyc;
        if (mism <= 5) $display("MISMATCH cycle %0d s_out: rtl %h gl %h", cyc, r_s_out, g_s_out); end
      else begin xonly = xonly + 1; if (xfirst < 0) xfirst = cyc; xlast = cyc; end
    end
    if (r_v_out !== g_v_out) begin
      if (known_diff(r_v_out, g_v_out)) begin mism = mism + 1; if (first_mism < 0) first_mism = cyc;
        if (mism <= 5) $display("MISMATCH cycle %0d v_out: rtl %h gl %h", cyc, r_v_out, g_v_out); end
      else begin xonly = xonly + 1; if (xfirst < 0) xfirst = cyc; xlast = cyc; end
    end
    if (r_x_out !== g_x_out) begin
      if (known_diff(r_x_out, g_x_out)) begin mism = mism + 1; if (first_mism < 0) first_mism = cyc;
        if (mism <= 5) $display("MISMATCH cycle %0d x_out: rtl %h gl %h", cyc, r_x_out, g_x_out); end
      else begin xonly = xonly + 1; if (xfirst < 0) xfirst = cyc; xlast = cyc; end
    end
    if (r_p_out !== g_p_out) begin
      if (known_diff(r_p_out, g_p_out)) begin mism = mism + 1; if (first_mism < 0) first_mism = cyc;
        if (mism <= 5) $display("MISMATCH cycle %0d p_out: rtl %h gl %h", cyc, r_p_out, g_p_out); end
      else begin xonly = xonly + 1; if (xfirst < 0) xfirst = cyc; xlast = cyc; end
    end
  end endtask
  function automatic known_diff(input [31:0] a, input [31:0] b);
    integer k; begin known_diff = 1'b0;
      for (k = 0; k < 32; k = k + 1) if ((a[k] !== 1'bx) && (b[k] !== 1'bx) && (a[k] !== b[k])) known_diff = 1'b1;
    end endfunction
  initial begin
    seed = 13584431; mism = 0; xonly = 0; xfirst = -1; xlast = -1; first_mism = -1;
    cyc = 0; drive;
    for (cyc = 0; cyc < 3000; cyc = cyc + 1) begin
      @(posedge clk); #0.4;
      if (cyc >= 8) compare;
      @(negedge clk);
      drive;
    end
    $display("GLS mac_pe: %0d cycles compared from cycle 8, %0d known-bit mismatches (first %0d), %0d netlist-X-only compares (cycles %0d..%0d)", 3000 - 8, mism, first_mism, xonly, xfirst, xlast);
    if (mism == 0 && (xlast < 1500)) $display("RESULT: PASS"); else $display("RESULT: FAIL");
    $finish;
  end
endmodule
