// tb_tile_mesh.sv -- Xcelium testbench for tile_mesh (drop 0.13): replays M1 through the register bus.
//   Vectors from sim/gen_tile_vectors.py (golden models only): the source bank image of tile (1,1) as
//   (39,32) codewords, the descriptor registers, the DMA program, the expected rows at tile (1,0).
//   make xrun_tiles. Add dv/sva/bind_all.sv to the file list to run the assertion set.
`timescale 1ns/1ps
module tb_tile_mesh;
  localparam int NX = 2, NY = 2, ROWS = 16, COLS = 8, N = NX * NY;
  localparam int M = 36, R0 = 2048, NPROG = 8;              // program words written by gen_tile_vectors.py
  logic clk = 0, rst_n = 0;
  always #0.25 clk = ~clk;                                    // 2 GHz
  logic        h_we [N], h_re [N];
  logic [7:0]  h_addr [N];
  logic [31:0] h_wdata [N], h_rdata [N];
  logic        err_pin [N], prog_done [N], drain_busy [N], done [N];
  logic        parity_err [N], crc_err [N], array_abft_sticky [N], acc_abft_sticky [N], ctrl_err_sticky [N];
  logic        seq_err_sticky [N], rq_err_sticky [N], ecc_ce [N], ecc_ue [N], wbuf_ce [N], wbuf_ue [N], rq_tbl_perr [N];
  logic        lost_err [N], fetch_timeout [N];

  localparam int FWL = 1 + 2 * (2 + 2) + 8 + 64;            // tile_mesh's link flit at its defaults (XW = YW = 2, WPF = 1)
  logic [38:0] dv_bd_rdata; logic [4:0] dv_rt_valid; logic [FWL-1:0] dv_rt_flit4;   // the testbench hooks (drop 0.35), tied off here
  tile_mesh #(.NX(NX), .NY(NY), .ROWS(ROWS), .COLS(COLS), .ACC_ROWS(64), .ABUF_DEPTH(256), .WBUF_DEPTH(512), .BANK_DEPTH(4096)) dut (
    .clk(clk), .rst_n(rst_n), .h_we(h_we), .h_re(h_re), .h_addr(h_addr), .h_wdata(h_wdata), .h_rdata(h_rdata), .err_pin(err_pin),
    .prog_done(prog_done), .drain_busy(drain_busy), .done(done), .parity_err(parity_err), .crc_err(crc_err),
    .array_abft_sticky(array_abft_sticky), .acc_abft_sticky(acc_abft_sticky), .ctrl_err_sticky(ctrl_err_sticky),
    .seq_err_sticky(seq_err_sticky), .rq_err_sticky(rq_err_sticky), .ecc_ce(ecc_ce), .ecc_ue(ecc_ue),
    .wbuf_ce(wbuf_ce), .wbuf_ue(wbuf_ue), .rq_tbl_perr(rq_tbl_perr), .lost_err(lost_err), .fetch_timeout(fetch_timeout),
    .dv_bd_node('0), .dv_bd_we(1'b0), .dv_bd_addr('0), .dv_bd_wdata('0), .dv_bd_rdata(dv_bd_rdata),
    .dv_fi_node('0), .dv_fi_sel('0), .dv_fi_en(1'b0), .dv_fi_idx('0), .dv_fi_mask('0), .dv_rt_valid(dv_rt_valid), .dv_rt_flit4(dv_rt_flit4));

  logic [15:0] cfg_v [19];
  logic [63:0] prog_v [16];
  logic [31:0] y_exp [M * COLS];
  int          mism, n, fd, code, addr;
  logic [38:0] word39;
  string       line;

  task automatic reg_write(int node, logic [7:0] a, logic [31:0] v);
    @(negedge clk); h_we[node] = 1; h_addr[node] = a; h_wdata[node] = v; @(negedge clk); h_we[node] = 0;
  endtask
  task automatic reg_read(int node, logic [7:0] a, output logic [31:0] v);
    @(negedge clk); h_re[node] = 1; h_addr[node] = a; @(negedge clk); h_re[node] = 0; v = h_rdata[node];
  endtask

  initial begin
    for (int i = 0; i < N; i++) begin h_we[i] = 0; h_re[i] = 0; h_addr[i] = 0; h_wdata[i] = 0; end
    // sparse bank image into tile (1,1)'s bank: "@addr word39" per line
    fd = $fopen("vectors_tile/bank11.hex", "r");
    if (fd == 0) begin $display("FAIL: vectors_tile/bank11.hex not found (run python3 sim/gen_tile_vectors.py)"); $finish; end
    while (!$feof(fd)) begin
      code = $fgets(line, fd);
      if (code > 0 && line.len() > 2) begin
        code = $sscanf(line, "@%h %h", addr, word39);
        if (code == 2) dut.g_y[1].g_x[1].u_t.u_bank.u_bank.mem[addr % 16][addr / 16] = word39;   // lane, row (drop 0.28)
      end
    end
    $fclose(fd);
    $readmemh("vectors_tile/cfg.hex", cfg_v);
    $readmemh("vectors_tile/prog.hex", prog_v);
    $readmemh("vectors_tile/y_exp.hex", y_exp);
    repeat (4) @(negedge clk); rst_n = 1; repeat (2) @(negedge clk);
    // descriptor and program through tile (0,0)'s registers, then start
    for (int i = 0; i < 18; i++) reg_write(0, 8'h10 + i, {16'd0, cfg_v[i]});
    reg_write(0, 8'h24, {16'd0, cfg_v[18]});
    for (int i = 0; i < NPROG; i++) begin
      reg_write(0, 8'h02, i); reg_write(0, 8'h03, prog_v[i][31:0]); reg_write(0, 8'h04, prog_v[i][63:32]);
    end
    reg_write(0, 8'h00, 32'd1);
    n = 0;
    while (!(prog_done[0] && !drain_busy[0]) && n < 200000) begin @(negedge clk); n++; end
    repeat (40) @(negedge clk);
    // result rows at tile (1,0): COLS words + check per row at R0 (codewords; data in [31:0])
    mism = 0;
    for (int m = 0; m < M; m++) begin
      for (int c = 0; c < COLS; c++) begin
        word39 = dut.g_y[0].g_x[1].u_t.u_bank.u_bank.mem[(R0 + m * (COLS + 1) + c) % 16][(R0 + m * (COLS + 1) + c) / 16];
        if (word39[31:0] !== y_exp[m * COLS + c]) begin
          mism++;
          if (mism <= 5) $display("row %0d col %0d: got %0d expected %0d", m, c, $signed(word39[31:0]), $signed(y_exp[m * COLS + c]));
        end
      end
    end
    if (mism == 0 && n < 200000 && !crc_err[0] && !parity_err[0] && !acc_abft_sticky[0] && !err_pin[0] && !lost_err[0] && !fetch_timeout[0])
      $display("PASS: M1 through the register bus on Xcelium, %0d rows bit-exact in %0d clocks, flags clean", M, n);
    else
      $display("FAIL: %0d mismatches, %0d clocks, crc %0d parity %0d abft %0d err_pin %0d lost %0d timeout %0d",
               mism, n, crc_err[0], parity_err[0], acc_abft_sticky[0], err_pin[0], lost_err[0], fetch_timeout[0]);
    $finish;
  end
endmodule
