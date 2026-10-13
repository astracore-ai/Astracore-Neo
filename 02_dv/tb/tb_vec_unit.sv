// tb_vec_unit.sv -- V1 on rtl/vec_unit.sv under Verilator and Icarus (drop 0.43): the command stream of
//   `python3 sim/run_vec_unit.py --dump <dir>` (the neosim test's seven programs on random rows, model/vec_ref.py's expected
//   rows and cycle counts) replayed as neo_core drives the unit: host writes of the tables and the program, RBUF entries,
//   then per run the rows in an accumulator model read the way acc_bank's port B reads (the row the cycle after the
//   read, held), go, the sequencer's done two cycles later, the unit's rows checked as they are emitted. Passes when every
//   emitted row equals the model's, every run takes the model's cycles (the sequencer's done to the unit's done), and no
//   flag rises. Run: make verilator / make iverilog_vec (vectors: make vectors_vec).
`timescale 1ns/1ps
`include "vec_cnt.vh"

module tb_vec_unit;
  localparam int COLS = `VEC_COLS;
  localparam int ROWS = `VEC_ROWS;
  localparam int ACC_ROWS = `VEC_ACC_ROWS;
  localparam int IDXW = $clog2(ACC_ROWS);

  logic clk = 1'b0;
  logic rst_n = 1'b0;
  always #0.5 clk = ~clk;

  logic                  vh_we;
  logic [11:0]           vh_addr;
  logic [31:0]           vh_wdata;
  logic                  go, seq_done;
  logic [IDXW-1:0]       cfg_m;
  logic                  acc_rd_en;
  logic [IDXW-1:0]       acc_rd_idx;
  logic signed [31:0]    acc_rd_data [COLS];
  logic                  rbuf_we;
  logic [IDXW-1:0]       rbuf_waddr;
  logic signed [7:0]     rbuf_wdata [ROWS];
  logic                  active, busy, done, rd_q, out_valid;
  wire  signed [31:0]    out_row [COLS];            // a net: Icarus 12 drives no variable array from an unpacked output port
  logic                  err_clear, ctrl_err_sticky, perr_sticky;

  vec_unit #(.COLS(COLS), .ROWS(ROWS), .ACC_ROWS(ACC_ROWS)) dut (
    .clk(clk), .rst_n(rst_n), .vh_we(vh_we), .vh_addr(vh_addr), .vh_wdata(vh_wdata),
    .go(go), .seq_done(seq_done), .cfg_m(cfg_m), .acc_rd_en(acc_rd_en), .acc_rd_idx(acc_rd_idx), .acc_rd_data(acc_rd_data),
    .rbuf_we(rbuf_we), .rbuf_we2(1'b0), .rbuf_waddr(rbuf_waddr), .rbuf_wdata(rbuf_wdata), .rbuf_wdata2(rbuf_wdata),
    .active(active), .busy(busy), .done(done), .rd_q(rd_q), .out_valid(out_valid), .out_row(out_row),
    .err_clear(err_clear), .ctrl_err_sticky(ctrl_err_sticky), .perr_sticky(perr_sticky),
    .ctrl_fault_inject(1'b0), .tbl_fault_inject(1'b0));

  logic [31:0] cmd  [0:`VEC_NCMD-1];
  logic [31:0] expv [0:`VEC_NEXP-1];
  logic signed [31:0] accm [0:ACC_ROWS*COLS-1];          // the accumulator rows of the current run
  string vecdir;
  int nout = 0, mismatches = 0, cyc_bad = 0, runs = 0;
  int pc, op, n, m, ecyc, cyc;

  // acc_bank's port B: the row read at an edge is on the data the cycle after, and held until the next read
  always @(posedge clk) begin
    if (acc_rd_en) for (int j = 0; j < COLS; j++) acc_rd_data[j] <= accm[int'(acc_rd_idx) * COLS + j];
  end

  // the rows as they are emitted
  always @(negedge clk) begin
    if (rst_n && out_valid) begin
      for (int j = 0; j < COLS; j++) begin
        if ((nout + 1) * COLS > `VEC_NEXP || out_row[j] !== $signed(expv[nout * COLS + j])) begin
          mismatches++;
          if (mismatches <= 6)
            $display("  mismatch: emitted row %0d lane %0d: got %0d expected %0d", nout, j, out_row[j], $signed(expv[nout * COLS + j]));
        end
      end
      nout++;
    end
  end

  initial begin
    if (!$value$plusargs("VECDIR=%s", vecdir)) vecdir = "build/vvec";
    $readmemh({vecdir, "/vec_cmd.hex"}, cmd);
    $readmemh({vecdir, "/vec_exp.hex"}, expv);
    vh_we = 1'b0; vh_addr = '0; vh_wdata = '0; go = 1'b0; seq_done = 1'b0; cfg_m = '0; rbuf_we = 1'b0; rbuf_waddr = '0;
    err_clear = 1'b0;
    for (int c = 0; c < ROWS; c++) rbuf_wdata[c] = '0;
    for (int j = 0; j < COLS; j++) acc_rd_data[j] = '0;
    for (int i = 0; i < ACC_ROWS * COLS; i++) accm[i] = '0;
    repeat (3) @(negedge clk);
    rst_n = 1'b1;
    repeat (2) @(negedge clk);
    pc = 0;
    while (cmd[pc] != 32'd0) begin
      op = int'(cmd[pc]);
      if (op == 1) begin                                       // a host write
        @(negedge clk);
        vh_we = 1'b1; vh_addr = cmd[pc + 1][11:0]; vh_wdata = cmd[pc + 2];
        @(negedge clk);
        vh_we = 1'b0;
        pc += 3;
      end else if (op == 2) begin                              // an RBUF entry
        @(negedge clk);
        rbuf_we = 1'b1; rbuf_waddr = cmd[pc + 1][IDXW-1:0];
        for (int c = 0; c < ROWS; c++) rbuf_wdata[c] = $signed(cmd[pc + 2 + c / 4][8 * (c % 4) +: 8]);
        @(negedge clk);
        rbuf_we = 1'b0;
        pc += 2 + ROWS / 4;
      end else if (op == 3) begin                              // a run: the rows, go, the sequencer's done, the unit's done
        n = int'(cmd[pc + 1]);
        ecyc = int'(cmd[pc + 2]);
        for (m = 0; m < n; m++)
          for (int j = 0; j < COLS; j++) accm[m * COLS + j] = $signed(cmd[pc + 3 + m * COLS + j]);
        @(negedge clk);
        cfg_m = IDXW'(n); go = 1'b1;
        @(negedge clk);
        go = 1'b0;
        @(negedge clk);
        seq_done = 1'b1;
        @(negedge clk);
        seq_done = 1'b0;
        cyc = 1;
        while (!done && cyc < 100000) begin
          @(negedge clk);
          cyc++;
        end
        if (cyc != ecyc) begin
          cyc_bad++;
          $display("  run %0d: %0d cycles from the sequencer's done to the unit's done, the model counts %0d", runs, cyc, ecyc);
        end
        runs++;
        @(negedge clk);
        pc += 3 + n * COLS;
      end else begin
        $display("ERROR: unknown command %0d at %0d", op, pc);
        $finish;
      end
    end
    repeat (4) @(negedge clk);
    if (nout == `VEC_NOUT && mismatches == 0 && cyc_bad == 0 && runs == `VEC_NRUNS && !ctrl_err_sticky && !perr_sticky)
      $display("RESULT: ALL PASS (%0d runs, %0d rows emitted bit-exact, every run at the model's cycle count, no flag)", runs, nout);
    else
      $display("RESULT: FAIL (runs %0d/%0d, rows %0d/%0d, mismatches %0d, cycle counts off %0d, ctrl_err %0b, perr %0b)",
               runs, `VEC_NRUNS, nout, `VEC_NOUT, mismatches, cyc_bad, ctrl_err_sticky, perr_sticky);
    $finish;
  end
endmodule
