// neo_mac_core_v02.sv -- AstraCore Neo MAC core v0.2 (2026-10-04)
//   act_feeder (local activation buffer, implicit im2col) -> neo_mac_core (32x32 double-buffered
//   weight-stationary array + ABFT column) -> acc_bank (per-core accumulator, ABFT at drain).
//   One run = one (kernel position, channel tile, output tile) pass over the output grid; the
//   compiler's work item for a layer is a sequence of runs with the first run of each output
//   chunk in overwrite mode (acc_first), then a drain. Weight tiles are loaded into the shadow
//   chain during the previous run (start >= ROWS+COLS cycles after that run's start).
module neo_mac_core_v02 #(
  parameter int ROWS       = 32,
  parameter int COLS       = 32,
  parameter int XW         = 8,
  parameter int WW         = 8,
  parameter int PW         = 32,
  parameter int WCW        = WW + $clog2(ROWS) + 1,
  parameter int ACC_ROWS   = 512,
  parameter int ABUF_DEPTH = 4096,
  parameter int IDXW       = $clog2(ACC_ROWS),
  parameter int AW         = $clog2(ABUF_DEPTH),
  parameter int PE_LAT     = 2,
  parameter int FAULT_ROW  = 0,
  parameter int FAULT_COL  = 0
)(
  input  logic                  clk,
  input  logic                  rst_n,
  // activation buffer write port
  input  logic                  abuf_we,
  input  logic [AW-1:0]         abuf_waddr,
  input  logic signed [XW-1:0]  abuf_wdata [ROWS],
  input  logic                  abuf_we2,                 // upper entry of the pair (drop 0.32)
  input  logic signed [XW-1:0]  abuf_wdata2 [ROWS],
  // run configuration and control
  input  logic signed [15:0]    cfg_h,
  input  logic signed [15:0]    cfg_w,
  input  logic signed [15:0]    cfg_ho,
  input  logic signed [15:0]    cfg_wo,
  input  logic signed [15:0]    cfg_oy0,
  input  logic signed [15:0]    cfg_oy_n,
  input  logic signed [15:0]    cfg_iy0,
  input  logic signed [15:0]    cfg_s,
  input  logic signed [15:0]    cfg_p,
  input  logic signed [15:0]    cfg_tile_pixels,
  input  logic signed [15:0]    cfg_regions_m1,
  input  logic signed [15:0]    cfg_ct,
  input  logic signed [15:0]    cfg_ky,
  input  logic signed [15:0]    cfg_kx,
  input  logic                  acc_first,
  input  logic                  start,
  output logic                  busy,
  // weight shadow-chain load
  input  logic                  w_load,
  input  logic signed [WW-1:0]  w_in  [COLS],
  input  logic signed [WCW-1:0] wc_in,
  // reduce port (partial sums from other cores)
  input  logic                  ext_valid,
  input  logic [IDXW-1:0]       ext_idx,
  input  logic signed [PW-1:0]  ext_y   [COLS],
  input  logic signed [PW-1:0]  ext_chk,
  output logic                  ext_ready,
  // drain
  input  logic                  rd_en,
  input  logic [IDXW-1:0]       rd_idx,
  output logic signed [PW-1:0]  rd_data [COLS],
  output logic signed [PW-1:0]  rd_chk,
  output logic                  rd_valid,
  // safety
  input  logic                  abft_clear,
  output logic                  array_abft_err,       // per-row check at the array output
  output logic                  array_abft_sticky,
  output logic                  acc_abft_err,         // check at drain, covers the accumulator too
  output logic                  acc_abft_sticky,
  output logic                  ctrl_err,             // feeder control duplication comparator (R7)
  output logic                  ctrl_err_sticky,
  // DV only
  input  logic                  fault_inject,
  input  logic                  ctrl_fault_inject
);
  localparam int LATENCY = PE_LAT * ROWS + COLS;

  logic signed [XW-1:0] x_vec [ROWS];
  logic                 f_valid;
  logic                 f_swap;
  logic [IDXW-1:0]      f_idx;
  logic                 f_first;

  act_feeder #(.ROWS(ROWS), .XW(XW), .ABUF_DEPTH(ABUF_DEPTH), .AW(AW), .IDXW(IDXW)) u_feeder (
    .clk(clk), .rst_n(rst_n),
    .abuf_we(abuf_we), .abuf_waddr(abuf_waddr), .abuf_wdata(abuf_wdata), .abuf_we2(abuf_we2), .abuf_wdata2(abuf_wdata2),
    .cfg_h(cfg_h), .cfg_w(cfg_w), .cfg_ho(cfg_ho), .cfg_wo(cfg_wo), .cfg_oy0(cfg_oy0), .cfg_oy_n(cfg_oy_n), .cfg_iy0(cfg_iy0),
    .cfg_s(cfg_s), .cfg_p(cfg_p), .cfg_tile_pixels(cfg_tile_pixels), .cfg_regions_m1(cfg_regions_m1), .cfg_ct(cfg_ct), .cfg_ky(cfg_ky), .cfg_kx(cfg_kx), .acc_first(acc_first),
    .start(start), .busy(busy),
    .x_vec(x_vec), .valid(f_valid), .swap(f_swap), .row_idx(f_idx), .first(f_first),
    .ctrl_clear(abft_clear), .ctrl_err(ctrl_err), .ctrl_err_sticky(ctrl_err_sticky),
    .ctrl_fault_inject(ctrl_fault_inject));

  logic signed [PW-1:0] y     [COLS];
  logic signed [PW-1:0] y_chk;
  logic                 valid_out;

  neo_mac_core #(.ROWS(ROWS), .COLS(COLS), .XW(XW), .WW(WW), .PW(PW), .WCW(WCW), .PE_LAT(PE_LAT),
                 .FAULT_ROW(FAULT_ROW), .FAULT_COL(FAULT_COL)) u_core (
    .clk(clk), .rst_n(rst_n),
    .w_load(w_load), .w_in(w_in), .wc_in(wc_in),
    .x_in(x_vec), .valid_in(f_valid), .swap_in(f_swap),
    .y(y), .y_chk(y_chk), .valid_out(valid_out),
    .abft_clear(abft_clear), .abft_err(array_abft_err), .abft_err_sticky(array_abft_sticky),
    .fault_inject(fault_inject));

  // row index and first flag travel the same LATENCY as the data
  logic [IDXW-1:0] idx_d;
  logic            first_d;
  delay_line #(.W(IDXW), .DEPTH(LATENCY)) u_idx_dl   (.clk(clk), .rst_n(rst_n), .d(f_idx),   .q(idx_d));
  delay_line #(.W(1),    .DEPTH(LATENCY)) u_first_dl (.clk(clk), .rst_n(rst_n), .d(f_first), .q(first_d));

  acc_bank #(.COLS(COLS), .PW(PW), .ACC_ROWS(ACC_ROWS), .IDXW(IDXW)) u_acc (
    .clk(clk), .rst_n(rst_n),
    .wr_valid(valid_out), .wr_idx(idx_d), .wr_first(first_d), .wr_y(y), .wr_chk(y_chk),
    .ext_valid(ext_valid), .ext_idx(ext_idx), .ext_y(ext_y), .ext_chk(ext_chk), .ext_ready(ext_ready),
    .rd_en(rd_en), .rd_idx(rd_idx), .rd_data(rd_data), .rd_chk(rd_chk), .rd_valid(rd_valid),
    .abft_clear(abft_clear), .abft_err(acc_abft_err), .abft_err_sticky(acc_abft_sticky));
endmodule
