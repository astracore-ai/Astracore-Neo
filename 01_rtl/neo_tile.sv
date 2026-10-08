// neo_tile.sv -- one mesh node (drop 0.7): router + tile interface + local bank + neo_core.
//   Since drop 0.29 the tile interface moves beats of BW = 16 words (one row of the 512-bit bank) on its link side and
//   drives the bank's row port directly; link_packer / link_unpacker re-block between the beats and the WPF-word link flits.
module neo_tile #(
  parameter int XW = 2, YW = 2, NX = 2, NY = 2,
  parameter int WPF = 1,                     // words per link flit (drop 0.25): 1 as simulated before, 32 = the 1024-bit links
  parameter int DWL = 32 + 32 * WPF,
  parameter int FWL = 1 + 2 * (XW + YW) + 8 + DWL,   // the link flit (router ports, mesh links)
  parameter int ROWS = 16, COLS = 8, PW = 32,
  parameter int WCW = 8 + $clog2(ROWS) + 1,
  parameter int ACC_ROWS = 64, ABUF_DEPTH = 256, WBUF_DEPTH = 512, BANK_DEPTH = 4096,
  parameter int IDXW = $clog2(ACC_ROWS), AW = $clog2(ABUF_DEPTH), WAW = $clog2(WBUF_DEPTH), BAW = $clog2(BANK_DEPTH),
  parameter int FETCH_TIMEOUT = 1048575,     // silicon default (drop 0.24): 2^20 - 1, see tile_nic.sv
  parameter int BIST_WORDS = BANK_DEPTH,
  parameter int PDEPTH = 32,                 // DMA program memory entries (drop 0.22: 32, was 16)
  parameter int PAW = $clog2(PDEPTH),
  parameter int DV_HOOKS = 0                 // 1: the testbench hooks (dv_* ports) exist; 0: the silicon tile, ports tied off (drop 0.35)
)(
  input  logic          clk,
  input  logic          rst_n,
  input  logic [XW-1:0] my_x,          // this tile's mesh coordinates (drop 0.35): strapped by tile_mesh from the generate indices,
  input  logic [YW-1:0] my_y,          // so every tile is one and the same module (parameters MY_X / MY_Y made 64 distinct modules)
  // mesh links (router ports 0..3 = N, E, S, W), packed: port p is bit p / bits [p*FWL +: FWL] (drop 0.35: no unpacked
  // arrays on the hierarchical block's boundary -- Verilator 5.020 generates change detection the lib-create wrapper's
  // C arrays cannot satisfy once the block has more instances than it inlines)
  input  logic [3:0]       in_valid,
  input  logic [4*FWL-1:0] in_flit,
  output logic [3:0]       in_ready,
  output logic [3:0]       out_valid,
  output logic [4*FWL-1:0] out_flit,
  input  logic [3:0]       out_ready,
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
  output logic          fetch_timeout,
  // testbench hooks (drop 0.35; DV_HOOKS = 1), as ports so that the tile is a hierarchical block of the Verilator build
  // (one tile verilated once, instantiated NX*NY times; a hierarchical block admits no hierarchical references):
  // the bank backdoor, the fault injection of the cocotb wrapper (dv_fi_sel 1 bank word OR, 2 program memory XOR, 3 cfg XOR,
  // 4 DMA pc XOR, 5 router out_flit XOR, 6 router out_valid cleared, 7 NIC transmit engine held; at the falling edge while
  // dv_fi_en) and the observation of the router's output registers
  input  logic          dv_bd_we,
  input  logic [BAW-1:0] dv_bd_addr,
  input  logic [38:0]   dv_bd_wdata,
  output logic [38:0]   dv_bd_rdata,
  input  logic          dv_fi_en,
  input  logic [2:0]    dv_fi_sel,
  input  logic [19:0]   dv_fi_idx,
  input  logic [DWL-1:0] dv_fi_mask,
  output logic [4:0]    dv_rt_valid,
  output logic [FWL-1:0] dv_rt_flit4
);
  /*verilator hier_block*/                   // drop 0.35: with --hierarchical, this module is verilated once as its own library
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
      assign r_in_flit[p]   = in_flit[p*FWL +: FWL];
      assign in_ready[p]    = r_in_ready[p];
      assign out_valid[p]   = r_out_valid[p];
      assign out_flit[p*FWL +: FWL] = r_out_flit[p];
      assign r_out_ready[p] = out_ready[p];
    end
  endgenerate

  // the testbench hooks, one per fault selector (drop 0.35)
  logic dv_bank_or, dv_prog_flip, dv_cfg_flip, dv_pc_flip, dv_rt_flip, dv_rt_clear, dv_tx_hold;
  assign dv_bank_or   = dv_fi_en && (dv_fi_sel == 3'd1);
  assign dv_prog_flip = dv_fi_en && (dv_fi_sel == 3'd2);
  assign dv_cfg_flip  = dv_fi_en && (dv_fi_sel == 3'd3);
  assign dv_pc_flip   = dv_fi_en && (dv_fi_sel == 3'd4);
  assign dv_rt_flip   = dv_fi_en && (dv_fi_sel == 3'd5);
  assign dv_rt_clear  = dv_fi_en && (dv_fi_sel == 3'd6);
  assign dv_tx_hold   = dv_fi_en && (dv_fi_sel == 3'd7);
  generate
    if (DV_HOOKS != 0) begin : g_dv_ob
      assign dv_rt_valid = {r_out_valid[4], r_out_valid[3], r_out_valid[2], r_out_valid[1], r_out_valid[0]};
      assign dv_rt_flit4 = r_out_flit[4];
    end else begin : g_nodv_ob
      assign dv_rt_valid = '0;
      assign dv_rt_flit4 = '0;
    end
  endgenerate

  noc_router #(.XW(XW), .YW(YW), .DW(DWL), .FW(FWL), .DV_HOOKS(DV_HOOKS)) u_router (
    .clk(clk), .rst_n(rst_n), .my_x(my_x), .my_y(my_y),
    .in_valid(r_in_valid), .in_flit(r_in_flit), .in_ready(r_in_ready),
    .out_valid(r_out_valid), .out_flit(r_out_flit), .out_ready(r_out_ready),
    .err_clear(err_clear), .parity_err_sticky(parity_err),
    .dv_flit_flip(dv_rt_flip), .dv_valid_clear(dv_rt_clear), .dv_port(dv_fi_idx[2:0]), .dv_mask(FWL'(dv_fi_mask)));

  // the link layer (drop 0.25; beats since drop 0.29): WPF words per flit on the router side, BW-word beats on the
  // interface side
  localparam int LANES = 16;                 // lanes of the 512-bit bank = words per beat
  localparam int BW  = LANES;
  localparam int DWB = 32 + 32 * BW;         // the beat payload
  localparam int FWB = 1 + 2 * (XW + YW) + 8 + DWB;
  logic           nic_tx_valid, nic_tx_ready, nic_rx_valid, nic_rx_ready;
  logic [FWB-1:0] nic_tx_flit, nic_rx_flit;
  link_packer #(.XW(XW), .YW(YW), .BW(BW), .WPF(WPF)) u_pack (
    .clk(clk), .rst_n(rst_n), .core_valid(nic_tx_valid), .core_flit(nic_tx_flit), .core_ready(nic_tx_ready),
    .link_valid(r_in_valid[4]), .link_flit(r_in_flit[4]), .link_ready(r_in_ready[4]));
  link_unpacker #(.XW(XW), .YW(YW), .BW(BW), .WPF(WPF)) u_unpack (
    .clk(clk), .rst_n(rst_n), .link_valid(r_out_valid[4]), .link_flit(r_out_flit[4]), .link_ready(r_out_ready[4]),
    .core_valid(nic_rx_valid), .core_flit(nic_rx_flit), .core_ready(nic_rx_ready));

  // the 512-bit bank (drop 0.28): 16 SECDED lanes per row, MBIST over rows; the interface drives the row port (drop 0.29)
  localparam int BROWS = BANK_DEPTH / LANES;
  localparam int BRAW  = (BROWS > 1) ? $clog2(BROWS) : 1;
  logic                b_we, b_re, b_rvalid;
  logic [BRAW-1:0]     b_wrow, b_rrow;
  logic [LANES-1:0]    b_wmask;
  logic [32*LANES-1:0] b_wdata, b_rdata;
  logic bist_start, bist_active, bist_done, bist_fail, bist_fail_ce;
  logic [BAW-1:0] bist_fail_addr;
  bank_bist_wrap #(.DEPTH(BANK_DEPTH), .AW(BAW), .BIST_WORDS(BIST_WORDS), .LANES(LANES), .DV_HOOKS(DV_HOOKS)) u_bank (
    .clk(clk), .rst_n(rst_n), .we(b_we), .wrow(b_wrow), .wmask(b_wmask), .wdata(b_wdata), .re(b_re), .rrow(b_rrow), .rdata(b_rdata), .rvalid(b_rvalid),
    .err_clear(err_clear), .ecc_ce_sticky(ecc_ce), .ecc_ue_sticky(ecc_ue),
    .bist_start(bist_start), .bist_active(bist_active), .bist_done(bist_done), .bist_fail(bist_fail), .bist_fail_ce(bist_fail_ce),
    .bist_fail_addr(bist_fail_addr),
    .dv_bd_we(dv_bd_we), .dv_bd_addr(dv_bd_addr), .dv_bd_wdata(dv_bd_wdata), .dv_bd_rdata(dv_bd_rdata),
    .dv_fi_or(dv_bank_or), .dv_fi_addr(dv_fi_idx[BAW-1:0]), .dv_fi_mask(dv_fi_mask[38:0]));

  // core buffers / ports driven by the NIC
  logic                  abuf_we, wbuf_we, abuf_we2, wbuf_we2;
  logic [AW-1:0]         abuf_waddr;
  logic [WAW-1:0]        wbuf_waddr;
  logic signed [7:0]     abuf_wdata [ROWS], abuf_wdata2 [ROWS];
  logic signed [7:0]     wbuf_wdata [COLS], wbuf_wdata2 [COLS];
  logic signed [WCW-1:0] wcbuf_wdata, wcbuf_wdata2;
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
  prog_mem #(.DEPTH(PDEPTH), .AW(PAW), .DV_HOOKS(DV_HOOKS)) u_prog (
                   .clk(clk), .rst_n(rst_n), .we(prog_we), .waddr(prog_waddr), .wdata(prog_wdata), .raddr(dma_pc), .rdata(dma_ins),
                   .err_clear(err_clear), .prog_ce_sticky(prog_ce), .prog_ue_sticky(prog_ue),
                   .dv_fi_flip(dv_prog_flip), .dv_fi_idx(dv_fi_idx[PAW-1:0]), .dv_fi_mask(dv_fi_mask[38:0]));
  tile_dma #(.XW(XW), .YW(YW), .PDEPTH(PDEPTH), .PAW(PAW), .DV_HOOKS(DV_HOOKS)) u_dma (
    .clk(clk), .rst_n(rst_n),
    .pc_out(dma_pc), .ins_in(dma_ins), .prog_start(prog_start), .fsm_err(dma_fsm_err),
    .prog_done(prog_done), .prog_busy(prog_busy),
    .cmd_valid(cmd_valid), .cmd_op(cmd_op), .cmd_x(cmd_x), .cmd_y(cmd_y), .cmd_addr(cmd_addr), .cmd_len(cmd_len),
    .cmd_base(cmd_base), .cmd_int8(cmd_int8), .cmd_busy(cmd_busy),
    .go(go), .done(done), .ct_free(ct_free), .reduce_ready(reduce_ready), .rdy_seen(rdy_seen), .rdy_clear(rdy_clear),
    .ntf_busy(ntf_busy), .tiles_ready(tiles_ready),
    .dv_pc_flip(dv_pc_flip), .dv_pc_mask(PAW'(dv_fi_mask[3:0])));
  // the duplicate engine: same inputs, outputs compared every cycle
  logic        d_cmd_valid, d_go, d_prog_done, d_prog_busy, d_rdy_clear, d_cmd_int8;
  logic [2:0]  d_cmd_op; logic [XW-1:0] d_cmd_x; logic [YW-1:0] d_cmd_y; logic [19:0] d_cmd_addr; logic [11:0] d_cmd_len;
  logic [15:0] d_cmd_base; logic signed [15:0] d_tiles_ready;
  logic        dma_err, dma_err_sticky, cfg_err, cfg_err_sticky, ctrl_path_err, iso_err;
  tile_dma #(.XW(XW), .YW(YW), .PDEPTH(PDEPTH), .PAW(PAW), .DV_HOOKS(DV_HOOKS)) u_dma_dup (
    .clk(clk), .rst_n(rst_n),
    .pc_out(dma_pc_d), .ins_in(dma_ins), .prog_start(prog_start), .fsm_err(dma_fsm_err_d),
    .prog_done(d_prog_done), .prog_busy(d_prog_busy),
    .cmd_valid(d_cmd_valid), .cmd_op(d_cmd_op), .cmd_x(d_cmd_x), .cmd_y(d_cmd_y), .cmd_addr(d_cmd_addr), .cmd_len(d_cmd_len),
    .cmd_base(d_cmd_base), .cmd_int8(d_cmd_int8), .cmd_busy(cmd_busy),
    .go(d_go), .done(done), .ct_free(ct_free), .reduce_ready(reduce_ready), .rdy_seen(rdy_seen), .rdy_clear(d_rdy_clear),
    .ntf_busy(ntf_busy), .tiles_ready(d_tiles_ready),
    .dv_pc_flip(1'b0), .dv_pc_mask('0));                 // the fault hits the primary engine only
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

  tile_nic #(.XW(XW), .YW(YW), .NX(NX), .NSRC(NX * NY), .BW(BW),
             .ROWS(ROWS), .COLS(COLS), .WCW(WCW), .IDXW(IDXW), .AW(AW), .WAW(WAW), .BAW(BAW), .PW(PW),
             .DFD(ACC_ROWS), .FETCH_TIMEOUT(FETCH_TIMEOUT), .DV_HOOKS(DV_HOOKS)) u_nic (
    .clk(clk), .rst_n(rst_n), .my_x(my_x), .my_y(my_y),
    .rx_valid(nic_rx_valid), .rx_flit(nic_rx_flit), .rx_ready(nic_rx_ready),
    .tx_valid(nic_tx_valid), .tx_flit(nic_tx_flit), .tx_ready(nic_tx_ready),
    .b_we(b_we), .b_wrow(b_wrow), .b_wmask(b_wmask), .b_wdata(b_wdata), .b_re(b_re), .b_rrow(b_rrow), .b_rdata(b_rdata), .b_rvalid(b_rvalid),
    .abuf_we(abuf_we), .abuf_waddr(abuf_waddr), .abuf_wdata(abuf_wdata), .abuf_we2(abuf_we2), .abuf_wdata2(abuf_wdata2),
    .wbuf_we(wbuf_we), .wbuf_waddr(wbuf_waddr), .wbuf_wdata(wbuf_wdata), .wcbuf_wdata(wcbuf_wdata),
    .wbuf_we2(wbuf_we2), .wbuf_wdata2(wbuf_wdata2), .wcbuf_wdata2(wcbuf_wdata2),
    .ext_valid(ext_valid), .ext_idx(ext_idx), .ext_y(ext_y), .ext_chk(ext_chk), .ext_ready(ext_ready),
    .rd_valid(rd_valid), .rd_data(rd_data), .rd_chk(rd_chk), .rq_valid(rq_valid), .rq_q(rq_q),
    .cmd_valid(cmd_valid), .cmd_op(cmd_op), .cmd_x(cmd_x), .cmd_y(cmd_y), .cmd_addr(cmd_addr), .cmd_len(cmd_len),
    .cmd_base(cmd_base), .cmd_int8(cmd_int8), .cmd_busy(cmd_busy), .drain_busy(drain_busy),
    .crc_err_sticky(crc_err), .err_clear(err_clear), .rdy_seen(rdy_seen), .rdy_clear(rdy_clear), .ntf_busy(ntf_busy),
    .partition(partition), .iso_err_sticky(iso_err), .lost_err_sticky(lost_err), .fetch_timeout_sticky(fetch_timeout),
    .dv_tx_hold(dv_tx_hold));

  host_if #(.IDXW(IDXW), .PAW(PAW), .DV_HOOKS(DV_HOOKS)) u_host (
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
    .flags({ecc_ce, ecc_ue, rq_err_sticky, seq_err_sticky, ctrl_err_sticky, acc_abft_sticky, array_abft_sticky, crc_err, parity_err}),
    .dv_cfg_flip(dv_cfg_flip), .dv_cfg_idx(dv_fi_idx[4:0]), .dv_cfg_mask(dv_fi_mask[15:0]));

  assign tile_flags = {iso_err, ctrl_path_err, prog_ce, bist_fail, fetch_timeout, lost_err, rq_tbl_perr, wbuf_ue, wbuf_ce, ecc_ce, ecc_ue, rq_err_sticky,
                       seq_err_sticky, ctrl_err_sticky, acc_abft_sticky, array_abft_sticky, crc_err, parity_err};
  esm #(.NFLAGS(18)) u_esm (
    .clk(clk), .rst_n(rst_n), .flags(tile_flags), .mask(err_mask), .err_clear(err_clear),
    .wd_enable(wd_enable), .wd_window(wd_window), .wd_kick(wd_kick), .cause(err_cause), .err_pin(err_pin));
  // what the tile does not read (lint): the core's busy / state / ct_free_idx / unlatched acc_abft, the engines' prog_busy,
  // the duplicate engine's rdy_clear, the table address bits above the core's columns, the fault index bits above the widest field
  logic unused_tile_bits;
  assign unused_tile_bits = ^{busy, state_dbg, ct_free_idx, acc_abft_err, prog_busy, d_prog_busy, d_rdy_clear, rq_tbl_addr, dv_fi_idx};

  neo_core #(.ROWS(ROWS), .COLS(COLS), .PW(PW), .ACC_ROWS(ACC_ROWS), .ABUF_DEPTH(ABUF_DEPTH), .WBUF_DEPTH(WBUF_DEPTH)) u_core (
    .clk(clk), .rst_n(rst_n),
    .abuf_we(abuf_we), .abuf_waddr(abuf_waddr), .abuf_wdata(abuf_wdata), .abuf_we2(abuf_we2), .abuf_wdata2(abuf_wdata2),
    .wbuf_we(wbuf_we), .wbuf_waddr(wbuf_waddr), .wbuf_wdata(wbuf_wdata), .wcbuf_wdata(wcbuf_wdata),
    .wbuf_we2(wbuf_we2), .wbuf_wdata2(wbuf_wdata2), .wcbuf_wdata2(wcbuf_wdata2),
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
