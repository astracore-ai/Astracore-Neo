// neo_top.sv -- the chip top (drop 0.42): the NX x NY tile mesh plus a column of bridge nodes at x = NX, the NoC-to-AXI4
//   bridges in them, the AXI4-Lite control plane, two clock domains with documented crossings (08_ip_product/
//   neo_top_integration.md). The silicon configuration is the default: 8 x 8 tiles of 32 x 32 cores, 2 MB banks, 1024-bit
//   links (WPF 32), a 512-bit AXI4 memory bridge in every row and the host bridge in row 0.
//   Geometry: the mesh is (NX + 1) x NY nodes. Columns 0 .. NX-1 are neo_tile, wired as tile_mesh wires them (edges
//   terminated with i_valid = 0, o_ready = 1); column NX holds one bridge node per row: a noc_router with no tile, its local
//   port into the bridge (or a sink where the row has no bridge role). XY routing reaches a bridge as it reaches a tile -- a
//   FETCH from (NX, y) goes east along row y into the bridge node, its response comes back the same way -- so the
//   coordinates are XW = $clog2(NX + 1) bits (4 for NX = 8; the DMA instruction already carries 4-bit coordinates, the flit
//   head grows by the extra bits). The tiles are elaborated with their NX parameter = NX + 1: a tile indexes its per-source
//   receive state y * NX + x, and with NX tiles per row a bridge's index would alias tile (0, y + 1) (bridge (NX, NY-1) wraps
//   onto tile 0) -- with NX + 1 every node of the mesh has its own source state (NSRC = 72 in silicon, the partial-sum leaf
//   72 x 1,024 bits). Nothing else in the tile depends on NX or XW. tile_mesh is untouched (the mesh8 build is unchanged).
//   Bridges: row y has the memory role (an AXI4 master, m_axi_* port y) when y < N_MEM_BRIDGES and the host role (an AXI4
//   slave, s_axi_* port y) when y < N_HOST_BRIDGES -- noc_axi_bridge.sv for the protocol. A row with neither role gets a
//   bridge node whose local port consumes and drops every flit: a request to it times out at the requester (fetch_timeout),
//   as against a dead tile. The bridge engine runs in aclk; its link_packer / link_unpacker pair is in clk; between them one
//   cdc_fifo of beat flits per direction.
//   Control plane: top_ctrl (aclk: the AXI4-Lite slave, the global registers, the interrupt) -> cdc_sync -> mesh_ctrl (clk:
//   the tiles' register buses, the tile-enable map in effect, the status levels) -- see those modules for the map and rules.
//   Tile enable: a tile whose bit is clear in the map in effect is fenced here: on each of its four mesh inputs a flit
//   addressed to it (dst = its coordinates) is consumed and dropped before the tile sees it, every other flit passes, so its
//   router forwards and its bank and registers do not answer (mesh_ctrl.sv says why it is not held in reset).
//   Clock domains and the crossings, exactly: (1) every bridge's data path, a cdc_fifo per direction (gray pointers, two-flop
//   synchronizers); (2) the register bus, cdc_sync (a request / acknowledge handshake with held payloads); (3) levels
//   through cdc_bits: the tiles' done and err_pin (masked by the enable map) and the effective enable map and the bridge
//   routers' parity flags into aclk, the tile-enable request into clk. No other signal crosses: the bridges' window bases,
//   partitions and error events are aclk signals between aclk modules.
//   Resets: rst_n (clk: the mesh, the bridges' mesh sides, mesh_ctrl) and aresetn (aclk: top_ctrl, the bridge engines) are
//   asserted together; at power-up aresetn is released first -- the control plane comes up and the tile-enable map can be
//   written -- then rst_n, and the tiles leave reset with that map in effect.
module neo_top #(
  parameter int NX = 8, NY = 8,
  parameter int ROWS = 32, COLS = 32, PW = 32,
  parameter int ACC_ROWS = 512, ABUF_DEPTH = 2048, WBUF_DEPTH = 1024, BANK_DEPTH = 524288,
  parameter int WPF = 32,                    // words per link flit: 32 = the 1024-bit links
  parameter int FETCH_TIMEOUT = 1048575,     // the tiles' fetch watchdog (tile_nic.sv)
  parameter int BIST_WORDS = BANK_DEPTH,
  parameter int AXI_DW = 512,                // the bridges' AXI4 data width: 128, 256 or 512
  parameter int AXI_IDW = 4,                 // the host bridges' AXI4 ID width
  parameter int N_MEM_BRIDGES = NY,          // memory bridges in rows 0 .. N_MEM_BRIDGES - 1
  parameter int N_HOST_BRIDGES = 1,          // host bridges in rows 0 .. N_HOST_BRIDGES - 1
  parameter int HOST_TIMEOUT = FETCH_TIMEOUT,  // aclk cycles a host read waits for its tile
  parameter int CTRL_AW = 18,                // the AXI4-Lite byte-address width
  parameter int CDC_DEPTH = 8,               // entries of each bridge FIFO (a power of two, >= 4)
  parameter int XW  = $clog2(NX + 1),        // coordinate widths with the bridge column
  parameter int YW  = (NY > 1) ? $clog2(NY) : 1,
  parameter int NT  = NX * NY,
  parameter int BAW = $clog2(BANK_DEPTH),
  parameter int NTW = (NT > 1) ? $clog2(NT) : 1,
  parameter int HAW = NTW + BAW + 2,         // a host bridge's window: NT banks of 4 * BANK_DEPTH bytes
  parameter int NM  = (N_MEM_BRIDGES > 0) ? N_MEM_BRIDGES : 1,     // port bundles (one tied off when a role is absent)
  parameter int NH  = (N_HOST_BRIDGES > 0) ? N_HOST_BRIDGES : 1
)(
  input  logic                    clk,         // the mesh clock
  input  logic                    rst_n,
  input  logic                    aclk,        // the AXI clock: the control plane and every bridge's AXI side
  input  logic                    aresetn,
  // control plane: AXI4-Lite slave
  input  logic [CTRL_AW-1:0]      s_axil_awaddr,
  input  logic                    s_axil_awvalid,
  output logic                    s_axil_awready,
  input  logic [31:0]             s_axil_wdata,
  input  logic [3:0]              s_axil_wstrb,
  input  logic                    s_axil_wvalid,
  output logic                    s_axil_wready,
  output logic [1:0]              s_axil_bresp,
  output logic                    s_axil_bvalid,
  input  logic                    s_axil_bready,
  input  logic [CTRL_AW-1:0]      s_axil_araddr,
  input  logic                    s_axil_arvalid,
  output logic                    s_axil_arready,
  output logic [31:0]             s_axil_rdata,
  output logic [1:0]              s_axil_rresp,
  output logic                    s_axil_rvalid,
  input  logic                    s_axil_rready,
  // host bridges: AXI4 slaves, bridge h at [h*W +: W]
  input  logic [NH*AXI_IDW-1:0]   s_axi_awid,
  input  logic [NH*HAW-1:0]       s_axi_awaddr,
  input  logic [NH*8-1:0]         s_axi_awlen,
  input  logic [NH*3-1:0]         s_axi_awsize,
  input  logic [NH*2-1:0]         s_axi_awburst,
  input  logic [NH-1:0]           s_axi_awvalid,
  output logic [NH-1:0]           s_axi_awready,
  input  logic [NH*AXI_DW-1:0]    s_axi_wdata,
  input  logic [NH*AXI_DW/8-1:0]  s_axi_wstrb,
  input  logic [NH-1:0]           s_axi_wlast,
  input  logic [NH-1:0]           s_axi_wvalid,
  output logic [NH-1:0]           s_axi_wready,
  output logic [NH*AXI_IDW-1:0]   s_axi_bid,
  output logic [NH*2-1:0]         s_axi_bresp,
  output logic [NH-1:0]           s_axi_bvalid,
  input  logic [NH-1:0]           s_axi_bready,
  input  logic [NH*AXI_IDW-1:0]   s_axi_arid,
  input  logic [NH*HAW-1:0]       s_axi_araddr,
  input  logic [NH*8-1:0]         s_axi_arlen,
  input  logic [NH*3-1:0]         s_axi_arsize,
  input  logic [NH*2-1:0]         s_axi_arburst,
  input  logic [NH-1:0]           s_axi_arvalid,
  output logic [NH-1:0]           s_axi_arready,
  output logic [NH*AXI_IDW-1:0]   s_axi_rid,
  output logic [NH*AXI_DW-1:0]    s_axi_rdata,
  output logic [NH*2-1:0]         s_axi_rresp,
  output logic [NH-1:0]           s_axi_rlast,
  output logic [NH-1:0]           s_axi_rvalid,
  input  logic [NH-1:0]           s_axi_rready,
  // memory bridges: AXI4 masters, bridge m at [m*W +: W] (AxID 0, AxLOCK 0; AxCACHE, AxPROT, AxQOS tied by the integrator)
  output logic [NM*40-1:0]        m_axi_awaddr,
  output logic [NM*8-1:0]         m_axi_awlen,
  output logic [NM*3-1:0]         m_axi_awsize,
  output logic [NM*2-1:0]         m_axi_awburst,
  output logic [NM-1:0]           m_axi_awvalid,
  input  logic [NM-1:0]           m_axi_awready,
  output logic [NM*AXI_DW-1:0]    m_axi_wdata,
  output logic [NM*AXI_DW/8-1:0]  m_axi_wstrb,
  output logic [NM-1:0]           m_axi_wlast,
  output logic [NM-1:0]           m_axi_wvalid,
  input  logic [NM-1:0]           m_axi_wready,
  input  logic [NM*2-1:0]         m_axi_bresp,
  input  logic [NM-1:0]           m_axi_bvalid,
  output logic [NM-1:0]           m_axi_bready,
  output logic [NM*40-1:0]        m_axi_araddr,
  output logic [NM*8-1:0]         m_axi_arlen,
  output logic [NM*3-1:0]         m_axi_arsize,
  output logic [NM*2-1:0]         m_axi_arburst,
  output logic [NM-1:0]           m_axi_arvalid,
  input  logic [NM-1:0]           m_axi_arready,
  input  logic [NM*AXI_DW-1:0]    m_axi_rdata,
  input  logic [NM*2-1:0]         m_axi_rresp,
  input  logic [NM-1:0]           m_axi_rlast,
  input  logic [NM-1:0]           m_axi_rvalid,
  output logic [NM-1:0]           m_axi_rready,
  // to the host and the safety island
  output logic                    irq,
  output logic                    err
);
  localparam int NC  = NX + 1;               // node columns: the tiles and the bridge column
  localparam int NN  = NC * NY;              // mesh nodes, indexed y * NC + x
  localparam int DWL = 32 + 32 * WPF;
  localparam int FWL = 1 + 2 * (XW + YW) + 8 + DWL;   // the link flit
  localparam int BW  = 16;                   // words per beat (a bank row)
  localparam int DWB = 32 + 32 * BW;
  localparam int FB  = 2 * (XW + YW) + 8 + DWB;       // a beat flit without its parity bit (the bridge FIFOs' width)
  localparam int TGW = $clog2(NT + NY);
  localparam int RQW = 1 + TGW + 8 + 32;

  // ------------------------------------------------------------------ the mesh links
  logic          o_valid [NN][4];
  logic [FWL-1:0] o_flit [NN][4];
  logic          o_ready [NN][4];
  logic          i_valid [NN][4];
  logic [FWL-1:0] i_flit [NN][4];
  logic          i_ready [NN][4];
  // the tiles' register buses and status (tile n = y * NX + x)
  logic [NT-1:0]    h_we, h_re;
  logic [7:0]       h_addr;
  logic [31:0]      h_wdata;
  logic [NT*32-1:0] h_rdata;
  logic [NT-1:0]    t_err_pin, t_prog_done, t_drain_busy;
  logic [NT-1:0]    en_eff;                  // the tile-enable map in effect (mesh_ctrl, clk)
  // the bridge nodes' routers (row y)
  logic [NY-1:0]    rtr_clear, rtr_parity;
  // bridges, aclk side (row y)
  logic [NY*34-1:0] win_base;
  logic [NY*4-1:0]  br_part;
  logic [NY-1:0]    ev_crc, ev_lost, ev_axi, ev_timeout, ev_host;

  generate
    for (genvar y = 0; y < NY; y++) begin : g_y
      for (genvar x = 0; x < NC; x++) begin : g_x
        localparam int m = y * NC + x;
        // north input (0) <- south output (2) of the node above; south input (2) <- north output (0) of the node below
        if (y > 0) begin : g_n_in
          assign i_valid[m][0] = o_valid[m-NC][2]; assign i_flit[m][0] = o_flit[m-NC][2]; assign o_ready[m-NC][2] = i_ready[m][0];
        end else begin : g_n_edge
          assign i_valid[m][0] = 1'b0; assign i_flit[m][0] = '0; assign o_ready[m][0] = 1'b1;
        end
        if (y < NY-1) begin : g_s_in
          assign i_valid[m][2] = o_valid[m+NC][0]; assign i_flit[m][2] = o_flit[m+NC][0]; assign o_ready[m+NC][0] = i_ready[m][2];
        end else begin : g_s_edge
          assign i_valid[m][2] = 1'b0; assign i_flit[m][2] = '0; assign o_ready[m][2] = 1'b1;
        end
        // west input (3) <- east output (1) of the node to the left; east input (1) <- west output (3) of the node to the right
        if (x > 0) begin : g_w_in
          assign i_valid[m][3] = o_valid[m-1][1]; assign i_flit[m][3] = o_flit[m-1][1]; assign o_ready[m-1][1] = i_ready[m][3];
        end else begin : g_w_edge
          assign i_valid[m][3] = 1'b0; assign i_flit[m][3] = '0; assign o_ready[m][3] = 1'b1;
        end
        if (x < NC-1) begin : g_e_in
          assign i_valid[m][1] = o_valid[m+1][3]; assign i_flit[m][1] = o_flit[m+1][3]; assign o_ready[m+1][3] = i_ready[m][1];
        end else begin : g_e_edge
          assign i_valid[m][1] = 1'b0; assign i_flit[m][1] = '0; assign o_ready[m][1] = 1'b1;
        end

        if (x < NX) begin : g_tile
          // ---- a tile, behind its fence ----
          localparam int n = y * NX + x;
          logic [3:0]      t_in_valid, t_in_ready, t_out_valid, t_out_ready, fence;
          logic [4*FWL-1:0] t_in_flit, t_out_flit;
          for (genvar p = 0; p < 4; p++) begin : g_lp
            // a flit addressed to a disabled tile is consumed here and dropped (dst_y, dst_x below the parity bit)
            assign fence[p]               = !en_eff[n] && (i_flit[m][p][FWL-2 -: YW] == YW'(y)) && (i_flit[m][p][FWL-2-YW -: XW] == XW'(x));
            assign t_in_valid[p]          = i_valid[m][p] && !fence[p];
            assign t_in_flit[p*FWL +: FWL] = i_flit[m][p];
            assign i_ready[m][p]          = t_in_ready[p] || fence[p];
            assign o_valid[m][p]          = t_out_valid[p];
            assign o_flit[m][p]           = t_out_flit[p*FWL +: FWL];
            assign t_out_ready[p]         = o_ready[m][p];
          end
          logic t_done, t_parity, t_crc, t_aabft, t_cabft, t_ctrl, t_seq, t_rq, t_ece, t_eue, t_wce, t_wue, t_tbl, t_lost, t_tmo;
          logic [38:0]    t_bd_rdata;
          logic [4:0]     t_rt_valid;
          logic [FWL-1:0] t_rt_flit4;
          neo_tile #(.XW(XW), .YW(YW), .NX(NC), .NY(NY), .WPF(WPF), .ROWS(ROWS), .COLS(COLS), .PW(PW), .ACC_ROWS(ACC_ROWS),
                     .ABUF_DEPTH(ABUF_DEPTH), .WBUF_DEPTH(WBUF_DEPTH), .BANK_DEPTH(BANK_DEPTH), .FETCH_TIMEOUT(FETCH_TIMEOUT),
                     .BIST_WORDS(BIST_WORDS), .DV_HOOKS(0)) u_t (
            .clk(clk), .rst_n(rst_n), .my_x(XW'(x)), .my_y(YW'(y)),
            .in_valid(t_in_valid), .in_flit(t_in_flit), .in_ready(t_in_ready),
            .out_valid(t_out_valid), .out_flit(t_out_flit), .out_ready(t_out_ready),
            .h_we(h_we[n]), .h_re(h_re[n]), .h_addr(h_addr), .h_wdata(h_wdata), .h_rdata(h_rdata[32*n +: 32]), .err_pin(t_err_pin[n]),
            .prog_done(t_prog_done[n]), .drain_busy(t_drain_busy[n]), .done(t_done), .parity_err(t_parity), .crc_err(t_crc),
            .array_abft_sticky(t_aabft), .acc_abft_sticky(t_cabft), .ctrl_err_sticky(t_ctrl), .seq_err_sticky(t_seq),
            .rq_err_sticky(t_rq), .ecc_ce(t_ece), .ecc_ue(t_eue), .wbuf_ce(t_wce), .wbuf_ue(t_wue), .rq_tbl_perr(t_tbl),
            .lost_err(t_lost), .fetch_timeout(t_tmo),
            .dv_bd_we(1'b0), .dv_bd_addr('0), .dv_bd_wdata('0), .dv_bd_rdata(t_bd_rdata),
            .dv_fi_en(1'b0), .dv_fi_sel('0), .dv_fi_idx('0), .dv_fi_mask('0), .dv_rt_valid(t_rt_valid), .dv_rt_flit4(t_rt_flit4));
          logic unused_tile;                 // the tile's flags are read through its registers; the testbench hooks are off
          assign unused_tile = ^{t_done, t_parity, t_crc, t_aabft, t_cabft, t_ctrl, t_seq, t_rq, t_ece, t_eue, t_wce, t_wue, t_tbl,
                                 t_lost, t_tmo, t_bd_rdata, t_rt_valid, t_rt_flit4};
        end else begin : g_bnode
          // ---- the bridge node of row y: a router, its local port into the bridge ----
          localparam int MEMY  = (y < N_MEM_BRIDGES) ? 1 : 0;
          localparam int HOSTY = (y < N_HOST_BRIDGES) ? 1 : 0;
          logic          r_in_valid  [5];
          logic [FWL-1:0] r_in_flit  [5];
          logic          r_in_ready  [5];
          logic          r_out_valid [5];
          logic [FWL-1:0] r_out_flit [5];
          logic          r_out_ready [5];
          for (genvar p = 0; p < 4; p++) begin : g_lp
            assign r_in_valid[p]  = i_valid[m][p];
            assign r_in_flit[p]   = i_flit[m][p];
            assign i_ready[m][p]  = r_in_ready[p];
            assign o_valid[m][p]  = r_out_valid[p];
            assign o_flit[m][p]   = r_out_flit[p];
            assign r_out_ready[p] = o_ready[m][p];
          end
          noc_router #(.XW(XW), .YW(YW), .DW(DWL), .FW(FWL), .DV_HOOKS(0)) u_rt (
            .clk(clk), .rst_n(rst_n), .my_x(XW'(NX)), .my_y(YW'(y)),
            .in_valid(r_in_valid), .in_flit(r_in_flit), .in_ready(r_in_ready),
            .out_valid(r_out_valid), .out_flit(r_out_flit), .out_ready(r_out_ready),
            .err_clear(rtr_clear[y]), .parity_err_sticky(rtr_parity[y]),
            .dv_flit_flip(1'b0), .dv_valid_clear(1'b0), .dv_port('0), .dv_mask('0));
          if ((MEMY != 0) || (HOSTY != 0)) begin : g_bridge
            // mesh side (clk): the packer / unpacker pair the NIC uses, beats of 16 words
            logic          up_valid, up_ready, pk_valid, pk_ready;
            logic [FB:0]   up_flit;          // the unpacker's beat (with its parity bit)
            logic [FB-1:0] pk_flit;
            link_unpacker #(.XW(XW), .YW(YW), .BW(BW), .WPF(WPF)) u_unpack (
              .clk(clk), .rst_n(rst_n), .link_valid(r_out_valid[4]), .link_flit(r_out_flit[4]), .link_ready(r_out_ready[4]),
              .core_valid(up_valid), .core_flit(up_flit), .core_ready(up_ready));
            link_packer #(.XW(XW), .YW(YW), .BW(BW), .WPF(WPF)) u_pack (
              .clk(clk), .rst_n(rst_n), .core_valid(pk_valid), .core_flit({1'b0, pk_flit}), .core_ready(pk_ready),
              .link_valid(r_in_valid[4]), .link_flit(r_in_flit[4]), .link_ready(r_in_ready[4]));
            logic unused_up_parity;          // the link parity was checked by the router; the bridge's data has its CRC
            assign unused_up_parity = up_flit[FB];
            // the crossing: one asynchronous FIFO of beats per direction
            logic          rx_valid, rx_ready, tx_valid, tx_ready;
            logic [FB-1:0] rx_flit, tx_flit;
            cdc_fifo #(.W(FB), .DEPTH(CDC_DEPTH)) u_rxq (
              .wclk(clk), .wrst_n(rst_n), .w_valid(up_valid), .w_data(up_flit[FB-1:0]), .w_ready(up_ready),
              .rclk(aclk), .rrst_n(aresetn), .r_valid(rx_valid), .r_data(rx_flit), .r_ready(rx_ready));
            cdc_fifo #(.W(FB), .DEPTH(CDC_DEPTH)) u_txq (
              .wclk(aclk), .wrst_n(aresetn), .w_valid(tx_valid), .w_data(tx_flit), .w_ready(tx_ready),
              .rclk(clk), .rrst_n(rst_n), .r_valid(pk_valid), .r_data(pk_flit), .r_ready(pk_ready));
            // AXI side (aclk): the engine
            logic [39:0]         b_awaddr, b_araddr;
            logic [7:0]          b_awlen, b_arlen;
            logic [2:0]          b_awsize, b_arsize;
            logic [1:0]          b_awburst, b_arburst, b_bresp_s, b_rresp_s;
            logic                b_awvalid, b_wlast, b_wvalid, b_bready, b_arvalid, b_rready;
            logic [AXI_DW-1:0]   b_wdata, b_rdata_s;
            logic [AXI_DW/8-1:0] b_wstrb;
            logic                b_awready_s, b_wready_s, b_bvalid_s, b_arready_s, b_rlast_s, b_rvalid_s;
            logic [AXI_IDW-1:0]  b_bid_s, b_rid_s;
            logic [1:0]          b_bresp_m, b_rresp_m;
            logic                b_awready, b_wready, b_bvalid, b_arready, b_rlast, b_rvalid;
            logic [AXI_DW-1:0]   b_rdata;
            // the slave-side inputs of this row (the host role's port y, or idle)
            logic [AXI_IDW-1:0]  sa_awid, sa_arid;
            logic [HAW-1:0]      sa_awaddr, sa_araddr;
            logic [7:0]          sa_awlen, sa_arlen;
            logic [2:0]          sa_awsize, sa_arsize;
            logic [1:0]          sa_awburst, sa_arburst;
            logic                sa_awvalid, sa_wlast, sa_wvalid, sa_bready, sa_arvalid, sa_rready;
            logic [AXI_DW-1:0]   sa_wdata;
            logic [AXI_DW/8-1:0] sa_wstrb;
            noc_axi_bridge #(.XW(XW), .YW(YW), .NX(NX), .NY(NY), .BAW(BAW), .AXI_DW(AXI_DW), .AXI_IDW(AXI_IDW),
                             .MEM(MEMY), .HOST(HOSTY), .HOST_TIMEOUT(HOST_TIMEOUT)) u_br (
              .clk(aclk), .rst_n(aresetn), .my_x(XW'(NX)), .my_y(YW'(y)),
              .rx_valid(rx_valid), .rx_flit(rx_flit), .rx_ready(rx_ready), .tx_valid(tx_valid), .tx_flit(tx_flit), .tx_ready(tx_ready),
              .win_base(win_base[34*y +: 34]), .partition(br_part[4*y +: 4]),
              .ev_crc(ev_crc[y]), .ev_lost(ev_lost[y]), .ev_axi(ev_axi[y]), .ev_timeout(ev_timeout[y]), .ev_host(ev_host[y]),
              .m_awaddr(b_awaddr), .m_awlen(b_awlen), .m_awsize(b_awsize), .m_awburst(b_awburst), .m_awvalid(b_awvalid),
              .m_awready(b_awready), .m_wdata(b_wdata), .m_wstrb(b_wstrb), .m_wlast(b_wlast), .m_wvalid(b_wvalid),
              .m_wready(b_wready), .m_bresp(b_bresp_m), .m_bvalid(b_bvalid), .m_bready(b_bready),
              .m_araddr(b_araddr), .m_arlen(b_arlen), .m_arsize(b_arsize), .m_arburst(b_arburst), .m_arvalid(b_arvalid),
              .m_arready(b_arready), .m_rdata(b_rdata), .m_rresp(b_rresp_m), .m_rlast(b_rlast), .m_rvalid(b_rvalid),
              .m_rready(b_rready),
              .s_awid(sa_awid), .s_awaddr(sa_awaddr), .s_awlen(sa_awlen), .s_awsize(sa_awsize), .s_awburst(sa_awburst),
              .s_awvalid(sa_awvalid), .s_awready(b_awready_s), .s_wdata(sa_wdata), .s_wstrb(sa_wstrb), .s_wlast(sa_wlast),
              .s_wvalid(sa_wvalid), .s_wready(b_wready_s), .s_bid(b_bid_s), .s_bresp(b_bresp_s), .s_bvalid(b_bvalid_s),
              .s_bready(sa_bready), .s_arid(sa_arid), .s_araddr(sa_araddr), .s_arlen(sa_arlen), .s_arsize(sa_arsize),
              .s_arburst(sa_arburst), .s_arvalid(sa_arvalid), .s_arready(b_arready_s), .s_rid(b_rid_s), .s_rdata(b_rdata_s),
              .s_rresp(b_rresp_s), .s_rlast(b_rlast_s), .s_rvalid(b_rvalid_s), .s_rready(sa_rready));
            // the memory role's port
            if (MEMY != 0) begin : g_mport
              assign m_axi_awaddr[40*y +: 40]          = b_awaddr;
              assign m_axi_awlen[8*y +: 8]             = b_awlen;
              assign m_axi_awsize[3*y +: 3]            = b_awsize;
              assign m_axi_awburst[2*y +: 2]           = b_awburst;
              assign m_axi_awvalid[y]                  = b_awvalid;
              assign b_awready                         = m_axi_awready[y];
              assign m_axi_wdata[AXI_DW*y +: AXI_DW]   = b_wdata;
              assign m_axi_wstrb[AXI_DW/8*y +: AXI_DW/8] = b_wstrb;
              assign m_axi_wlast[y]                    = b_wlast;
              assign m_axi_wvalid[y]                   = b_wvalid;
              assign b_wready                          = m_axi_wready[y];
              assign b_bresp_m                         = m_axi_bresp[2*y +: 2];
              assign b_bvalid                          = m_axi_bvalid[y];
              assign m_axi_bready[y]                   = b_bready;
              assign m_axi_araddr[40*y +: 40]          = b_araddr;
              assign m_axi_arlen[8*y +: 8]             = b_arlen;
              assign m_axi_arsize[3*y +: 3]            = b_arsize;
              assign m_axi_arburst[2*y +: 2]           = b_arburst;
              assign m_axi_arvalid[y]                  = b_arvalid;
              assign b_arready                         = m_axi_arready[y];
              assign b_rdata                           = m_axi_rdata[AXI_DW*y +: AXI_DW];
              assign b_rresp_m                         = m_axi_rresp[2*y +: 2];
              assign b_rlast                           = m_axi_rlast[y];
              assign b_rvalid                          = m_axi_rvalid[y];
              assign m_axi_rready[y]                   = b_rready;
            end else begin : g_nomport
              assign b_awready = 1'b0; assign b_wready = 1'b0; assign b_bresp_m = '0; assign b_bvalid = 1'b0;
              assign b_arready = 1'b0; assign b_rdata = '0; assign b_rresp_m = '0; assign b_rlast = 1'b0; assign b_rvalid = 1'b0;
              logic unused_mport;
              assign unused_mport = ^{b_awaddr, b_awlen, b_awsize, b_awburst, b_awvalid, b_wdata, b_wstrb, b_wlast, b_wvalid, b_bready,
                                      b_araddr, b_arlen, b_arsize, b_arburst, b_arvalid, b_rready};
            end
            // the host role's port
            if (HOSTY != 0) begin : g_sport
              assign sa_awid    = s_axi_awid[AXI_IDW*y +: AXI_IDW];
              assign sa_awaddr  = s_axi_awaddr[HAW*y +: HAW];
              assign sa_awlen   = s_axi_awlen[8*y +: 8];
              assign sa_awsize  = s_axi_awsize[3*y +: 3];
              assign sa_awburst = s_axi_awburst[2*y +: 2];
              assign sa_awvalid = s_axi_awvalid[y];
              assign s_axi_awready[y] = b_awready_s;
              assign sa_wdata   = s_axi_wdata[AXI_DW*y +: AXI_DW];
              assign sa_wstrb   = s_axi_wstrb[AXI_DW/8*y +: AXI_DW/8];
              assign sa_wlast   = s_axi_wlast[y];
              assign sa_wvalid  = s_axi_wvalid[y];
              assign s_axi_wready[y] = b_wready_s;
              assign s_axi_bid[AXI_IDW*y +: AXI_IDW] = b_bid_s;
              assign s_axi_bresp[2*y +: 2] = b_bresp_s;
              assign s_axi_bvalid[y] = b_bvalid_s;
              assign sa_bready  = s_axi_bready[y];
              assign sa_arid    = s_axi_arid[AXI_IDW*y +: AXI_IDW];
              assign sa_araddr  = s_axi_araddr[HAW*y +: HAW];
              assign sa_arlen   = s_axi_arlen[8*y +: 8];
              assign sa_arsize  = s_axi_arsize[3*y +: 3];
              assign sa_arburst = s_axi_arburst[2*y +: 2];
              assign sa_arvalid = s_axi_arvalid[y];
              assign s_axi_arready[y] = b_arready_s;
              assign s_axi_rid[AXI_IDW*y +: AXI_IDW] = b_rid_s;
              assign s_axi_rdata[AXI_DW*y +: AXI_DW] = b_rdata_s;
              assign s_axi_rresp[2*y +: 2] = b_rresp_s;
              assign s_axi_rlast[y]  = b_rlast_s;
              assign s_axi_rvalid[y] = b_rvalid_s;
              assign sa_rready  = s_axi_rready[y];
            end else begin : g_nosport
              assign sa_awid = '0; assign sa_awaddr = '0; assign sa_awlen = '0; assign sa_awsize = '0; assign sa_awburst = '0;
              assign sa_awvalid = 1'b0; assign sa_wdata = '0; assign sa_wstrb = '0; assign sa_wlast = 1'b0; assign sa_wvalid = 1'b0;
              assign sa_bready = 1'b0; assign sa_arid = '0; assign sa_araddr = '0; assign sa_arlen = '0; assign sa_arsize = '0;
              assign sa_arburst = '0; assign sa_arvalid = 1'b0; assign sa_rready = 1'b0;
              logic unused_sport;
              assign unused_sport = ^{b_awready_s, b_wready_s, b_bid_s, b_bresp_s, b_bvalid_s, b_arready_s, b_rid_s, b_rdata_s,
                                      b_rresp_s, b_rlast_s, b_rvalid_s};
            end
          end else begin : g_sink
            // no bridge role in this row: the local port consumes and drops (a request times out at the requester)
            assign r_in_valid[4] = 1'b0;
            assign r_in_flit[4]  = '0;
            assign r_out_ready[4] = 1'b1;
            assign ev_crc[y] = 1'b0; assign ev_lost[y] = 1'b0; assign ev_axi[y] = 1'b0; assign ev_timeout[y] = 1'b0;
            assign ev_host[y] = 1'b0;
            logic unused_sink;
            assign unused_sink = ^{r_in_ready[4], r_out_valid[4], r_out_flit[4], win_base[34*y +: 34], br_part[4*y +: 4]};
          end
        end
      end
    end
    // a role with no bridge at all: its port bundle tied off
    if (N_MEM_BRIDGES == 0) begin : g_no_mem
      assign m_axi_awaddr = '0; assign m_axi_awlen = '0; assign m_axi_awsize = '0; assign m_axi_awburst = '0;
      assign m_axi_awvalid = '0; assign m_axi_wdata = '0; assign m_axi_wstrb = '0; assign m_axi_wlast = '0; assign m_axi_wvalid = '0;
      assign m_axi_bready = '0; assign m_axi_araddr = '0; assign m_axi_arlen = '0; assign m_axi_arsize = '0; assign m_axi_arburst = '0;
      assign m_axi_arvalid = '0; assign m_axi_rready = '0;
      logic unused_no_mem;
      assign unused_no_mem = ^{m_axi_awready, m_axi_wready, m_axi_bresp, m_axi_bvalid, m_axi_arready, m_axi_rdata, m_axi_rresp,
                               m_axi_rlast, m_axi_rvalid};
    end
    if (N_HOST_BRIDGES == 0) begin : g_no_host
      assign s_axi_awready = '0; assign s_axi_wready = '0; assign s_axi_bid = '0; assign s_axi_bresp = '0; assign s_axi_bvalid = '0;
      assign s_axi_arready = '0; assign s_axi_rid = '0; assign s_axi_rdata = '0; assign s_axi_rresp = '0; assign s_axi_rlast = '0;
      assign s_axi_rvalid = '0;
      logic unused_no_host;
      assign unused_no_host = ^{s_axi_awid, s_axi_awaddr, s_axi_awlen, s_axi_awsize, s_axi_awburst, s_axi_awvalid, s_axi_wdata,
                                s_axi_wstrb, s_axi_wlast, s_axi_wvalid, s_axi_bready, s_axi_arid, s_axi_araddr, s_axi_arlen,
                                s_axi_arsize, s_axi_arburst, s_axi_arvalid, s_axi_rready};
    end
  endgenerate

  // ------------------------------------------------------------------ the control plane
  logic             rqa_req, rqa_busy, rqa_done, rqb_req, rqb_done;
  logic [RQW-1:0]   rqa_data, rqb_data;
  logic [32:0]      rqa_rsp, rqb_rsp;
  logic [NT-1:0]    tile_en_a, en_req_c, st_done_c, st_err_c, st_done_a, st_err_a, st_en_a;
  logic [NY-1:0]    st_par_a;
  top_ctrl #(.NX(NX), .NY(NY), .ROWS(ROWS), .COLS(COLS), .BAW(BAW), .WPF(WPF), .AXI_DW(AXI_DW), .N_MEM(N_MEM_BRIDGES),
             .N_HOST(N_HOST_BRIDGES), .CTRL_AW(CTRL_AW)) u_ctrl (
    .clk(aclk), .rst_n(aresetn),
    .s_awaddr(s_axil_awaddr), .s_awvalid(s_axil_awvalid), .s_awready(s_axil_awready), .s_wdata(s_axil_wdata), .s_wstrb(s_axil_wstrb),
    .s_wvalid(s_axil_wvalid), .s_wready(s_axil_wready), .s_bresp(s_axil_bresp), .s_bvalid(s_axil_bvalid), .s_bready(s_axil_bready),
    .s_araddr(s_axil_araddr), .s_arvalid(s_axil_arvalid), .s_arready(s_axil_arready), .s_rdata(s_axil_rdata), .s_rresp(s_axil_rresp),
    .s_rvalid(s_axil_rvalid), .s_rready(s_axil_rready),
    .rq_req(rqa_req), .rq_data(rqa_data), .rq_busy(rqa_busy), .rq_done(rqa_done), .rq_rsp(rqa_rsp),
    .st_done(st_done_a), .st_err(st_err_a), .st_en(st_en_a), .st_parity(st_par_a), .tile_en(tile_en_a),
    .win_base(win_base), .br_part(br_part), .ev_crc(ev_crc), .ev_lost(ev_lost), .ev_axi(ev_axi), .ev_timeout(ev_timeout),
    .ev_host(ev_host), .irq(irq), .err(err));
  // crossing (2): the register bus
  cdc_sync #(.RW(RQW), .SW(33)) u_rq (
    .a_clk(aclk), .a_rst_n(aresetn), .a_req(rqa_req), .a_req_data(rqa_data), .a_busy(rqa_busy), .a_done(rqa_done), .a_rsp_data(rqa_rsp),
    .b_clk(clk), .b_rst_n(rst_n), .b_req(rqb_req), .b_req_data(rqb_data), .b_done(rqb_done), .b_rsp_data(rqb_rsp));
  mesh_ctrl #(.NX(NX), .NY(NY)) u_mctrl (
    .clk(clk), .rst_n(rst_n), .rq_req(rqb_req), .rq_data(rqb_data), .rq_done(rqb_done), .rq_rsp(rqb_rsp),
    .h_we(h_we), .h_re(h_re), .h_addr(h_addr), .h_wdata(h_wdata), .h_rdata(h_rdata), .rtr_clear(rtr_clear), .rtr_parity(rtr_parity),
    .prog_done(t_prog_done), .drain_busy(t_drain_busy), .err_pin(t_err_pin), .en_req(en_req_c), .en_eff(en_eff),
    .st_done(st_done_c), .st_err(st_err_c));
  // crossing (3): levels -- the enable request into clk (no reset: it holds the request through the mesh reset), the
  // status into aclk
  cdc_bits #(.W(NT), .RESET(0)) u_en_sync   (.clk(clk),  .rst_n(rst_n),   .d(tile_en_a),  .q(en_req_c));
  cdc_bits #(.W(NT))            u_done_sync (.clk(aclk), .rst_n(aresetn), .d(st_done_c),  .q(st_done_a));
  cdc_bits #(.W(NT))            u_err_sync  (.clk(aclk), .rst_n(aresetn), .d(st_err_c),   .q(st_err_a));
  cdc_bits #(.W(NT))            u_eff_sync  (.clk(aclk), .rst_n(aresetn), .d(en_eff),     .q(st_en_a));
  cdc_bits #(.W(NY))            u_par_sync  (.clk(aclk), .rst_n(aresetn), .d(rtr_parity), .q(st_par_a));
endmodule
