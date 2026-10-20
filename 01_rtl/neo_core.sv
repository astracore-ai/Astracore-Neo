// neo_core.sv -- AstraCore Neo MAC core with its sequencer (drop 0.4).
//   core_seq (layer descriptor -> runs, shadow loads, drain) + neo_mac_core_v02 (feeder, array,
//   accumulator) + requant (INT32 -> INT8 per output channel). The host or DMA fills the activation
//   and weight buffers and the requantization tables, writes the descriptor, pulses go, and reads
//   the drained rows (INT32 on rd_data/rd_valid, INT8 on rq_q/rq_valid) until done. All three ABFT/duplication
//   error flags are sticky and cleared together.
//   Drop 0.42: NCHK = 4, the 2:4 sparse-capable core. The weight buffer's write ports take the new entry ({index,
//   weight} per column, NCHK check weights; wbuf_mem's header), cfg_sparse (the descriptor's flag, host_if's cfg[18]
//   bit 0 on the tile) selects the sparse mode of the feeder and the row buses. The sequencer is unchanged: cfg_ct_n,
//   cfg_ct0 and the run count are in the mode's channel tiles (64 channels in sparse mode at 32 rows), and a sparse
//   run's weight tile is ROWS entries as a dense one is. NCHK = 1 is the core of drop 0.41.
//   Drop 0.43 (D2): the storage drop 0.41's synchronous reads put behind the R10 comparator joins its list (seq_err):
//   w_load_q against the duplicate's own d_w_load_q (d_w_load delayed), the weight buffer's latched read address
//   (wbuf_mem raddr_q: the leaf's port-B latch and ra0_q, the entry rdata shows) against d_w_raddr_q (d_w_raddr delayed),
//   and the accumulator's stage-0 control and port-B latch against their complemented copies (acc_bank ctrl_err). Each
//   of these, corrupted, feeds the array or the accumulator a consistent wrong row -- a weight row loaded a cycle early or
//   late or from another entry, a partial-sum row added to the wrong row, overwritten instead of accumulated, or the wrong
//   row drained -- which neither the SECDED lanes nor the ABFT check sees.
//   Drop 0.43 (stream B2): architectural clock gating -- the datapath (feeder, array, accumulator: neo_mac_core_v02) runs
//   on a gated clock (neo_icg) enabled while the sequencer is busy and for four cycles after (the drain's last row through
//   the accumulator's read and its two ABFT stages), on an activation-buffer write (the DMA fills the buffer before go),
//   on err_clear (the stickies) and through the reset; each requantizer gates itself (requant.sv). cg_en shows the three
//   enables, tel_ev the telemetry's core events (a row entering the array; the feeder stalled: the sequencer in a load,
//   start or run state with no row entering), both observation only.
//   Drop 0.43 (stream C2): the vector unit (vec_unit.sv) between the accumulator drain and the requantizer pair. With its
//   program empty (slot 0 END, the reset state) the requantizers take rd_data / rd_valid as before -- the bypass, every
//   dense layer's rows and cycles unchanged. A descriptor started with a program loaded (vec_on, latched at go) runs the
//   sequencer's drain as before but nothing leaves on rd_valid (the INT32 / PSUM drains are for layers without a program:
//   a statistics round of a softmax or a norm emits nothing, and nothing then reaches the NIC's drain FIFO); the unit then
//   reads the accumulator rows again through the drain port (the sequencer is idle by then), runs the program per row and
//   feeds the requantizers with the rows it emits; `done` is then the unit's (the DMA's WAIT_DONE waits for it).
//   Its tables are written through vec_we / vec_addr / vec_wdata (host_if's VEC_ADDR / VEC_DATA), its residual
//   buffer through rbuf_* (the NIC's FETCH_A with arg 1); vec_err_sticky (its duplicated control's comparator) and
//   vec_perr_sticky (its table parity) are new flags. The unit runs on the core's clock; its busy is in the core's busy,
//   so the gated datapath clock (above) runs while the unit reads the accumulator, and the requantizers' clock enables
//   follow their input valid, the unit's rows included.
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
  parameter int NCHK       = 1,                 // check columns: 4 = the 2:4 sparse-capable core (drop 0.42)
  parameter int IW         = (NCHK > 1) ? 2 : 0,   // index bits per weight (derived)
  parameter int FAULT_ROW  = 0,
  parameter int FAULT_COL  = 0,
  parameter int DV_HOOKS   = 0                  // 1: the weight-buffer fault hook exists (drop 0.37); the tile leaves it at 0
)(
  input  logic                  clk,
  input  logic                  rst_n,
  input  logic                  test_en,      // drop 0.44 (review #8): test mode -- the datapath's and the requantizers' clock gates forced on
  // DMA write ports
  input  logic                  abuf_we,
  input  logic [AW-1:0]         abuf_waddr,
  input  logic signed [XW-1:0]  abuf_wdata [ROWS],
  input  logic                  abuf_we2,                 // the upper entry of the same pair, abuf_waddr even (drop 0.32)
  input  logic signed [XW-1:0]  abuf_wdata2 [ROWS],
  input  logic                  wbuf_we,
  input  logic [WAW-1:0]        wbuf_waddr,
  input  logic [WW+IW-1:0]      wbuf_wdata [COLS],        // {index, weight} per column (drop 0.42: WW + 2 bits at NCHK = 4)
  input  logic [NCHK*WCW-1:0]   wcbuf_wdata,              // the check weights, column k at [k*WCW +: WCW]
  input  logic                  wbuf_we2,                 // the upper entry of the same pair, wbuf_waddr even (drop 0.32)
  input  logic [WW+IW-1:0]      wbuf_wdata2 [COLS],
  input  logic [NCHK*WCW-1:0]   wcbuf_wdata2,
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
  input  logic                  cfg_sparse,         // drop 0.42 (NCHK = 4): the layer is 2:4 sparse along K
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
  // drained results: INT32 (ABFT-checked) and, three cycles later, requantized INT8 (drop 0.41: requant in three stages)
  // -- with a vector program (drop 0.43, C2) the INT8 rows are the unit's and rd_valid stays low
  output wire  signed [PW-1:0]  rd_data [COLS],      // nets (drop 0.43): Icarus 12 passes no unpacked output port on through a variable
  output logic signed [PW-1:0]  rd_chk,
  output logic                  rd_valid,
  output wire  signed [7:0]     rq_q [COLS],
  output logic                  rq_valid,
  // requantization tables (per output channel) and mode
  input  logic                  rq_tbl_we,
  input  logic [$clog2(COLS)-1:0] rq_tbl_addr,
  input  logic [15:0]           rq_tbl_mult,
  input  logic [4:0]            rq_tbl_shift,
  input  logic signed [7:0]     rq_tbl_zp,
  input  logic                  rq_relu,
  // the vector unit (drop 0.43): its tables and program, its residual buffer (pairs of entries at even rbuf_waddr)
  input  logic                  vec_we,
  input  logic [11:0]           vec_addr,
  input  logic [31:0]           vec_wdata,
  input  logic                  rbuf_we,
  input  logic                  rbuf_we2,
  input  logic [IDXW-1:0]       rbuf_waddr,
  input  logic signed [XW-1:0]  rbuf_wdata [ROWS],
  input  logic signed [XW-1:0]  rbuf_wdata2 [ROWS],
  // safety
  input  logic                  err_clear,
  output logic                  array_abft_sticky,
  output logic                  acc_abft_err,
  output logic                  acc_abft_err_valid,   // drop 0.41: acc_abft_err is the verdict of the row rd_valid showed two cycles before
  output logic                  acc_abft_sticky,
  output logic                  ctrl_err_sticky,
  output logic                  seq_err_sticky,       // R10: lockstep sequencer comparator
  output logic                  rq_err_sticky,        // R10: duplicated requantization comparator
  output logic                  wbuf_ce_sticky,       // weight buffer ECC: corrected / uncorrectable
  output logic                  wbuf_ue_sticky,
  output logic                  rq_tbl_perr_sticky,   // requantization table parity
  output logic                  vec_err_sticky,       // drop 0.43: the vector unit's duplicated control disagrees (R7)
  output logic                  vec_perr_sticky,      // drop 0.43: a vector-unit table entry failed its parity on use (R10)
  // DV only
  input  logic                  fault_inject,
  input  logic                  ctrl_fault_inject,
  input  logic                  seq_fault_inject,     // flips bit 0 of the primary sequencer's drain index
  input  logic                  rq_fault_inject,      // flips bit 12 of column 0 into the primary requant
  input  logic                  rq_tbl_fault_inject,  // flips bit 0 of column 0's requant multiplier (table parity)
  input  logic                  vec_ctrl_fault_inject, // drop 0.43: flips bit 0 of the vector unit's primary row index
  input  logic                  vec_tbl_fault_inject,  // drop 0.43: flips a reserved bit of the unit's slot-0 op word as used
  input  logic                  dv_wbuf_flip,         // drop 0.37 (DV_HOOKS = 1): a stored weight codeword's lane XOR a mask at the falling edge
  input  logic [WAW-1:0]        dv_wbuf_addr,
  input  logic [3:0]            dv_wbuf_lane,
  input  logic [38:0]           dv_wbuf_mask,
  // drop 0.43: observation -- the clock enables {requant duplicate, requant, datapath} and the telemetry events
  // {feeder stall, array valid}
  output logic [2:0]            cg_en,
  output logic [1:0]            tel_ev
);
  logic signed [15:0]    f_ct, f_ky, f_kx;
  logic                  f_first, f_start, f_busy;
  logic                  w_load;
  logic                  w_load_q;                  // drop 0.41: w_load one cycle later, beside the weight buffer's registered read
  logic [WAW-1:0]        w_raddr;
  logic [WAW-1:0]        w_raddr_q;                 // drop 0.43: the address of the entry the weight buffer shows (its latch)
  logic                  d_w_load_q;                // drop 0.43: the duplicate's w_load and w_raddr delayed one cycle
  logic [WAW-1:0]        d_w_raddr_q;
  logic                  acc_ctrl_err;              // drop 0.43: the accumulator's stage-0 control against its copies
  wire  [WW+IW-1:0]      w_in [COLS];               // nets (drop 0.43), as rd_data and rq_q
  logic [NCHK*WCW-1:0]   wc_in;
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
  logic                  array_abft_err, array_abft_err_valid, ctrl_err;
  logic                  acc_ext_ready;
  logic                  ext_gated;
  assign ext_gated = ext_valid && reduce_ready;
  assign ext_ready = acc_ext_ready && reduce_ready;

  wbuf_mem #(.COLS(COLS), .WW(WW), .WCW(WCW), .DEPTH(WBUF_DEPTH), .WAW(WAW), .NCHK(NCHK), .DV_HOOKS(DV_HOOKS)) u_wbuf (
    .clk(clk), .rst_n(rst_n), .we(wbuf_we), .waddr(wbuf_waddr), .wdata(wbuf_wdata), .wcdata(wcbuf_wdata),
    .we2(wbuf_we2), .wdata2(wbuf_wdata2), .wcdata2(wcbuf_wdata2),
    .raddr(w_raddr), .rdata(w_in), .rcdata(wc_in), .raddr_q(w_raddr_q),
    .err_clear(err_clear), .ecc_ce_sticky(wbuf_ce_sticky), .ecc_ue_sticky(wbuf_ue_sticky),
    .dv_fi_flip(dv_wbuf_flip), .dv_fi_addr(dv_wbuf_addr), .dv_fi_lane(dv_wbuf_lane), .dv_fi_mask(dv_wbuf_mask));

  logic                  seq_done, seq_busy;       // the sequencer's (done / busy are the core's: the unit's too, drop 0.43)
  core_seq #(.ROWS(ROWS), .COLS(COLS), .IDXW(IDXW), .WBUF_DEPTH(WBUF_DEPTH), .WAW(WAW),
             .PE_LAT(PE_LAT)) u_seq (
    .clk(clk), .rst_n(rst_n),
    .cfg_k(cfg_k), .cfg_ct_n(cfg_ct_n), .cfg_ct0(cfg_ct0), .cfg_ky0(cfg_ky0), .cfg_kx0(cfg_kx0),
    .cfg_rn(cfg_rn), .cfg_contrib_n(cfg_contrib_n), .cfg_m(cfg_m), .tiles_ready(tiles_ready),
    .go(go), .done(seq_done), .busy(seq_busy), .state_dbg(state_dbg), .ct_free(ct_free), .ct_free_idx(ct_free_idx),
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
                   (seq_done != d_done) || (reduce_ready != d_reduce_ready) || (state_dbg != d_state) ||
                   // drop 0.43 (the header): the delayed load strobe; the latched entry address while either copy of
                   // the strobe has the shadow chain take it (the leaf's latch has no reset); the accumulator's control
                   (w_load_q != d_w_load_q) || ((w_load_q || d_w_load_q) && (w_raddr_q != d_w_raddr_q)) ||
                   acc_ctrl_err;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n)         seq_err_sticky <= 1'b0;
    else if (err_clear) seq_err_sticky <= 1'b0;
    else if (seq_err)   seq_err_sticky <= 1'b1;
  end

  // drop 0.41: the weight buffer's read is registered (w_in / wc_in are the entry at the w_raddr of the previous cycle),
  // so the array takes the shadow-load strobe one cycle late, in the cycle its data is valid. The lockstep comparator
  // above compares the two sequencers' own w_load / w_raddr, not this copy.
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) w_load_q <= 1'b0;
    else        w_load_q <= w_load;
  end
  // drop 0.43: the duplicate sequencer's copies of the same delay (each from its own sequencer, so synthesis keeps two)
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin d_w_load_q <= 1'b0; d_w_raddr_q <= '0; end
    else        begin d_w_load_q <= d_w_load; d_w_raddr_q <= d_w_raddr; end
  end

  // the datapath's clock gate (drop 0.43)
  logic       clk_dp, dp_en, arr_valid;
  logic [3:0] busy_q;                        // busy one to four cycles ago: the drain's last row in the accumulator's stages
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) busy_q <= '0;
    else        busy_q <= {busy_q[2:0], busy};
  end
  assign dp_en = busy || (busy_q != 4'd0) || abuf_we || abuf_we2 || err_clear || !rst_n;
  neo_icg u_icg_dp (.clk(clk), .en(dp_en), .te(test_en), .gclk(clk_dp));

  // the vector unit (drop 0.43): the accumulator's drain port is the sequencer's during its drain and the unit's after it
  // (the two never overlap: the unit starts on the sequencer's done); vec_on, latched at go, says this descriptor runs the
  // unit's program -- the requantizers then take the unit's rows and `done` is the unit's
  logic                  dp_rd_en, dp_rd_valid;
  logic [IDXW-1:0]       dp_rd_idx;
  logic                  vu_rd_en, vu_rd_q, vu_active, vu_busy, vu_done, vu_out_valid;
  logic [IDXW-1:0]       vu_rd_idx;
  wire  signed [PW-1:0]  vu_out [COLS];                  // a net, as rd_data: Icarus 12 drives no variable array from a port
  logic                  vec_on;
  assign dp_rd_en  = rd_en || vu_rd_en;
  assign dp_rd_idx = vu_rd_en ? vu_rd_idx : rd_idx;
  assign rd_valid  = dp_rd_valid && !vec_on;             // the raw drain of a layer without a program only
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n)  vec_on <= 1'b0;
    else if (go) vec_on <= vu_active;
  end
  assign done = vec_on ? vu_done : seq_done;
  assign busy = seq_busy || vu_busy;
  vec_unit #(.COLS(COLS), .ROWS(ROWS), .PW(PW), .ACC_ROWS(ACC_ROWS), .IDXW(IDXW)) u_vec (
    .clk(clk), .rst_n(rst_n),
    .vh_we(vec_we), .vh_addr(vec_addr), .vh_wdata(vec_wdata),
    .go(go), .seq_done(seq_done), .cfg_m(cfg_m),
    .acc_rd_en(vu_rd_en), .acc_rd_idx(vu_rd_idx), .acc_rd_data(rd_data),
    .rbuf_we(rbuf_we), .rbuf_we2(rbuf_we2), .rbuf_waddr(rbuf_waddr), .rbuf_wdata(rbuf_wdata), .rbuf_wdata2(rbuf_wdata2),
    .active(vu_active), .busy(vu_busy), .done(vu_done), .rd_q(vu_rd_q), .out_valid(vu_out_valid), .out_row(vu_out),
    .err_clear(err_clear), .ctrl_err_sticky(vec_err_sticky), .perr_sticky(vec_perr_sticky),
    .ctrl_fault_inject(vec_ctrl_fault_inject), .tbl_fault_inject(vec_tbl_fault_inject));

  neo_mac_core_v02 #(.ROWS(ROWS), .COLS(COLS), .XW(XW), .WW(WW), .PW(PW), .WCW(WCW), .ACC_ROWS(ACC_ROWS),
                     .ABUF_DEPTH(ABUF_DEPTH), .IDXW(IDXW), .AW(AW), .PE_LAT(PE_LAT), .NCHK(NCHK),
                     .FAULT_ROW(FAULT_ROW), .FAULT_COL(FAULT_COL)) u_dp (
    .clk(clk_dp), .rst_n(rst_n),
    .abuf_we(abuf_we), .abuf_waddr(abuf_waddr), .abuf_wdata(abuf_wdata), .abuf_we2(abuf_we2), .abuf_wdata2(abuf_wdata2),
    .cfg_h(cfg_h), .cfg_w(cfg_w), .cfg_ho(cfg_ho), .cfg_wo(cfg_wo), .cfg_oy0(cfg_oy0), .cfg_oy_n(cfg_oy_n), .cfg_iy0(cfg_iy0),
    .cfg_s(cfg_s), .cfg_p(cfg_p), .cfg_tile_pixels(cfg_tile_pixels), .cfg_regions_m1(cfg_regions_m1), .cfg_ct(f_ct), .cfg_ky(f_ky), .cfg_kx(f_kx), .acc_first(f_first),
    .cfg_sparse(cfg_sparse), .start(f_start), .busy(f_busy),
    .w_load(w_load_q), .w_in(w_in), .wc_in(wc_in),
    .ext_valid(ext_gated), .ext_idx(ext_idx), .ext_y(ext_y), .ext_chk(ext_chk), .ext_ready(acc_ext_ready),
    .rd_en(dp_rd_en), .rd_idx(dp_rd_idx), .rd_data(rd_data), .rd_chk(rd_chk), .rd_valid(dp_rd_valid),
    .abft_clear(err_clear), .array_abft_err(array_abft_err), .array_abft_err_valid(array_abft_err_valid), .array_abft_sticky(array_abft_sticky),
    .acc_abft_err(acc_abft_err), .acc_abft_err_valid(acc_abft_err_valid), .acc_abft_sticky(acc_abft_sticky),
    .ctrl_err(ctrl_err), .ctrl_err_sticky(ctrl_err_sticky), .acc_ctrl_err(acc_ctrl_err),
    .fault_inject(fault_inject), .ctrl_fault_inject(ctrl_fault_inject), .arr_valid(arr_valid));
  assign tel_ev = {(state_dbg == 3'd1 || state_dbg == 3'd2 || state_dbg == 3'd3) && !arr_valid, arr_valid};

  // requantization, duplicated with a comparator (R10); the fault hook corrupts the primary's input only. Drop 0.43: the
  // input is the accumulator's drain (rd_data / its valid) or, for a descriptor with a vector program, the unit's rows
  logic signed [PW-1:0] rq_in   [COLS];
  logic signed [PW-1:0] rq_in_p [COLS];
  logic                 rq_in_valid;
  wire  signed [7:0]    rq_q_d [COLS];
  logic                 rq_valid_d;
  logic                 rq_err;
  assign rq_in_valid = vec_on ? vu_out_valid : dp_rd_valid;
  generate
    for (genvar j = 0; j < COLS; j++) begin : g_rqin
      assign rq_in[j] = vec_on ? vu_out[j] : rd_data[j];
      if (j == 0) begin : g_f
        assign rq_in_p[j] = rq_in[j] ^ {{(PW-13){1'b0}}, rq_fault_inject, 12'd0};
      end else begin : g_n
        assign rq_in_p[j] = rq_in[j];
      end
    end
  endgenerate
  logic rq_cg_en, rq_cg_en_d;                // the requantizers' clock enables (whole signals: neosim connects no output to a bit)
  requant #(.COLS(COLS), .PW(PW), .MW(16)) u_rq (
    .clk(clk), .rst_n(rst_n), .test_en(test_en),
    .tbl_we(rq_tbl_we), .tbl_addr(rq_tbl_addr), .tbl_mult(rq_tbl_mult), .tbl_shift(rq_tbl_shift), .tbl_zp(rq_tbl_zp),
    .relu(rq_relu),
    .in_valid(rq_in_valid), .in_acc(rq_in_p), .out_valid(rq_valid), .out_q(rq_q),
    .err_clear(err_clear), .tbl_perr_sticky(rq_tbl_perr_sticky), .tbl_fault_inject(rq_tbl_fault_inject), .cg_en(rq_cg_en));
  logic rq_tbl_perr_d;
  requant #(.COLS(COLS), .PW(PW), .MW(16)) u_rq_dup (
    .clk(clk), .rst_n(rst_n), .test_en(test_en),
    .tbl_we(rq_tbl_we), .tbl_addr(rq_tbl_addr), .tbl_mult(rq_tbl_mult), .tbl_shift(rq_tbl_shift), .tbl_zp(rq_tbl_zp),
    .relu(rq_relu),
    .in_valid(rq_in_valid), .in_acc(rq_in), .out_valid(rq_valid_d), .out_q(rq_q_d),
    .err_clear(err_clear), .tbl_perr_sticky(rq_tbl_perr_d), .tbl_fault_inject(1'b0), .cg_en(rq_cg_en_d));
  assign cg_en = {rq_cg_en_d, rq_cg_en, dp_en};
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
  assign unused_core_bits = ^{d_busy, d_ct_free, d_ct_free_idx, array_abft_err, array_abft_err_valid, ctrl_err, rq_tbl_perr_d, vu_rd_q};
endmodule
