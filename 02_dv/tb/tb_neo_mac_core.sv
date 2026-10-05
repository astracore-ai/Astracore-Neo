// tb_neo_mac_core.sv -- self-checking testbench for neo_mac_core v0.1.
//   Vectors come from model/systolic_ref.py (numpy golden model), read with $readmemh
//   from +VECDIR=<dir> (default "vectors"). params.vh in that directory sets the sizes.
//   Weights load into the shadow chain; a one-cycle swap token precedes each tile's rows.
//   T1: stream M activation rows, compare every Y[m][j] and the check column with the golden
//       values, expect zero ABFT flags.
//   T2: re-stream with fault_inject pulsed in the cycle PE(FAULT_ROW,FAULT_COL) processes row
//       FAULT_M; expect exactly one output off by +1 at (FAULT_M, FAULT_COL) and abft_err
//       asserted for exactly that row, and abft_err_sticky set.
//   Run: make iverilog | make xrun | make verilator   (see Makefile)
`timescale 1ns/1ps
`include "params.vh"

module tb_neo_mac_core;
  localparam int ROWS = `VEC_ROWS;
  localparam int COLS = `VEC_COLS;
  localparam int M    = `VEC_M;
  localparam int FR   = `VEC_FAULT_ROW;
  localparam int FC   = `VEC_FAULT_COL;
  localparam int MF   = `VEC_FAULT_M;
  localparam int XW = 8, WW = 8, PW = 32;
  localparam int WCW = WW + $clog2(ROWS) + 1;
  localparam int PE_LAT = `VEC_PE_LAT;
  localparam int LAT = PE_LAT * ROWS + COLS;

  logic clk = 1'b0;
  logic rst_n = 1'b0;
  always #0.5 clk = ~clk;   // 1 GHz TB clock (timing is cycle-based; period is irrelevant)

  logic                  w_load;
  logic signed [WW-1:0]  w_in  [COLS];
  logic signed [WCW-1:0] wc_in;
  logic signed [XW-1:0]  x_in  [ROWS];
  logic                  valid_in;
  logic                  swap_in;
  logic signed [PW-1:0]  y     [COLS];
  logic signed [PW-1:0]  y_chk;
  logic                  valid_out;
  logic                  abft_clear;
  logic                  abft_err;
  logic                  abft_err_sticky;
  logic                  fault_inject;

  neo_mac_core #(.ROWS(ROWS), .COLS(COLS), .XW(XW), .WW(WW), .PW(PW),
                 .FAULT_ROW(FR), .FAULT_COL(FC)) dut (
    .clk(clk), .rst_n(rst_n), .w_load(w_load), .w_in(w_in), .wc_in(wc_in),
    .x_in(x_in), .valid_in(valid_in), .swap_in(swap_in), .y(y), .y_chk(y_chk), .valid_out(valid_out),
    .abft_clear(abft_clear), .abft_err(abft_err), .abft_err_sticky(abft_err_sticky),
    .fault_inject(fault_inject));

  // ---------------- golden vectors ----------------
  logic [7:0]  x_mem  [0:M*ROWS-1];
  logic [7:0]  w_mem  [0:ROWS*COLS-1];
  logic [15:0] wc_mem [0:ROWS-1];
  logic [31:0] y_mem  [0:M*COLS-1];
  logic [31:0] yc_mem [0:M-1];
  string vecdir;

  // ---------------- scoreboard ----------------
  int out_row;                 // index of the next expected output row
  int mismatches;              // total mismatching outputs (data + check column)
  int err_rows;                // rows with abft_err asserted
  int first_mm_row, first_mm_col;
  longint first_mm_delta;
  bit err_at_row [0:M-1];

  task automatic clear_scoreboard();
    out_row = 0; mismatches = 0; err_rows = 0;
    first_mm_row = -1; first_mm_col = -1; first_mm_delta = 0;
    for (int m = 0; m < M; m++) err_at_row[m] = 1'b0;
  endtask

  always @(negedge clk) begin
    if (rst_n && valid_out) begin
      if (out_row < M) begin
        for (int j = 0; j < COLS; j++) begin
          if (y[j] !== $signed(y_mem[out_row*COLS + j])) begin
            mismatches++;
            if (mismatches <= 8)
              $display("  MM row %0d col %0d: got %0d expected %0d (abft_err=%b)", out_row, j, y[j], $signed(y_mem[out_row*COLS + j]), abft_err);
            if (first_mm_row < 0) begin
              first_mm_row = out_row; first_mm_col = j;
              first_mm_delta = longint'(y[j]) - longint'($signed(y_mem[out_row*COLS + j]));
            end
          end
        end
        if (y_chk !== $signed(yc_mem[out_row])) begin
          mismatches++;
          if (mismatches <= 8)
            $display("  MM row %0d check column: got %0d expected %0d (abft_err=%b)", out_row, y_chk, $signed(yc_mem[out_row]), abft_err);
          if (first_mm_row < 0) begin
            first_mm_row = out_row; first_mm_col = COLS;
            first_mm_delta = longint'(y_chk) - longint'($signed(yc_mem[out_row]));
          end
        end
        if (abft_err) begin err_rows++; err_at_row[out_row] = 1'b1; end
      end else begin
        $display("ERROR: valid_out asserted for more than M=%0d rows", M);
        mismatches++;
      end
      out_row++;
    end
  end

  // ---------------- PE(0,0) monitor: the first data cycles, to localise a zero/X datapath on a real simulator ----------------
  int cyc = 0, mon_left = 0;
  bit mon_started = 0;
  always @(negedge clk) begin
    cyc++;
    if (rst_n && !mon_started && valid_in === 1'b1) begin mon_started = 1; mon_left = 6; end
    if (mon_left > 0) begin
      mon_left--;
      $display("PEMON cycle %0d: tb valid_in=%b swap_in=%b x_in[0]=%0d | PE(0,0) s_in=%b v_in=%b x_in=%0d w_q=%0d w_sh=%0d prod_q=%0d p_out=%0d | PE(1,0) s_in=%b v_in=%b x_in=%0d w_q=%0d | skew s_row[1]=%b",
               cyc, valid_in, swap_in, x_in[0],
               dut.u_array.g_row[0].g_col[0].g_data.u_pe.s_in, dut.u_array.g_row[0].g_col[0].g_data.u_pe.v_in,
               dut.u_array.g_row[0].g_col[0].g_data.u_pe.x_in, dut.u_array.g_row[0].g_col[0].g_data.u_pe.w_q,
               dut.u_array.g_row[0].g_col[0].g_data.u_pe.w_sh, dut.u_array.g_row[0].g_col[0].g_data.u_pe.prod_q,
               dut.u_array.g_row[0].g_col[0].g_data.u_pe.p_out,
               dut.u_array.g_row[1].g_col[0].g_data.u_pe.s_in, dut.u_array.g_row[1].g_col[0].g_data.u_pe.v_in,
               dut.u_array.g_row[1].g_col[0].g_data.u_pe.x_in, dut.u_array.g_row[1].g_col[0].g_data.u_pe.w_q,
               dut.u_skew.s_row[1]);
    end
    if (rst_n && valid_out === 1'b1 && out_row < 2)
      $display("PEMON cycle %0d: valid_out row %0d y[0]=%0d y[1]=%0d y_chk=%0d expected y[0]=%0d yc=%0d", cyc, out_row, y[0], y[1], y_chk, $signed(y_mem[out_row*COLS]), $signed(yc_mem[out_row]));
  end

  // ---------------- drivers ----------------
  task automatic idle_inputs();
    w_load = 1'b0; wc_in = '0; valid_in = 1'b0; swap_in = 1'b0; fault_inject = 1'b0; abft_clear = 1'b0;
    for (int j = 0; j < COLS; j++) w_in[j] = '0;
    for (int i = 0; i < ROWS; i++) x_in[i] = '0;
  endtask

  // Shift weights into the SHADOW chain from the top: on cycle t feed row ROWS-1-t.
  task automatic load_weights();
    for (int t = 0; t < ROWS; t++) begin
      @(negedge clk);
      w_load = 1'b1;
      for (int j = 0; j < COLS; j++) w_in[j] = $signed(w_mem[(ROWS-1-t)*COLS + j]);
      wc_in = $signed(wc_mem[ROWS-1-t]);
    end
    @(negedge clk);
    w_load = 1'b0;
    for (int j = 0; j < COLS; j++) w_in[j] = '0;
    wc_in = '0;
  endtask

  // Cycle 0: swap token (shadow -> active). Cycles 1..M: unskewed rows. fault_cycle < 0 disables injection.
  task automatic stream_rows(input int fault_cycle);
    for (int s = 0; s < M + 1 + LAT + 2; s++) begin
      @(negedge clk);
      swap_in      = (s == 0);
      valid_in     = (s >= 1 && s <= M);
      fault_inject = (s == fault_cycle);
      for (int i = 0; i < ROWS; i++) x_in[i] = (s >= 1 && s <= M) ? $signed(x_mem[(s-1)*ROWS + i]) : '0;
    end
    @(negedge clk);
    swap_in = 1'b0; valid_in = 1'b0; fault_inject = 1'b0;
  endtask

  int fails = 0;

  initial begin
    if (!$value$plusargs("VECDIR=%s", vecdir)) vecdir = "vectors";
    $readmemh({vecdir, "/x.hex"},      x_mem);
    $readmemh({vecdir, "/w.hex"},      w_mem);
    $readmemh({vecdir, "/wc.hex"},     wc_mem);
    $readmemh({vecdir, "/y_exp.hex"},  y_mem);
    $readmemh({vecdir, "/yc_exp.hex"}, yc_mem);
    $display("tb_neo_mac_core: ROWS=%0d COLS=%0d (+1 ABFT column) M=%0d LATENCY=%0d vectors=%s",
             ROWS, COLS, M, LAT, vecdir);

    idle_inputs();
    clear_scoreboard();
    repeat (3) @(negedge clk);
    rst_n = 1'b1;
    repeat (2) @(negedge clk);

    load_weights();

    // ---- T1: clean dataflow ----
    clear_scoreboard();
    stream_rows(-1);
    if (out_row == M && mismatches == 0 && err_rows == 0 && !abft_err_sticky) begin
      $display("T1 dataflow  : %0d rows x %0d outputs + check column match golden, 0 ABFT flags -> PASS", M, COLS);
    end else begin
      $display("T1 dataflow  : rows=%0d/%0d mismatches=%0d err_rows=%0d sticky=%0b -> FAIL (first mismatch row %0d col %0d delta %0d)",
               out_row, M, mismatches, err_rows, abft_err_sticky, first_mm_row, first_mm_col, first_mm_delta);
      fails++;
    end

    // ---- T2: single MAC fault in PE(FR,FC) while row MF passes through ----
    clear_scoreboard();
    stream_rows(1 + MF + PE_LAT * FR + FC);
    if (out_row == M && mismatches == 1 && first_mm_row == MF && first_mm_col == FC && first_mm_delta == 1
        && err_rows == 1 && err_at_row[MF] && abft_err_sticky) begin
      $display("T2 MAC fault : PE(%0d,%0d) fault -> output (%0d,%0d) off by +1 only, ABFT flags exactly row %0d, sticky set -> PASS",
               FR, FC, MF, FC, MF);
    end else begin
      $display("T2 MAC fault : mismatches=%0d first(row %0d col %0d delta %0d) err_rows=%0d err_at[%0d]=%0b sticky=%0b -> FAIL",
               mismatches, first_mm_row, first_mm_col, first_mm_delta, err_rows, MF, err_at_row[MF], abft_err_sticky);
      fails++;
    end

    // ---- T3: sticky clears ----
    @(negedge clk); abft_clear = 1'b1;
    @(negedge clk); abft_clear = 1'b0;
    @(negedge clk);
    if (!abft_err_sticky) $display("T3 clear     : abft_err_sticky cleared -> PASS");
    else begin $display("T3 clear     : abft_err_sticky still set -> FAIL"); fails++; end

    if (fails == 0) $display("RESULT: ALL PASS");
    else            $display("RESULT: %0d FAILURES", fails);
    $finish;
  end
endmodule
