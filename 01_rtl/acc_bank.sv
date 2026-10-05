// acc_bank.sv -- per-core output accumulator with ABFT at drain, v0.3 (drop 0.6: reduce port).
//   ACC_ROWS x COLS x PW-bit partial sums plus one check-column accumulator per row.
//   Write: when wr_valid, acc[wr_idx] <= (wr_first ? 0 : acc[wr_idx]) + wr_y; the check sum
//   accumulates alongside, so sum_j acc[r][j] == acc_chk[r] holds across every K-tile and any
//   single-point fault in the array OR in this memory breaks it.
//   Reduce (R8): partial sums from other cores arrive on ext_*; acc[ext_idx] += ext_y and the
//   check column += ext_chk, so the drain-time ABFT also covers the reduction transfer. The local
//   write has priority (ext_ready = !wr_valid); the sequencer gates acceptance to its reduce state.
//   Drain: rd_en reads row rd_idx one cycle later with rd_valid; abft_err flags a mismatch.
//   In silicon this is a 64 KB ECC SRAM with a two-cycle read-modify-write; behavioural model.
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
  logic signed [PW-1:0] acc     [ACC_ROWS][COLS];
  logic signed [PW-1:0] acc_chk [ACC_ROWS];

  assign ext_ready = !wr_valid;

  always_ff @(posedge clk) begin
    if (wr_valid) begin
      for (int j = 0; j < COLS; j++) begin
        acc[wr_idx][j] <= (wr_first ? PW'(0) : acc[wr_idx][j]) + wr_y[j];
      end
      acc_chk[wr_idx] <= (wr_first ? PW'(0) : acc_chk[wr_idx]) + wr_chk;
    end else if (ext_valid) begin
      for (int j = 0; j < COLS; j++) begin
        acc[ext_idx][j] <= acc[ext_idx][j] + ext_y[j];
      end
      acc_chk[ext_idx] <= acc_chk[ext_idx] + ext_chk;
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rd_valid <= 1'b0;
      rd_chk   <= '0;
      for (int j = 0; j < COLS; j++) rd_data[j] <= '0;
    end else begin
      rd_valid <= rd_en;
      if (rd_en) begin
        for (int j = 0; j < COLS; j++) rd_data[j] <= acc[rd_idx][j];
        rd_chk <= acc_chk[rd_idx];
      end
    end
  end

  abft_checker #(.COLS(COLS), .PW(PW)) u_abft (
    .clk(clk), .rst_n(rst_n), .valid(rd_valid), .y(rd_data), .y_chk(rd_chk),
    .abft_clear(abft_clear), .err(abft_err), .err_sticky(abft_err_sticky));
endmodule
