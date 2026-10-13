`timescale 1ns/1ps
module tb_gls_requant;
  integer seed;
  reg clk = 1'b0;
  always #0.5 clk = ~clk;
  integer cyc, mism, xonly, xfirst, xlast, first_mism;
  wire [0:0] i_clk = clk;
  reg  [0:0] i_rst_n;
  reg  [0:0] i_tbl_we;
  reg  [4:0] i_tbl_addr;
  reg  [15:0] i_tbl_mult;
  reg  [4:0] i_tbl_shift;
  reg  [7:0] i_tbl_zp;
  reg  [0:0] i_relu;
  reg  [0:0] i_in_valid;
  reg  [1023:0] i_in_acc;
  reg  [0:0] i_err_clear;
  reg  [0:0] i_tbl_fault_inject;
  wire [0:0] r_out_valid, g_out_valid;
  wire [255:0] r_out_q, g_out_q;
  wire [0:0] r_tbl_perr_sticky, g_tbl_perr_sticky;
  wire [0:0] r_cg_en, g_cg_en;
  wire signed [31:0] a_in_acc [32];
  assign a_in_acc[0] = i_in_acc[1023:992];
  assign a_in_acc[1] = i_in_acc[991:960];
  assign a_in_acc[2] = i_in_acc[959:928];
  assign a_in_acc[3] = i_in_acc[927:896];
  assign a_in_acc[4] = i_in_acc[895:864];
  assign a_in_acc[5] = i_in_acc[863:832];
  assign a_in_acc[6] = i_in_acc[831:800];
  assign a_in_acc[7] = i_in_acc[799:768];
  assign a_in_acc[8] = i_in_acc[767:736];
  assign a_in_acc[9] = i_in_acc[735:704];
  assign a_in_acc[10] = i_in_acc[703:672];
  assign a_in_acc[11] = i_in_acc[671:640];
  assign a_in_acc[12] = i_in_acc[639:608];
  assign a_in_acc[13] = i_in_acc[607:576];
  assign a_in_acc[14] = i_in_acc[575:544];
  assign a_in_acc[15] = i_in_acc[543:512];
  assign a_in_acc[16] = i_in_acc[511:480];
  assign a_in_acc[17] = i_in_acc[479:448];
  assign a_in_acc[18] = i_in_acc[447:416];
  assign a_in_acc[19] = i_in_acc[415:384];
  assign a_in_acc[20] = i_in_acc[383:352];
  assign a_in_acc[21] = i_in_acc[351:320];
  assign a_in_acc[22] = i_in_acc[319:288];
  assign a_in_acc[23] = i_in_acc[287:256];
  assign a_in_acc[24] = i_in_acc[255:224];
  assign a_in_acc[25] = i_in_acc[223:192];
  assign a_in_acc[26] = i_in_acc[191:160];
  assign a_in_acc[27] = i_in_acc[159:128];
  assign a_in_acc[28] = i_in_acc[127:96];
  assign a_in_acc[29] = i_in_acc[95:64];
  assign a_in_acc[30] = i_in_acc[63:32];
  assign a_in_acc[31] = i_in_acc[31:0];
  wire signed [7:0] a_out_q [32];
  assign r_out_q[255:248] = a_out_q[0];
  assign r_out_q[247:240] = a_out_q[1];
  assign r_out_q[239:232] = a_out_q[2];
  assign r_out_q[231:224] = a_out_q[3];
  assign r_out_q[223:216] = a_out_q[4];
  assign r_out_q[215:208] = a_out_q[5];
  assign r_out_q[207:200] = a_out_q[6];
  assign r_out_q[199:192] = a_out_q[7];
  assign r_out_q[191:184] = a_out_q[8];
  assign r_out_q[183:176] = a_out_q[9];
  assign r_out_q[175:168] = a_out_q[10];
  assign r_out_q[167:160] = a_out_q[11];
  assign r_out_q[159:152] = a_out_q[12];
  assign r_out_q[151:144] = a_out_q[13];
  assign r_out_q[143:136] = a_out_q[14];
  assign r_out_q[135:128] = a_out_q[15];
  assign r_out_q[127:120] = a_out_q[16];
  assign r_out_q[119:112] = a_out_q[17];
  assign r_out_q[111:104] = a_out_q[18];
  assign r_out_q[103:96] = a_out_q[19];
  assign r_out_q[95:88] = a_out_q[20];
  assign r_out_q[87:80] = a_out_q[21];
  assign r_out_q[79:72] = a_out_q[22];
  assign r_out_q[71:64] = a_out_q[23];
  assign r_out_q[63:56] = a_out_q[24];
  assign r_out_q[55:48] = a_out_q[25];
  assign r_out_q[47:40] = a_out_q[26];
  assign r_out_q[39:32] = a_out_q[27];
  assign r_out_q[31:24] = a_out_q[28];
  assign r_out_q[23:16] = a_out_q[29];
  assign r_out_q[15:8] = a_out_q[30];
  assign r_out_q[7:0] = a_out_q[31];
  requant #(.COLS(32), .PW(32), .MW(16)) u_rtl (.clk(i_clk), .rst_n(i_rst_n), .tbl_we(i_tbl_we), .tbl_addr(i_tbl_addr), .tbl_mult(i_tbl_mult), .tbl_shift(i_tbl_shift), .tbl_zp(i_tbl_zp), .relu(i_relu), .in_valid(i_in_valid), .in_acc(a_in_acc), .err_clear(i_err_clear), .tbl_fault_inject(i_tbl_fault_inject), .out_valid(r_out_valid), .out_q(a_out_q), .tbl_perr_sticky(r_tbl_perr_sticky), .cg_en(r_cg_en));
  requant_gl u_gl (.clk(i_clk), .rst_n(i_rst_n), .tbl_we(i_tbl_we), .tbl_addr(i_tbl_addr), .tbl_mult(i_tbl_mult), .tbl_shift(i_tbl_shift), .tbl_zp(i_tbl_zp), .relu(i_relu), .in_valid(i_in_valid), .in_acc(i_in_acc), .err_clear(i_err_clear), .tbl_fault_inject(i_tbl_fault_inject), .out_valid(g_out_valid), .out_q(g_out_q), .tbl_perr_sticky(g_tbl_perr_sticky), .cg_en(g_cg_en));
  task drive; begin
    i_rst_n = (cyc >= 4);
    i_tbl_we = $random(seed);
    i_tbl_addr = $random(seed);
    i_tbl_mult = $random(seed);
    i_tbl_shift = $random(seed);
    i_tbl_zp = $random(seed);
    i_relu = $random(seed);
    i_in_valid = $random(seed);
    i_in_acc = {$random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed), $random(seed)};
    i_err_clear = $random(seed);
    i_tbl_fault_inject = $random(seed);
  end endtask
  task compare; begin
    if (r_out_valid !== g_out_valid) begin
      if (known_diff(r_out_valid, g_out_valid)) begin mism = mism + 1; if (first_mism < 0) first_mism = cyc;
        if (mism <= 5) $display("MISMATCH cycle %0d out_valid: rtl %h gl %h", cyc, r_out_valid, g_out_valid); end
      else begin xonly = xonly + 1; if (xfirst < 0) xfirst = cyc; xlast = cyc; end
    end
    if (r_out_q !== g_out_q) begin
      if (known_diff(r_out_q, g_out_q)) begin mism = mism + 1; if (first_mism < 0) first_mism = cyc;
        if (mism <= 5) $display("MISMATCH cycle %0d out_q: rtl %h gl %h", cyc, r_out_q, g_out_q); end
      else begin xonly = xonly + 1; if (xfirst < 0) xfirst = cyc; xlast = cyc; end
    end
    if (r_tbl_perr_sticky !== g_tbl_perr_sticky) begin
      if (known_diff(r_tbl_perr_sticky, g_tbl_perr_sticky)) begin mism = mism + 1; if (first_mism < 0) first_mism = cyc;
        if (mism <= 5) $display("MISMATCH cycle %0d tbl_perr_sticky: rtl %h gl %h", cyc, r_tbl_perr_sticky, g_tbl_perr_sticky); end
      else begin xonly = xonly + 1; if (xfirst < 0) xfirst = cyc; xlast = cyc; end
    end
    if (r_cg_en !== g_cg_en) begin
      if (known_diff(r_cg_en, g_cg_en)) begin mism = mism + 1; if (first_mism < 0) first_mism = cyc;
        if (mism <= 5) $display("MISMATCH cycle %0d cg_en: rtl %h gl %h", cyc, r_cg_en, g_cg_en); end
      else begin xonly = xonly + 1; if (xfirst < 0) xfirst = cyc; xlast = cyc; end
    end
  end endtask
  function automatic known_diff(input [255:0] a, input [255:0] b);
    integer k; begin known_diff = 1'b0;
      for (k = 0; k < 256; k = k + 1) if ((a[k] !== 1'bx) && (b[k] !== 1'bx) && (a[k] !== b[k])) known_diff = 1'b1;
    end endfunction
  initial begin
    seed = 486272851; mism = 0; xonly = 0; xfirst = -1; xlast = -1; first_mism = -1;
    cyc = 0; drive;
    for (cyc = 0; cyc < 3000; cyc = cyc + 1) begin
      @(posedge clk); #0.4;
      if (cyc >= 8) compare;
      @(negedge clk);
      drive;
    end
    $display("GLS requant: %0d cycles compared from cycle 8, %0d known-bit mismatches (first %0d), %0d netlist-X-only compares (cycles %0d..%0d)", 3000 - 8, mism, first_mism, xonly, xfirst, xlast);
    if (mism == 0 && (xlast < 1500)) $display("RESULT: PASS"); else $display("RESULT: FAIL");
    $finish;
  end
endmodule
