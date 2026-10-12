// acc_bank.sv -- per-core output accumulator with ABFT at drain, v0.3 (drop 0.6: reduce port).
//   ACC_ROWS x COLS x PW-bit partial sums plus one check-column accumulator per row.
//   Write: when wr_valid, acc[wr_idx] <= (wr_first ? 0 : acc[wr_idx]) + wr_y; the check sum
//   accumulates alongside, so sum_j acc[r][j] == acc_chk[r] holds across every K-tile and any
//   single-point fault in the array OR in this memory breaks it.
//   Reduce (R8): partial sums from other cores arrive on ext_*; acc[ext_idx] += ext_y and the
//   check column += ext_chk, so the drain-time ABFT also covers the reduction transfer. The local
//   write has priority (ext_ready = !wr_valid); the sequencer gates acceptance to its reduce state.
//   Drain: rd_en reads row rd_idx one cycle later with rd_valid; abft_err flags a mismatch.
//   In silicon this is a 64 KB ECC SRAM with a two-cycle read-modify-write; behavioural model. Drop 0.40: the rows live
//   in a neo_mem_rf leaf of ACC_ROWS words x (COLS + 1) x PW bits (the check column is the top word): port A, the
//   asynchronous read at the written row, feeds the accumulate; port B, registered, is the drain read. The leaf is what
//   the synthesis flow black-boxes and a memory compiler replaces; the adders and the ABFT check stay here.
module acc_bank #(
  parameter int COLS     = 32,
  parameter int PW       = 32,
  parameter int ACC_ROWS = 512,
  parameter int IDXW     = $clog2(ACC_ROWS)
)(
  input  logic                 clk,
  input  logic                 rst_n,
  input  logic                 wr_valid,
  input  logic [IDXW-1:0]      wr_idx,
  input  logic                 wr_first,
  input  logic signed [PW-1:0] wr_y   [COLS],
  input  logic signed [PW-1:0] wr_chk,
  input  logic                 ext_valid,
  input  logic [IDXW-1:0]      ext_idx,
  input  logic signed [PW-1:0] ext_y   [COLS],
  input  logic signed [PW-1:0] ext_chk,
  output logic                 ext_ready,
  input  logic                 rd_en,
  input  logic [IDXW-1:0]      rd_idx,
  output logic signed [PW-1:0] rd_data [COLS],
  output logic signed [PW-1:0] rd_chk,
  output logic                 rd_valid,
  input  logic                 abft_clear,
  output logic                 abft_err,
  output logic                 abft_err_sticky
);
  localparam int RW = (COLS + 1) * PW;              // a row: COLS partial sums and the check sum
  logic [RW-1:0]        row_old;                    // port A: the row at acc_idx, before this cycle's write
  logic [RW-1:0]        row_new;                    // what the write stores
  logic [RW-1:0]        row_rd;                     // port B: the drained row
  logic [IDXW-1:0]      acc_idx;
  logic                 acc_we;
  logic                 unused_bd;
  logic [RW-1:0]        unused_bd_rd;

  assign ext_ready = !wr_valid;
  assign acc_idx   = wr_valid ? wr_idx : ext_idx;   // the local write has priority
  assign acc_we    = wr_valid || ext_valid;

  // the accumulate: a local write adds wr_y to the row (or to zero on wr_first), a partial-sum row adds ext_y to it
  always_comb begin
    for (int j = 0; j < COLS; j++) begin
      logic signed [PW-1:0] old_j;
      old_j = (wr_valid && wr_first) ? PW'(0) : row_old[j*PW +: PW];
      row_new[j*PW +: PW] = old_j + (wr_valid ? wr_y[j] : ext_y[j]);
    end
    begin
      logic signed [PW-1:0] old_c;
      old_c = (wr_valid && wr_first) ? PW'(0) : row_old[COLS*PW +: PW];
      row_new[COLS*PW +: PW] = old_c + (wr_valid ? wr_chk : ext_chk);
    end
  end

  neo_mem_rf #(.W(RW), .D(ACC_ROWS), .G(1), .RB(1), .DV_HOOKS(0)) u_mem (
    .clk(clk),
    .we(acc_we), .waddr(acc_idx), .wdata(row_new),
    .ra_addr(acc_idx), .ra_data(row_old),
    .rb_en(rd_en), .rb_addr(rd_idx), .rb_data(row_rd),
    .bd_we('0), .bd_addr('0), .bd_wdata('0), .bd_rdata(unused_bd_rd),
    .fi_en(1'b0), .fi_or(1'b0), .fi_addr('0), .fi_mask('0));
  assign unused_bd = ^unused_bd_rd;

  always_comb begin
    for (int j = 0; j < COLS; j++) rd_data[j] = row_rd[j*PW +: PW];
    rd_chk = row_rd[COLS*PW +: PW];
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) rd_valid <= 1'b0;
    else        rd_valid <= rd_en;
  end

  abft_checker #(.COLS(COLS), .PW(PW)) u_abft (
    .clk(clk), .rst_n(rst_n), .valid(rd_valid), .y(rd_data), .y_chk(rd_chk),
    .abft_clear(abft_clear), .err(abft_err), .err_sticky(abft_err_sticky));
endmodule
