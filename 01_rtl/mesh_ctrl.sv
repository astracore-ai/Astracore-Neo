// mesh_ctrl.sv -- the chip top's control plane, mesh side (drop 0.44): serves the register accesses top_ctrl sends through
//   cdc_sync on the tiles' host register buses, keeps the tile-enable map in effect, and registers the status levels that
//   cross back to the AXI clock. Runs in the mesh clock (clk, rst_n); single-clock.
//   An access (rq_req, one cycle, the payload {we, target, word address, data} held by cdc_sync): the decode is registered --
//   the next cycle drives the target tile's h_we / h_re for one cycle with h_addr / h_wdata on the buses shared by every tile
//   (host_if samples them only under its strobe), and the cycle after that takes the read data host_if registered (a 64:1
//   mux on the registered target) and answers (rq_done, rq_rsp = {err, rdata}). A target that is a bridge node (NT + b) is
//   its router's two registers: 0x00 CTRL (bit 1 pulses the router's err_clear), 0x01 STATUS (bit 8 its parity flag). A
//   disabled tile gets no bus cycle and answers err (SLVERR at the AXI side).
//   Tile enable (the fusing map, the brief's rule made concrete): en_eff[n] takes the requested bit (en_req, synchronized
//   from top_ctrl's TILE_EN without a reset, so it holds the request through the mesh reset) whenever tile n is idle --
//   while the mesh is in reset (busy is reset), after reset until it is started (a CTRL write with bit 0 through this
//   block), and again once its program has ended (prog_done rising) -- and holds otherwise, so a tile is never fenced in the
//   middle of a run. en_eff has no reset either: it loads the request on every clock while the mesh is in reset, so the
//   tiles leave reset with the map written before. A tile whose bit is 0 is fenced by neo_top -- flits addressed to it are
//   consumed at its mesh inputs and dropped, its router forwards everything else -- its register window refuses accesses
//   here, and its done / err_pin are masked out of the levels below. (The brief asked for the tile held in reset: its router
//   is inside the tile on the same rst_n, and a router in reset takes every flit offered (in_ready = !occ[1] = 1) and drops
//   it, so a tile in reset would black-hole every XY route through its position; a separate router reset is a tile change.)
//   Status levels (registered here, synchronized by cdc_bits in the AXI clock): st_done[n] = prog_done && !drain_busy (the
//   program ended and its drain has left), st_err[n] = err_pin, both of enabled tiles only; en_eff itself.
module mesh_ctrl #(
  parameter int NX = 8, NY = 8,
  parameter int NT  = NX * NY,
  parameter int TGW = $clog2(NT + NY),
  parameter int RQW = 1 + TGW + 8 + 32
)(
  input  logic             clk,
  input  logic             rst_n,
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
  // the tile-enable map: requested (synchronized) and in effect
  input  logic [NT-1:0]    en_req,
  output logic [NT-1:0]    en_eff,
  // status levels toward the AXI clock
  output logic [NT-1:0]    st_done,
  output logic [NT-1:0]    st_err
);
  localparam int NTW = (NT > 1) ? $clog2(NT) : 1;
  localparam int NYW = (NY > 1) ? $clog2(NY) : 1;
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
  logic [NT-1:0]  busy, pd_q;
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
      // the tile-enable map: a tile is busy from a start through it until its program ends
      pd_q <= prog_done;
      for (int n = 0; n < NT; n++) begin
        if (rq_req && q_tile && (q_tgt == TGW'(n)) && en_eff[n] && q_we && (q_reg == 8'h00) && q_wd[0]) busy[n] <= 1'b1;
        else if (prog_done[n] && !pd_q[n]) busy[n] <= 1'b0;
      end
      st_done <= prog_done & ~drain_busy & en_eff;
      st_err  <= err_pin & en_eff;
    end
  end
  // en_eff: no reset -- it loads the request whenever the tile is idle, every clock of the mesh reset included
  always_ff @(posedge clk) begin
    for (int n = 0; n < NT; n++) begin
      if (!busy[n]) en_eff[n] <= en_req[n];
    end
  end
  // the answer
  logic [31:0] rd_tile, rd_node;
  logic [NYW-1:0] b2i;                       // the bridge node of a node target, as an index
  assign b2i     = NYW'(t2 - TGW'(NT));
  assign rd_tile = h_rdata[32 * t2 +: 32];
  assign rd_node = (r2 == 8'h01) ? {23'd0, rtr_parity[b2i], 8'd0} : 32'd0;
  assign rq_done = s2;
  assign rq_rsp  = {e2, (e2 || w2) ? 32'd0 : (n2 ? rd_node : rd_tile)};
endmodule
