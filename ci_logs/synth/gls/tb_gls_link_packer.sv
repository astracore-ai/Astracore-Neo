`timescale 1ns/1ps
module tb_gls_link_packer;
  integer seed;
  reg clk = 1'b0;
  always #0.5 clk = ~clk;
  integer cyc, mism, xonly, xfirst, xlast, first_mism;
  wire [0:0] i_clk = clk;
  reg  [0:0] i_rst_n;
  reg  [0:0] i_core_valid;
  reg  [564:0] i_core_flit;
  reg  [0:0] i_link_ready;
  wire [0:0] r_core_ready, g_core_ready;
  wire [0:0] r_link_valid, g_link_valid;
  wire [1076:0] r_link_flit, g_link_flit;
  link_packer #(.XW(3), .YW(3), .BW(16), .WPF(32)) u_rtl (.clk(i_clk), .rst_n(i_rst_n), .core_valid(i_core_valid), .core_flit(i_core_flit), .link_ready(i_link_ready), .core_ready(r_core_ready), .link_valid(r_link_valid), .link_flit(r_link_flit));
  link_packer_gl u_gl (.clk(i_clk), .rst_n(i_rst_n), .core_valid(i_core_valid), .core_flit(i_core_flit), .link_ready(i_link_ready), .core_ready(g_core_ready), .link_valid(g_link_valid), .link_flit(g_link_flit));
  task drive; begin
    i_rst_n = (cyc >= 4);
    i_core_valid = $random(seed);
    i_core_flit = {$random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed)};
    i_link_ready = $random(seed);
  end endtask
  task compare; begin
    if (r_core_ready !== g_core_ready) begin
      if (known_diff(r_core_ready, g_core_ready)) begin mism = mism + 1; if (first_mism < 0) first_mism = cyc;
        if (mism <= 5) $display("MISMATCH cycle %0d core_ready: rtl %h gl %h", cyc, r_core_ready, g_core_ready); end
      else begin xonly = xonly + 1; if (xfirst < 0) xfirst = cyc; xlast = cyc; end
    end
    if (r_link_valid !== g_link_valid) begin
      if (known_diff(r_link_valid, g_link_valid)) begin mism = mism + 1; if (first_mism < 0) first_mism = cyc;
        if (mism <= 5) $display("MISMATCH cycle %0d link_valid: rtl %h gl %h", cyc, r_link_valid, g_link_valid); end
      else begin xonly = xonly + 1; if (xfirst < 0) xfirst = cyc; xlast = cyc; end
    end
    if (r_link_flit !== g_link_flit) begin
      if (known_diff(r_link_flit, g_link_flit)) begin mism = mism + 1; if (first_mism < 0) first_mism = cyc;
        if (mism <= 5) $display("MISMATCH cycle %0d link_flit: rtl %h gl %h", cyc, r_link_flit, g_link_flit); end
      else begin xonly = xonly + 1; if (xfirst < 0) xfirst = cyc; xlast = cyc; end
    end
  end endtask
  function automatic known_diff(input [1076:0] a, input [1076:0] b);
    integer k; begin known_diff = 1'b0;
      for (k = 0; k < 1077; k = k + 1) if ((a[k] !== 1'bx) && (b[k] !== 1'bx) && (a[k] !== b[k])) known_diff = 1'b1;
    end endfunction
  initial begin
    seed = 442147882; mism = 0; xonly = 0; xfirst = -1; xlast = -1; first_mism = -1;
    cyc = 0; drive;
    for (cyc = 0; cyc < 3000; cyc = cyc + 1) begin
      @(posedge clk); #0.4;
      if (cyc >= 8) compare;
      @(negedge clk);
      drive;
    end
    $display("GLS link_packer: %0d cycles compared from cycle 8, %0d known-bit mismatches (first %0d), %0d netlist-X-only compares (cycles %0d..%0d)", 3000 - 8, mism, first_mism, xonly, xfirst, xlast);
    if (mism == 0 && (xlast < 1500)) $display("RESULT: PASS"); else $display("RESULT: FAIL");
    $finish;
  end
endmodule
