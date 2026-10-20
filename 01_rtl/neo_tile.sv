// neo_tile.sv -- one mesh node (drop 0.7): router + tile interface + local bank + neo_core.
//   Since drop 0.29 the tile interface moves beats of BW = 16 words (one row of the 512-bit bank) on its link side and
//   drives the bank's row port directly; link_packer / link_unpacker re-block between the beats and the WPF-word link flits.
//   Drop 0.43 (stream B2): the telemetry counters (tile_telem, read through host_if at 0x40 .. 0x4C) and, for the
//   testbenches (DV_HOOKS = 1; zero in silicon), dv_obs: the twelve clock enables of the tile's gated blocks and the
//   telemetry's eleven events, so a test counts what the counters count and how long each block's clock was off --
//   bits 0 datapath, 1 requant, 2 requant duplicate, 3 NIC receive, 4 NIC transmit, 5..9 router output stage N, E, S, W, L,
//   10 DMA engine, 11 DMA duplicate (clock enables); 12 array valid, 13 feeder stall, 14 fetch wait, 15 drain, 16..20 link
//   stall N, E, S, W, L, 21 bank read busy, 22 bank write busy (the counters' events 1 .. 11, tile_telem.sv).
//   Drop 0.43, the power-gating hooks: the tile is two domains. The router is always on -- it has its own reset
//   (router_rst_n, the mesh's) and the ungated clock, so it forwards the mesh's traffic while the rest of the tile is in
//   reset, clocked off or unpowered (B1's fence then drops what is addressed to the tile). Everything else (the NIC, the
//   packer / unpacker, the bank, the core, the DMA, host_if, the ESM, the telemetry) is the switchable domain: its clock
//   is clk through one neo_icg enabled by tile_clk_en, its reset rst_n, and its outputs reach the always-on side through
//   neo_iso cells clamped while iso_en -- the link into the router (valid and flit 0: an unpowered domain's outputs float),
//   the router's local output ready (1: whatever reaches it drains), and the status toward the control plane
//   (h_rdata, err_pin, prog_done, drain_busy, done, the stickies but the router's, the lockstep pair: 0; the dv_* hooks
//   are not isolated -- testbench ports, constant or a black box's pins in silicon, the synthesis job's DV_HOOKS gate). The
//   control plane sequences them (mesh_ctrl.sv: iso on, clock off, reset, power off; power on, reset released with the
//   clock still off, clock on, iso off). Nothing is retained: a tile comes back from reset and re-loads its program,
//   descriptor and tables.
//   In a tile_mesh tile_clk_en = 1, iso_en = 0 and router_rst_n = rst_n (the tile of drop 0.42).
//   Drop 0.43, dual-tile lockstep: drain_crc_last / drain_msg_cnt, the NIC's last drain message's CRC and its message
//   count, for the control plane's comparison of a lockstep pair (top_ctrl.sv / mesh_ctrl.sv).
//   Drop 0.43 (stream C2): the core's vector unit -- its tables written through host_if's VEC_ADDR / VEC_DATA, its residual
//   buffer filled by the NIC (FETCH_A with arg 1); its two flags join the existing causes: the duplicated control's
//   comparator the lockstep cause (seq: the sequencer pair and the vector unit's control pair), the table parity the
//   table-parity cause (tbl_perr: the requant tables and the vector unit's), so the cause register keeps its layout.
//   The DMA engines see the NIC's drain_busy: a DRAIN_WR waits for the previous drain message to complete, so one program
//   can run several GO / WAIT_DONE pairs (an attention round's key tiles) each with its own drain.
//   Drop 0.44 (S1): four causes more -- abuf_ce / abuf_ue (the activation buffer's SECDED, act_feeder.sv), act_perr (the
//   activation path's byte parity at the array's row inputs, neo_mac_core.sv) and drain_ovf (a row pushed into the full
//   drain FIFO, tile_nic.sv) at cause bits 18 .. 21, the watchdog at 22 (host_if's ERR_MASK / ERR_CAUSE 23 bits wide;
//   STATUS bits 17 .. 20); the link packer's check of the NIC's beat parity (link_pack.sv) joins the parity cause (bit 0)
//   beside the router's; the MBIST engine's illegal-state check is in bist_fail (mbist.sv); the index / first lines'
//   parity is in the ctrl cause (neo_mac_core_v02.sv). The weight buffer's and the program memory's ECC flags are
//   qualified by a real read (the shadow-load strobe, the DMA's fetch), as the bank's were: arrays power up random.
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
  parameter int NCHK = 1,                    // the core's check columns: 4 = the 2:4 sparse-capable silicon core (drop 0.42)
  parameter int IW = (NCHK > 1) ? 2 : 0,     // index bits per weight (derived)
  parameter int DV_HOOKS = 0                 // 1: the testbench hooks (dv_* ports) exist; 0: the silicon tile, ports tied off (drop 0.35)
)(
  input  logic          clk,
  input  logic          rst_n,                 // the switchable domain's reset (drop 0.43: the router has its own)
  input  logic          router_rst_n,          // drop 0.43: the router's reset -- the mesh's, so a fused / sleeping tile forwards
  input  logic          tile_clk_en,           // drop 0.43: the switchable domain's clock enable (1: running)
  input  logic          iso_en,                // drop 0.43: its outputs isolated (clamped) toward the router and the control plane
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
  output logic          parity_err,           // the router's (always on); drop 0.44: pk_perr below is the packer's check
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
  output logic          abuf_ce,              // drop 0.44: the activation buffer's SECDED corrected / uncorrectable
  output logic          abuf_ue,
  output logic          act_perr,             // drop 0.44: the activation path's byte parity failed at the array
  output logic          pk_perr,              // drop 0.44: the link packer received a beat of odd parity from the NIC
  output logic          drain_ovf,            // drop 0.44: a row was pushed into the full drain FIFO
  // drop 0.43, lockstep: the last drain message's CRC and the count of drain messages (tile_nic.sv)
  output logic [15:0]   drain_crc_last,
  output logic [15:0]   drain_msg_cnt,
  // testbench hooks (drop 0.35; DV_HOOKS = 1), as ports so that the tile is a hierarchical block of the Verilator build
  // (one tile verilated once, instantiated NX*NY times; a hierarchical block admits no hierarchical references):
  // the bank backdoor, the fault injection of the cocotb wrapper (dv_fi_sel 1 bank word OR, 2 program memory XOR, 3 cfg XOR,
  // 4 DMA pc XOR, 7 NIC transmit engine held -- at the falling edge while dv_fi_en; 5 router output register loaded with its
  // flit XOR the mask, 6 loaded without the valid, so the flit vanishes -- at the rising edge that loads the flit while
  // dv_fi_en, drop 0.38) and the observation of the router's output registers
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
  output logic [31:0]   dv_obs                 // drop 0.43: the clock enables and the telemetry events (above); 0 at DV_HOOKS = 0
);
  /*verilator hier_block*/                   // drop 0.35: with --hierarchical, this module is verilated once as its own library
  // the switchable domain (drop 0.43): its clock (gated by tile_clk_en) and its outputs before the isolation cells
  logic                  clk_t;
  neo_icg u_icg_tile (.clk(clk), .en(tile_clk_en), .te(1'b0), .gclk(clk_t));
  logic [31:0]           h_rdata_t;
  logic                  err_pin_t, prog_done_t, drain_busy_t, done_t, crc_err_t, array_abft_sticky_t, acc_abft_sticky_t;
  logic                  ctrl_err_sticky_t, seq_err_sticky_t, rq_err_sticky_t, ecc_ce_t, ecc_ue_t, wbuf_ce_t, wbuf_ue_t, rq_tbl_perr_t;
  logic                  lost_err_t, fetch_timeout_t;
  logic                  abuf_ce_t, abuf_ue_t, act_perr_t, drain_ovf_t;   // drop 0.44
  logic                  pk_perr_t;                             // drop 0.44: the packer's check of the NIC's beat parity
  logic                  parity_err_t;                          // the parity cause: the router's sticky or the packer's
  logic [15:0]           drain_crc_last_t, drain_msg_cnt_t;
  logic [38:0]           dv_bd_rdata_t;
  logic [31:0]           dv_obs_t;
  logic                  pk_link_valid, up_link_ready;     // the packer's link valid and the unpacker's link ready, before isolation
  logic [FWL-1:0]        pk_link_flit;                     // ... and the packer's flit
  logic [15:0]           rt_prio;              // the router's priority map and error clear from host_if, after isolation
  logic                  rt_err_clear;
  // host interface and error signaling
  logic                  prog_we, prog_start, err_clear, rq_tbl_we, rq_relu, wd_enable, wd_kick, selftest_inject;
  logic [PAW-1:0]        prog_waddr;
  logic [63:0]           prog_wdata;
  logic signed [15:0]    cfg [20];
  logic [IDXW-1:0]       cfg_m;
  logic [4:0]            rq_tbl_addr, rq_tbl_shift;
  logic [15:0]           rq_tbl_mult;
  logic                  vec_we;                       // drop 0.43: the vector unit's table writes, its residual buffer's fill
  logic [11:0]           vec_addr;
  logic [31:0]           vec_wdata;
  logic                  rbuf_we, rbuf_we2;
  logic [IDXW-1:0]       rbuf_waddr;
  logic                  vec_err_sticky, vec_perr_sticky, seq_err_core, rq_tbl_perr_core;
  logic signed [7:0]     rq_tbl_zp;
  logic [22:0]           err_mask, err_cause;    // drop 0.44: 22 causes and the watchdog
  logic [3:0]            partition;
  logic [15:0]           prio;                 // drop 0.43: the router's priority map (host_if 0x0C)
  logic [4:0]            rt_stage_en;          // drop 0.43: the router's output stages' clock enables
  logic [2:0]            core_cg_en;           // drop 0.43: the core's {requant dup, requant, datapath} clock enables
  logic [1:0]            core_ev;              // ... its telemetry events {feeder stall, array valid}
  logic [1:0]            nic_cg_en;            // ... the NIC's {transmit, receive} clock enables
  logic                  nic_drain;            // ... its drain cycles
  logic                  dma_cg_en, dma_cg_en_d, dma_fetch_wait, dma_fetch_wait_d;
  logic                  tel_snap, tel_clear;
  logic [31:0]           tel_rdata;
  logic [23:0]           wd_window;
  logic [21:0]           tile_flags;

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
  // the hooks' decode (DV_HOOKS = 1); at DV_HOOKS = 0 every select is a constant and the ports are tied off (the
  // silicon tile carries none of it -- the synthesis job's DV_HOOKS gate, drop 0.40)
  logic dv_bank_or, dv_prog_flip, dv_cfg_flip, dv_pc_flip, dv_rt_flip, dv_rt_clear, dv_tx_hold;
  generate
    if (DV_HOOKS != 0) begin : g_dv_ob
      assign dv_bank_or   = dv_fi_en && (dv_fi_sel == 3'd1);
      assign dv_prog_flip = dv_fi_en && (dv_fi_sel == 3'd2);
      assign dv_cfg_flip  = dv_fi_en && (dv_fi_sel == 3'd3);
      assign dv_pc_flip   = dv_fi_en && (dv_fi_sel == 3'd4);
      assign dv_rt_flip   = dv_fi_en && (dv_fi_sel == 3'd5);
      assign dv_rt_clear  = dv_fi_en && (dv_fi_sel == 3'd6);
      assign dv_tx_hold   = dv_fi_en && (dv_fi_sel == 3'd7);
      assign dv_rt_valid = {r_out_valid[4], r_out_valid[3], r_out_valid[2], r_out_valid[1], r_out_valid[0]};
      assign dv_rt_flit4 = r_out_flit[4];
    end else begin : g_nodv_ob
      assign dv_bank_or = 1'b0; assign dv_prog_flip = 1'b0; assign dv_cfg_flip = 1'b0; assign dv_pc_flip = 1'b0;
      assign dv_rt_flip = 1'b0; assign dv_rt_clear = 1'b0; assign dv_tx_hold = 1'b0;
      assign dv_rt_valid = '0;
      assign dv_rt_flit4 = '0;
      logic unused_dv_sel;
      assign unused_dv_sel = ^{dv_fi_en, dv_fi_sel};
    end
  endgenerate

  noc_router #(.XW(XW), .YW(YW), .DW(DWL), .FW(FWL), .DV_HOOKS(DV_HOOKS)) u_router (
    .clk(clk), .rst_n(router_rst_n), .my_x(my_x), .my_y(my_y), .prio(rt_prio),
    .in_valid(r_in_valid), .in_flit(r_in_flit), .in_ready(r_in_ready),
    .out_valid(r_out_valid), .out_flit(r_out_flit), .out_ready(r_out_ready),
    .err_clear(rt_err_clear), .parity_err_sticky(parity_err), .stage_en(rt_stage_en),
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
    .clk(clk_t), .rst_n(rst_n), .core_valid(nic_tx_valid), .core_flit(nic_tx_flit), .core_ready(nic_tx_ready),
    .link_valid(pk_link_valid), .link_flit(pk_link_flit), .link_ready(r_in_ready[4]),
    .err_clear(err_clear), .perr_sticky(pk_perr_t));
  assign parity_err_t = parity_err | pk_perr_t;
  link_unpacker #(.XW(XW), .YW(YW), .BW(BW), .WPF(WPF)) u_unpack (
    .clk(clk_t), .rst_n(rst_n), .link_valid(r_out_valid[4]), .link_flit(r_out_flit[4]), .link_ready(up_link_ready),
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
    .clk(clk_t), .rst_n(rst_n), .we(b_we), .wrow(b_wrow), .wmask(b_wmask), .wdata(b_wdata), .re(b_re), .rrow(b_rrow), .rdata(b_rdata), .rvalid(b_rvalid),
    .err_clear(err_clear), .ecc_ce_sticky(ecc_ce_t), .ecc_ue_sticky(ecc_ue_t),
    .bist_start(bist_start), .bist_active(bist_active), .bist_done(bist_done), .bist_fail(bist_fail), .bist_fail_ce(bist_fail_ce),
    .bist_fail_addr(bist_fail_addr),
    .dv_bd_we(dv_bd_we), .dv_bd_addr(dv_bd_addr), .dv_bd_wdata(dv_bd_wdata), .dv_bd_rdata(dv_bd_rdata_t),
    .dv_fi_or(dv_bank_or), .dv_fi_addr(dv_fi_idx[BAW-1:0]), .dv_fi_mask(dv_fi_mask[38:0]));

  // core buffers / ports driven by the NIC
  logic                  abuf_we, wbuf_we, abuf_we2, wbuf_we2;
  logic [AW-1:0]         abuf_waddr;
  logic [WAW-1:0]        wbuf_waddr;
  logic signed [7:0]     abuf_wdata [ROWS], abuf_wdata2 [ROWS];
  logic [8+IW-1:0]       wbuf_wdata [COLS], wbuf_wdata2 [COLS];     // {index, weight} (drop 0.42)
  logic [NCHK*WCW-1:0]   wcbuf_wdata, wcbuf_wdata2;
  logic                  ext_valid, ext_ready, reduce_ready;
  logic [IDXW-1:0]       ext_idx;
  logic signed [PW-1:0]  ext_y [COLS];
  logic signed [PW-1:0]  ext_chk;
  logic signed [PW-1:0]  rd_data [COLS];
  logic signed [PW-1:0]  rd_chk;
  logic                  rd_valid, rq_valid, busy, ct_free, acc_abft_err, acc_abft_err_valid;
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
  logic        dma_fetch, dma_fetch_d;         // drop 0.44: the engines take the word on rdata (the ECC flags' qualifier)
  prog_mem #(.DEPTH(PDEPTH), .AW(PAW), .DV_HOOKS(DV_HOOKS)) u_prog (
                   .clk(clk_t), .rst_n(rst_n), .we(prog_we), .waddr(prog_waddr), .wdata(prog_wdata), .raddr(dma_pc),
                   .rd_q(dma_fetch || dma_fetch_d), .rdata(dma_ins),
                   .err_clear(err_clear), .prog_ce_sticky(prog_ce), .prog_ue_sticky(prog_ue),
                   .dv_fi_flip(dv_prog_flip), .dv_fi_idx(dv_fi_idx[PAW-1:0]), .dv_fi_mask(dv_fi_mask[38:0]));
  tile_dma #(.XW(XW), .YW(YW), .PDEPTH(PDEPTH), .PAW(PAW), .DV_HOOKS(DV_HOOKS)) u_dma (
    .clk(clk_t), .rst_n(rst_n),
    .pc_out(dma_pc), .ins_in(dma_ins), .fetch(dma_fetch), .prog_start(prog_start), .fsm_err(dma_fsm_err),
    .prog_done(prog_done_t), .prog_busy(prog_busy),
    .cmd_valid(cmd_valid), .cmd_op(cmd_op), .cmd_x(cmd_x), .cmd_y(cmd_y), .cmd_addr(cmd_addr), .cmd_len(cmd_len),
    .cmd_base(cmd_base), .cmd_int8(cmd_int8), .cmd_busy(cmd_busy),
    .go(go), .done(done_t), .ct_free(ct_free), .reduce_ready(reduce_ready), .rdy_seen(rdy_seen), .rdy_clear(rdy_clear),
    .ntf_busy(ntf_busy), .drain_busy(drain_busy_t), .tiles_ready(tiles_ready),
    .dv_pc_flip(dv_pc_flip), .dv_pc_mask(PAW'(dv_fi_mask[3:0])), .cg_en(dma_cg_en), .fetch_wait(dma_fetch_wait));
  // the duplicate engine: same inputs, outputs compared every cycle
  logic        d_cmd_valid, d_go, d_prog_done, d_prog_busy, d_rdy_clear, d_cmd_int8;
  logic [2:0]  d_cmd_op; logic [XW-1:0] d_cmd_x; logic [YW-1:0] d_cmd_y; logic [19:0] d_cmd_addr; logic [11:0] d_cmd_len;
  logic [15:0] d_cmd_base; logic signed [15:0] d_tiles_ready;
  logic        dma_err, dma_err_sticky, cfg_err, cfg_err_sticky, ctrl_path_err, iso_err;
  tile_dma #(.XW(XW), .YW(YW), .PDEPTH(PDEPTH), .PAW(PAW), .DV_HOOKS(DV_HOOKS)) u_dma_dup (
    .clk(clk_t), .rst_n(rst_n),
    .pc_out(dma_pc_d), .ins_in(dma_ins), .fetch(dma_fetch_d), .prog_start(prog_start), .fsm_err(dma_fsm_err_d),
    .prog_done(d_prog_done), .prog_busy(d_prog_busy),
    .cmd_valid(d_cmd_valid), .cmd_op(d_cmd_op), .cmd_x(d_cmd_x), .cmd_y(d_cmd_y), .cmd_addr(d_cmd_addr), .cmd_len(d_cmd_len),
    .cmd_base(d_cmd_base), .cmd_int8(d_cmd_int8), .cmd_busy(cmd_busy),
    .go(d_go), .done(done_t), .ct_free(ct_free), .reduce_ready(reduce_ready), .rdy_seen(rdy_seen), .rdy_clear(d_rdy_clear),
    .ntf_busy(ntf_busy), .drain_busy(drain_busy_t), .tiles_ready(d_tiles_ready),
    .dv_pc_flip(1'b0), .dv_pc_mask('0),                  // the fault hits the primary engine only
    .cg_en(dma_cg_en_d), .fetch_wait(dma_fetch_wait_d));
  assign dma_err = (cmd_valid != d_cmd_valid) || (cmd_op != d_cmd_op) || (cmd_x != d_cmd_x) || (cmd_y != d_cmd_y) ||
                   (cmd_addr != d_cmd_addr) || (cmd_len != d_cmd_len) || (cmd_base != d_cmd_base) || (cmd_int8 != d_cmd_int8) ||
                   (go != d_go) || (prog_done_t != d_prog_done) || (dma_pc != dma_pc_d) || (tiles_ready != d_tiles_ready);
  always_ff @(posedge clk_t or negedge rst_n) begin
    if (!rst_n) begin dma_err_sticky <= 1'b0; cfg_err_sticky <= 1'b0; end
    else begin
      if (err_clear) begin dma_err_sticky <= 1'b0; cfg_err_sticky <= 1'b0; end
      if (dma_err) dma_err_sticky <= 1'b1;
      if (cfg_err) cfg_err_sticky <= 1'b1;
    end
  end
  assign ctrl_path_err = dma_err_sticky | cfg_err_sticky | prog_ue | dma_fsm_err | dma_fsm_err_d;

  tile_nic #(.XW(XW), .YW(YW), .NX(NX), .NSRC(NX * NY), .BW(BW),
             .ROWS(ROWS), .COLS(COLS), .WCW(WCW), .IDXW(IDXW), .AW(AW), .WAW(WAW), .BAW(BAW), .PW(PW), .NCHK(NCHK),
             .DFD(ACC_ROWS), .FETCH_TIMEOUT(FETCH_TIMEOUT), .DV_HOOKS(DV_HOOKS)) u_nic (
    .clk(clk_t), .rst_n(rst_n), .my_x(my_x), .my_y(my_y),
    .rx_valid(nic_rx_valid), .rx_flit(nic_rx_flit), .rx_ready(nic_rx_ready),
    .tx_valid(nic_tx_valid), .tx_flit(nic_tx_flit), .tx_ready(nic_tx_ready),
    .b_we(b_we), .b_wrow(b_wrow), .b_wmask(b_wmask), .b_wdata(b_wdata), .b_re(b_re), .b_rrow(b_rrow), .b_rdata(b_rdata), .b_rvalid(b_rvalid),
    .abuf_we(abuf_we), .abuf_waddr(abuf_waddr), .abuf_wdata(abuf_wdata), .abuf_we2(abuf_we2), .abuf_wdata2(abuf_wdata2),
    .rbuf_we(rbuf_we), .rbuf_we2(rbuf_we2), .rbuf_waddr(rbuf_waddr),
    .wbuf_we(wbuf_we), .wbuf_waddr(wbuf_waddr), .wbuf_wdata(wbuf_wdata), .wcbuf_wdata(wcbuf_wdata),
    .wbuf_we2(wbuf_we2), .wbuf_wdata2(wbuf_wdata2), .wcbuf_wdata2(wcbuf_wdata2),
    .ext_valid(ext_valid), .ext_idx(ext_idx), .ext_y(ext_y), .ext_chk(ext_chk), .ext_ready(ext_ready),
    .rd_valid(rd_valid), .rd_data(rd_data), .rd_chk(rd_chk), .rq_valid(rq_valid), .rq_q(rq_q),
    .cmd_valid(cmd_valid), .cmd_op(cmd_op), .cmd_x(cmd_x), .cmd_y(cmd_y), .cmd_addr(cmd_addr), .cmd_len(cmd_len),
    .cmd_base(cmd_base), .cmd_int8(cmd_int8), .cmd_busy(cmd_busy), .drain_busy(drain_busy_t),
    .crc_err_sticky(crc_err_t), .err_clear(err_clear), .rdy_seen(rdy_seen), .rdy_clear(rdy_clear), .ntf_busy(ntf_busy),
    .partition(partition), .iso_err_sticky(iso_err), .lost_err_sticky(lost_err_t), .fetch_timeout_sticky(fetch_timeout_t),
    .drain_ovf_sticky(drain_ovf_t),
    .dv_tx_hold(dv_tx_hold), .cg_en(nic_cg_en), .tel_drain(nic_drain), .drain_crc_last(drain_crc_last_t), .drain_msg_cnt(drain_msg_cnt_t));

  host_if #(.IDXW(IDXW), .PAW(PAW), .DV_HOOKS(DV_HOOKS)) u_host (
    .clk(clk_t), .rst_n(rst_n), .h_we(h_we), .h_re(h_re), .h_addr(h_addr), .h_wdata(h_wdata), .h_rdata(h_rdata_t),
    .prog_we(prog_we), .prog_waddr(prog_waddr), .prog_wdata(prog_wdata), .prog_start(prog_start), .err_clear(err_clear),
    .cfg(cfg), .cfg_m(cfg_m),
    .rq_tbl_we(rq_tbl_we), .rq_tbl_addr(rq_tbl_addr), .rq_tbl_mult(rq_tbl_mult), .rq_tbl_shift(rq_tbl_shift), .rq_tbl_zp(rq_tbl_zp),
    .rq_relu(rq_relu), .vec_we(vec_we), .vec_addr(vec_addr), .vec_wdata(vec_wdata),
    .err_mask(err_mask), .err_cause(err_cause), .wd_enable(wd_enable), .wd_window(wd_window), .wd_kick(wd_kick),
    .selftest_inject(selftest_inject), .partition(partition), .prio(prio), .tel_snap(tel_snap), .tel_clear(tel_clear),
    .tel_rdata(tel_rdata), .cfg_err(cfg_err),
    .bist_start(bist_start), .bist_active(bist_active), .bist_done(bist_done), .bist_fail(bist_fail), .bist_fail_ce(bist_fail_ce),
    .bist_fail_addr(20'(bist_fail_addr)),        // 20-bit field: a 512K-word bank needs 19 (drop 0.22; the 16-bit field went negative at BAW = 19)
    .prog_done(prog_done_t), .drain_busy(drain_busy_t), .core_done(done_t),
    .flags({drain_ovf_t, act_perr_t, abuf_ue_t, abuf_ce_t,
            ecc_ce_t, ecc_ue_t, rq_err_sticky_t, seq_err_sticky_t, ctrl_err_sticky_t, acc_abft_sticky_t, array_abft_sticky_t, crc_err_t, parity_err_t}),
    .dv_cfg_flip(dv_cfg_flip), .dv_cfg_idx(dv_fi_idx[4:0]), .dv_cfg_mask(dv_fi_mask[15:0]));

  assign tile_flags = {drain_ovf_t, act_perr_t, abuf_ue_t, abuf_ce_t,
                       iso_err, ctrl_path_err, prog_ce, bist_fail, fetch_timeout_t, lost_err_t, rq_tbl_perr_t, wbuf_ue_t, wbuf_ce_t, ecc_ce_t, ecc_ue_t, rq_err_sticky_t,
                       seq_err_sticky_t, ctrl_err_sticky_t, acc_abft_sticky_t, array_abft_sticky_t, crc_err_t, parity_err_t};
  esm #(.NFLAGS(22)) u_esm (
    .clk(clk_t), .rst_n(rst_n), .flags(tile_flags), .mask(err_mask), .err_clear(err_clear),
    .wd_enable(wd_enable), .wd_window(wd_window), .wd_kick(wd_kick), .cause(err_cause), .err_pin(err_pin_t));
  // what the tile does not read (lint): the core's busy / state / ct_free_idx / unlatched acc_abft and its row marker, the
  // engines' prog_busy, the duplicate engine's rdy_clear, the table address bits above the core's columns, the fault index bits
  // above the widest field
  logic unused_tile_bits;
  assign unused_tile_bits = ^{busy, state_dbg, ct_free_idx, acc_abft_err, acc_abft_err_valid, prog_busy, d_prog_busy, d_rdy_clear,
                              rq_tbl_addr, dv_fi_idx};

  // the vector unit's flags (drop 0.43) in the causes of their kind: the lockstep cause and the table-parity cause
  // (the switchable domain's flags, so STATUS, the ESM and the isolated outputs all see them)
  assign seq_err_sticky_t = seq_err_core | vec_err_sticky;
  assign rq_tbl_perr_t    = rq_tbl_perr_core | vec_perr_sticky;

  // the layer descriptor's flags (drop 0.42): cfg[18] (host register 0x22, reserved before) -- bit 0 the 2:4 sparse mode.
  // A descriptor register, so it is kept twice and compared with the others (host_if's cfg_err): a flipped mode bit
  // would feed the array consistently wrong bytes, which the ABFT cannot see
  logic cfg_sparse;
  assign cfg_sparse = cfg[18][0];
  neo_core #(.ROWS(ROWS), .COLS(COLS), .PW(PW), .ACC_ROWS(ACC_ROWS), .ABUF_DEPTH(ABUF_DEPTH), .WBUF_DEPTH(WBUF_DEPTH),
             .NCHK(NCHK)) u_core (
    .clk(clk_t), .rst_n(rst_n),
    .abuf_we(abuf_we), .abuf_waddr(abuf_waddr), .abuf_wdata(abuf_wdata), .abuf_we2(abuf_we2), .abuf_wdata2(abuf_wdata2),
    .wbuf_we(wbuf_we), .wbuf_waddr(wbuf_waddr), .wbuf_wdata(wbuf_wdata), .wcbuf_wdata(wcbuf_wdata),
    .wbuf_we2(wbuf_we2), .wbuf_wdata2(wbuf_wdata2), .wcbuf_wdata2(wcbuf_wdata2),
    .cfg_h(cfg[0]), .cfg_w(cfg[1]), .cfg_ho(cfg[2]), .cfg_wo(cfg[3]), .cfg_oy0(cfg[4]), .cfg_oy_n(cfg[5]), .cfg_iy0(cfg[6]),
    .cfg_s(cfg[7]), .cfg_p(cfg[8]), .cfg_k(cfg[9]), .cfg_ct_n(cfg[10]), .cfg_ct0(cfg[11]),
    .cfg_ky0(cfg[12]), .cfg_kx0(cfg[13]), .cfg_rn(cfg[14]),
    .cfg_contrib_n(cfg[15]), .cfg_tile_pixels(cfg[16]), .cfg_regions_m1(cfg[17]), .cfg_sparse(cfg_sparse),
    .tiles_ready(tiles_ready), .cfg_m(cfg_m),
    .go(go), .done(done_t), .busy(busy), .state_dbg(state_dbg), .ct_free(ct_free), .ct_free_idx(ct_free_idx),
    .ext_valid(ext_valid), .ext_idx(ext_idx), .ext_y(ext_y), .ext_chk(ext_chk), .ext_ready(ext_ready), .reduce_ready(reduce_ready),
    .rd_data(rd_data), .rd_chk(rd_chk), .rd_valid(rd_valid), .rq_q(rq_q), .rq_valid(rq_valid),
    .rq_tbl_we(rq_tbl_we), .rq_tbl_addr(rq_tbl_addr[$clog2(COLS)-1:0]), .rq_tbl_mult(rq_tbl_mult), .rq_tbl_shift(rq_tbl_shift),
    .rq_tbl_zp(rq_tbl_zp), .rq_relu(rq_relu),
    .vec_we(vec_we), .vec_addr(vec_addr), .vec_wdata(vec_wdata),
    .rbuf_we(rbuf_we), .rbuf_we2(rbuf_we2), .rbuf_waddr(rbuf_waddr), .rbuf_wdata(abuf_wdata), .rbuf_wdata2(abuf_wdata2),
    .err_clear(err_clear), .array_abft_sticky(array_abft_sticky_t), .acc_abft_err(acc_abft_err), .acc_abft_err_valid(acc_abft_err_valid),
    .acc_abft_sticky(acc_abft_sticky_t), .ctrl_err_sticky(ctrl_err_sticky_t),
    .seq_err_sticky(seq_err_core), .rq_err_sticky(rq_err_sticky_t),
    .wbuf_ce_sticky(wbuf_ce_t), .wbuf_ue_sticky(wbuf_ue_t), .rq_tbl_perr_sticky(rq_tbl_perr_core),
    .vec_err_sticky(vec_err_sticky), .vec_perr_sticky(vec_perr_sticky),
    .abuf_ce_sticky(abuf_ce_t), .abuf_ue_sticky(abuf_ue_t), .act_perr_sticky(act_perr_t),
    .fault_inject(selftest_inject), .ctrl_fault_inject(1'b0), .seq_fault_inject(1'b0), .rq_fault_inject(1'b0), .rq_tbl_fault_inject(1'b0),
    .vec_ctrl_fault_inject(1'b0), .vec_tbl_fault_inject(1'b0),
    .dv_wbuf_flip(1'b0), .dv_wbuf_addr('0), .dv_wbuf_lane('0), .dv_wbuf_mask('0),    // the core's hooks are reached from the core-level test
    .dv_abuf_flip(1'b0), .dv_abuf_addr('0), .dv_abuf_lane('0), .dv_abuf_mask('0),
    .cg_en(core_cg_en), .tel_ev(core_ev));

  // the telemetry (drop 0.43): the counters' events, bit 0 the cycle count (tile_telem.sv for the list)
  logic [11:0] tel_ev;
  assign tel_ev = {b_we, b_re, r_out_valid[4] && !r_out_ready[4], r_out_valid[3] && !r_out_ready[3], r_out_valid[2] && !r_out_ready[2],
                   r_out_valid[1] && !r_out_ready[1], r_out_valid[0] && !r_out_ready[0], nic_drain, dma_fetch_wait, core_ev, 1'b1};
  tile_telem #(.NCNT(12)) u_telem (
    .clk(clk_t), .rst_n(rst_n), .ev(tel_ev), .snap(tel_snap), .clear(tel_clear), .rsel(h_addr[3:0]), .rdata(tel_rdata));
  // the observation port (DV_HOOKS = 1): the twelve clock enables, then the eleven events
  generate
    if (DV_HOOKS != 0) begin : g_dv_obs
      assign dv_obs_t = {9'd0, tel_ev[11:1], dma_cg_en_d, dma_cg_en, rt_stage_en, nic_cg_en, core_cg_en};
    end else begin : g_nodv_obs
      assign dv_obs_t = '0;
      logic unused_obs;
      assign unused_obs = ^{rt_stage_en, nic_cg_en, core_cg_en, dma_cg_en, dma_cg_en_d};
    end
  endgenerate
  logic unused_dup_wait;                       // the duplicate engine's fetch wait (the primary's is the counter's)
  assign unused_dup_wait = dma_fetch_wait_d;

  // the isolation cells (drop 0.43): the switchable domain's outputs toward the router and the control plane
  neo_iso #(.W(1), .CLAMP(0)) u_iso_lv (.iso_en(iso_en), .d(pk_link_valid), .q(r_in_valid[4]));     // no flit into the router
  neo_iso #(.W(FWL), .CLAMP(0)) u_iso_lf (.iso_en(iso_en), .d(pk_link_flit), .q(r_in_flit[4]));     // (its bits defined too)
  neo_iso #(.W(1), .CLAMP(1)) u_iso_lr (.iso_en(iso_en), .d(up_link_ready), .q(r_out_ready[4]));    // the local output drains
  // (the cells drive whole vectors, split by part-selects: neosim connects an output port to a signal and assigns no
  // concatenation)
  logic [16:0]  rt_iso_q;
  logic [85:0]  st_iso_q;
  neo_iso #(.W(17), .CLAMP(0)) u_iso_rt (.iso_en(iso_en), .d({prio, err_clear}), .q(rt_iso_q));   // one class, no clear
  assign rt_prio      = rt_iso_q[16:1];
  assign rt_err_clear = rt_iso_q[0];
  neo_iso #(.W(32 + 22 + 32), .CLAMP(0)) u_iso_st (.iso_en(iso_en),
    .d({h_rdata_t, err_pin_t, prog_done_t, drain_busy_t, done_t, crc_err_t, array_abft_sticky_t, acc_abft_sticky_t, ctrl_err_sticky_t,
        seq_err_sticky_t, rq_err_sticky_t, ecc_ce_t, ecc_ue_t, wbuf_ce_t, wbuf_ue_t, rq_tbl_perr_t, lost_err_t, fetch_timeout_t,
        abuf_ce_t, abuf_ue_t, act_perr_t, pk_perr_t, drain_ovf_t,
        drain_crc_last_t, drain_msg_cnt_t}),
    .q(st_iso_q));
  assign h_rdata           = st_iso_q[85:54];
  assign err_pin           = st_iso_q[53];
  assign prog_done         = st_iso_q[52];
  assign drain_busy        = st_iso_q[51];
  assign done              = st_iso_q[50];
  assign crc_err           = st_iso_q[49];
  assign array_abft_sticky = st_iso_q[48];
  assign acc_abft_sticky   = st_iso_q[47];
  assign ctrl_err_sticky   = st_iso_q[46];
  assign seq_err_sticky    = st_iso_q[45];
  assign rq_err_sticky     = st_iso_q[44];
  assign ecc_ce            = st_iso_q[43];
  assign ecc_ue            = st_iso_q[42];
  assign wbuf_ce           = st_iso_q[41];
  assign wbuf_ue           = st_iso_q[40];
  assign rq_tbl_perr       = st_iso_q[39];
  assign lost_err          = st_iso_q[38];
  assign fetch_timeout     = st_iso_q[37];
  assign abuf_ce           = st_iso_q[36];
  assign abuf_ue           = st_iso_q[35];
  assign act_perr          = st_iso_q[34];
  assign pk_perr           = st_iso_q[33];
  assign drain_ovf         = st_iso_q[32];
  assign drain_crc_last    = st_iso_q[31:16];
  assign drain_msg_cnt     = st_iso_q[15:0];
  // the testbench hooks pass straight out (DV_HOOKS = 0: the bank's tied-off pins and a constant -- nothing for a clamp)
  assign dv_bd_rdata       = dv_bd_rdata_t;
  assign dv_obs            = dv_obs_t;
endmodule
