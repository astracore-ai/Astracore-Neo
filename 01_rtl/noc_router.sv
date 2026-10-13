// noc_router.sv -- 2D-mesh router, XY routing, single-flit packets, parity-checked (drop 0.4).
//   Ports 0..4 = N, E, S, W, L (local). Flit layout, MSB first:
//     parity (1) | dst_y (YW) | dst_x (XW) | src_y (YW) | src_x (XW) | seq (8) | payload (DW)
//   Each input has a 2-entry FIFO; each output a registered stage with ready/valid handshake.
//   Routing: X first (E/W), then Y (S = +y, N = -y), then local. Round-robin arbitration per
//   output among the inputs whose head flit requests it. A flit whose parity is wrong is
//   dropped at the input and parity_err_sticky is raised: the sender's end-to-end sequence
//   check and timeout recover the packet, the error pin reports the fault.
//   Drop 0.41 (the same function, cycle for cycle; the arbitration was `(rr + 1 + k) % 5` in a priority chain behind a
//   1,077-bit parity reduce on the FIFO head, 2.57 ns on the ASAP7 SS proxy):
//   - the parity is computed when a flit is pushed (^in_flit, on the input side) and stored with the entry (fbad), so
//     head_bad is a 2:1 mux of two stored bits; so is the route: the XY compares of the pushed flit's destination with
//     the straps my_x / my_y (static: tied by the mesh, false paths in TILE_SDC), one-hot, stored with the entry (freq);
//   - the request is one-hot (rq[o][i]: head i valid, good parity, routed to o); the rotating
//     priority of output o is a one-hot pointer ptr[o] = the input after the last grant; the grant is the first set bit
//     of rq & (the bits at and above ptr) if any, else the first set bit of rq (the double-vector first-set), one-hot;
//     the output register loads the head through an AND-OR of the grant. ptr resets to input 0 (the old rr = 4), so the
//     order of grants is the old one. fire needs only that a request exists; the occupancy is a 2-bit thermometer
//     (occ; count is derived from it), so the pop updates it without an increment; the stored bits are read through
//     rd_a, a copy of the read pointer, so the data mux's 1,077-bit fanout on rd stays off the arbitration path.
//   Drop 0.43 (stream B2) -- per-partition priority and clock-gated output stages:
//   - two priority classes. The flit's seq field carries its partition (seq[3:0], the NIC's and the bridges' {4'd0,
//     partition}); prio[p] = 1 makes partition p the high class (a register of the control plane: host_if's PRIO in a
//     tile, top_ctrl's in the bridge column; all zero = one class). The class is looked up when a flit is pushed and
//     stored with the entry (fcls, beside fbad / freq), so the arbitration reads one stored bit per head; a change of prio
//     applies to the flits pushed after it. Per output: a high-class request wins over the low-class ones, round-robin
//     inside each class with a pointer per class (ptr_hi, ptr_lo: one-hot as ptr was; a shared pointer would let the
//     high-class grants move it past a low-class input every time and starve it), and a low-class request is promoted
//     for one grant after PRIO_N high-class grants of its output (wcnt counts them while a low-class request waits, wsat
//     says it reached PRIO_N) -- the bound a mixed-criticality integrator asks for: a waiting low-class head lets at most
//     5 PRIO_N + 4 fires of its output go to other inputs, a high-class head at most 5 + floor(4 / PRIO_N) (44 and 5 at
//     PRIO_N = 8; the formal harness proves both for every prio). With prio = 0 every head is of the low class: the
//     grant is drop 0.41's on ptr_lo, cycle for cycle (wcnt stays 0), which run_noc.py N3 checks against the 0.40 router.
//     Both classes' picks are formed side by side (g_hi, g_lo: drop 0.41's first-set each) and the class chosen last, so
//     the choice (use_hi) runs beside the pick instead of in front of it.
//   - each output stage (out_valid, out_flit, its pointers and wcnt / wsat) runs on its own gated clock (neo_icg), enabled
//     when the stage loads a flit or hands one on (fire[o], out_valid && out_ready) -- exactly the cycles in which one of
//     its registers changes, so the gating is invisible (the formal harness asserts it); the input FIFOs stay on clk.
module noc_router #(
  parameter int XW = 3,
  parameter int YW = 3,
  parameter int DW = 32,
  parameter int FW = 1 + 2 * (XW + YW) + 8 + DW,
  parameter int DV_HOOKS = 0,    // 1: the output-register fault hooks below exist (drop 0.35)
  parameter int PRIO_N = 8       // drop 0.43: high-class grants a waiting low-class request lets pass before it is promoted (>= 1)
)(
  input  logic          clk,
  input  logic          rst_n,
  input  logic [XW-1:0] my_x,          // this router's mesh coordinates (drop 0.35: ports strapped by the mesh, not parameters)
  input  logic [YW-1:0] my_y,
  input  logic [15:0]   prio,          // drop 0.43: bit p = partition p is the high class (static: a control-plane register)
  input  logic          in_valid  [5],
  input  logic [FW-1:0] in_flit   [5],
  output logic          in_ready  [5],
  output logic          out_valid [5],
  output logic [FW-1:0] out_flit  [5],
  input  logic          out_ready [5],
  input  logic          err_clear,
  output logic          parity_err_sticky,
  output logic [4:0]    stage_en,      // drop 0.43: the output stages' clock enables (observation: the gated fractions)
  // testbench hooks (drop 0.35; DV_HOOKS = 1), applied to the flit that output dv_port LOADS at a rising edge while the hook
  // is high: the register is written with the flit XOR dv_mask (dv_flit_flip), or written without its valid, so the flit
  // vanishes (dv_valid_clear). Drop 0.38: load-time, no longer a falling-edge write of the register -- a value written on the
  // other clock edge reached the neighbouring tile a cycle late in Verilator's flat build (the mesh link crosses the tile
  // boundary; run 126, M16 at 32x32), while the loaded value is an ordinary register update that every simulator orders
  input  logic          dv_flit_flip,
  input  logic          dv_valid_clear,
  input  logic [2:0]    dv_port,
  input  logic [FW-1:0] dv_mask
);
  localparam int WCW = $clog2(PRIO_N + 1);   // wcnt: 0 .. PRIO_N
  // ---- input FIFOs (depth 2), each entry with the flit's parity, route and class computed at the push ----
  logic [FW-1:0] fifo [5][2];
  logic          fbad [5][2];   // the entry's parity failed (odd), from ^in_flit at the push
  logic          fcls [5][2];   // the entry's class (drop 0.43): prio[its partition] at the push
  logic [1:0]    occ   [5];     // the occupancy as a thermometer: bit 0 = at least one entry, bit 1 = two
  logic [1:0]    count [5];     // the occupancy 0..2 (from occ; kept for the formal harness and waveforms)
  logic          unused_count;  // (lint: count is read by nothing in here)
  assign unused_count = ^{count[0], count[1], count[2], count[3], count[4]};
  logic          rd    [5];
  logic          rd_a  [5];     // a copy of rd for the stored parity / route bits (rd drives the FIFO's 2:1 data mux)
  logic          wr    [5];
  logic [FW-1:0] head  [5];
  logic          head_valid [5];
  logic          head_bad [5];  // parity failure on the head flit
  logic [4:0]    freq  [5][2];  // the entry's output port, one-hot (bit o = port o), from in_flit's destination at the push
  logic [4:0]    req_oh [5];    // requested output port of the head flit, one-hot
  logic          in_bad [5];    // parity of the flit being pushed
  logic [4:0]    in_req [5];    // output port of the flit being pushed, one-hot
  logic          in_cls [5];    // class of the flit being pushed (drop 0.43)
  logic [4:0]    hcls;          // input i's head is of the high class (drop 0.43)

  generate
    for (genvar i = 0; i < 5; i++) begin : g_in
      assign count[i]      = {occ[i][1], occ[i][0] && !occ[i][1]};
      assign in_ready[i]   = !occ[i][1];
      assign head[i]       = fifo[i][rd[i]];
      assign head_valid[i] = occ[i][0];
      assign head_bad[i]   = fbad[i][rd_a[i]];
      assign req_oh[i]     = freq[i][rd_a[i]];
      assign hcls[i]       = fcls[i][rd_a[i]];
      assign in_bad[i]     = (^in_flit[i]) != 1'b0;   // even parity over the whole flit
      assign in_cls[i]     = prio[in_flit[i][DW +: 4]];
      logic [XW-1:0] dx;
      logic [YW-1:0] dy;
      logic          xe, ye;
      assign dx = in_flit[i][FW-2-YW -: XW];
      assign dy = in_flit[i][FW-2 -: YW];
      assign xe = (dx == my_x);
      assign ye = (dy == my_y);
      assign in_req[i] = {xe && ye,                   // 4 local
                          dx < my_x,                  // 3 W
                          xe && (dy > my_y),          // 2 S
                          dx > my_x,                  // 1 E
                          xe && (dy < my_y)};         // 0 N
    end
  endgenerate

  // ---- per-output arbitration: two classes (drop 0.43), each round-robin with a one-hot rotating pointer ----
  logic [4:0]     ptr_lo [5];   // output o, the low class: one-hot, the input after the class's last grant (first in priority)
  logic [4:0]     ptr_hi [5];   // ... the high class
  logic [WCW-1:0] wcnt   [5];   // output o: high-class grants since its last low-class grant, while a low-class request waits
  logic           wsat   [5];   // output o: wcnt == PRIO_N -- a waiting low-class request goes next
  logic           unused_wcnt;  // (lint: wcnt is read through the stages' own copies; the array is for the harness)
  assign unused_wcnt = ^{wcnt[0], wcnt[1], wcnt[2], wcnt[3], wcnt[4]};
  logic [4:0]     rq     [5];   // output o: the inputs whose head may go to o
  logic [4:0]     rq_hi  [5];   // ... of the high class
  logic [4:0]     rq_lo  [5];   // ... of the low class
  logic           use_hi [5];   // output o serves the high class this cycle
  logic [4:0]     ge_hi  [5];   // output o: the inputs at and above ptr_hi (thermometer)
  logic [4:0]     ge_lo  [5];   // ... at and above ptr_lo
  logic [4:0]     m_hi   [5];   // output o: rq_hi at and above ptr_hi
  logic [4:0]     m_lo   [5];   // ... rq_lo at and above ptr_lo
  logic [4:0]     g_hi   [5];   // output o: the high class's round-robin pick, one-hot (0 = no request of the class)
  logic [4:0]     g_lo   [5];   // ... the low class's
  logic [4:0]     gnt    [5];   // output o: the granted input, one-hot (0 = none)
  logic           fire   [5];   // transfer into output register o this cycle
  logic           pop    [5];   // input i is popped this cycle
  logic           drop   [5];   // input i head dropped for bad parity

  always_comb begin
    for (int o = 0; o < 5; o++) begin
      for (int i = 0; i < 5; i++) rq[o][i] = head_valid[i] && !head_bad[i] && req_oh[i][o];
      rq_hi[o]  = rq[o] & hcls;
      rq_lo[o]  = rq[o] & ~hcls;
      use_hi[o] = (rq_hi[o] != 5'd0) && !(wsat[o] && (rq_lo[o] != 5'd0));   // a waiting low promoted after PRIO_N
      // both classes' picks side by side, the class chosen last (the choice off the pick's path)
      ge_hi[o]  = {|ptr_hi[o], |ptr_hi[o][3:0], |ptr_hi[o][2:0], |ptr_hi[o][1:0], ptr_hi[o][0]};
      ge_lo[o]  = {|ptr_lo[o], |ptr_lo[o][3:0], |ptr_lo[o][2:0], |ptr_lo[o][1:0], ptr_lo[o][0]};
      m_hi[o]   = rq_hi[o] & ge_hi[o];
      m_lo[o]   = rq_lo[o] & ge_lo[o];
      for (int i = 0; i < 5; i++) begin               // the first set bit at and above the pointer, else the first
        g_hi[o][i] = (m_hi[o] != 5'd0) ? (m_hi[o][i] && ((m_hi[o] & ((5'd1 << i) - 5'd1)) == 5'd0))
                                       : (rq_hi[o][i] && ((rq_hi[o] & ((5'd1 << i) - 5'd1)) == 5'd0));
        g_lo[o][i] = (m_lo[o] != 5'd0) ? (m_lo[o][i] && ((m_lo[o] & ((5'd1 << i) - 5'd1)) == 5'd0))
                                       : (rq_lo[o][i] && ((rq_lo[o] & ((5'd1 << i) - 5'd1)) == 5'd0));
      end
      gnt[o]  = use_hi[o] ? g_hi[o] : g_lo[o];
      fire[o] = (rq[o] != 5'd0) && (!out_valid[o] || out_ready[o]);   // a grant exists exactly when a request does
    end
    for (int i = 0; i < 5; i++) begin
      drop[i] = head_valid[i] && head_bad[i];
      pop[i]  = drop[i];
      for (int o = 0; o < 5; o++) if (gnt[o][i] && fire[o]) pop[i] = 1'b1;
    end
  end

  // the output registers' next flit: the granted head as an AND-OR of the one-hot grant
  logic [FW-1:0] gflit [5];
  always_comb begin
    for (int o = 0; o < 5; o++) begin
      gflit[o] = '0;
      for (int i = 0; i < 5; i++) if (gnt[o][i]) gflit[o] = gflit[o] | head[i];
    end
  end

  // ---- the input FIFOs and the sticky (clk) ----
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int i = 0; i < 5; i++) begin
        occ[i] <= '0; rd[i] <= 1'b0; rd_a[i] <= 1'b0; wr[i] <= 1'b0;
      end
      parity_err_sticky <= 1'b0;
    end else begin
      for (int i = 0; i < 5; i++) begin
        // input FIFO push/pop
        if (in_valid[i] && in_ready[i]) begin
          fifo[i][wr[i]] <= in_flit[i];
          fbad[i][wr[i]] <= in_bad[i];
          freq[i][wr[i]] <= in_req[i];
          fcls[i][wr[i]] <= in_cls[i];
          wr[i] <= !wr[i];
        end
        if (pop[i]) rd[i] <= !rd[i];
        rd_a[i] <= rd_a[i] ^ pop[i];                     // = rd, kept apart from the data mux's fanout
        // occupancy: a push alone shifts a one in, a pop alone shifts it out, both or neither hold (as muxes, no
        // increment: two entries need a pop to take a push, so push && pop leaves occ as it is)
        occ[i] <= {pop[i] ? ((in_valid[i] && in_ready[i]) && occ[i][1]) : ((in_valid[i] && in_ready[i]) ? occ[i][0] : occ[i][1]),
                   (in_valid[i] && in_ready[i]) || (pop[i] ? occ[i][1] : occ[i][0])};
        if (drop[i]) parity_err_sticky <= 1'b1;
      end
      if (err_clear) parity_err_sticky <= 1'b0;
    end
  end

  // ---- the output stages, each on its gated clock (drop 0.43) ----
  generate
    for (genvar o = 0; o < 5; o++) begin : g_out
      // the hooks, as terms of the output register's load (zero when DV_HOOKS = 0: the terms vanish with the constant)
      logic dv_flip_o, dv_clear_o;
      assign dv_flip_o  = (DV_HOOKS != 0) && dv_flit_flip  && (dv_port == 3'(o));
      assign dv_clear_o = (DV_HOOKS != 0) && dv_valid_clear && (dv_port == 3'(o));
      // the stage's clock: on when it loads a flit or hands its flit on (every cycle one of its registers changes), and
      // through the reset (the asynchronous reset needs no clock; the simulators' first cycles may see no reset edge)
      logic gclk;
      assign stage_en[o] = fire[o] || (out_valid[o] && out_ready[o]) || !rst_n;
      neo_icg u_icg (.clk(clk), .en(stage_en[o]), .te(1'b0), .gclk(gclk));
      // the stage's registers (its own variables -- one array written by five differently clocked blocks is a multiply
      // driven variable to the simulators -- presented on the arrays by continuous assignment)
      logic           ov;             // out_valid[o]
      logic [FW-1:0]  of;             // out_flit[o]
      logic [4:0]     plo, phi;       // ptr_lo[o], ptr_hi[o]
      logic [WCW-1:0] wc;             // wcnt[o]
      logic           ws;             // wsat[o]
      assign out_valid[o] = ov;
      assign out_flit[o]  = of;
      assign ptr_lo[o]    = plo;
      assign ptr_hi[o]    = phi;
      assign wcnt[o]      = wc;
      assign wsat[o]      = ws;
      always_ff @(posedge gclk or negedge rst_n) begin
        if (!rst_n) begin
          ov <= 1'b0; of <= '0;
          plo <= 5'b00001; phi <= 5'b00001;                    // input 0 first (the old rr = 4)
          wc <= '0; ws <= 1'b0;
        end else if (fire[o]) begin
          ov <= !dv_clear_o;
          of <= gflit[o] ^ (dv_flip_o ? dv_mask : FW'(0));
          if (use_hi[o]) phi <= {gnt[o][3:0], gnt[o][4]};      // the input after the granted one, in its class
          else           plo <= {gnt[o][3:0], gnt[o][4]};
          if (rq_lo[o] == 5'd0 || !use_hi[o]) begin            // no low-class request waits, or one is granted
            wc <= '0; ws <= 1'b0;
          end else if (!ws) begin                               // a high-class grant while a low-class request waits
            wc <= wc + WCW'(1);
            ws <= (wc == WCW'(PRIO_N - 1));
          end
        end else if (ov && out_ready[o]) begin
          ov <= 1'b0;
        end
      end
    end
  endgenerate
endmodule
