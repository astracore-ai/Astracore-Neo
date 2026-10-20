// tb_neo_mac_core.sv -- self-checking testbench for neo_mac_core (v0.3, drop 0.42: the 2:4 sparse tests T6 / T7).
//   Vectors come from model/systolic_ref.py (numpy golden model), read with $readmemh
//   from +VECDIR=<dir> (default "vectors"). params.vh in that directory sets the sizes and VEC_NCHK, the core's check
//   columns (1: the dense-only core; 4: the sparse-capable silicon core -- dense rows present channel r in byte r of the
//   2*ROWS-byte row, dense weights index 0 and their check weight in column 0).
//   Weights load into the shadow chain; a one-cycle swap token precedes each tile's rows.
//   T1: stream M activation rows, compare every Y[m][j] and the check column with the golden
//       values, expect zero ABFT flags.
//   T2: re-stream with fault_inject pulsed in the cycle PE(FAULT_ROW,FAULT_COL) processes row
//       FAULT_M; expect exactly one output off by +1 at (FAULT_M, FAULT_COL) and abft_err
//       asserted for exactly that row, and abft_err_sticky set. Drop 0.41: the verdict of a row comes
//       two cycles after its valid_out, on abft_err with abft_err_valid; the k-th abft_err_valid is row k.
//   T3: abft_err_sticky clears.
//   NCHK = 4 (drop 0.42):
//   T6: a 2:4 sparse tile (64 input channels at 32 rows; rows 2g, 2g + 1 share channels 4g .. 4g + 3, each PE with its
//       weight's position as its index, four check weights per row) streamed with sparse = 1: every output and the
//       summed check columns equal the model's dense arithmetic on the pruned weights, zero ABFT flags.
//   T7: faults under sparse mode, every check column exercised -- (a) the MAC fault of T2 at PE(FAULT_ROW, FAULT_COL)
//       while that PE holds index 0, 1, 2 and 3 (four weight sets from the model; check column k covers the product):
//       one output off by +1, exactly that row flagged; (b) the check weight of column k = 0..3 in row FAULT_ROW off by
//       one: the data outputs untouched, exactly the rows whose channel 4*(FAULT_ROW/2) + k is nonzero flagged.
//   Run: make iverilog | make xrun | make verilator   (see Makefile)
`timescale 1ns/1ps
`include "params.vh"
`ifndef VEC_NCHK
`define VEC_NCHK 1
`endif

module tb_neo_mac_core;
  localparam int ROWS = `VEC_ROWS;
  localparam int COLS = `VEC_COLS;
  localparam int M    = `VEC_M;
  localparam int FR   = `VEC_FAULT_ROW;
  localparam int FC   = `VEC_FAULT_COL;
  localparam int MF   = `VEC_FAULT_M;
  localparam int NCHK = `VEC_NCHK;
  localparam int XW = 8, WW = 8, PW = 32;
  localparam int WCW = WW + $clog2(ROWS) + 1;
  localparam int IW = (NCHK > 1) ? 2 : 0;
  localparam int XB = (NCHK > 1) ? 2 * ROWS : ROWS;      // activation bytes per row
  localparam int PE_LAT = `VEC_PE_LAT;
  localparam int LAT = PE_LAT * ROWS + COLS;

  logic clk = 1'b0;
  logic rst_n = 1'b0;
  always #0.5 clk = ~clk;   // 1 GHz TB clock (timing is cycle-based; period is irrelevant)

  logic                  w_load;
  logic [WW+IW-1:0]      w_in  [COLS];
  logic [NCHK*WCW-1:0]   wc_in;
  logic [XB*XW-1:0]      x_in;          // the packed row, byte c at [c*XW +: XW] (drop 0.42)
  logic                  valid_in;
  logic                  swap_in;
  logic                  sparse;
  wire  signed [PW-1:0]  y     [COLS];         // a net: Icarus 12 does not drive a variable array from an unpacked output port (drop 0.43)
  logic signed [PW-1:0]  y_chk;
  logic                  valid_out;
  logic                  abft_clear;
  logic                  abft_err;
  logic                  abft_err_valid;
  logic                  abft_err_sticky;
  logic                  fault_inject;
  // drop 0.44: the row's byte parities (the feeder forms them from the decoded buffer word), here from x_in, with one bit
  // inverted while par_fault for T8; the array's row-input check (xpar_err_sticky) must stay clear otherwise
  logic [XB-1:0]         x_par_ok, x_par;
  logic                  par_fault;
  logic                  xpar_err, xpar_err_sticky;
  genvar pc;
  generate
    for (pc = 0; pc < XB; pc++) begin : g_xpar
      assign x_par_ok[pc] = ^x_in[pc*XW +: XW];
    end
  endgenerate
  assign x_par = x_par_ok ^ {{(XB-1){1'b0}}, par_fault};

  neo_mac_core #(.ROWS(ROWS), .COLS(COLS), .XW(XW), .WW(WW), .PW(PW), .NCHK(NCHK),
                 .FAULT_ROW(FR), .FAULT_COL(FC)) dut (
    .clk(clk), .rst_n(rst_n), .w_load(w_load), .w_in(w_in), .wc_in(wc_in),
    .x_in(x_in), .x_par(x_par), .valid_in(valid_in), .swap_in(swap_in), .sparse(sparse), .y(y), .y_chk(y_chk), .valid_out(valid_out),
    .abft_clear(abft_clear), .abft_err(abft_err), .abft_err_valid(abft_err_valid), .abft_err_sticky(abft_err_sticky),
    .xpar_err(xpar_err), .xpar_err_sticky(xpar_err_sticky),
    .fault_inject(fault_inject));

  // ---------------- golden vectors ----------------
  logic [7:0]  x_mem  [0:M*ROWS-1];
  logic [7:0]  w_mem  [0:ROWS*COLS-1];
  logic [15:0] wc_mem [0:ROWS-1];
  logic [31:0] y_mem  [0:M*COLS-1];
  logic [31:0] yc_mem [0:M-1];
  // the sparse vectors (NCHK = 4): 64-channel rows, the stored weights / indices / four check weights per row; T7's sets
  logic [7:0]  xs_mem  [0:M*2*ROWS-1];
  logic [7:0]  ws_mem  [0:ROWS*COLS-1];
  logic [3:0]  is_mem  [0:ROWS*COLS-1];
  logic [15:0] wcs_mem [0:ROWS*4-1];
  logic [31:0] ys_mem  [0:M*COLS-1];
  logic [31:0] ycs_mem [0:M-1];
  logic [7:0]  w7_mem  [0:4*ROWS*COLS-1];
  logic [3:0]  i7_mem  [0:4*ROWS*COLS-1];
  logic [15:0] wc7_mem [0:4*ROWS*4-1];
  logic [31:0] y7_mem  [0:4*M*COLS-1];
  string vecdir;

  // ---------------- scoreboard ----------------
  // the expected outputs of the current stream (copied from the vectors before it) and whether y_chk is compared
  logic [31:0] exp_y  [0:M*COLS-1];
  logic [31:0] exp_yc [0:M-1];
  bit          cmp_chk;
  int out_row;                 // index of the next expected output row
  int mismatches;              // total mismatching outputs (data + check column)
  int err_rows;                // rows with abft_err asserted
  int err_row;                 // the row the next abft_err_valid reports (drop 0.41)
  // mismatch record: flops written by the scoreboard process only (non-blocking), cleared by sb_clear
  logic signed [31:0] first_mm_row, first_mm_col;
  logic signed [63:0] first_mm_delta;
  logic               sb_clear;
  bit err_at_row [0:M-1];

  task automatic clear_scoreboard();
    sb_clear = 1'b1;
    @(negedge clk); @(negedge clk);
    sb_clear = 1'b0;
    out_row = 0; mismatches = 0; err_rows = 0; err_row = 0;
    for (int m = 0; m < M; m++) err_at_row[m] = 1'b0;
  endtask

  always @(negedge clk) begin
    if (sb_clear) begin
      first_mm_row <= -1; first_mm_col <= -1; first_mm_delta <= 0;
    end
    if (rst_n && valid_out && !sb_clear) begin
      if (out_row < M) begin
        for (int j = 0; j < COLS; j++) begin
          if (y[j] !== $signed(exp_y[out_row*COLS + j])) begin
            mismatches++;
            if (mismatches <= 8)
              $display("  MM row %0d col %0d: got %0d expected %0d (abft_err=%b)", out_row, j, y[j], $signed(exp_y[out_row*COLS + j]), abft_err);
            first_mm_row <= out_row; first_mm_col <= j;          // last mismatch (the only one in T2)
            first_mm_delta <= longint'(y[j]) - longint'($signed(exp_y[out_row*COLS + j]));
          end
        end
        if (cmp_chk && y_chk !== $signed(exp_yc[out_row])) begin
          mismatches++;
          if (mismatches <= 8)
            $display("  MM row %0d check column: got %0d expected %0d (abft_err=%b)", out_row, y_chk, $signed(exp_yc[out_row]), abft_err);
          first_mm_row <= out_row; first_mm_col <= COLS;
          first_mm_delta <= longint'(y_chk) - longint'($signed(exp_yc[out_row]));
        end
      end else begin
        $display("ERROR: valid_out asserted for more than M=%0d rows", M);
        mismatches++;
      end
      out_row++;
    end
    // the ABFT verdicts, two cycles behind their rows (drop 0.41)
    if (rst_n && abft_err_valid && !sb_clear) begin
      if (err_row < M) begin
        if (abft_err) begin err_rows++; err_at_row[err_row] = 1'b1; end
      end else begin
        $display("ERROR: abft_err_valid asserted for more than M=%0d rows", M);
        mismatches++;
      end
      err_row++;
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
      $display("PEMON cycle %0d: tb valid_in=%b swap_in=%b x_in[0]=%0d | PE(0,0) s_in=%b v_in=%b x_in=%0h w_q=%0d w_sh=%0h prod_q=%0d p_out=%0d | PE(1,0) s_in=%b v_in=%b x_in=%0h w_q=%0d | skew s_row[1]=%b",
               cyc, valid_in, swap_in, $signed(x_in[7:0]),
               dut.u_array.g_row[0].g_col[0].g_data.u_pe.s_in, dut.u_array.g_row[0].g_col[0].g_data.u_pe.v_in,
               dut.u_array.g_row[0].g_col[0].g_data.u_pe.x_in, dut.u_array.g_row[0].g_col[0].g_data.u_pe.w_q,
               dut.u_array.g_row[0].g_col[0].g_data.u_pe.w_sh, dut.u_array.g_row[0].g_col[0].g_data.u_pe.prod_q,
               dut.u_array.g_row[0].g_col[0].g_data.u_pe.p_out,
               dut.u_array.g_row[1].g_col[0].g_data.u_pe.s_in, dut.u_array.g_row[1].g_col[0].g_data.u_pe.v_in,
               dut.u_array.g_row[1].g_col[0].g_data.u_pe.x_in, dut.u_array.g_row[1].g_col[0].g_data.u_pe.w_q,
               dut.u_skew.s_row[1]);
    end
    if (rst_n && valid_out === 1'b1 && out_row < 2)
      $display("PEMON cycle %0d: valid_out row %0d y[0]=%0d y[1]=%0d y_chk=%0d expected y[0]=%0d yc=%0d", cyc, out_row, y[0], y[1], y_chk, $signed(exp_y[out_row*COLS]), $signed(exp_yc[out_row]));
  end

  // ---------------- drivers ----------------
  task automatic idle_inputs();
    w_load = 1'b0; wc_in = '0; valid_in = 1'b0; swap_in = 1'b0; fault_inject = 1'b0; abft_clear = 1'b0; sparse = 1'b0; par_fault = 1'b0;
    for (int j = 0; j < COLS; j++) w_in[j] = '0;
    x_in = '0;
  endtask

  // Shift weights into the SHADOW chain from the top: on cycle t feed row ROWS-1-t.
  // set < 0: the dense tile (index 0, the check weight in column 0); set = 0: the sparse tile; set = 1..4: T7's set
  // set-1; chk_bump_col >= 0 adds one to that check column's weight in row FR (T7b)
  task automatic load_weights(input int set, input int chk_bump_col);
    for (int t = 0; t < ROWS; t++) begin
      int r;
      r = ROWS - 1 - t;
      @(negedge clk);
      w_load = 1'b1;
      wc_in = '0;
      for (int j = 0; j < COLS; j++) begin
        logic [7:0] wv;
        logic [3:0] iv;
        if (set < 0) begin
          wv = w_mem[r*COLS + j]; iv = 4'd0;
        end else if (set == 0) begin
          wv = ws_mem[r*COLS + j]; iv = is_mem[r*COLS + j];
        end else begin
          wv = w7_mem[(set-1)*ROWS*COLS + r*COLS + j]; iv = i7_mem[(set-1)*ROWS*COLS + r*COLS + j];
        end
        w_in[j] = (WW+IW)'({iv[1:0], wv});
      end
      for (int k = 0; k < NCHK; k++) begin
        logic [15:0] cv;
        if (set < 0)       cv = (k == 0) ? wc_mem[r] : 16'd0;
        else if (set == 0) cv = wcs_mem[r*4 + k];
        else               cv = wc7_mem[(set-1)*ROWS*4 + r*4 + k];
        if (r == FR && k == chk_bump_col) cv = cv + 16'd1;
        wc_in[k*WCW +: WCW] = cv[WCW-1:0];
      end
    end
    @(negedge clk);
    w_load = 1'b0;
    for (int j = 0; j < COLS; j++) w_in[j] = '0;
    wc_in = '0;
  endtask

  // the expected outputs of a stream: set < 0 dense, 0 sparse, 1..4 T7's set-1 (its check sums are the row sums)
  task automatic expect_set(input int set, input bit check_chk);
    for (int m = 0; m < M; m++) begin
      logic [31:0] s;
      s = '0;
      for (int j = 0; j < COLS; j++) begin
        if (set < 0)       exp_y[m*COLS + j] = y_mem[m*COLS + j];
        else if (set == 0) exp_y[m*COLS + j] = ys_mem[m*COLS + j];
        else               exp_y[m*COLS + j] = y7_mem[(set-1)*M*COLS + m*COLS + j];
        s = s + exp_y[m*COLS + j];
      end
      exp_yc[m] = (set < 0) ? yc_mem[m] : (set == 0) ? ycs_mem[m] : s;
    end
    cmp_chk = check_chk;
  endtask

  // Cycle 0: swap token (shadow -> active). Cycles 1..M: unskewed rows. fault_cycle < 0 disables injection.
  // sp = 1 streams the 64-channel sparse rows with sparse = 1, else the dense rows (bytes ROWS.. zero on the 4-column core)
  task automatic stream_rows(input int fault_cycle, input bit sp);
    sparse = sp;
    for (int s = 0; s < M + 1 + LAT + 4; s++) begin   // + 2: the last verdict (drop 0.41)
      @(negedge clk);
      swap_in      = (s == 0);
      valid_in     = (s >= 1 && s <= M);
      fault_inject = (s == fault_cycle);
      for (int i = 0; i < XB; i++) begin
        if (!(s >= 1 && s <= M))   x_in[i*XW +: XW] = '0;
        else if (sp)               x_in[i*XW +: XW] = xs_mem[(s-1)*2*ROWS + i];
        else                       x_in[i*XW +: XW] = (i < ROWS) ? x_mem[(s-1)*ROWS + i] : 8'd0;
      end
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
    if (NCHK > 1) begin
      $readmemh({vecdir, "/x_sp.hex"},      xs_mem);
      $readmemh({vecdir, "/w_sp.hex"},      ws_mem);
      $readmemh({vecdir, "/i_sp.hex"},      is_mem);
      $readmemh({vecdir, "/wc_sp.hex"},     wcs_mem);
      $readmemh({vecdir, "/y_sp_exp.hex"},  ys_mem);
      $readmemh({vecdir, "/yc_sp_exp.hex"}, ycs_mem);
      $readmemh({vecdir, "/w_t7.hex"},      w7_mem);
      $readmemh({vecdir, "/i_t7.hex"},      i7_mem);
      $readmemh({vecdir, "/wc_t7.hex"},     wc7_mem);
      $readmemh({vecdir, "/y_t7_exp.hex"},  y7_mem);
    end
    $display("tb_neo_mac_core: ROWS=%0d COLS=%0d (+%0d ABFT check column(s)) M=%0d LATENCY=%0d vectors=%s",
             ROWS, COLS, NCHK, M, LAT, vecdir);

    idle_inputs();
    sb_clear = 1'b0;
    repeat (3) @(negedge clk);
    rst_n = 1'b1;
    clear_scoreboard();
    repeat (2) @(negedge clk);

    load_weights(-1, -1);
    expect_set(-1, 1'b1);

    // ---- T1: clean dataflow ----
    clear_scoreboard();
    stream_rows(-1, 1'b0);
    if (out_row == M && err_row == M && mismatches == 0 && err_rows == 0 && !abft_err_sticky && !xpar_err_sticky) begin
      $display("T1 dataflow  : %0d rows x %0d outputs + check column match golden, 0 ABFT flags, row parity clean -> PASS", M, COLS);
    end else begin
      $display("T1 dataflow  : rows=%0d/%0d verdicts=%0d mismatches=%0d err_rows=%0d sticky=%0b -> FAIL (first mismatch row %0d col %0d delta %0d)",
               out_row, M, err_row, mismatches, err_rows, abft_err_sticky, first_mm_row, first_mm_col, first_mm_delta);
      fails++;
    end

    // ---- T2: single MAC fault in PE(FR,FC) while row MF passes through ----
    clear_scoreboard();
    stream_rows(1 + MF + PE_LAT * FR + FC, 1'b0);
    if (out_row == M && err_row == M && mismatches == 1 && first_mm_row == MF && first_mm_col == FC && first_mm_delta == 1
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

    // ---- T8 (drop 0.44): a byte of a valid row presented with the wrong parity bit -> xpar_err_sticky, nothing on ABFT ----
    @(negedge clk);
    x_in = {XB*XW/8{8'h5a}}; valid_in = 1'b1; par_fault = 1'b1;
    @(negedge clk);
    valid_in = 1'b0; par_fault = 1'b0;
    repeat (3) @(negedge clk);
    if (xpar_err_sticky && !abft_err_sticky) $display("T8 row parity: byte 0 of a valid row with its parity bit inverted -> xpar_err_sticky, ABFT silent -> PASS");
    else begin $display("T8 row parity: xpar_err_sticky=%0b abft_err_sticky=%0b -> FAIL", xpar_err_sticky, abft_err_sticky); fails++; end
    abft_clear = 1'b1; @(negedge clk); abft_clear = 1'b0; @(negedge clk);
    if (xpar_err_sticky) begin $display("T8 row parity: the sticky did not clear -> FAIL"); fails++; end
    repeat (LAT + 8) @(negedge clk);          // the row streamed in leaves the array before the next test's scoreboard
    clear_scoreboard();

    if (NCHK > 1) begin
      // ---- T6: the 2:4 sparse dataflow ----
      load_weights(0, -1);
      expect_set(0, 1'b1);
      clear_scoreboard();
      stream_rows(-1, 1'b1);
      if (out_row == M && err_row == M && mismatches == 0 && err_rows == 0 && !abft_err_sticky) begin
        $display("T6 sparse    : %0d rows of %0d channels (2:4 on %0d rows), %0d outputs + summed check columns match golden, 0 ABFT flags -> PASS",
                 M, 2 * ROWS, ROWS, COLS);
      end else begin
        $display("T6 sparse    : rows=%0d/%0d verdicts=%0d mismatches=%0d err_rows=%0d sticky=%0b -> FAIL (first mismatch row %0d col %0d delta %0d)",
                 out_row, M, err_row, mismatches, err_rows, abft_err_sticky, first_mm_row, first_mm_col, first_mm_delta);
        fails++;
      end
      // ---- T7a: the MAC fault while PE(FR, FC) holds index 0..3 ----
      for (int k = 0; k < 4; k++) begin
        @(negedge clk); abft_clear = 1'b1;
        @(negedge clk); abft_clear = 1'b0;
        load_weights(1 + k, -1);
        expect_set(1 + k, 1'b1);
        clear_scoreboard();
        stream_rows(1 + MF + PE_LAT * FR + FC, 1'b1);
        if (out_row == M && err_row == M && mismatches == 1 && first_mm_row == MF && first_mm_col == FC && first_mm_delta == 1
            && err_rows == 1 && err_at_row[MF] && abft_err_sticky) begin
          $display("T7 index %0d   : PE(%0d,%0d) holding index %0d (check column %0d covers it), MAC fault -> output (%0d,%0d) off by +1 only, ABFT flags exactly row %0d -> PASS",
                   k, FR, FC, k, k, MF, FC, MF);
        end else begin
          $display("T7 index %0d   : mismatches=%0d first(row %0d col %0d delta %0d) err_rows=%0d err_at[%0d]=%0b sticky=%0b -> FAIL",
                   k, mismatches, first_mm_row, first_mm_col, first_mm_delta, err_rows, MF, err_at_row[MF], abft_err_sticky);
          fails++;
        end
      end
      // ---- T7b: check column k's weight in row FR off by one ----
      for (int k = 0; k < 4; k++) begin
        int nexp, nbad;
        @(negedge clk); abft_clear = 1'b1;
        @(negedge clk); abft_clear = 1'b0;
        load_weights(0, k);
        expect_set(0, 1'b0);                         // the data outputs are compared, the check sum is off by design
        clear_scoreboard();
        stream_rows(-1, 1'b1);
        nexp = 0; nbad = 0;
        for (int m = 0; m < M; m++) begin
          bit e;
          e = (xs_mem[m*2*ROWS + 4*(FR/2) + k] != 8'd0);
          nexp += int'(e);
          if (err_at_row[m] != e) nbad++;
        end
        if (out_row == M && err_row == M && mismatches == 0 && nbad == 0 && err_rows == nexp) begin
          $display("T7 check %0d   : check weight of column %0d in row %0d off by one -> data untouched, ABFT flags exactly the %0d rows whose channel %0d is nonzero -> PASS",
                   k, k, FR, nexp, 4*(FR/2) + k);
        end else begin
          $display("T7 check %0d   : rows=%0d mismatches=%0d err_rows=%0d (expect %0d) rows misflagged=%0d -> FAIL", k, out_row, mismatches, err_rows, nexp, nbad);
          fails++;
        end
      end
    end

    if (fails == 0) $display("RESULT: ALL PASS");
    else            $display("RESULT: %0d FAILURES", fails);
    $finish;
  end
endmodule
