// neo_core.sv -- AstraCore Neo MAC core with its sequencer (drop 0.4).
//   core_seq (layer descriptor -> runs, shadow loads, drain) + neo_mac_core_v02 (feeder, array,
//   accumulator) + requant (INT32 -> INT8 per output channel). The host or DMA fills the activation
//   and weight buffers and the requantization tables, writes the descriptor, pulses go, and reads
//   the drained rows (INT32 on rd_data/rd_valid, INT8 on rq_q/rq_valid) until done. All three ABFT/duplication
//   error flags are sticky and cleared together.
module neo_core #(
  parameter int ROWS       = 32,
  parameter int COLS       = 32,
  parameter int XW         = 8,
  parameter int WW         = 8,
  parameter int PW         = 32,
  parameter int WCW        = WW + $clog2(ROWS) + 1,
  parameter int ACC_ROWS   = 512,
  parameter int ABUF_DEPTH = 4096,
  parameter int WBUF_DEPTH = 1024,
  parameter int IDXW       = $clog2(ACC_ROWS),
  parameter int AW         = $clog2(ABUF_DEPTH),
  parameter int WAW        = $clog2(WBUF_DEPTH),
  parameter int PE_LAT     = 2,
  parameter int FAULT_ROW  = 0,
  parameter int FAULT_COL  = 0
)(
  input  logic                  clk,
  input  logic                  rst_n,
  // DMA write ports
  input  logic                  abuf_we,
  input  logic [AW-1:0]         abuf_waddr,
  input  logic signed [XW-1:0]  abuf_wdata [ROWS],
  input  logic                  abuf_we2,                 // the upper entry of the same pair, abuf_waddr even (drop 0.32)
  input  logic signed [XW-1:0]  abuf_wdata2 [ROWS],
  input  logic                  wbuf_we,
  input  logic [WAW-1:0]        wbuf_waddr,
  input  logic signed [WW-1:0]  wbuf_wdata [COLS],
  input  logic signed [WCW-1:0] wcbuf_wdata,
  input  logic                  wbuf_we2,                 // the upper entry of the same pair, wbuf_waddr even (drop 0.32)
  input  logic signed [WW-1:0]  wbuf_wdata2 [COLS],
  input  logic signed [WCW-1:0] wcbuf_wdata2,
  // layer descriptor
  input  logic signed [15:0]    cfg_h,
  input  logic signed [15:0]    cfg_w,
  input  logic signed [15:0]    cfg_ho,
  input  logic signed [15:0]    cfg_wo,
  input  logic signed [15:0]    cfg_oy0,
  input  logic signed [15:0]    cfg_oy_n,
  input  logic signed [15:0]    cfg_iy0,
  input  logic signed [15:0]    cfg_s,
  input  logic signed [15:0]    cfg_p,
  input  logic signed [15:0]    cfg_k,
  input  logic signed [15:0]    cfg_ct_n,
  input  logic signed [15:0]    cfg_ct0,
  input  logic signed [15:0]    cfg_ky0,
  input  logic signed [15:0]    cfg_kx0,
  input  logic signed [15:0]    cfg_rn,
  input  logic signed [15:0]    cfg_contrib_n,
  input  logic signed [15:0]    cfg_tile_pixels,
  input  logic signed [15:0]    cfg_regions_m1,     // activation buffer regions minus one (0 = all tiles resident)
  input  logic signed [15:0]    tiles_ready,        // from the DMA; tie to a large value when all tiles are resident
  input  logic [IDXW-1:0]       cfg_m,
  input  logic                  go,
  output logic                  done,
  output logic                  busy,
  output logic [2:0]            state_dbg,
  output logic                  ct_free,
  output logic signed [15:0]    ct_free_idx,
  // reduce port: partial sums from contributor cores (accepted only while reduce_ready)
  input  logic                  ext_valid,
  input  logic [IDXW-1:0]       ext_idx,
  input  logic signed [PW-1:0]  ext_y   [COLS],
  input  logic signed [PW-1:0]  ext_chk,
  output logic                  ext_ready,
  output logic                  reduce_ready,
  // drained results: INT32 (ABFT-checked) and, two cycles later, requantized INT8
  output logic signed [PW-1:0]  rd_data [COLS],
  output logic signed [PW-1:0]  rd_chk,
  output logic                  rd_valid,
  output logic signed [7:0]     rq_q [COLS],
  output logic                  rq_valid,
  // requantization tables (per output channel) and mode
  input  logic                  rq_tbl_we,
  input  logic [$clog2(COLS)-1:0] rq_tbl_addr,
  input  logic [15:0]           rq_tbl_mult,
  input  logic [4:0]            rq_tbl_shift,
  input  logic signed [7:0]     rq_tbl_zp,
  input  logic                  rq_relu,
  // safety
  input  logic                  err_clear,
  output logic                  array_abft_sticky,
  output logic                  acc_abft_err,
  output logic                  acc_abft_sticky,
  output logic                  ctrl_err_sticky,
  output logic                  seq_err_sticky,       // R10: lockstep sequencer comparator
  output logic                  rq_err_sticky,        // R10: duplicated requantization comparator
  output logic                  wbuf_ce_sticky,       // weight buffer ECC: corrected / uncorrectable
  output logic                  wbuf_ue_sticky,
  output logic                  rq_tbl_perr_sticky,   // requantization table parity
  // DV only
  input  logic                  fault_inject,
  input  logic                  ctrl_fault_inject,
  input  logic                  seq_fault_inject,     // flips bit 0 of the primary sequencer's drain index
  input  logic                  rq_fault_inject,      // flips bit 12 of column 0 into the primary requant
  input  logic                  rq_tbl_fault_inject   // flips bit 0 of column 0's requant multiplier (table parity)
);
  logic signed [15:0]    f_ct, f_ky, f_kx;
  logic                  f_first, f_start, f_busy;
  logic                  w_load;
  logic [WAW-1:0]        w_raddr;
  logic signed [WW-1:0]  w_in [COLS];
  logic signed [WCW-1:0] wc_in;
  logic                  rd_en;
  logic [IDXW-1:0]       rd_idx_p;
  logic [IDXW-1:0]       rd_idx;
  // duplicate sequencer outputs (R10)
  logic signed [15:0]    d_ct, d_ky, d_kx;
  logic                  d_first, d_start, d_w_load, d_rd_en, d_done, d_busy, d_ct_free, d_reduce_ready;
  logic [WAW-1:0]        d_w_raddr;
  logic [IDXW-1:0]       d_rd_idx;
  logic [2:0]            d_state;
  logic signed [15:0]    d_ct_free_idx;
  logic                  seq_err;
  logic                  array_abft_err, ctrl_err;
  logic                  acc_ext_ready;
  logic                  ext_gated;
  assign ext_gated = ext_valid && reduce_ready;
  assign ext_ready = acc_ext_ready && reduce_ready;

  wbuf_mem #(.COLS(COLS), .WW(WW), .WCW(WCW), .DEPTH(WBUF_DEPTH), .WAW(WAW)) u_wbuf (
    .clk(clk), .rst_n(rst_n), .we(wbuf_we), .waddr(wbuf_waddr), .wdata(wbuf_wdata), .wcdata(wcbuf_wdata),
    .we2(wbuf_we2), .wdata2(wbuf_wdata2), .wcdata2(wcbuf_wdata2),
    .raddr(w_raddr), .rdata(w_in), .rcdata(wc_in),
    .err_clear(err_clear), .ecc_ce_sticky(wbuf_ce_sticky), .ecc_ue_sticky(wbuf_ue_sticky));

  core_seq #(.ROWS(ROWS), .COLS(COLS), .IDXW(IDXW), .WBUF_DEPTH(WBUF_DEPTH), .WAW(WAW),
             .PE_LAT(PE_LAT)) u_seq (
    .clk(clk), .rst_n(rst_n),
    .cfg_k(cfg_k), .cfg_ct_n(cfg_ct_n), .cfg_ct0(cfg_ct0), .cfg_ky0(cfg_ky0), .cfg_kx0(cfg_kx0),
    .cfg_rn(cfg_rn), .cfg_contrib_n(cfg_contrib_n), .cfg_m(cfg_m), .tiles_ready(tiles_ready),
    .go(go), .done(done), .busy(busy), .state_dbg(state_dbg), .ct_free(ct_free), .ct_free_idx(ct_free_idx),
    .reduce_ready(reduce_ready), .ext_fire(ext_gated && acc_ext_ready),
    .f_ct(f_ct), .f_ky(f_ky), .f_kx(f_kx), .f_first(f_first), .f_start(f_start), .f_busy(f_busy),
    .w_load(w_load), .w_raddr(w_raddr),
    .rd_en(rd_en), .rd_idx(rd_idx_p));
  assign rd_idx = rd_idx_p ^ {{(IDXW-1){1'b0}}, seq_fault_inject};

  // R10: a second sequencer in lockstep, same inputs, compared every cycle
  core_seq #(.ROWS(ROWS), .COLS(COLS), .IDXW(IDXW), .WBUF_DEPTH(WBUF_DEPTH), .WAW(WAW),
             .PE_LAT(PE_LAT)) u_seq_dup (
    .clk(clk), .rst_n(rst_n),
    .cfg_k(cfg_k), .cfg_ct_n(cfg_ct_n), .cfg_ct0(cfg_ct0), .cfg_ky0(cfg_ky0), .cfg_kx0(cfg_kx0),
    .cfg_rn(cfg_rn), .cfg_contrib_n(cfg_contrib_n), .cfg_m(cfg_m), .tiles_ready(tiles_ready),
    .go(go), .done(d_done), .busy(d_busy), .state_dbg(d_state), .ct_free(d_ct_free), .ct_free_idx(d_ct_free_idx),
    .reduce_ready(d_reduce_ready), .ext_fire(ext_gated && acc_ext_ready),
    .f_ct(d_ct), .f_ky(d_ky), .f_kx(d_kx), .f_first(d_first), .f_start(d_start), .f_busy(f_busy),
    .w_load(d_w_load), .w_raddr(d_w_raddr),
    .rd_en(d_rd_en), .rd_idx(d_rd_idx));
  assign seq_err = (f_start != d_start) || (f_first != d_first) || (f_ct != d_ct) || (f_ky != d_ky) || (f_kx != d_kx) ||
                   (w_load != d_w_load) || (w_raddr != d_w_raddr) || (rd_en != d_rd_en) || (rd_idx != d_rd_idx) ||
                   (done != d_done) || (reduce_ready != d_reduce_ready) || (state_dbg != d_state);
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n)         seq_err_sticky <= 1'b0;
    else if (err_clear) seq_err_sticky <= 1'b0;
    else if (seq_err)   seq_err_sticky <= 1'b1;
  end

  neo_mac_core_v02 #(.ROWS(ROWS), .COLS(COLS), .XW(XW), .WW(WW), .PW(PW), .WCW(WCW), .ACC_ROWS(ACC_ROWS),
                     .ABUF_DEPTH(ABUF_DEPTH), .IDXW(IDXW), .AW(AW), .PE_LAT(PE_LAT),
                     .FAULT_ROW(FAULT_ROW), .FAULT_COL(FAULT_COL)) u_dp (
    .clk(clk), .rst_n(rst_n),
    .abuf_we(abuf_we), .abuf_waddr(abuf_waddr), .abuf_wdata(abuf_wdata), .abuf_we2(abuf_we2), .abuf_wdata2(abuf_wdata2),
    .cfg_h(cfg_h), .cfg_w(cfg_w), .cfg_ho(cfg_ho), .cfg_wo(cfg_wo), .cfg_oy0(cfg_oy0), .cfg_oy_n(cfg_oy_n), .cfg_iy0(cfg_iy0),
    .cfg_s(cfg_s), .cfg_p(cfg_p), .cfg_tile_pixels(cfg_tile_pixels), .cfg_regions_m1(cfg_regions_m1), .cfg_ct(f_ct), .cfg_ky(f_ky), .cfg_kx(f_kx), .acc_first(f_first),
    .start(f_start), .busy(f_busy),
    .w_load(w_load), .w_in(w_in), .wc_in(wc_in),
    .ext_valid(ext_gated), .ext_idx(ext_idx), .ext_y(ext_y), .ext_chk(ext_chk), .ext_ready(acc_ext_ready),
    .rd_en(rd_en), .rd_idx(rd_idx), .rd_data(rd_data), .rd_chk(rd_chk), .rd_valid(rd_valid),
    .abft_clear(err_clear), .array_abft_err(array_abft_err), .array_abft_sticky(array_abft_sticky),
    .acc_abft_err(acc_abft_err), .acc_abft_sticky(acc_abft_sticky),
    .ctrl_err(ctrl_err), .ctrl_err_sticky(ctrl_err_sticky),
    .fault_inject(fault_inject), .ctrl_fault_inject(ctrl_fault_inject));

  // requantization, duplicated with a comparator (R10); the fault hook corrupts the primary's input only
  logic signed [PW-1:0] rq_in_p [COLS];
  logic signed [7:0]    rq_q_d [COLS];
  logic                 rq_valid_d;
  logic                 rq_err;
  generate
    for (genvar j = 0; j < COLS; j++) begin : g_rqin
      if (j == 0) begin : g_f
        assign rq_in_p[j] = rd_data[j] ^ {{(PW-13){1'b0}}, rq_fault_inject, 12'd0};
      end else begin : g_n
        assign rq_in_p[j] = rd_data[j];
      end
    end
  endgenerate
  requant #(.COLS(COLS), .PW(PW), .MW(16)) u_rq (
    .clk(clk), .rst_n(rst_n),
    .tbl_we(rq_tbl_we), .tbl_addr(rq_tbl_addr), .tbl_mult(rq_tbl_mult), .tbl_shift(rq_tbl_shift), .tbl_zp(rq_tbl_zp),
    .relu(rq_relu),
    .in_valid(rd_valid), .in_acc(rq_in_p), .out_valid(rq_valid), .out_q(rq_q),
    .err_clear(err_clear), .tbl_perr_sticky(rq_tbl_perr_sticky), .tbl_fault_inject(rq_tbl_fault_inject));
  logic rq_tbl_perr_d;
  requant #(.COLS(COLS), .PW(PW), .MW(16)) u_rq_dup (
    .clk(clk), .rst_n(rst_n),
    .tbl_we(rq_tbl_we), .tbl_addr(rq_tbl_addr), .tbl_mult(rq_tbl_mult), .tbl_shift(rq_tbl_shift), .tbl_zp(rq_tbl_zp),
    .relu(rq_relu),
    .in_valid(rd_valid), .in_acc(rd_data), .out_valid(rq_valid_d), .out_q(rq_q_d),
    .err_clear(err_clear), .tbl_perr_sticky(rq_tbl_perr_d), .tbl_fault_inject(1'b0));
  always_comb begin
    rq_err = (rq_valid != rq_valid_d);
    for (int j = 0; j < COLS; j++) if (rq_valid && (rq_q[j] != rq_q_d[j])) rq_err = 1'b1;
  end
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n)         rq_err_sticky <= 1'b0;
    else if (err_clear) rq_err_sticky <= 1'b0;
    else if (rq_err)    rq_err_sticky <= 1'b1;
  end
  // outputs of the duplicates and the datapath that nothing reads (lint): the duplicate sequencer's busy / ct_free /
  // ct_free_idx (its compared outputs are in seq_err), the duplicate requant's parity flag, the unlatched ABFT and
  // control errors (their sticky versions are the outputs)
  logic unused_core_bits;
  assign unused_core_bits = ^{d_busy, d_ct_free, d_ct_free_idx, array_abft_err, ctrl_err, rq_tbl_perr_d};
endmodule
