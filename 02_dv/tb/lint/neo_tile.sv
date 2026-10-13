// neo_tile.sv (lint stub, drop 0.42) -- neo_tile's parameters and ports with no body, for `make lint`'s pass over neo_top at
//   the silicon parameters: Verilator flattens every instance even with --lint-only, and 64 tiles of 32x32 cores need more
//   than 16 GB (measured: 2x2 1.5 GB, 4x4 over 5.8 GB), so the top's own logic -- the bridges, the control plane, the bridge
//   column's routers, the fences, the wiring at XW = 4 / YW = 3 -- is linted against this stub, and the real tile is linted
//   on its own at the chip top's tile parameters (the pass before it in the Makefile). Every port of rtl/neo_tile.sv, in its
//   order and widths: a port added there and not here fails this pass (pin not found), so the two cannot drift silently.
//   Outputs are tied to zero, inputs and parameters sunk.
module neo_tile #(
  parameter int XW = 2, YW = 2, NX = 2, NY = 2,
  parameter int WPF = 1,
  parameter int DWL = 32 + 32 * WPF,
  parameter int FWL = 1 + 2 * (XW + YW) + 8 + DWL,
  parameter int ROWS = 16, COLS = 8, PW = 32,
  parameter int WCW = 8 + $clog2(ROWS) + 1,
  parameter int ACC_ROWS = 64, ABUF_DEPTH = 256, WBUF_DEPTH = 512, BANK_DEPTH = 4096,
  parameter int IDXW = $clog2(ACC_ROWS), AW = $clog2(ABUF_DEPTH), WAW = $clog2(WBUF_DEPTH), BAW = $clog2(BANK_DEPTH),
  parameter int FETCH_TIMEOUT = 1048575,
  parameter int BIST_WORDS = BANK_DEPTH,
  parameter int PDEPTH = 32,
  parameter int PAW = $clog2(PDEPTH),
  parameter int NCHK = 1,
  parameter int IW = (NCHK > 1) ? 2 : 0,
  parameter int DV_HOOKS = 0
)(
  input  logic          clk,
  input  logic          rst_n,
  input  logic          router_rst_n,
  input  logic          tile_clk_en,
  input  logic          iso_en,
  input  logic [XW-1:0] my_x,
  input  logic [YW-1:0] my_y,
  input  logic [3:0]       in_valid,
  input  logic [4*FWL-1:0] in_flit,
  output logic [3:0]       in_ready,
  output logic [3:0]       out_valid,
  output logic [4*FWL-1:0] out_flit,
  input  logic [3:0]       out_ready,
  input  logic          h_we,
  input  logic          h_re,
  input  logic [7:0]    h_addr,
  input  logic [31:0]   h_wdata,
  output logic [31:0]   h_rdata,
  output logic          err_pin,
  output logic          prog_done,
  output logic          drain_busy,
  output logic          done,
  output logic          parity_err,
  output logic          crc_err,
  output logic          array_abft_sticky,
  output logic          acc_abft_sticky,
  output logic          ctrl_err_sticky,
  output logic          seq_err_sticky,
  output logic          rq_err_sticky,
  output logic          ecc_ce,
  output logic          ecc_ue,
  output logic          wbuf_ce,
  output logic          wbuf_ue,
  output logic          rq_tbl_perr,
  output logic          lost_err,
  output logic          fetch_timeout,
  output logic [15:0]   drain_crc_last,
  output logic [15:0]   drain_msg_cnt,
  input  logic          dv_bd_we,
  input  logic [BAW-1:0] dv_bd_addr,
  input  logic [38:0]   dv_bd_wdata,
  output logic [38:0]   dv_bd_rdata,
  input  logic          dv_fi_en,
  input  logic [2:0]    dv_fi_sel,
  input  logic [19:0]   dv_fi_idx,
  input  logic [DWL-1:0] dv_fi_mask,
  output logic [4:0]    dv_rt_valid,
  output logic [FWL-1:0] dv_rt_flit4,
  output logic [31:0]   dv_obs
);
  assign in_ready = '0; assign out_valid = '0; assign out_flit = '0; assign h_rdata = '0; assign err_pin = 1'b0;
  assign prog_done = 1'b0; assign drain_busy = 1'b0; assign done = 1'b0; assign parity_err = 1'b0; assign crc_err = 1'b0;
  assign array_abft_sticky = 1'b0; assign acc_abft_sticky = 1'b0; assign ctrl_err_sticky = 1'b0; assign seq_err_sticky = 1'b0;
  assign rq_err_sticky = 1'b0; assign ecc_ce = 1'b0; assign ecc_ue = 1'b0; assign wbuf_ce = 1'b0; assign wbuf_ue = 1'b0;
  assign rq_tbl_perr = 1'b0; assign lost_err = 1'b0; assign fetch_timeout = 1'b0; assign dv_bd_rdata = '0; assign dv_rt_valid = '0;
  assign dv_rt_flit4 = '0; assign dv_obs = '0; assign drain_crc_last = '0; assign drain_msg_cnt = '0;
  localparam int UNUSED_PARAMS = NX + NY + ROWS + COLS + PW + WCW + IDXW + AW + WAW + FETCH_TIMEOUT + BIST_WORDS + PAW + NCHK + IW + DV_HOOKS;
  logic unused_stub;
  assign unused_stub = ^{clk, rst_n, router_rst_n, tile_clk_en, iso_en, my_x, my_y, in_valid, in_flit, out_ready, h_we, h_re, h_addr, h_wdata, dv_bd_we, dv_bd_addr,
                         dv_bd_wdata, dv_fi_en, dv_fi_sel, dv_fi_idx, dv_fi_mask, 32'(UNUSED_PARAMS)};
endmodule
