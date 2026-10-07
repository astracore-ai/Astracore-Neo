// tb_neo_core.sv -- replay of the C1 convolution on neo_core for Xcelium / Verilator / Icarus.
//   Vectors come from `python3 sim/run_conv_core.py --dump vectors_core` (abuf.hex, wbuf.hex,
//   y_exp.hex, desc.vh). The testbench is the host side: load the activation buffer and the weight
//   buffer through the DMA ports, write the layer descriptor, pulse go, collect rd_data while
//   rd_valid until done, compare with the numpy reference, and check that no error flag rose.
//   Run: make xrun_core | make verilator_core | make iverilog_core
`timescale 1ns/1ps
`include "desc.vh"

module tb_neo_core;
  localparam int ROWS = `CORE_ROWS;
  localparam int COLS = `CORE_COLS;
  localparam int XW = 8, WW = 8, PW = 32;
  localparam int WCW = WW + $clog2(ROWS) + 1;
  localparam int ACC_ROWS = 64, ABUF_DEPTH = 256, WBUF_DEPTH = 512;
  localparam int IDXW = $clog2(ACC_ROWS), AW = $clog2(ABUF_DEPTH), WAW = $clog2(WBUF_DEPTH);
  localparam int M = `CORE_M;

  logic clk = 1'b0;
  logic rst_n = 1'b0;
  always #0.5 clk = ~clk;

  logic                  abuf_we;
  logic [AW-1:0]         abuf_waddr;
  logic signed [XW-1:0]  abuf_wdata [ROWS];
  logic                  wbuf_we;
  logic [WAW-1:0]        wbuf_waddr;
  logic signed [WW-1:0]  wbuf_wdata [COLS];
  logic signed [WCW-1:0] wcbuf_wdata;
  logic signed [15:0]    cfg_h, cfg_w, cfg_ho, cfg_wo, cfg_s, cfg_p, cfg_k, cfg_ct_n, cfg_tile_pixels;
  logic signed [15:0]    cfg_ct0, cfg_ky0, cfg_kx0, cfg_rn, cfg_contrib_n, cfg_regions_m1, tiles_ready, cfg_oy0, cfg_oy_n, cfg_iy0;
  logic                  seq_err_sticky, rq_err_sticky, seq_fault_inject, rq_fault_inject, rq_tbl_fault_inject;
  logic                  wbuf_ce_sticky, wbuf_ue_sticky, rq_tbl_perr_sticky;
  logic                  ext_valid, ext_ready, reduce_ready, ct_free;
  logic [IDXW-1:0]       ext_idx;
  logic signed [PW-1:0]  ext_y [COLS];
  logic signed [PW-1:0]  ext_chk;
  logic signed [15:0]    ct_free_idx;
  logic signed [7:0]     rq_q [COLS];
  logic                  rq_valid, rq_tbl_we, rq_relu;
  logic [$clog2(COLS)-1:0] rq_tbl_addr;
  logic [15:0]           rq_tbl_mult;
  logic [4:0]            rq_tbl_shift;
  logic signed [7:0]     rq_tbl_zp;
  logic [IDXW-1:0]       cfg_m;
  logic                  go, done, busy;
  logic [2:0]            state_dbg;
  logic signed [PW-1:0]  rd_data [COLS];
  logic signed [PW-1:0]  rd_chk;
  logic                  rd_valid;
  logic                  err_clear, array_abft_sticky, acc_abft_err, acc_abft_sticky, ctrl_err_sticky;
  logic                  fault_inject, ctrl_fault_inject;

  neo_core #(.ROWS(ROWS), .COLS(COLS), .XW(XW), .WW(WW), .PW(PW), .ACC_ROWS(ACC_ROWS),
             .ABUF_DEPTH(ABUF_DEPTH), .WBUF_DEPTH(WBUF_DEPTH)) dut (
    .clk(clk), .rst_n(rst_n),
    .abuf_we(abuf_we), .abuf_waddr(abuf_waddr), .abuf_wdata(abuf_wdata), .abuf_we2(1'b0), .abuf_wdata2(abuf_wdata),
    .wbuf_we(wbuf_we), .wbuf_waddr(wbuf_waddr), .wbuf_wdata(wbuf_wdata), .wcbuf_wdata(wcbuf_wdata),
    .wbuf_we2(1'b0), .wbuf_wdata2(wbuf_wdata), .wcbuf_wdata2(wcbuf_wdata),     // one entry per cycle here (the pair port is the DMA's, drop 0.32)
    .cfg_h(cfg_h), .cfg_w(cfg_w), .cfg_ho(cfg_ho), .cfg_wo(cfg_wo), .cfg_oy0(cfg_oy0), .cfg_oy_n(cfg_oy_n), .cfg_iy0(cfg_iy0),
    .cfg_s(cfg_s), .cfg_p(cfg_p),
    .cfg_k(cfg_k), .cfg_ct_n(cfg_ct_n), .cfg_ct0(cfg_ct0), .cfg_ky0(cfg_ky0), .cfg_kx0(cfg_kx0), .cfg_rn(cfg_rn),
    .cfg_contrib_n(cfg_contrib_n), .cfg_tile_pixels(cfg_tile_pixels), .cfg_regions_m1(cfg_regions_m1),
    .tiles_ready(tiles_ready), .cfg_m(cfg_m),
    .go(go), .done(done), .busy(busy), .state_dbg(state_dbg), .ct_free(ct_free), .ct_free_idx(ct_free_idx),
    .ext_valid(ext_valid), .ext_idx(ext_idx), .ext_y(ext_y), .ext_chk(ext_chk), .ext_ready(ext_ready),
    .reduce_ready(reduce_ready),
    .rd_data(rd_data), .rd_chk(rd_chk), .rd_valid(rd_valid), .rq_q(rq_q), .rq_valid(rq_valid),
    .rq_tbl_we(rq_tbl_we), .rq_tbl_addr(rq_tbl_addr), .rq_tbl_mult(rq_tbl_mult), .rq_tbl_shift(rq_tbl_shift),
    .rq_tbl_zp(rq_tbl_zp), .rq_relu(rq_relu),
    .err_clear(err_clear), .array_abft_sticky(array_abft_sticky), .acc_abft_err(acc_abft_err),
    .acc_abft_sticky(acc_abft_sticky), .ctrl_err_sticky(ctrl_err_sticky),
    .seq_err_sticky(seq_err_sticky), .rq_err_sticky(rq_err_sticky),
    .fault_inject(fault_inject), .ctrl_fault_inject(ctrl_fault_inject),
    .seq_fault_inject(seq_fault_inject), .rq_fault_inject(rq_fault_inject), .rq_tbl_fault_inject(rq_tbl_fault_inject),
    .wbuf_ce_sticky(wbuf_ce_sticky), .wbuf_ue_sticky(wbuf_ue_sticky), .rq_tbl_perr_sticky(rq_tbl_perr_sticky));

  // vectors
  logic [8*ROWS-1:0]       abuf_mem [0:`CORE_N_ABUF-1];
  logic [8*COLS+WCW-1:0]   wbuf_mem [0:`CORE_N_WBUF-1];
  logic [31:0]             y_mem    [0:M*COLS-1];
  string vecdir;

  int out_row = 0;
  int mismatches = 0;
  int acc_flags = 0;

  // X monitor: report the first cycle each key signal is unknown (which is where a real simulator
  // disagrees with the zero-initialised Python simulator)
  int cyc = 0;
  int timeout_n = 0;
  bit seen_x_xvec = 0, seen_x_y = 0, seen_x_wr = 0, seen_x_seq = 0;
  always @(negedge clk) begin
    cyc++;
    if (rst_n) begin
      if (!seen_x_xvec && dut.u_dp.f_valid === 1'b1 && $isunknown(dut.u_dp.x_vec[0])) begin
        seen_x_xvec = 1; $display("XMON cycle %0d: feeder x_vec[0] is X while f_valid=1 (seq state %0d, addr %0d)", cyc, dut.u_seq.state, dut.u_dp.u_feeder.addr); end
      if (!seen_x_y && dut.u_dp.valid_out === 1'b1 && $isunknown(dut.u_dp.y[0])) begin
        seen_x_y = 1; $display("XMON cycle %0d: array y[0] is X while valid_out=1", cyc); end
      if (!seen_x_wr && dut.u_dp.valid_out === 1'b1 && ($isunknown(dut.u_dp.idx_d) || $isunknown(dut.u_dp.first_d))) begin
        seen_x_wr = 1; $display("XMON cycle %0d: accumulator write index/first is X (idx_d=%b first_d=%b)", cyc, dut.u_dp.idx_d, dut.u_dp.first_d); end
      if (!seen_x_seq && ($isunknown(dut.u_seq.state) || $isunknown(dut.u_dp.f_valid))) begin
        seen_x_seq = 1; $display("XMON cycle %0d: sequencer state or feeder valid is X (state=%b f_valid=%b)", cyc, dut.u_seq.state, dut.u_dp.f_valid); end
    end
  end

  // value monitor (2-state simulators): weights in PE row 0 after the first swap, the first activation
  // vector, the first array output row with its check column, and the first accumulator write
  bit vm_w = 0, vm_x = 0, vm_y = 0, vm_acc = 0;
  always @(negedge clk) begin
    if (rst_n) begin
      if (!vm_w && dut.u_dp.u_core.u_array.g_row[0].g_col[0].g_data.u_pe.w_q !== 0) begin
        vm_w = 1;
        $display("VMON cycle %0d: PE row 0 active weights w_q[0..7] = %0d %0d %0d %0d %0d %0d %0d %0d, check PE wc_q = %0d; expected from wbuf entry 0: %0d %0d %0d %0d %0d %0d %0d %0d chk %0d",
                 cyc,
                 dut.u_dp.u_core.u_array.g_row[0].g_col[0].g_data.u_pe.w_q, dut.u_dp.u_core.u_array.g_row[0].g_col[1].g_data.u_pe.w_q,
                 dut.u_dp.u_core.u_array.g_row[0].g_col[2].g_data.u_pe.w_q, dut.u_dp.u_core.u_array.g_row[0].g_col[3].g_data.u_pe.w_q,
                 dut.u_dp.u_core.u_array.g_row[0].g_col[4].g_data.u_pe.w_q, dut.u_dp.u_core.u_array.g_row[0].g_col[5].g_data.u_pe.w_q,
                 dut.u_dp.u_core.u_array.g_row[0].g_col[6].g_data.u_pe.w_q, dut.u_dp.u_core.u_array.g_row[0].g_col[7].g_data.u_pe.w_q,
                 dut.u_dp.u_core.u_array.g_row[0].g_col[8].g_check.u_pe.w_q,
                 $signed(wbuf_mem[0][7:0]), $signed(wbuf_mem[0][15:8]), $signed(wbuf_mem[0][23:16]), $signed(wbuf_mem[0][31:24]),
                 $signed(wbuf_mem[0][39:32]), $signed(wbuf_mem[0][47:40]), $signed(wbuf_mem[0][55:48]), $signed(wbuf_mem[0][63:56]),
                 $signed(wbuf_mem[0][8*COLS +: WCW]));
        $display("VMON: wbuf read data at entry 0 as the sequencer sees it: w_in[0..3] = %0d %0d %0d %0d wc_in = %0d (w_raddr=%0d)",
                 dut.w_in[0], dut.w_in[1], dut.w_in[2], dut.w_in[3], dut.wc_in, dut.w_raddr);
      end
      if (!vm_x && dut.u_dp.f_valid === 1'b1) begin
        vm_x = 1;
        $display("VMON cycle %0d: first feeder vector x_vec[0..3] = %0d %0d %0d %0d (seq state %0d, ct %0d ky %0d kx %0d); abuf entry 0 bytes 0..3 = %0d %0d %0d %0d",
                 cyc, dut.u_dp.x_vec[0], dut.u_dp.x_vec[1], dut.u_dp.x_vec[2], dut.u_dp.x_vec[3], dut.u_seq.state, dut.u_seq.ct, dut.u_seq.ky, dut.u_seq.kx,
                 $signed(abuf_mem[0][7:0]), $signed(abuf_mem[0][15:8]), $signed(abuf_mem[0][23:16]), $signed(abuf_mem[0][31:24]));
      end
      if (!vm_y && dut.u_dp.valid_out === 1'b1) begin
        vm_y = 1;
        $display("VMON cycle %0d: first array output y[0..7] = %0d %0d %0d %0d %0d %0d %0d %0d y_chk = %0d (sum of y = %0d), idx_d=%0d first_d=%b",
                 cyc, dut.u_dp.y[0], dut.u_dp.y[1], dut.u_dp.y[2], dut.u_dp.y[3], dut.u_dp.y[4], dut.u_dp.y[5], dut.u_dp.y[6], dut.u_dp.y[7], dut.u_dp.y_chk,
                 dut.u_dp.y[0] + dut.u_dp.y[1] + dut.u_dp.y[2] + dut.u_dp.y[3] + dut.u_dp.y[4] + dut.u_dp.y[5] + dut.u_dp.y[6] + dut.u_dp.y[7],
                 dut.u_dp.idx_d, dut.u_dp.first_d);
      end
    end
  end

  always @(negedge clk) begin
    if (rst_n && rd_valid) begin
      if (out_row < M) begin
        for (int j = 0; j < COLS; j++) begin
          if (rd_data[j] !== $signed(y_mem[out_row*COLS + j])) begin
            mismatches++;
            if (mismatches <= 5)
              $display("  mismatch row %0d col %0d: got %0d expected %0d", out_row, j, rd_data[j], $signed(y_mem[out_row*COLS + j]));
          end
        end
        if (acc_abft_err) acc_flags++;
      end else begin
        $display("ERROR: more than M=%0d rows drained", M);
        mismatches++;
      end
      out_row++;
    end
  end

  initial begin
    if (!$value$plusargs("VECDIR=%s", vecdir)) vecdir = "vectors_core";
    $readmemh({vecdir, "/abuf.hex"},  abuf_mem);
    $readmemh({vecdir, "/wbuf.hex"},  wbuf_mem);
    $readmemh({vecdir, "/y_exp.hex"}, y_mem);
    $display("tb_neo_core: ROWS=%0d COLS=%0d conv %0dx%0dx%0d k%0d s%0d p%0d -> M=%0d rows, %0d channel tiles",
             ROWS, COLS, `CORE_CT_N * ROWS, `CORE_H, `CORE_W, `CORE_K, `CORE_S, `CORE_P, M, `CORE_CT_N);

    abuf_we = 0; abuf_waddr = '0; wbuf_we = 0; wbuf_waddr = '0; wcbuf_wdata = '0;
    for (int c = 0; c < ROWS; c++) abuf_wdata[c] = '0;
    for (int j = 0; j < COLS; j++) wbuf_wdata[j] = '0;
    go = 0; err_clear = 0; fault_inject = 0; ctrl_fault_inject = 0;
    ext_valid = 0; ext_idx = '0; ext_chk = '0; for (int j = 0; j < COLS; j++) ext_y[j] = '0;
    rq_tbl_we = 0; rq_tbl_addr = '0; rq_tbl_mult = '0; rq_tbl_shift = '0; rq_tbl_zp = '0; rq_relu = 0;
    cfg_h = `CORE_H; cfg_w = `CORE_W; cfg_ho = `CORE_HO; cfg_wo = `CORE_WO; cfg_s = `CORE_S; cfg_p = `CORE_P;
    cfg_k = `CORE_K; cfg_ct_n = `CORE_CT_N; cfg_tile_pixels = `CORE_TILE_PIXELS; cfg_m = M;
    cfg_ct0 = 0; cfg_ky0 = 0; cfg_kx0 = 0; cfg_rn = `CORE_RN; cfg_contrib_n = 0;
    cfg_regions_m1 = 16'h7FFF; tiles_ready = 16'h7FFF; seq_fault_inject = 0; rq_fault_inject = 0; rq_tbl_fault_inject = 0;
    cfg_oy0 = 0; cfg_oy_n = `CORE_HO; cfg_iy0 = 0;
    repeat (3) @(negedge clk);
    rst_n = 1'b1;
    repeat (2) @(negedge clk);

    // DMA: activation channel tiles
    for (int a = 0; a < `CORE_N_ABUF; a++) begin
      @(negedge clk);
      abuf_we = 1'b1; abuf_waddr = a;
      for (int c = 0; c < ROWS; c++) abuf_wdata[c] = $signed(abuf_mem[a][8*c +: 8]);
    end
    @(negedge clk); abuf_we = 1'b0;
    // DMA: weight tiles
    for (int a = 0; a < `CORE_N_WBUF; a++) begin
      @(negedge clk);
      wbuf_we = 1'b1; wbuf_waddr = a;
      for (int j = 0; j < COLS; j++) wbuf_wdata[j] = $signed(wbuf_mem[a][8*j +: 8]);
      wcbuf_wdata = $signed(wbuf_mem[a][8*COLS +: WCW]);
    end
    @(negedge clk); wbuf_we = 1'b0;

    // go
    @(negedge clk); go = 1'b1;
    @(negedge clk); go = 1'b0;
    timeout_n = 0;
    while (!done && timeout_n < 200000) begin
      @(negedge clk);
      timeout_n++;
    end
    if (!done) begin
      $display("ERROR: timeout waiting for done");
      $finish;
    end
    repeat (3) @(negedge clk);

    if (out_row == M && mismatches == 0 && acc_flags == 0 && !array_abft_sticky && !acc_abft_sticky && !ctrl_err_sticky
        && !seq_err_sticky && !rq_err_sticky)
      $display("RESULT: ALL PASS (%0d rows drained, no mismatches, no error flags)", out_row);
    else
      $display("RESULT: FAIL (rows=%0d/%0d mismatches=%0d acc_flags=%0d sticky array/acc/ctrl=%0b/%0b/%0b)",
               out_row, M, mismatches, acc_flags, array_abft_sticky, acc_abft_sticky, ctrl_err_sticky);
    $finish;
  end
endmodule
