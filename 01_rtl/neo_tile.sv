// neo_tile.sv -- one mesh node (drop 0.7): router + tile interface + local bank + neo_core.
module neo_tile #(
  parameter int XW = 2, YW = 2, NX = 2, NY = 2, DW = 64,
  parameter int FW = 1 + 2 * (XW + YW) + 8 + DW,     // the tile interface's flit (one word)
  parameter int WPF = 1,                     // words per link flit (drop 0.25): 1 as simulated before, 32 = the 1024-bit links
  parameter int DWL = 32 + 32 * WPF,
  parameter int FWL = 1 + 2 * (XW + YW) + 8 + DWL,   // the link flit (router ports, mesh links)
  parameter int MY_X = 0, MY_Y = 0,
  parameter int ROWS = 16, COLS = 8, PW = 32,
  parameter int WCW = 8 + $clog2(ROWS) + 1,
  parameter int ACC_ROWS = 64, ABUF_DEPTH = 256, WBUF_DEPTH = 512, BANK_DEPTH = 4096,
  parameter int IDXW = $clog2(ACC_ROWS), AW = $clog2(ABUF_DEPTH), WAW = $clog2(WBUF_DEPTH), BAW = $clog2(BANK_DEPTH),
  parameter int FETCH_TIMEOUT = 1048575,     // silicon default (drop 0.24): 2^20 - 1, see tile_nic.sv
  parameter int BIST_WORDS = BANK_DEPTH,
  parameter int PDEPTH = 32,                 // DMA program memory entries (drop 0.22: 32, was 16)
  parameter int PAW = $clog2(PDEPTH)
)(
  input  logic          clk,
  input  logic          rst_n,
  // mesh links (router ports 0..3 = N, E, S, W)
  input  logic          in_valid  [4],
  input  logic [FWL-1:0] in_flit  [4],
  output logic          in_ready  [4],
  output logic          out_valid [4],
  output logic [FWL-1:0] out_flit [4],
  input  logic          out_ready [4],
  // host register bus (host_if inside; see rtl/host_if.sv for the map)
  input  logic          h_we,
  input  logic          h_re,
  input  logic [7:0]    h_addr,
  input  logic [31:0]   h_wdata,
  output logic [31:0]   h_rdata,
  output logic          err_pin,
  // status (also readable through the register map)
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
  output logic          fetch_timeout
);
  // host interface and error signaling
  logic                  prog_we, prog_start, err_clear, rq_tbl_we, rq_relu, wd_enable, wd_kick, selftest_inject;
  logic [PAW-1:0]        prog_waddr;
  logic [63:0]           prog_wdata;
  logic signed [15:0]    cfg [20];
  logic [IDXW-1:0]       cfg_m;
  logic [4:0]            rq_tbl_addr, rq_tbl_shift;
  logic [15:0]           rq_tbl_mult;
  logic signed [7:0]     rq_tbl_zp;
  logic [18:0]           err_mask, err_cause;
  logic [3:0]            partition;
  logic [23:0]           wd_window;
  logic [17:0]           tile_flags;
  logic                  core_done_pulse;

  // router <-> NIC local port
  logic          r_in_valid  [5];
  logic [FWL-1:0] r_in_flit  [5];
  logic          r_in_ready  [5];
  logic          r_out_valid [5];
  logic [FWL-1:0] r_out_flit [5];
  logic          r_out_ready [5];
  generate
    for (genvar p = 0; p < 4; p++) begin : g_p
      assign r_in_valid[p]  = in_valid[p];
      assign r_in_flit[p]   = in_flit[p];
      assign in_ready[p]    = r_in_ready[p];
      assign out_valid[p]   = r_out_valid[p];
      assign out_flit[p]    = r_out_flit[p];
      assign r_out_ready[p] = out_ready[p];
    end
  endgenerate

  noc_router #(.XW(XW), .YW(YW), .DW(DWL), .FW(FWL), .MY_X(MY_X), .MY_Y(MY_Y)) u_router (
    .clk(clk), .rst_n(rst_n),
    .in_valid(r_in_valid), .in_flit(r_in_flit), .in_ready(r_in_ready),
    .out_valid(r_out_valid), .out_flit(r_out_flit), .out_ready(r_out_ready),
    .err_clear(err_clear), .parity_err_sticky(parity_err));

  // the link layer (drop 0.25): WPF words per flit on the router side, one word per flit on the interface side
  logic          nic_tx_valid, nic_tx_ready, nic_rx_valid, nic_rx_ready;
  logic [FW-1:0] nic_tx_flit, nic_rx_flit;
  link_packer #(.XW(XW), .YW(YW), .DW(DW), .WPF(WPF)) u_pack (
    .clk(clk), .rst_n(rst_n), .core_valid(nic_tx_valid), .core_flit(nic_tx_flit), .core_ready(nic_tx_ready),
    .link_valid(r_in_valid[4]), .link_flit(r_in_flit[4]), .link_ready(r_in_ready[4]));
  link_unpacker #(.XW(XW), .YW(YW), .DW(DW), .WPF(WPF)) u_unpack (
    .clk(clk), .rst_n(rst_n), .link_valid(r_out_valid[4]), .link_flit(r_out_flit[4]), .link_ready(r_out_ready[4]),
    .core_valid(nic_rx_valid), .core_flit(nic_rx_flit), .core_ready(nic_rx_ready));

  // bank
  logic          b_we, b_re, b_rvalid;
  logic [BAW-1:0] b_waddr, b_raddr;
  logic [31:0]   b_wdata, b_rdata;
  logic bist_start, bist_active, bist_done, bist_fail, bist_fail_ce;
  logic [BAW-1:0] bist_fail_addr;
  // the 512-bit bank (drop 0.28): 16 SECDED lanes per row; the interface still moves one word per cycle on its bank
  // side, through bank_word_port, until stage 2 widens its serve and writeback paths
  localparam int LANES = 16;
  localparam int BROWS = BANK_DEPTH / LANES;
  localparam int BRAW  = (BROWS > 1) ? $clog2(BROWS) : 1;
  logic              r_we, r_re;
  logic [BRAW-1:0]   r_wrow, r_rrow;
  logic [LANES-1:0]  r_wmask;
  logic [32*LANES-1:0] r_wdata, r_rdata;
  bank_word_port #(.DEPTH(BANK_DEPTH), .AW(BAW), .LANES(LANES)) u_bank_port (
    .clk(clk), .rst_n(rst_n), .we(b_we), .waddr(b_waddr), .wdata(b_wdata), .re(b_re), .raddr(b_raddr), .rdata(b_rdata),
    .r_we(r_we), .r_wrow(r_wrow), .r_wmask(r_wmask), .r_wdata(r_wdata), .r_re(r_re), .r_rrow(r_rrow), .r_rdata(r_rdata));
  bank_bist_wrap #(.DEPTH(BANK_DEPTH), .AW(BAW), .BIST_WORDS(BIST_WORDS), .LANES(LANES)) u_bank (
    .clk(clk), .rst_n(rst_n), .we(r_we), .wrow(r_wrow), .wmask(r_wmask), .wdata(r_wdata), .re(r_re), .rrow(r_rrow), .rdata(r_rdata), .rvalid(b_rvalid),
    .err_clear(err_clear), .ecc_ce_sticky(ecc_ce), .ecc_ue_sticky(ecc_ue),
    .bist_start(bist_start), .bist_active(bist_active), .bist_done(bist_done), .bist_fail(bist_fail), .bist_fail_ce(bist_fail_ce),
    .bist_fail_addr(bist_fail_addr));

  // core buffers / ports driven by the NIC
  logic                  abuf_we, wbuf_we;
  logic [AW-1:0]         abuf_waddr;
  logic [WAW-1:0]        wbuf_waddr;
  logic signed [7:0]     abuf_wdata [ROWS];
  logic signed [7:0]     wbuf_wdata [COLS];
  logic signed [WCW-1:0] wcbuf_wdata;
  logic                  ext_valid, ext_ready, reduce_ready;
  logic [IDXW-1:0]       ext_idx;
  logic signed [PW-1:0]  ext_y [COLS];
  logic signed [PW-1:0]  ext_chk;
  logic signed [PW-1:0]  rd_data [COLS];
  logic signed [PW-1:0]  rd_chk;
  logic                  rd_valid, rq_valid, busy, ct_free, acc_abft_err;
  logic signed [7:0]     rq_q [COLS];
  logic [2:0]            state_dbg;
  logic signed [15:0]    ct_free_idx;
  // DMA program engine -> NIC command port and core go
  logic                  cmd_valid, cmd_busy, go, prog_busy;
  logic [2:0]            cmd_op;
  logic [XW-1:0]         cmd_x;
  logic [YW-1:0]         cmd_y;
  logic [19:0]           cmd_addr;
  logic [11:0]           cmd_len;
  logic [15:0]           cmd_base;
  logic                  cmd_int8;
  logic signed [15:0]    tiles_ready;
  logic                  rdy_seen, rdy_clear, ntf_busy;

  // program memory with SECDED, shared by the lockstep pair of DMA engines (drop 0.16)
  logic [PAW-1:0] dma_pc, dma_pc_d;
  logic [63:0] dma_ins;
  logic        prog_ce, prog_ue, dma_fsm_err, dma_fsm_err_d;
  prog_mem #(.DEPTH(PDEPTH), .AW(PAW)) u_prog (.clk(clk), .rst_n(rst_n), .we(prog_we), .waddr(prog_waddr), .wdata(prog_wdata), .raddr(dma_pc), .rdata(dma_ins),
                   .err_clear(err_clear), .prog_ce_sticky(prog_ce), .prog_ue_sticky(prog_ue));
  tile_dma #(.XW(XW), .YW(YW), .PDEPTH(PDEPTH), .PAW(PAW)) u_dma (
    .clk(clk), .rst_n(rst_n),
    .pc_out(dma_pc), .ins_in(dma_ins), .prog_start(prog_start), .fsm_err(dma_fsm_err),
    .prog_done(prog_done), .prog_busy(prog_busy),
    .cmd_valid(cmd_valid), .cmd_op(cmd_op), .cmd_x(cmd_x), .cmd_y(cmd_y), .cmd_addr(cmd_addr), .cmd_len(cmd_len),
    .cmd_base(cmd_base), .cmd_int8(cmd_int8), .cmd_busy(cmd_busy),
    .go(go), .done(done), .ct_free(ct_free), .reduce_ready(reduce_ready), .rdy_seen(rdy_seen), .rdy_clear(rdy_clear),
    .ntf_busy(ntf_busy), .tiles_ready(tiles_ready));
  // the duplicate engine: same inputs, outputs compared every cycle
  logic        d_cmd_valid, d_go, d_prog_done, d_prog_busy, d_rdy_clear, d_cmd_int8;
  logic [2:0]  d_cmd_op; logic [XW-1:0] d_cmd_x; logic [YW-1:0] d_cmd_y; logic [19:0] d_cmd_addr; logic [11:0] d_cmd_len;
  logic [15:0] d_cmd_base; logic signed [15:0] d_tiles_ready;
  logic        dma_err, dma_err_sticky, cfg_err, cfg_err_sticky, ctrl_path_err, iso_err;
  tile_dma #(.XW(XW), .YW(YW), .PDEPTH(PDEPTH), .PAW(PAW)) u_dma_dup (
    .clk(clk), .rst_n(rst_n),
    .pc_out(dma_pc_d), .ins_in(dma_ins), .prog_start(prog_start), .fsm_err(dma_fsm_err_d),
    .prog_done(d_prog_done), .prog_busy(d_prog_busy),
    .cmd_valid(d_cmd_valid), .cmd_op(d_cmd_op), .cmd_x(d_cmd_x), .cmd_y(d_cmd_y), .cmd_addr(d_cmd_addr), .cmd_len(d_cmd_len),
    .cmd_base(d_cmd_base), .cmd_int8(d_cmd_int8), .cmd_busy(cmd_busy),
    .go(d_go), .done(done), .ct_free(ct_free), .reduce_ready(reduce_ready), .rdy_seen(rdy_seen), .rdy_clear(d_rdy_clear),
    .ntf_busy(ntf_busy), .tiles_ready(d_tiles_ready));
  assign dma_err = (cmd_valid != d_cmd_valid) || (cmd_op != d_cmd_op) || (cmd_x != d_cmd_x) || (cmd_y != d_cmd_y) ||
                   (cmd_addr != d_cmd_addr) || (cmd_len != d_cmd_len) || (cmd_base != d_cmd_base) || (cmd_int8 != d_cmd_int8) ||
                   (go != d_go) || (prog_done != d_prog_done) || (dma_pc != dma_pc_d) || (tiles_ready != d_tiles_ready);
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin dma_err_sticky <= 1'b0; cfg_err_sticky <= 1'b0; end
    else begin
      if (err_clear) begin dma_err_sticky <= 1'b0; cfg_err_sticky <= 1'b0; end
      if (dma_err) dma_err_sticky <= 1'b1;
      if (cfg_err) cfg_err_sticky <= 1'b1;
    end
  end
  assign ctrl_path_err = dma_err_sticky | cfg_err_sticky | prog_ue | dma_fsm_err | dma_fsm_err_d;

  tile_nic #(.XW(XW), .YW(YW), .NX(NX), .NSRC(NX * NY), .DW(DW), .FW(FW), .MY_X(MY_X), .MY_Y(MY_Y),
             .ROWS(ROWS), .COLS(COLS), .WCW(WCW), .IDXW(IDXW), .AW(AW), .WAW(WAW), .BAW(BAW), .PW(PW),
             .DFD(ACC_ROWS), .FETCH_TIMEOUT(FETCH_TIMEOUT)) u_nic (
    .clk(clk), .rst_n(rst_n),
    .rx_valid(nic_rx_valid), .rx_flit(nic_rx_flit), .rx_ready(nic_rx_ready),
    .tx_valid(nic_tx_valid), .tx_flit(nic_tx_flit), .tx_ready(nic_tx_ready),
    .b_we(b_we), .b_waddr(b_waddr), .b_wdata(b_wdata), .b_re(b_re), .b_raddr(b_raddr), .b_rdata(b_rdata), .b_rvalid(b_rvalid),
    .abuf_we(abuf_we), .abuf_waddr(abuf_waddr), .abuf_wdata(abuf_wdata),
    .wbuf_we(wbuf_we), .wbuf_waddr(wbuf_waddr), .wbuf_wdata(wbuf_wdata), .wcbuf_wdata(wcbuf_wdata),
    .ext_valid(ext_valid), .ext_idx(ext_idx), .ext_y(ext_y), .ext_chk(ext_chk), .ext_ready(ext_ready),
    .rd_valid(rd_valid), .rd_data(rd_data), .rd_chk(rd_chk), .rq_valid(rq_valid), .rq_q(rq_q),
    .cmd_valid(cmd_valid), .cmd_op(cmd_op), .cmd_x(cmd_x), .cmd_y(cmd_y), .cmd_addr(cmd_addr), .cmd_len(cmd_len),
    .cmd_base(cmd_base), .cmd_int8(cmd_int8), .cmd_busy(cmd_busy), .drain_busy(drain_busy),
    .crc_err_sticky(crc_err), .err_clear(err_clear), .rdy_seen(rdy_seen), .rdy_clear(rdy_clear), .ntf_busy(ntf_busy),
    .partition(partition), .iso_err_sticky(iso_err), .lost_err_sticky(lost_err), .fetch_timeout_sticky(fetch_timeout));

  host_if #(.IDXW(IDXW), .PAW(PAW)) u_host (
    .clk(clk), .rst_n(rst_n), .h_we(h_we), .h_re(h_re), .h_addr(h_addr), .h_wdata(h_wdata), .h_rdata(h_rdata),
    .prog_we(prog_we), .prog_waddr(prog_waddr), .prog_wdata(prog_wdata), .prog_start(prog_start), .err_clear(err_clear),
    .cfg(cfg), .cfg_m(cfg_m),
    .rq_tbl_we(rq_tbl_we), .rq_tbl_addr(rq_tbl_addr), .rq_tbl_mult(rq_tbl_mult), .rq_tbl_shift(rq_tbl_shift), .rq_tbl_zp(rq_tbl_zp),
    .rq_relu(rq_relu),
    .err_mask(err_mask), .err_cause(err_cause), .wd_enable(wd_enable), .wd_window(wd_window), .wd_kick(wd_kick),
    .selftest_inject(selftest_inject), .partition(partition), .cfg_err(cfg_err),
    .bist_start(bist_start), .bist_active(bist_active), .bist_done(bist_done), .bist_fail(bist_fail), .bist_fail_ce(bist_fail_ce),
    .bist_fail_addr(20'(bist_fail_addr)),        // 20-bit field: a 512K-word bank needs 19 (drop 0.22; the 16-bit field went negative at BAW = 19)
    .prog_done(prog_done), .drain_busy(drain_busy), .core_done(done),
    .flags({ecc_ce, ecc_ue, rq_err_sticky, seq_err_sticky, ctrl_err_sticky, acc_abft_sticky, array_abft_sticky, crc_err, parity_err}));

  assign tile_flags = {iso_err, ctrl_path_err, prog_ce, bist_fail, fetch_timeout, lost_err, rq_tbl_perr, wbuf_ue, wbuf_ce, ecc_ce, ecc_ue, rq_err_sticky,
                       seq_err_sticky, ctrl_err_sticky, acc_abft_sticky, array_abft_sticky, crc_err, parity_err};
  esm #(.NFLAGS(18)) u_esm (
    .clk(clk), .rst_n(rst_n), .flags(tile_flags), .mask(err_mask), .err_clear(err_clear),
    .wd_enable(wd_enable), .wd_window(wd_window), .wd_kick(wd_kick), .cause(err_cause), .err_pin(err_pin));

  neo_core #(.ROWS(ROWS), .COLS(COLS), .PW(PW), .ACC_ROWS(ACC_ROWS), .ABUF_DEPTH(ABUF_DEPTH), .WBUF_DEPTH(WBUF_DEPTH)) u_core (
    .clk(clk), .rst_n(rst_n),
    .abuf_we(abuf_we), .abuf_waddr(abuf_waddr), .abuf_wdata(abuf_wdata),
    .wbuf_we(wbuf_we), .wbuf_waddr(wbuf_waddr), .wbuf_wdata(wbuf_wdata), .wcbuf_wdata(wcbuf_wdata),
    .cfg_h(cfg[0]), .cfg_w(cfg[1]), .cfg_ho(cfg[2]), .cfg_wo(cfg[3]), .cfg_oy0(cfg[4]), .cfg_oy_n(cfg[5]), .cfg_iy0(cfg[6]),
    .cfg_s(cfg[7]), .cfg_p(cfg[8]), .cfg_k(cfg[9]), .cfg_ct_n(cfg[10]), .cfg_ct0(cfg[11]),
    .cfg_ky0(cfg[12]), .cfg_kx0(cfg[13]), .cfg_rn(cfg[14]),
    .cfg_contrib_n(cfg[15]), .cfg_tile_pixels(cfg[16]), .cfg_regions_m1(cfg[17]),
    .tiles_ready(tiles_ready), .cfg_m(cfg_m),
    .go(go), .done(done), .busy(busy), .state_dbg(state_dbg), .ct_free(ct_free), .ct_free_idx(ct_free_idx),
    .ext_valid(ext_valid), .ext_idx(ext_idx), .ext_y(ext_y), .ext_chk(ext_chk), .ext_ready(ext_ready), .reduce_ready(reduce_ready),
    .rd_data(rd_data), .rd_chk(rd_chk), .rd_valid(rd_valid), .rq_q(rq_q), .rq_valid(rq_valid),
    .rq_tbl_we(rq_tbl_we), .rq_tbl_addr(rq_tbl_addr[$clog2(COLS)-1:0]), .rq_tbl_mult(rq_tbl_mult), .rq_tbl_shift(rq_tbl_shift),
    .rq_tbl_zp(rq_tbl_zp), .rq_relu(rq_relu),
    .err_clear(err_clear), .array_abft_sticky(array_abft_sticky), .acc_abft_err(acc_abft_err),
    .acc_abft_sticky(acc_abft_sticky), .ctrl_err_sticky(ctrl_err_sticky),
    .seq_err_sticky(seq_err_sticky), .rq_err_sticky(rq_err_sticky),
    .wbuf_ce_sticky(wbuf_ce), .wbuf_ue_sticky(wbuf_ue), .rq_tbl_perr_sticky(rq_tbl_perr),
    .fault_inject(selftest_inject), .ctrl_fault_inject(1'b0), .seq_fault_inject(1'b0), .rq_fault_inject(1'b0), .rq_tbl_fault_inject(1'b0));
endmodule
