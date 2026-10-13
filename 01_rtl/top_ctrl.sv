// top_ctrl.sv -- the chip top's control plane, AXI side (drop 0.42): one AXI4-Lite slave (32-bit data, CTRL_AW-bit byte
//   addresses) in front of every tile's host register bus and the global registers. Runs in the AXI clock -- the port called
//   clk here is neo_top's aclk; the tiles' buses are in the mesh clock behind cdc_sync (mesh_ctrl.sv), the status levels come
//   in through cdc_bits, so this module itself is single-clock.
//   Address map (byte addresses; the integration note and 04_software/neo_host.h carry the same):
//     0x0_0000 + 1 KB * n   tile n = y * NX + x (n < NX * NY <= 64): its host_if registers, word address = byte offset / 4,
//                           through the register-bus handshake (one access in flight, the decode registered); a disabled
//                           tile (the effective enable map, decided on the mesh side) answers SLVERR with no bus cycle
//     0x1_0000 + 1 KB * b   bridge node b (row b, b < NY): its router's registers on the mesh side, host_if's positions --
//                           0x00 CTRL (w: bit 1 clears the parity flag), 0x01 STATUS (r: bit 8 parity), the rest reads 0
//     0x2_0000 + ...        global registers (here, no handshake):
//       0x000 ID 'NEOT'  0x004 VERSION 0x00000045 (drop 0.43)  0x008 CONFIG {COLS, ROWS, N_HOST, N_MEM, NY, NX}  0x00C CONFIG2 {BAW, WPF,
//       AXI_DW}  0x010 SCRATCH r/w  0x018 TIMER_LO (r; latches the high word)  0x01C TIMER_HI (r; the latched word)
//       0x020 / 0x024 TILE_EN lo / hi (r/w; reset all ones; bit n = tile n)  0x028 / 0x02C TILE_EN_EFF lo / hi (r: the map in
//       effect, from the mesh side)  0x030 / 0x034 DONE lo / hi (r: prog_done && !drain_busy, enabled tiles)  0x038 / 0x03C
//       ERR lo / hi (r: err_pin, enabled tiles)  0x040 IRQ_STATUS (r; write 1 clears): bit 0 done (a tile's done rose), bit 1
//       err (a tile's err_pin rose), bit 2 bridge (a bridge error event, or a bridge router's parity flag rose), bit 3
//       lockstep (a pair's mismatch rose; drop 0.43), bit 4 command (the command queue wrote a completion record; drop 0.43)
//       0x044 IRQ_MASK (r/w, 1 masks; reset 0x1F)  0x048 STATUS (r: bit 0 irq, bit 1 err)
//       0x04C PRIO (r/w, drop 0.43): [15:0] the bridge column routers' priority map, bit p = partition p is the high class
//       (noc_router.sv; a tile's router has its own in its host_if, 0x0C -- software writes the same map into every one)
//       0x050 + 4 k LS_PAIR k (r/w, k < NLS, drop 0.43): bit 31 enable, [13:8] tile b, [5:0] tile a -- the lockstep pair
//       compared on the mesh side (mesh_ctrl.sv); configure it while both tiles are idle, then enable
//       0x070 LS_ERR (r): bit k = pair k mismatched (sticky on the mesh side; it clears when the pair's enable is cleared)
//       0x080 + 4 j PWR_STATE j (r, j < 8, drop 0.43): tile 8 j + i in bits [4 i +: 3] -- its power sequencer's state:
//       0 ON, 1 ISO, 2 CLKOFF, 3 RST, 4 OFF, 5 PWRON, 6 RSTREL, 7 CLKON (mesh_ctrl.sv; crossed gray-coded)
//       0x0A0 CQ_CTRL (r/w, drop 0.43): bit 0 enable; a write with bit 1 resets the queue (CQ_HEAD, CC_TAIL and the
//       sticky error to 0, a command in progress dropped)  0x0A4 CQ_RING (r/w): [9:0] the command ring's word address in the
//       command memory, [19:16] log2 of its entries (0 .. 8)  0x0A8 CQ_TAIL (r/w): the doorbell, the index past the last
//       command written  0x0AC CQ_HEAD (r): the index of the next command to play  0x0B0 CC_RING (r/w): the completion
//       ring, as CQ_RING  0x0B4 CC_HEAD (r/w): the index past the last record the host has taken  0x0B8 CC_TAIL (r): the
//       index past the last record written  0x0BC CQ_STATUS (r): bit 0 a command in progress, [7:4] the player's state,
//       bit 8 a command since the queue's reset saw a refused write or a tile's err_pin
//       0x0C0 TEL_CTRL (w, drop 0.43): bit 0 snapshot, bit 1 clear -- the bridges' wait counters and the window's aclk
//       cycle count (as a tile's TEL_CTRL)  0x0C4 TEL_CYCLES (r): the snapshot's cycles
//       0x100 + 0x20 * b     bridge b: +0x0 WIN_LO (bits 31:6 of the window base; 5:0 read 0) +0x4 WIN_HI (bits 39:32 in 7:0)
//                            +0x8 CTRL (bits 3:0: the partition of the host role's requests) +0xC ERR (r, write 1 clears
//                            bits 4:0): 0 crc, 1 lost, 2 axi, 3 timeout, 4 host, 5 the router's parity flag (a level; clear
//                            it through the bridge node's CTRL); drop 0.43: +0x10 RD_WAIT, +0x14 WR_WAIT (r: snapshots of the
//                            cycles an AXI read / write of the memory role waited -- a burst outstanding, no data beat; the
//                            bridge-side telemetry), +0x18 STATUS (r: bit 0 writes buffered or awaiting B)
//     0x2_1000 + 4 * w      the command memory (drop 0.43): 1,024 words, r/w -- the rings and the register images
//     anything else: DECERR. Writes take the strobed bytes and zeros elsewhere (host_axil's rule); one transaction at a
//     time, writes before reads.
//   Command queue (drop 0.43): commands in a ring of the command memory, four words each -- the tile mask (lo, hi), the
//   image {entries [31:16], word address [9:0]}, a tag -- played in order while CQ_CTRL.enable, CQ_HEAD != CQ_TAIL and the
//   completion ring has room (a ring holds 2^n - 1; image word bit 15: complete without waiting for done -- a command
//   that only configures). The player reads the image -- entries of two words, {single [14],
//   tile [13:8], register [7:0]} and the value -- and writes each value into that register of every tile of the mask (tile
//   [13:8] alone when single) through the register-bus handshake the host's window accesses use (one access in flight; a
//   host access waiting is served first); the image's last entries are the CTRL starts. After the last entry it waits for
//   every tile of the mask to be done (DONE, which mesh_ctrl drops from the start's bus cycle; a tile that is not on
//   counts as done, its writes refused) or one to raise err_pin, and for no memory bridge to hold unanswered writes (the
//   drains are in memory), then writes the completion record {tag, {entries played [31:16], error [0]}, TIMER lo, TIMER hi}
//   at CC_TAIL, advances CC_TAIL and CQ_HEAD and raises IRQ_STATUS bit 4: an interrupt per completion. The images are in
//   the command memory, not in a bank as the brief placed them: the control plane has no mesh port to read a bank with (the
//   host bridge's slave is the bank reader), and the command memory is where a licensee's host or DMA puts them instead.
//   Tile-enable rule (the power map since drop 0.43): TILE_EN is a request; the mesh side applies a bit while the mesh is in
//   reset or while that tile is idle (not started since reset, or its program has ended) and holds it pending otherwise; a
//   cleared bit powers the tile down (isolation, clock off, reset, power off), a set bit powers it up (power on, reset
//   released, clock on, isolation off) -- PWR_STATE shows each tile's sequencer, TILE_EN_EFF the tiles that are on. A tile
//   that is not on is fenced (mesh_ctrl.sv / neo_top.sv); nothing of it is retained.
//   irq = any unmasked IRQ_STATUS bit; err (to the safety island) = any enabled tile's err_pin, any bridge error bit, any
//   bridge router's parity flag, any lockstep pair's mismatch -- a level, registered, not maskable.
module top_ctrl #(
  parameter int NX = 8, NY = 8,
  parameter int ROWS = 32, COLS = 32,
  parameter int BAW = 19, WPF = 32, AXI_DW = 512,
  parameter int N_MEM = 8, N_HOST = 1,
  parameter int CTRL_AW = 18,                // at least 18: three 64 KB regions
  parameter int NLS = 4,                     // drop 0.43: lockstep pairs, 1 .. 8
  parameter int NT  = NX * NY,
  parameter int NTW = (NT > 1) ? $clog2(NT) : 1,
  parameter int LSW = 1 + 2 * NTW,           // a pair's configuration {enable, tile a, tile b}
  parameter int TGW = $clog2(NT + NY),       // a register-bus target: tile n, or bridge node NT + b
  parameter int RQW = 1 + TGW + 8 + 32       // the request: {we, target, word address, data}
)(
  input  logic                 clk,          // the AXI clock (aclk)
  input  logic                 rst_n,        // the AXI reset (aresetn)
  // AXI4-Lite slave
  input  logic [CTRL_AW-1:0]   s_awaddr,
  input  logic                 s_awvalid,
  output logic                 s_awready,
  input  logic [31:0]          s_wdata,
  input  logic [3:0]           s_wstrb,
  input  logic                 s_wvalid,
  output logic                 s_wready,
  output logic [1:0]           s_bresp,
  output logic                 s_bvalid,
  input  logic                 s_bready,
  input  logic [CTRL_AW-1:0]   s_araddr,
  input  logic                 s_arvalid,
  output logic                 s_arready,
  output logic [31:0]          s_rdata,
  output logic [1:0]           s_rresp,
  output logic                 s_rvalid,
  input  logic                 s_rready,
  // register-bus accesses into the mesh clock (cdc_sync side A)
  output logic                 rq_req,
  output logic [RQW-1:0]       rq_data,
  input  logic                 rq_busy,
  input  logic                 rq_done,
  input  logic [32:0]          rq_rsp,       // {err, rdata}
  // status levels from the mesh clock, synchronized (cdc_bits)
  input  logic [NT-1:0]        st_done,
  input  logic [NT-1:0]        st_err,
  input  logic [NT-1:0]        st_en,
  input  logic [NY-1:0]        st_parity,
  // the tile-enable request (into the mesh clock through cdc_bits)
  output logic [NT-1:0]        tile_en,
  // the bridge column routers' priority map (into the mesh clock through cdc_bits; drop 0.43)
  output logic [15:0]          prio,
  // drop 0.43: the lockstep pairs (into the mesh clock, static), their errors and the power states (from it, synchronized)
  output logic [NLS*LSW-1:0]   ls_cfg,
  input  logic [NLS-1:0]       st_ls,
  input  logic [3*NT-1:0]      st_pwr,
  // bridges (row b at [b*W +: W])
  output logic [NY*34-1:0]     win_base,     // bits 39:6 of each window base
  output logic [NY*4-1:0]      br_part,
  input  logic [NY-1:0]        ev_crc,
  input  logic [NY-1:0]        ev_lost,
  input  logic [NY-1:0]        ev_axi,
  input  logic [NY-1:0]        ev_timeout,
  input  logic [NY-1:0]        ev_host,
  input  logic [NY-1:0]        ev_rd_wait,   // drop 0.43: the memory roles' AXI-side wait cycles (telemetry)
  input  logic [NY-1:0]        ev_wr_wait,
  input  logic [NY-1:0]        br_busy,      // drop 0.43: a memory role holds writes not yet answered
  output logic                 irq,
  output logic                 err
);
  localparam logic [1:0] R_OKAY = 2'b00, R_SLVERR = 2'b10, R_DECERR = 2'b11;
  localparam logic [2:0] A_IDLE = 3'd0, A_DEC = 3'd1, A_WAIT = 3'd2, A_RSP = 3'd3;
  localparam int NB = NY;                    // bridge rows
  localparam int NBW = (NB > 1) ? $clog2(NB) : 1;
  localparam int RGW = CTRL_AW - 16;         // the region bits above the 64 KB regions

  // ---- registers ----
  logic [2:0]          a_state;
  logic                a_we;
  logic [CTRL_AW-1:0]  a_addr;
  logic [31:0]         a_wd;                 // the write data, strobed bytes or zero
  logic [1:0]          a_resp;
  logic [31:0]         a_rdata;
  logic [31:0]         scratch;
  logic [63:0]         timer;
  logic [31:0]         timer_hi;
  logic [NT-1:0]       en_q;
  logic [4:0]          irq_status, irq_mask;
  logic [15:0]         prio_q;
  logic [LSW-1:0]      ls_q [NLS];           // drop 0.43: the lockstep pairs
  logic [NLS-1:0]      ls_q1;                // their errors a cycle ago (edges)
  logic                cq_irq;               // drop 0.43: the command queue wrote a completion record (one cycle)
  // drop 0.43: the command queue's registers and the bridges' telemetry snapshots (below), read by the global decode
  localparam logic [3:0] P_IDLE = 4'd0, P_CMD = 4'd1, P_ENT = 4'd2, P_VAL = 4'd3, P_TGT = 4'd4, P_REQ = 4'd5, P_RSP = 4'd6,
                         P_GUARD = 4'd7, P_WAIT = 4'd8, P_REC = 4'd9;
  logic [3:0]          p_state;
  logic                cq_en, cq_err;
  logic [9:0]          cq_base, cc_base;
  logic [3:0]          cq_lg, cc_lg;
  logic [7:0]          cq_tail, cq_head, cc_head, cc_tail;   // ring indices, each below its ring's size
  logic [31:0]         ts_cyc;
  logic [31:0]         ts_rd [NB], ts_wr [NB];
  logic [33:0]         wb_q [NB];            // window bases, bits 39:6
  logic [3:0]          part_q [NB];
  logic [4:0]          br_err [NB];          // sticky: crc, lost, axi, timeout, host
  logic [NT-1:0]       done_q, errp_q;       // the levels a cycle ago (edges)
  logic [NY-1:0]       par_q;

  // ---- AXI4-Lite handshakes: accept one transaction, decode it from the registered address ----
  assign s_awready = (a_state == A_IDLE) && s_awvalid && s_wvalid;
  assign s_wready  = s_awready;
  assign s_arready = (a_state == A_IDLE) && !(s_awvalid && s_wvalid);
  assign s_bvalid  = (a_state == A_RSP) && a_we;
  assign s_bresp   = a_resp;
  assign s_rvalid  = (a_state == A_RSP) && !a_we;
  assign s_rresp   = a_resp;
  assign s_rdata   = a_rdata;

  // the decode: region, tile / bridge node, register offsets
  logic [1:0]      d_region;                 // 0 tile windows, 1 bridge-node windows, 2 globals, 3 nothing
  logic [5:0]      d_win;                    // the window index (a[15:10])
  logic [7:0]      d_reg;                    // the word address inside a window (a[9:2])
  logic [9:0]      d_g;                      // the word index of a global register (a[11:2])
  logic            d_tile_ok, d_node_ok, d_glob_ok, d_cmem_ok;
  logic [TGW-1:0]  d_target;
  assign d_region  = (a_addr[CTRL_AW-1:16] == RGW'(0)) ? 2'd0 : (a_addr[CTRL_AW-1:16] == RGW'(1)) ? 2'd1 :
                     (a_addr[CTRL_AW-1:16] == RGW'(2)) ? 2'd2 : 2'd3;
  assign d_win     = a_addr[15:10];
  assign d_reg     = a_addr[9:2];
  assign d_g       = a_addr[11:2];
  assign d_tile_ok = (d_region == 2'd0) && (32'(d_win) < NT);
  assign d_node_ok = (d_region == 2'd1) && (32'(d_win) < NB);
  assign d_glob_ok = (d_region == 2'd2) && (a_addr[15:12] == 4'd0);
  assign d_cmem_ok = (d_region == 2'd2) && (a_addr[15:12] == 4'd1);     // drop 0.43: the command memory
  assign d_target  = d_tile_ok ? TGW'(d_win) : TGW'(32'(d_win) + NT);
  // the register bus: the host's access (A_DEC, first) or the command queue's (cq_issue) -- never both in a cycle, one in flight
  logic            axi_bus, cq_issue;
  logic [RQW-1:0]  cq_data;
  assign axi_bus   = (a_state == A_DEC) && (d_tile_ok || d_node_ok);
  assign rq_req    = (axi_bus && !rq_busy) || cq_issue;
  assign rq_data   = cq_issue ? cq_data : {a_we, d_target, d_reg, a_wd};

  // the global registers: read value and validity of the word d_g
  logic [63:0] en64, eff64, done64, err64;
  assign en64   = 64'(en_q);
  assign eff64  = 64'(st_en);
  assign done64 = 64'(st_done);
  assign err64  = 64'(st_err);
  logic        g_hit;                        // d_g names a register
  logic [31:0] g_rd;
  // drop 0.43: the power states as 4-bit fields, gray to the state index (the gray-code cycle is the reflected binary code)
  logic [255:0] pwr256;
  always_comb begin
    pwr256 = '0;
    for (int n = 0; n < NT; n++)
      pwr256[4 * n +: 4] = {1'b0, st_pwr[3 * n + 2], st_pwr[3 * n + 2] ^ st_pwr[3 * n + 1],
                            st_pwr[3 * n + 2] ^ st_pwr[3 * n + 1] ^ st_pwr[3 * n]};
  end
  localparam int LKW = (NLS > 1) ? $clog2(NLS) : 1;
  logic [LKW-1:0] g_lsk;                     // the pair of an LS_PAIR access (d_g - 0x14)
  logic           g_is_ls;
  assign g_lsk   = LKW'(d_g - 10'h014);
  assign g_is_ls = (d_g >= 10'h014) && (32'(d_g) < 32'h14 + NLS);
  logic [4:0]  g_b;                          // the bridge of a bridge register (d_g[7:3] - 8)
  logic        g_is_br;
  logic [NBW-1:0] g_bi;                      // ... as an index (valid when g_is_br)
  assign g_b     = d_g[7:3] - 5'd8;
  assign g_bi    = NBW'(g_b);
  assign g_is_br = (d_g[9:8] == 2'd0) && (d_g[7:6] != 2'd0) && (32'(g_b) < NB);    // byte offsets 0x100 .. 0x100 + 0x20 * NB
  always_comb begin
    g_hit = 1'b1; g_rd = '0;
    if (g_is_br) begin
      case (d_g[2:0])
        3'd0: g_rd = {wb_q[g_bi][25:0], 6'd0};
        3'd1: g_rd = {24'd0, wb_q[g_bi][33:26]};
        3'd2: g_rd = {28'd0, part_q[g_bi]};
        3'd3: g_rd = {26'd0, st_parity[g_bi], br_err[g_bi]};
        3'd4: g_rd = ts_rd[g_bi];
        3'd5: g_rd = ts_wr[g_bi];
        3'd6: g_rd = {31'd0, br_busy[g_bi]};
        default: g_hit = 1'b0;
      endcase
    end else begin
      case (d_g)
        10'h000: g_rd = 32'h4E45_4F54;                                    // 'NEOT'
        10'h001: g_rd = 32'h0000_0045;                                    // drop 0.43
        10'h002: g_rd = {8'(COLS), 8'(ROWS), 4'(N_HOST), 4'(N_MEM), 4'(NY), 4'(NX)};
        10'h003: g_rd = {8'(BAW), 8'(WPF), 16'(AXI_DW)};
        10'h004: g_rd = scratch;
        10'h006: g_rd = timer[31:0];
        10'h007: g_rd = timer_hi;
        10'h008: g_rd = en64[31:0];
        10'h009: g_rd = en64[63:32];
        10'h00A: g_rd = eff64[31:0];
        10'h00B: g_rd = eff64[63:32];
        10'h00C: g_rd = done64[31:0];
        10'h00D: g_rd = done64[63:32];
        10'h00E: g_rd = err64[31:0];
        10'h00F: g_rd = err64[63:32];
        10'h010: g_rd = {27'd0, irq_status};
        10'h011: g_rd = {27'd0, irq_mask};
        10'h012: g_rd = {30'd0, err, irq};
        10'h013: g_rd = {16'd0, prio_q};
        10'h01C: g_rd = 32'(st_ls);
        10'h020, 10'h021, 10'h022, 10'h023, 10'h024, 10'h025, 10'h026, 10'h027: g_rd = pwr256[32 * d_g[2:0] +: 32];
        10'h028: g_rd = {31'd0, cq_en};
        10'h029: g_rd = {12'd0, cq_lg, 6'd0, cq_base};
        10'h02A: g_rd = {24'd0, cq_tail};
        10'h02B: g_rd = {24'd0, cq_head};
        10'h02C: g_rd = {12'd0, cc_lg, 6'd0, cc_base};
        10'h02D: g_rd = {24'd0, cc_head};
        10'h02E: g_rd = {24'd0, cc_tail};
        10'h02F: g_rd = {23'd0, cq_err, p_state, 3'd0, (p_state != P_IDLE)};
        10'h030: g_rd = 32'd0;
        10'h031: g_rd = ts_cyc;
        default: g_hit = 1'b0;
      endcase
      if (g_is_ls) begin
        g_hit = 1'b1;
        g_rd  = {ls_q[g_lsk][LSW-1], 17'd0, 6'(ls_q[g_lsk][NTW-1:0]), 2'd0, 6'(ls_q[g_lsk][2*NTW-1:NTW])};   // {en, b, a}
      end
    end
  end
  logic g_do;                                // a global register access is performed this cycle
  assign g_do = (a_state == A_DEC) && d_glob_ok;

  // ------------------------------------------------------------------ the bridges' telemetry (drop 0.43)
  logic        tel_snap, tel_clr;
  logic [31:0] tc_cyc;
  logic [31:0] tc_rd [NB], tc_wr [NB];
  assign tel_snap = g_do && a_we && !g_is_br && (d_g == 10'h030) && a_wd[0];
  assign tel_clr  = g_do && a_we && !g_is_br && (d_g == 10'h030) && a_wd[1];
  function automatic logic [31:0] sat_inc(input logic [31:0] c, input logic e);
    return (e && (c != 32'hFFFF_FFFF)) ? c + 32'd1 : c;
  endfunction
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      tc_cyc <= '0; ts_cyc <= '0;
      for (int b = 0; b < NB; b++) begin tc_rd[b] <= '0; tc_wr[b] <= '0; ts_rd[b] <= '0; ts_wr[b] <= '0; end
    end else begin
      if (tel_snap) begin
        ts_cyc <= tc_cyc;
        for (int b = 0; b < NB; b++) begin ts_rd[b] <= tc_rd[b]; ts_wr[b] <= tc_wr[b]; end
      end
      if (tel_clr) begin
        tc_cyc <= '0;
        for (int b = 0; b < NB; b++) begin tc_rd[b] <= '0; tc_wr[b] <= '0; end
      end else begin
        tc_cyc <= sat_inc(tc_cyc, 1'b1);
        for (int b = 0; b < NB; b++) begin tc_rd[b] <= sat_inc(tc_rd[b], ev_rd_wait[b]); tc_wr[b] <= sat_inc(tc_wr[b], ev_wr_wait[b]); end
      end
    end
  end

  // ------------------------------------------------------------------ the command queue (drop 0.43)
  logic        cq_rst;
  logic [7:0]  cq_msk, cc_msk, cq_head_nx, cc_tail_nx;
  logic [1:0]  p_k;                                  // the word of the command being read / of the record being written
  logic [63:0] p_mask;                               // the command's tiles
  logic [9:0]  p_img;                                // its image's next word
  logic [15:0] p_left, p_nent;                       // entries left / played
  logic [31:0] p_tag, p_val;
  logic [7:0]  p_reg;
  logic        p_single, p_err, p_nowait;
  logic [6:0]  p_from;                               // the mask scan's start (64: past the last tile)
  logic [5:0]  p_tile;                               // the write's tile
  logic [1:0]  p_guard;
  logic [63:0] p_tmr;
  assign cq_msk     = 8'((9'd1 << cq_lg) - 9'd1);
  assign cc_msk     = 8'((9'd1 << cc_lg) - 9'd1);
  assign cq_head_nx = (cq_head + 8'd1) & cq_msk;
  assign cc_tail_nx = (cc_tail + 8'd1) & cc_msk;
  assign cq_rst     = g_do && a_we && !g_is_br && (d_g == 10'h028) && a_wd[1];
  // the command memory: the host's access in A_DEC first, the player's otherwise
  logic        axi_cm, p_mem;
  logic [9:0]  cm_raddr, cm_waddr, p_raddr, p_waddr;
  logic        cm_we, p_we;
  logic [31:0] cm_wdata, cm_rdata, p_wdata;
  assign axi_cm   = (a_state == A_DEC) && d_cmem_ok;
  assign p_mem    = !axi_cm;
  assign cm_raddr = axi_cm ? a_addr[11:2] : p_raddr;
  assign cm_we    = (axi_cm && a_we) || (p_we && p_mem);
  assign cm_waddr = axi_cm ? a_addr[11:2] : p_waddr;
  assign cm_wdata = axi_cm ? a_wd : p_wdata;
  logic [31:0] unused_cm_rb, unused_cm_bd;           // the leaf's unused read ports (RB = 0, no hooks)
  neo_mem_rf #(.W(32), .D(1024), .RB(0), .DV_HOOKS(0)) u_cmem (
    .clk(clk), .we(cm_we), .waddr(cm_waddr), .wdata(cm_wdata), .ra_addr(cm_raddr), .ra_data(cm_rdata),
    .rb_en(1'b0), .rb_addr('0), .rb_data(unused_cm_rb),
    .bd_we(1'b0), .bd_addr('0), .bd_wdata('0), .bd_rdata(unused_cm_bd), .fi_en(1'b0), .fi_or(1'b0), .fi_addr('0), .fi_mask('0));
  // the player's addresses: command word p_k at the head, image word p_img, record word p_k at the tail
  assign p_raddr  = (p_state == P_CMD) ? cq_base + {cq_head, p_k} : p_img;
  assign p_waddr  = cc_base + {cc_tail, p_k};
  assign p_we     = (p_state == P_REC);
  always_comb begin
    case (p_k)
      2'd0:    p_wdata = p_tag;
      2'd1:    p_wdata = {p_nent, 15'd0, p_err};
      2'd2:    p_wdata = p_tmr[31:0];
      default: p_wdata = p_tmr[63:32];
    endcase
  end
  // the mask scan: the lowest tile of the mask at or after p_from
  logic [63:0] scan;
  logic        scan_hit;
  logic [5:0]  scan_tile;
  assign scan = p_mask & ~((64'd1 << p_from) - 64'd1) & {64{p_from < 7'd64}};
  always_comb begin
    scan_hit = 1'b0; scan_tile = '0;
    for (int t = 63; t >= 0; t--) if (scan[t]) begin scan_hit = 1'b1; scan_tile = 6'(t); end
  end
  logic [63:0] done64m, err64m;                    // the levels at the mask's width (a tile not on, or past NT: done)
  assign done64m = 64'(st_done) | ~64'(st_en);
  assign err64m  = 64'(st_err);
  logic        p_done_all, p_err_any;
  assign p_done_all = ((done64m & p_mask) == p_mask) && !(|br_busy);
  assign p_err_any  = |(err64m & p_mask);
  assign cq_issue   = (p_state == P_REQ) && !rq_busy && !axi_bus;
  assign cq_data    = {1'b1, TGW'(p_tile), p_reg, p_val};
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      p_state <= P_IDLE; cq_en <= 1'b0; cq_err <= 1'b0; cq_base <= '0; cc_base <= '0; cq_lg <= '0; cc_lg <= '0;
      cq_tail <= '0; cq_head <= '0; cc_head <= '0; cc_tail <= '0; p_k <= '0; p_mask <= '0; p_img <= '0; p_left <= '0;
      p_nent <= '0; p_tag <= '0; p_val <= '0; p_reg <= '0; p_single <= 1'b0; p_err <= 1'b0; p_nowait <= 1'b0; p_from <= '0;
      p_tile <= '0;
      p_guard <= '0; p_tmr <= '0; cq_irq <= 1'b0;
    end else begin
      cq_irq <= 1'b0;
      // the host's writes of the queue's registers
      if (g_do && a_we && !g_is_br) begin
        case (d_g)
          10'h028: cq_en <= a_wd[0];
          10'h029: begin cq_base <= a_wd[9:0]; cq_lg <= (a_wd[19:16] > 4'd8) ? 4'd8 : a_wd[19:16]; end
          10'h02A: cq_tail <= a_wd[7:0];
          10'h02C: begin cc_base <= a_wd[9:0]; cc_lg <= (a_wd[19:16] > 4'd8) ? 4'd8 : a_wd[19:16]; end
          10'h02D: cc_head <= a_wd[7:0];
          default: ;
        endcase
      end
      if (cq_rst) begin
        p_state <= P_IDLE; cq_head <= '0; cc_tail <= '0; cq_err <= 1'b0;
      end else begin
        case (p_state)
          P_IDLE: if (cq_en && (cq_head != (cq_tail & cq_msk)) && (cc_tail_nx != (cc_head & cc_msk))) begin
            p_state <= P_CMD; p_k <= 2'd0;
          end
          P_CMD: if (p_mem) begin                     // the command's four words
            case (p_k)
              2'd0: p_mask[31:0]  <= cm_rdata;
              2'd1: p_mask[63:32] <= cm_rdata;
              2'd2: begin p_img <= cm_rdata[9:0]; p_left <= cm_rdata[31:16]; p_nowait <= cm_rdata[15]; end
              default: p_tag <= cm_rdata;
            endcase
            p_k <= p_k + 2'd1;
            if (p_k == 2'd3) begin
              p_nent <= '0; p_err <= 1'b0;
              p_state <= (p_left == 16'd0) ? P_GUARD : P_ENT;
            end
          end
          P_ENT: if (p_mem) begin                     // an entry's first word: its register and target
            p_reg <= cm_rdata[7:0]; p_tile <= cm_rdata[13:8]; p_single <= cm_rdata[14]; p_img <= p_img + 10'd1;
            p_state <= P_VAL;
          end
          P_VAL: if (p_mem) begin                     // its value
            p_val <= cm_rdata; p_img <= p_img + 10'd1; p_from <= 7'd0;
            p_state <= p_single ? P_REQ : P_TGT;
          end
          P_TGT: begin                                // the next tile of the mask, or the entry is done
            if (scan_hit) begin p_tile <= scan_tile; p_state <= P_REQ; end
            else begin
              p_left <= p_left - 16'd1; p_nent <= p_nent + 16'd1;
              p_state <= (p_left == 16'd1) ? P_GUARD : P_ENT;
            end
          end
          P_REQ: if (cq_issue) p_state <= P_RSP;
          P_RSP: if (rq_done) begin
            if (rq_rsp[32]) p_err <= 1'b1;            // a refused write (a tile that is not on)
            if (p_single) begin
              p_left <= p_left - 16'd1; p_nent <= p_nent + 16'd1;
              p_state <= (p_left == 16'd1) ? P_GUARD : P_ENT;
            end else begin
              p_from <= 7'(p_tile) + 7'd1; p_state <= P_TGT;
            end
          end
          P_GUARD: begin                              // the done levels behind the last write's answer (synchronizer skew)
            p_guard <= p_guard + 2'd1;
            if (p_guard == 2'd3) p_state <= P_WAIT;
          end
          P_WAIT: if (p_nowait || p_done_all || p_err_any) begin
            p_tmr <= timer; if (p_err_any) p_err <= 1'b1; p_k <= 2'd0; p_state <= P_REC;
          end
          P_REC: if (p_mem) begin                     // the completion record's four words
            p_k <= p_k + 2'd1;
            if (p_k == 2'd3) begin
              cc_tail <= cc_tail_nx; cq_head <= cq_head_nx; cq_irq <= 1'b1; p_state <= P_IDLE;
              if (p_err) cq_err <= 1'b1;
            end
          end
          default: p_state <= P_IDLE;
        endcase
      end
    end
  end

  // the bridge events, the interrupt causes
  logic        br_event;
  logic [4:0]  br_any_bits;
  always_comb begin
    br_any_bits = '0;
    for (int b = 0; b < NB; b++) br_any_bits = br_any_bits | br_err[b];
  end
  assign br_event = (|ev_crc) || (|ev_lost) || (|ev_axi) || (|ev_timeout) || (|ev_host) || (|(st_parity & ~par_q));

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      a_state <= A_IDLE; a_we <= 1'b0; a_addr <= '0; a_wd <= '0; a_resp <= R_OKAY; a_rdata <= '0;
      scratch <= '0; timer <= '0; timer_hi <= '0; en_q <= '1; irq_status <= '0; irq_mask <= 5'b11111; prio_q <= '0;
      done_q <= '0; errp_q <= '0; par_q <= '0; irq <= 1'b0; err <= 1'b0; ls_q1 <= '0;
      for (int k = 0; k < NLS; k++) ls_q[k] <= '0;
      for (int b = 0; b < NB; b++) begin wb_q[b] <= '0; part_q[b] <= '0; br_err[b] <= '0; end
    end else begin
      timer <= timer + 64'd1;
      done_q <= st_done; errp_q <= st_err; par_q <= st_parity;
      // bridge error stickies (a write of 1 clears; a new event wins)
      for (int b = 0; b < NB; b++) begin
        if (g_do && a_we && g_is_br && (g_b == 5'(b)) && (d_g[2:0] == 3'd3)) br_err[b] <= br_err[b] & ~a_wd[4:0];
        if (ev_crc[b])     br_err[b][0] <= 1'b1;
        if (ev_lost[b])    br_err[b][1] <= 1'b1;
        if (ev_axi[b])     br_err[b][2] <= 1'b1;
        if (ev_timeout[b]) br_err[b][3] <= 1'b1;
        if (ev_host[b])    br_err[b][4] <= 1'b1;
      end
      // interrupt causes (a write of 1 clears; a new event wins)
      ls_q1 <= st_ls;
      if (g_do && a_we && !g_is_br && (d_g == 10'h010)) irq_status <= irq_status & ~a_wd[4:0];
      if (|(st_done & ~done_q)) irq_status[0] <= 1'b1;
      if (|(st_err & ~errp_q))  irq_status[1] <= 1'b1;
      if (br_event)             irq_status[2] <= 1'b1;
      if (|(st_ls & ~ls_q1))    irq_status[3] <= 1'b1;
      if (cq_irq)               irq_status[4] <= 1'b1;
      irq <= |(irq_status & ~irq_mask);
      err <= (|st_err) || (|br_any_bits) || (|st_parity) || (|st_ls);
      // the transaction
      case (a_state)
        A_IDLE: begin
          if (s_awready) begin
            a_state <= A_DEC; a_we <= 1'b1; a_addr <= s_awaddr;
            a_wd <= {s_wstrb[3] ? s_wdata[31:24] : 8'd0, s_wstrb[2] ? s_wdata[23:16] : 8'd0,
                     s_wstrb[1] ? s_wdata[15:8] : 8'd0, s_wstrb[0] ? s_wdata[7:0] : 8'd0};
          end else if (s_arvalid) begin
            a_state <= A_DEC; a_we <= 1'b0; a_addr <= s_araddr;
          end
        end
        A_DEC: begin
          if (d_tile_ok || d_node_ok) begin
            if (!rq_busy) a_state <= A_WAIT;                  // rq_req is high this cycle
          end else if (d_cmem_ok) begin               // drop 0.43: the command memory, one cycle
            a_state <= A_RSP; a_resp <= R_OKAY; a_rdata <= a_we ? 32'd0 : cm_rdata;
          end else if (d_glob_ok) begin
            a_state <= A_RSP;
            a_resp  <= g_hit ? R_OKAY : R_DECERR;
            a_rdata <= a_we ? 32'd0 : g_rd;
            if (!a_we && !g_is_br && (d_g == 10'h006)) timer_hi <= timer[63:32];   // TIMER_LO latches the high word
            if (a_we && !g_is_br) begin
              case (d_g)
                10'h004: scratch <= a_wd;
                10'h008: en_q <= NT'({en64[63:32], a_wd});
                10'h009: en_q <= NT'({a_wd, en64[31:0]});
                10'h011: irq_mask <= a_wd[4:0];
                10'h013: prio_q <= a_wd[15:0];
                default: ;
              endcase
            end
            if (a_we && !g_is_br && g_is_ls) ls_q[g_lsk] <= {a_wd[31], NTW'(a_wd[5:0]), NTW'(a_wd[13:8])};   // {en, a, b}
            if (a_we && g_is_br) begin
              case (d_g[2:0])
                3'd0: wb_q[g_bi][25:0]  <= a_wd[31:6];
                3'd1: wb_q[g_bi][33:26] <= a_wd[7:0];
                3'd2: part_q[g_bi]      <= a_wd[3:0];
                default: ;
              endcase
            end
          end else begin
            a_state <= A_RSP; a_resp <= R_DECERR; a_rdata <= '0;
          end
        end
        A_WAIT: if (rq_done) begin
          a_state <= A_RSP; a_resp <= rq_rsp[32] ? R_SLVERR : R_OKAY; a_rdata <= a_we ? 32'd0 : rq_rsp[31:0];
        end
        A_RSP: if (a_we ? s_bready : s_rready) a_state <= A_IDLE;
        default: a_state <= A_IDLE;
      endcase
    end
  end
  assign tile_en = en_q;
  assign prio    = prio_q;
  generate
    for (genvar k = 0; k < NLS; k++) begin : g_ls
      assign ls_cfg[k * LSW +: LSW] = ls_q[k];
    end
  endgenerate
  generate
    for (genvar b = 0; b < NB; b++) begin : g_br
      assign win_base[34 * b +: 34] = wb_q[b];
      assign br_part[4 * b +: 4]    = part_q[b];
    end
  endgenerate
  logic unused_ctrl;                         // the byte lanes below a word; the mask's levels the player does not read
  assign unused_ctrl = ^{a_addr[1:0], unused_cm_rb, unused_cm_bd};
endmodule
