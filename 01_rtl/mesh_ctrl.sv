// mesh_ctrl.sv -- the chip top's control plane, mesh side (drop 0.42): serves the register accesses top_ctrl sends through
//   cdc_sync on the tiles' host register buses, keeps the tile-enable map in effect, and registers the status levels that
//   cross back to the AXI clock. Runs in the mesh clock (clk, rst_n); single-clock.
//   An access (rq_req, one cycle, the payload {we, target, word address, data} held by cdc_sync): the decode is registered --
//   the next cycle drives the target tile's h_we / h_re for one cycle with h_addr / h_wdata on the buses shared by every tile
//   (host_if samples them only under its strobe), and the cycle after that takes the read data host_if registered (a 64:1
//   mux on the registered target) and answers (rq_done, rq_rsp = {err, rdata}). A target that is a bridge node (NT + b) is
//   its router's two registers: 0x00 CTRL (bit 1 pulses the router's err_clear), 0x01 STATUS (bit 8 its parity flag). A
//   tile that is not on (below) gets no bus cycle and answers err (SLVERR at the AXI side).
//   Tile enable = the power map (drop 0.43, stream B2; B1's fusing map, the brief's rule made concrete): en_tgt[n] takes the
//   requested bit (en_req, synchronized from top_ctrl's TILE_EN without a reset, so it holds the request through the mesh
//   reset) whenever tile n is idle -- while the mesh is in reset (busy is reset), after reset until it is started (a CTRL
//   write with bit 0 through this block), and again once its program has ended (prog_done rising) -- and holds otherwise,
//   so a tile is never switched off in the middle of a run. A per-tile sequencer follows en_tgt through eight states, a
//   gray-code cycle (one state bit changes per step, so the state crosses to the AXI clock bit by bit and reads as a state
//   of the sequence -- top_ctrl's PWR_STATE), each step DWELL mesh clocks (the power-on step PWR_DWELL, the switch's ramp):
//     0 ON (000)  1 ISO (001): isolation on  2 CLKOFF (011): the domain's clock gated  3 RST (010): its reset asserted
//     4 OFF (110): tile_pwr_en low (the power switch)  5 PWRON (111): power on, reset held  6 RSTREL (101): the reset
//     released with the clock still off (no edge meets the release)  7 CLKON (100): the clock on, still isolated -> ON
//   -- the down sequence iso on, clock off, power off and the up sequence power on, reset, clock on, iso off that the brief
//   asks for, with the reset asserted before the power goes and released before the clock comes. The outputs (tile_iso_en,
//   tile_clk_en, tile_rst_n, tile_pwr_en) are flops loaded from the state, so none of them glitches (tile_rst_n is an
//   asynchronous reset: it is the AND of rst_n and its flop -- and, in test mode, rst_n alone: test_en bypasses the
//   sequencer's reset so a scan flow controls every tile's reset from the pin, drop 0.44). A sequence runs to its rest state (ON or OFF) before the
//   target is looked at again. The sequencer has no reset: while the mesh is in reset it loads the requested rest state on
//   every clock, so the tiles leave reset in the map written before (a disabled tile powered off, never sequenced).
//   en_eff[n] = the tile is ON: the fence of neo_top drops what is addressed to a tile that is not (its router, on the
//   mesh's reset, keeps forwarding everything else -- neo_tile's router_rst_n), its register window refuses accesses here,
//   its done / err_pin are masked out of the levels below, and top_ctrl's TILE_EN_EFF reads it. Nothing is retained: a
//   tile that comes back is a tile out of reset (its program, descriptor, tables and bank contents re-loaded by software).
//   (The sequencer is here and not in top_ctrl, where the brief placed it: the four outputs and the idle rule are of the
//   mesh clock, and a sequence stepped in the AXI clock would cross bit by bit with no order guaranteed between the
//   bits; top_ctrl holds the request and reads the state.)
//   Dual-tile lockstep (drop 0.43): NLS pair configurations (ls_cfg, top_ctrl's LS_PAIR: {enable, tile a, tile b}, static,
//   crossed by cdc_bits); while a pair is enabled and both tiles are on, each side's drain messages (the NIC's
//   drain_msg_cnt advancing, drain_crc_last the message's CRC) fold into a 16-bit signature (rotate left one, XOR the CRC)
//   and a message count from the enable; whenever the two counts are equal and both signatures are up to date, the
//   signatures must be equal, and when both tiles are done (program ended, drained, not restarted) the counts must be --
//   either mismatch sets ls_err[k], sticky until the pair is disabled. The signature makes each comparison cover every
//   message before it, so a pair that drifts by a message (one tile ahead) loses no comparison.
//   Status levels (registered here, synchronized by cdc_bits in the AXI clock): st_done[n] = prog_done && !drain_busy && not
//   busy (the program ended and its drain has left, and no start since -- the command queue's completion), st_err[n] =
//   err_pin, both of tiles that are on; en_eff; the power states; the lockstep errors.
module mesh_ctrl #(
  parameter int NX = 8, NY = 8,
  parameter int NLS = 4,                     // drop 0.43: lockstep pairs (top_ctrl's LS_PAIR registers), 1 .. 8
  parameter int DWELL = 4,                   // drop 0.43: mesh clocks per power-sequence step
  parameter int PWR_DWELL = 16,              // ... for the power-on step (an integrator with a power-good acknowledge
                                             //     replaces the count with it)
  parameter int NT  = NX * NY,
  parameter int NTW = (NT > 1) ? $clog2(NT) : 1,
  parameter int LSW = 1 + 2 * NTW,           // a lockstep pair's configuration
  parameter int TGW = $clog2(NT + NY),
  parameter int RQW = 1 + TGW + 8 + 32
)(
  input  logic             clk,
  input  logic             rst_n,
  input  logic             test_en,          // drop 0.44 (review #8): test mode -- the tiles' resets follow rst_n directly
  // register accesses from the AXI clock (cdc_sync side B)
  input  logic             rq_req,
  input  logic [RQW-1:0]   rq_data,
  output logic             rq_done,
  output logic [32:0]      rq_rsp,
  // the tiles' host register buses
  output logic [NT-1:0]    h_we,
  output logic [NT-1:0]    h_re,
  output logic [7:0]       h_addr,
  output logic [31:0]      h_wdata,
  input  logic [NT*32-1:0] h_rdata,
  // the bridge nodes' routers
  output logic [NY-1:0]    rtr_clear,
  input  logic [NY-1:0]    rtr_parity,
  // the tiles' status
  input  logic [NT-1:0]    prog_done,
  input  logic [NT-1:0]    drain_busy,
  input  logic [NT-1:0]    err_pin,
  // the tile-enable (power) map: requested (synchronized) and in effect (on)
  input  logic [NT-1:0]    en_req,
  output logic [NT-1:0]    en_eff,
  // drop 0.43: the power sequencing per tile
  output logic [NT-1:0]    tile_rst_n,       // the switchable domain's reset (asynchronous, glitch-free)
  output logic [NT-1:0]    tile_clk_en,      // its clock enable (neo_tile's neo_icg)
  output logic [NT-1:0]    tile_iso_en,      // its isolation cells
  output logic [NT-1:0]    tile_pwr_en,      // its power switch (neo_top's port)
  output logic [3*NT-1:0]  pwr_state,        // the sequencer's state per tile, gray-coded, toward the AXI clock
  // drop 0.43: dual-tile lockstep
  input  logic [16*NT-1:0] drain_crc,        // tile n's drain_crc_last at [16 n +: 16]
  input  logic [16*NT-1:0] drain_cnt,        // tile n's drain_msg_cnt
  input  logic [NLS*LSW-1:0] ls_cfg,         // pair k at [k * LSW +: LSW]: {enable, tile a, tile b}
  output logic [NLS-1:0]   ls_err,
  // status levels toward the AXI clock
  output logic [NT-1:0]    st_done,
  output logic [NT-1:0]    st_err
);
  localparam int NYW = (NY > 1) ? $clog2(NY) : 1;
  localparam int PDW = $clog2(PWR_DWELL + 1);
  localparam logic [2:0] P_ON = 3'b000, P_ISO = 3'b001, P_CLKOFF = 3'b011, P_RST = 3'b010, P_OFF = 3'b110, P_PWRON = 3'b111,
                         P_RSTREL = 3'b101, P_CLKON = 3'b100;
  logic           q_we;
  logic [TGW-1:0] q_tgt;
  logic [7:0]     q_reg;
  logic [31:0]    q_wd;
  assign {q_we, q_tgt, q_reg, q_wd} = rq_data;
  logic           q_tile, q_node, q_ok;
  logic [TGW-1:0] q_b;                       // the bridge node of a node target
  logic [NTW-1:0] q_ti;                      // the tile of a tile target, as an index
  assign q_tile = (32'(q_tgt) < NT);
  assign q_node = !q_tile && (32'(q_tgt) < NT + NY);
  assign q_b    = q_tgt - TGW'(NT);
  assign q_ti   = NTW'(q_tgt);
  assign q_ok   = q_node || (q_tile && en_eff[q_ti]);

  logic           s1, s2;                    // the access's bus cycle; its answer
  logic           e1, e2;                    // refused
  logic           w1, w2;                    // a write
  logic           n1, n2;                    // a bridge-node target
  logic [TGW-1:0] t1, t2;
  logic [7:0]     r1, r2;
  logic [NT-1:0]  busy, pd_q, en_tgt;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      s1 <= 1'b0; s2 <= 1'b0; e1 <= 1'b0; e2 <= 1'b0; w1 <= 1'b0; w2 <= 1'b0; n1 <= 1'b0; n2 <= 1'b0; t1 <= '0; t2 <= '0;
      r1 <= '0; r2 <= '0; h_we <= '0; h_re <= '0; h_addr <= '0; h_wdata <= '0; rtr_clear <= '0; busy <= '0; pd_q <= '0;
      st_done <= '0; st_err <= '0;
    end else begin
      // cycle 1: the bus cycle (registered decode), the bridge-node write
      s1 <= rq_req; e1 <= !q_ok; w1 <= q_we; n1 <= q_node; t1 <= q_tgt; r1 <= q_reg;
      h_we <= '0; h_re <= '0; rtr_clear <= '0;
      if (rq_req) begin
        h_addr <= q_reg; h_wdata <= q_wd;
        for (int n = 0; n < NT; n++) begin
          if (q_tile && (q_tgt == TGW'(n)) && en_eff[n]) begin h_we[n] <= q_we; h_re[n] <= !q_we; end
        end
        for (int b = 0; b < NY; b++) begin
          if (q_node && (q_b == TGW'(b)) && q_we && (q_reg == 8'h00) && q_wd[1]) rtr_clear[b] <= 1'b1;
        end
      end
      // cycle 2: the answer (host_if's read data is registered at the end of cycle 1)
      s2 <= s1; e2 <= e1; w2 <= w1; n2 <= n1; t2 <= t1; r2 <= r1;
      // a tile is busy from a start through it until its program ends
      pd_q <= prog_done;
      for (int n = 0; n < NT; n++) begin
        if (rq_req && q_tile && (q_tgt == TGW'(n)) && en_eff[n] && q_we && (q_reg == 8'h00) && q_wd[0]) busy[n] <= 1'b1;
        else if (prog_done[n] && !pd_q[n]) busy[n] <= 1'b0;
      end
      st_done <= prog_done & ~drain_busy & ~busy & en_eff;
      st_err  <= err_pin & en_eff;
    end
  end
  // en_tgt: no reset -- it loads the request whenever the tile is idle, every clock of the mesh reset included
  always_ff @(posedge clk) begin
    for (int n = 0; n < NT; n++) begin
      if (!busy[n]) en_tgt[n] <= en_req[n];
    end
  end

  // ------------------------------------------------------------------ the power sequencers (drop 0.43)
  // in_rst: high through the mesh reset and the clock after it -- the sequencers load the requested rest state while it is
  // (a flop of rst_n, so that rst_n itself stays an asynchronous reset everywhere)
  logic in_rst;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) in_rst <= 1'b1;
    else        in_rst <= 1'b0;
  end
  generate
    for (genvar n = 0; n < NT; n++) begin : g_pwr
      logic [2:0]     ps, ps_nx;
      logic [PDW-1:0] dw;
      logic           go;                    // the dwell is over and the state moves on
      always_comb begin
        case (ps)
          P_ON:     ps_nx = P_ISO;
          P_ISO:    ps_nx = P_CLKOFF;
          P_CLKOFF: ps_nx = P_RST;
          P_RST:    ps_nx = P_OFF;
          P_OFF:    ps_nx = P_PWRON;
          P_PWRON:  ps_nx = P_RSTREL;
          P_RSTREL: ps_nx = P_CLKON;
          default:  ps_nx = P_ON;
        endcase
      end
      assign go = (dw == '0) && (((ps == P_ON) && !en_tgt[n]) || ((ps == P_OFF) && en_tgt[n]) || ((ps != P_ON) && (ps != P_OFF)));
      // no reset: in the mesh reset (in_rst) the requested rest state and its outputs, every clock
      logic iso_q, clk_q, rst_q, pwr_q;
      always_ff @(posedge clk) begin
        if (in_rst) begin
          ps <= en_req[n] ? P_ON : P_OFF; dw <= '0;
          iso_q <= !en_req[n]; clk_q <= en_req[n]; rst_q <= en_req[n]; pwr_q <= en_req[n];
        end else begin
          if (go) begin
            ps <= ps_nx;
            dw <= (ps_nx == P_PWRON) ? PDW'(PWR_DWELL - 1) : PDW'(DWELL - 1);
          end else if (dw != '0) begin
            dw <= dw - PDW'(1);
          end
          // the outputs one clock after the state: isolated but in ON; clocked in ON, ISO, CLKON; out of reset in ON, ISO,
          // CLKOFF, RSTREL, CLKON; powered but in OFF
          iso_q <= (ps != P_ON);
          clk_q <= (ps == P_ON) || (ps == P_ISO) || (ps == P_CLKON);
          rst_q <= (ps == P_ON) || (ps == P_ISO) || (ps == P_CLKOFF) || (ps == P_RSTREL) || (ps == P_CLKON);
          pwr_q <= (ps != P_OFF);
        end
      end
      assign en_eff[n]       = (ps == P_ON) && !iso_q;    // the outputs have followed: on and unisolated
      assign tile_iso_en[n]  = iso_q;
      assign tile_clk_en[n]  = clk_q;
      assign tile_rst_n[n]   = test_en ? rst_n : (rst_n && rst_q);   // test mode bypasses the sequencer's reset (drop 0.44)
      assign tile_pwr_en[n]  = pwr_q;
      assign pwr_state[3 * n +: 3] = ps;
    end
  endgenerate

  // ------------------------------------------------------------------ dual-tile lockstep (drop 0.43)
  generate
    for (genvar k = 0; k < NLS; k++) begin : g_ls
      logic           en;
      logic [NTW-1:0] ta, tb;
      assign {en, ta, tb} = ls_cfg[k * LSW +: LSW];
      // drop 0.44 (review #12): the three fields cross from the AXI clock bit by bit (cdc_bits), so a single LS_PAIR write
      // may show its enable one clock before or after its indices. The enable in effect (en_s) is en delayed three clocks:
      // the indices have settled within one clock of en, crc_* / cnt_* follow them one clock later, and seen_* take the
      // settled counts in the clock before the comparison starts -- so one write configures and enables a pair safely
      logic [2:0]     en_q;
      logic           en_s;
      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) en_q <= '0;
        else        en_q <= {en_q[1:0], en};
      end
      assign en_s = en_q[2];
      // the two tiles' (count, CRC), selected by the pair's indices and registered: the 64:1 selects end at flops, the
      // comparison below works a clock behind the tiles (a message's count and CRC change together, so nothing is lost)
      logic [15:0] crc_a, crc_b, cnt_a, cnt_b;
      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin crc_a <= '0; crc_b <= '0; cnt_a <= '0; cnt_b <= '0; end
        else begin
          crc_a <= drain_crc[16 * ta +: 16];
          crc_b <= drain_crc[16 * tb +: 16];
          cnt_a <= drain_cnt[16 * ta +: 16];
          cnt_b <= drain_cnt[16 * tb +: 16];
        end
      end
      logic        active, new_a, new_b, cur, done_ab;
      logic [15:0] seen_a, seen_b, sig_a, sig_b, na, nb;
      assign active  = en_s && (ta != tb) && en_eff[ta] && en_eff[tb];
      assign new_a   = (cnt_a != seen_a);
      assign new_b   = (cnt_b != seen_b);
      assign cur     = !new_a && !new_b;        // both signatures hold every message counted so far
      assign done_ab = st_done[ta] && st_done[tb];
      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
          seen_a <= '0; seen_b <= '0; sig_a <= '0; sig_b <= '0; na <= '0; nb <= '0; ls_err[k] <= 1'b0;
        end else if (!active) begin             // the counts so far are the starting point; the error clears with the enable
          seen_a <= cnt_a; seen_b <= cnt_b; sig_a <= '0; sig_b <= '0; na <= '0; nb <= '0;
          if (!en_s) ls_err[k] <= 1'b0;
        end else begin
          if (new_a) begin seen_a <= cnt_a; sig_a <= {sig_a[14:0], sig_a[15]} ^ crc_a; na <= na + 16'd1; end
          if (new_b) begin seen_b <= cnt_b; sig_b <= {sig_b[14:0], sig_b[15]} ^ crc_b; nb <= nb + 16'd1; end
          if (cur && (na == nb) && (sig_a != sig_b)) ls_err[k] <= 1'b1;
          if (cur && done_ab && (na != nb)) ls_err[k] <= 1'b1;
        end
      end
    end
  endgenerate

  // the answer
  logic [31:0] rd_tile, rd_node;
  logic [NYW-1:0] b2i;                       // the bridge node of a node target, as an index
  assign b2i     = NYW'(t2 - TGW'(NT));
  assign rd_tile = h_rdata[32 * t2 +: 32];
  assign rd_node = (r2 == 8'h01) ? {23'd0, rtr_parity[b2i], 8'd0} : 32'd0;
  assign rq_done = s2;
  assign rq_rsp  = {e2, (e2 || w2) ? 32'd0 : (n2 ? rd_node : rd_tile)};
endmodule
