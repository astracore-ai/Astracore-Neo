// noc_router.sv -- 2D-mesh router, XY routing, single-flit packets, parity-checked (drop 0.4).
//   Ports 0..4 = N, E, S, W, L (local). Flit layout, MSB first:
//     parity (1) | dst_y (YW) | dst_x (XW) | src_y (YW) | src_x (XW) | seq (8) | payload (DW)
//   Each input has a 2-entry FIFO; each output a registered stage with ready/valid handshake.
//   Routing: X first (E/W), then Y (S = +y, N = -y), then local. Round-robin arbitration per
//   output among the inputs whose head flit requests it. A flit whose parity is wrong is
//   dropped at the input and parity_err_sticky is raised: the sender's end-to-end sequence
//   check and timeout recover the packet, the error pin reports the fault.
//   Drop 0.42 (the same function, cycle for cycle; the arbitration was `(rr + 1 + k) % 5` in a priority chain behind a
//   1,077-bit parity reduce on the FIFO head, 2.57 ns on the ASAP7 SS proxy):
//   - the parity is computed when a flit is pushed (^in_flit, on the input side) and stored with the entry (fbad), so
//     head_bad is a 2:1 mux of two stored bits;
//   - the request is one-hot from the XY compares (rq[o][i]: head i valid, good parity, routed to o); the rotating
//     priority of output o is a one-hot pointer ptr[o] = the input after the last grant; the grant is the first set bit
//     of rq & (the bits at and above ptr) if any, else the first set bit of rq (the double-vector first-set), one-hot;
//     the output register loads the head through an AND-OR of the grant. ptr resets to input 0 (the old rr = 4), so the
//     order of grants is the old one.
module noc_router #(
  parameter int XW = 3,
  parameter int YW = 3,
  parameter int DW = 32,
  parameter int FW = 1 + 2 * (XW + YW) + 8 + DW,
  parameter int DV_HOOKS = 0     // 1: the output-register fault hooks below exist (drop 0.35)
)(
  input  logic          clk,
  input  logic          rst_n,
  input  logic [XW-1:0] my_x,          // this router's mesh coordinates (drop 0.35: ports strapped by the mesh, not parameters)
  input  logic [YW-1:0] my_y,
  input  logic          in_valid  [5],
  input  logic [FW-1:0] in_flit   [5],
  output logic          in_ready  [5],
  output logic          out_valid [5],
  output logic [FW-1:0] out_flit  [5],
  input  logic          out_ready [5],
  input  logic          err_clear,
  output logic          parity_err_sticky,
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
  // ---- input FIFOs (depth 2), each entry with the parity of the flit computed at the push ----
  logic [FW-1:0] fifo [5][2];
  logic          fbad [5][2];   // the entry's parity failed (odd), from ^in_flit at the push
  logic [1:0]    count [5];
  logic          rd    [5];
  logic          wr    [5];
  logic [FW-1:0] head  [5];
  logic          head_valid [5];
  logic          head_bad [5];  // parity failure on the head flit
  logic [4:0]    req_oh [5];    // requested output port of the head flit, one-hot (bit o = port o)
  logic          in_bad [5];    // parity of the flit being pushed

  generate
    for (genvar i = 0; i < 5; i++) begin : g_in
      assign in_ready[i]   = (count[i] != 2'd2);
      assign head[i]       = fifo[i][rd[i]];
      assign head_valid[i] = (count[i] != 2'd0);
      assign head_bad[i]   = fbad[i][rd[i]];
      assign in_bad[i]     = (^in_flit[i]) != 1'b0;   // even parity over the whole flit
      logic [XW-1:0] dx;
      logic [YW-1:0] dy;
      logic          xe, ye;
      assign dx = head[i][FW-2-YW -: XW];
      assign dy = head[i][FW-2 -: YW];
      assign xe = (dx == my_x);
      assign ye = (dy == my_y);
      assign req_oh[i] = {xe && ye,                   // 4 local
                          dx < my_x,                  // 3 W
                          xe && (dy > my_y),          // 2 S
                          dx > my_x,                  // 1 E
                          xe && (dy < my_y)};         // 0 N
    end
  endgenerate

  // ---- per-output round-robin arbitration: one-hot rotating priority ----
  logic [4:0] ptr    [5];      // output o: one-hot, the input after the last grant (the first in priority)
  logic [4:0] ge     [5];      // output o: the inputs at and above ptr (thermometer)
  logic [4:0] rq     [5];      // output o: the inputs whose head may go to o
  logic [4:0] masked [5];      // output o: rq at and above ptr
  logic [4:0] gnt    [5];      // output o: the granted input, one-hot (0 = none)
  logic       fire   [5];      // transfer into output register o this cycle
  logic       pop    [5];      // input i is popped this cycle
  logic       drop   [5];      // input i head dropped for bad parity

  generate
    for (genvar o = 0; o < 5; o++) begin : g_ge
      assign ge[o] = {|ptr[o], |ptr[o][3:0], |ptr[o][2:0], |ptr[o][1:0], ptr[o][0]};
    end
  endgenerate

  always_comb begin
    for (int o = 0; o < 5; o++) begin
      for (int i = 0; i < 5; i++) rq[o][i] = head_valid[i] && !head_bad[i] && req_oh[i][o];
      masked[o] = rq[o] & ge[o];
      for (int i = 0; i < 5; i++) begin               // the first set bit of masked, else of rq
        gnt[o][i] = (masked[o] != 5'd0) ? (masked[o][i] && ((masked[o] & ((5'd1 << i) - 5'd1)) == 5'd0))
                                        : (rq[o][i] && ((rq[o] & ((5'd1 << i) - 5'd1)) == 5'd0));
      end
      fire[o] = (gnt[o] != 5'd0) && (!out_valid[o] || out_ready[o]);
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

  // the hooks, as terms of the output register's load (zero when DV_HOOKS = 0: the terms vanish with the constant)
  logic          dv_flip_o  [5];     // output o loads its flit XOR dv_mask
  logic          dv_clear_o [5];     // output o loads its flit without the valid
  generate
    for (genvar o = 0; o < 5; o++) begin : g_dv
      assign dv_flip_o[o]  = (DV_HOOKS != 0) && dv_flit_flip  && (dv_port == 3'(o));
      assign dv_clear_o[o] = (DV_HOOKS != 0) && dv_valid_clear && (dv_port == 3'(o));
    end
  endgenerate

  // ---- state ----
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int i = 0; i < 5; i++) begin
        count[i] <= '0; rd[i] <= 1'b0; wr[i] <= 1'b0; ptr[i] <= 5'b00001;    // input 0 first (the old rr = 4)
        out_valid[i] <= 1'b0; out_flit[i] <= '0;
      end
      parity_err_sticky <= 1'b0;
    end else begin
      for (int i = 0; i < 5; i++) begin
        // input FIFO push/pop
        if (in_valid[i] && in_ready[i]) begin
          fifo[i][wr[i]] <= in_flit[i];
          fbad[i][wr[i]] <= in_bad[i];
          wr[i] <= !wr[i];
        end
        if (pop[i]) rd[i] <= !rd[i];
        if ((in_valid[i] && in_ready[i]) && !pop[i])      count[i] <= count[i] + 2'd1;
        else if (!(in_valid[i] && in_ready[i]) && pop[i]) count[i] <= count[i] - 2'd1;
        // output stage
        if (fire[i]) begin
          out_valid[i] <= !dv_clear_o[i];
          out_flit[i]  <= gflit[i] ^ (dv_flip_o[i] ? dv_mask : FW'(0));
          ptr[i]       <= {gnt[i][3:0], gnt[i][4]};        // the input after the granted one
        end else if (out_valid[i] && out_ready[i]) begin
          out_valid[i] <= 1'b0;
        end
        if (drop[i]) parity_err_sticky <= 1'b1;
      end
      if (err_clear) parity_err_sticky <= 1'b0;
    end
  end
endmodule
