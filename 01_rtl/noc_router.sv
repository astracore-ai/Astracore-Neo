// noc_router.sv -- 2D-mesh router, XY routing, single-flit packets, parity-checked (drop 0.4).
//   Ports 0..4 = N, E, S, W, L (local). Flit layout, MSB first:
//     parity (1) | dst_y (YW) | dst_x (XW) | src_y (YW) | src_x (XW) | seq (8) | payload (DW)
//   Each input has a 2-entry FIFO; each output a registered stage with ready/valid handshake.
//   Routing: X first (E/W), then Y (S = +y, N = -y), then local. Round-robin arbitration per
//   output among the inputs whose head flit requests it. A flit whose parity is wrong is
//   dropped at the input and parity_err_sticky is raised: the sender's end-to-end sequence
//   check and timeout recover the packet, the error pin reports the fault.
module noc_router #(
  parameter int XW = 3,
  parameter int YW = 3,
  parameter int DW = 32,
  parameter int FW = 1 + 2 * (XW + YW) + 8 + DW,
  parameter int MY_X = 0,
  parameter int MY_Y = 0
)(
  input  logic          clk,
  input  logic          rst_n,
  input  logic          in_valid  [5],
  input  logic [FW-1:0] in_flit   [5],
  output logic          in_ready  [5],
  output logic          out_valid [5],
  output logic [FW-1:0] out_flit  [5],
  input  logic          out_ready [5],
  input  logic          err_clear,
  output logic          parity_err_sticky
);
  // ---- input FIFOs (depth 2) ----
  logic [FW-1:0] fifo [5][2];
  logic [1:0]    count [5];
  logic          rd    [5];
  logic          wr    [5];
  logic [FW-1:0] head  [5];
  logic          head_valid [5];
  logic [2:0]    req   [5];     // requested output port of the head flit (0..4)
  logic          head_bad [5];  // parity failure on the head flit

  generate
    for (genvar i = 0; i < 5; i++) begin : g_in
      assign in_ready[i]   = (count[i] != 2'd2);
      assign head[i]       = fifo[i][rd[i]];
      assign head_valid[i] = (count[i] != 2'd0);
      assign head_bad[i]   = (^head[i]) != 1'b0;      // even parity over the whole flit
      logic [XW-1:0] dx;
      logic [YW-1:0] dy;
      assign dx = head[i][FW-2-YW -: XW];
      assign dy = head[i][FW-2 -: YW];
      always_comb begin
        if (dx > MY_X)      req[i] = 3'd1;            // E
        else if (dx < MY_X) req[i] = 3'd3;            // W
        else if (dy > MY_Y) req[i] = 3'd2;            // S
        else if (dy < MY_Y) req[i] = 3'd0;            // N
        else                req[i] = 3'd4;            // local
      end
    end
  endgenerate

  // ---- per-output round-robin arbitration ----
  logic [2:0] rr   [5];        // last granted input per output
  logic [2:0] sel  [5];        // selected input this cycle (5 = none)
  logic       fire [5];        // transfer into output register o this cycle
  logic       pop  [5];        // input i is popped this cycle
  logic       drop [5];        // input i head dropped for bad parity

  always_comb begin
    for (int o = 0; o < 5; o++) begin
      sel[o] = 3'd5;
      for (int k = 0; k < 5; k++) begin
        int c;
        c = (rr[o] + 1 + k) % 5;
        if (sel[o] == 3'd5 && head_valid[c] && !head_bad[c] && req[c] == o) sel[o] = c[2:0];
      end
      fire[o] = (sel[o] != 3'd5) && (!out_valid[o] || out_ready[o]);
    end
    for (int i = 0; i < 5; i++) begin
      drop[i] = head_valid[i] && head_bad[i];
      pop[i]  = drop[i] || (fire[req[i]] && (sel[req[i]] == i));
    end
  end

  // ---- state ----
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int i = 0; i < 5; i++) begin
        count[i] <= '0; rd[i] <= 1'b0; wr[i] <= 1'b0; rr[i] <= 3'd4;
        out_valid[i] <= 1'b0; out_flit[i] <= '0;
      end
      parity_err_sticky <= 1'b0;
    end else begin
      for (int i = 0; i < 5; i++) begin
        // input FIFO push/pop
        if (in_valid[i] && in_ready[i]) begin
          fifo[i][wr[i]] <= in_flit[i];
          wr[i] <= !wr[i];
        end
        if (pop[i]) rd[i] <= !rd[i];
        if ((in_valid[i] && in_ready[i]) && !pop[i])      count[i] <= count[i] + 2'd1;
        else if (!(in_valid[i] && in_ready[i]) && pop[i]) count[i] <= count[i] - 2'd1;
        // output stage
        if (fire[i]) begin
          out_valid[i] <= 1'b1;
          out_flit[i]  <= head[sel[i]];
          rr[i]        <= sel[i];
        end else if (out_valid[i] && out_ready[i]) begin
          out_valid[i] <= 1'b0;
        end
        if (drop[i]) parity_err_sticky <= 1'b1;
      end
      if (err_clear) parity_err_sticky <= 1'b0;
    end
  end
endmodule
