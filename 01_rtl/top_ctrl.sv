// top_ctrl.sv -- the chip top's control plane, AXI side (drop 0.44): one AXI4-Lite slave (32-bit data, CTRL_AW-bit byte
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
//       0x000 ID 'NEOT'  0x004 VERSION 0x00000044  0x008 CONFIG {COLS, ROWS, N_HOST, N_MEM, NY, NX}  0x00C CONFIG2 {BAW, WPF,
//       AXI_DW}  0x010 SCRATCH r/w  0x018 TIMER_LO (r; latches the high word)  0x01C TIMER_HI (r; the latched word)
//       0x020 / 0x024 TILE_EN lo / hi (r/w; reset all ones; bit n = tile n)  0x028 / 0x02C TILE_EN_EFF lo / hi (r: the map in
//       effect, from the mesh side)  0x030 / 0x034 DONE lo / hi (r: prog_done && !drain_busy, enabled tiles)  0x038 / 0x03C
//       ERR lo / hi (r: err_pin, enabled tiles)  0x040 IRQ_STATUS (r; write 1 clears): bit 0 done (a tile's done rose), bit 1
//       err (a tile's err_pin rose), bit 2 bridge (a bridge error event, or a bridge router's parity flag rose)  0x044
//       IRQ_MASK (r/w, 1 masks; reset 0x7)  0x048 STATUS (r: bit 0 irq, bit 1 err)
//       0x100 + 0x20 * b     bridge b: +0x0 WIN_LO (bits 31:6 of the window base; 5:0 read 0) +0x4 WIN_HI (bits 39:32 in 7:0)
//                            +0x8 CTRL (bits 3:0: the partition of the host role's requests) +0xC ERR (r, write 1 clears
//                            bits 4:0): 0 crc, 1 lost, 2 axi, 3 timeout, 4 host, 5 the router's parity flag (a level; clear
//                            it through the bridge node's CTRL)
//     anything else: DECERR. Writes take the strobed bytes and zeros elsewhere (host_axil's rule); one transaction at a
//     time, writes before reads.
//   Tile-enable rule (the fusing map): TILE_EN is a request; the mesh side applies a bit while the mesh is in reset or while
//   that tile is idle (not started since reset, or its program has ended) and holds it pending otherwise -- TILE_EN_EFF
//   shows what is in effect. A disabled tile is fenced (mesh_ctrl.sv / neo_top.sv), not reset.
//   irq = any unmasked IRQ_STATUS bit; err (to the safety island) = any enabled tile's err_pin, any bridge error bit, any
//   bridge router's parity flag -- a level, registered, not maskable.
module top_ctrl #(
  parameter int NX = 8, NY = 8,
  parameter int ROWS = 32, COLS = 32,
  parameter int BAW = 19, WPF = 32, AXI_DW = 512,
  parameter int N_MEM = 8, N_HOST = 1,
  parameter int CTRL_AW = 18,                // at least 18: three 64 KB regions
  parameter int NT  = NX * NY,
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
  // bridges (row b at [b*W +: W])
  output logic [NY*34-1:0]     win_base,     // bits 39:6 of each window base
  output logic [NY*4-1:0]      br_part,
  input  logic [NY-1:0]        ev_crc,
  input  logic [NY-1:0]        ev_lost,
  input  logic [NY-1:0]        ev_axi,
  input  logic [NY-1:0]        ev_timeout,
  input  logic [NY-1:0]        ev_host,
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
  logic [2:0]          irq_status, irq_mask;
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
  logic            d_tile_ok, d_node_ok, d_glob_ok;
  logic [TGW-1:0]  d_target;
  assign d_region  = (a_addr[CTRL_AW-1:16] == RGW'(0)) ? 2'd0 : (a_addr[CTRL_AW-1:16] == RGW'(1)) ? 2'd1 :
                     (a_addr[CTRL_AW-1:16] == RGW'(2)) ? 2'd2 : 2'd3;
  assign d_win     = a_addr[15:10];
  assign d_reg     = a_addr[9:2];
  assign d_g       = a_addr[11:2];
  assign d_tile_ok = (d_region == 2'd0) && (32'(d_win) < NT);
  assign d_node_ok = (d_region == 2'd1) && (32'(d_win) < NB);
  assign d_glob_ok = (d_region == 2'd2) && (a_addr[15:12] == 4'd0);
  assign d_target  = d_tile_ok ? TGW'(d_win) : TGW'(32'(d_win) + NT);
  assign rq_req    = (a_state == A_DEC) && (d_tile_ok || d_node_ok) && !rq_busy;
  assign rq_data   = {a_we, d_target, d_reg, a_wd};

  // the global registers: read value and validity of the word d_g
  logic [63:0] en64, eff64, done64, err64;
  assign en64   = 64'(en_q);
  assign eff64  = 64'(st_en);
  assign done64 = 64'(st_done);
  assign err64  = 64'(st_err);
  logic        g_hit;                        // d_g names a register
  logic [31:0] g_rd;
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
        default: g_hit = 1'b0;
      endcase
    end else begin
      case (d_g)
        10'h000: g_rd = 32'h4E45_4F54;                                    // 'NEOT'
        10'h001: g_rd = 32'h0000_0044;                                    // drop 0.44
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
        10'h010: g_rd = {29'd0, irq_status};
        10'h011: g_rd = {29'd0, irq_mask};
        10'h012: g_rd = {30'd0, err, irq};
        default: g_hit = 1'b0;
      endcase
    end
  end
  logic g_do;                                // a global register access is performed this cycle
  assign g_do = (a_state == A_DEC) && d_glob_ok;

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
      scratch <= '0; timer <= '0; timer_hi <= '0; en_q <= '1; irq_status <= '0; irq_mask <= 3'b111;
      done_q <= '0; errp_q <= '0; par_q <= '0; irq <= 1'b0; err <= 1'b0;
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
      if (g_do && a_we && !g_is_br && (d_g == 10'h010)) irq_status <= irq_status & ~a_wd[2:0];
      if (|(st_done & ~done_q)) irq_status[0] <= 1'b1;
      if (|(st_err & ~errp_q))  irq_status[1] <= 1'b1;
      if (br_event)             irq_status[2] <= 1'b1;
      irq <= |(irq_status & ~irq_mask);
      err <= (|st_err) || (|br_any_bits) || (|st_parity);
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
                10'h011: irq_mask <= a_wd[2:0];
                default: ;
              endcase
            end
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
  generate
    for (genvar b = 0; b < NB; b++) begin : g_br
      assign win_base[34 * b +: 34] = wb_q[b];
      assign br_part[4 * b +: 4]    = part_q[b];
    end
  endgenerate
  logic unused_ctrl;                         // the byte lanes below a word
  assign unused_ctrl = ^{a_addr[1:0]};
endmodule
