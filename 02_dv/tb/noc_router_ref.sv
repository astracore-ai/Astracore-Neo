/* verilator lint_off DECLFILENAME */        // two modules, the reference and the pair, in one file by design
// noc_router_ref.sv -- the drop-0.40 router (rtl/noc_router.sv as it was before drop 0.41), kept as the golden model of the
//   arbitration order: `(rr + 1 + k) % 5` priority chain per output, parity reduced at the FIFO head. Never to change.
//   noc_router_pair wires the reference and rtl/noc_router.sv to the same inputs (hooks included) so that a test can compare
//   every output cycle by cycle (sim/run_noc.py N3): drop 0.41's one-hot arbitration with the parity stored at FIFO entry
//   must be the same function, grant for grant.
module noc_router_ref #(
  parameter int XW = 3,
  parameter int YW = 3,
  parameter int DW = 32,
  parameter int FW = 1 + 2 * (XW + YW) + 8 + DW,
  parameter int DV_HOOKS = 0
)(
  input  logic          clk,
  input  logic          rst_n,
  input  logic [XW-1:0] my_x,
  input  logic [YW-1:0] my_y,
  input  logic          in_valid  [5],
  input  logic [FW-1:0] in_flit   [5],
  output logic          in_ready  [5],
  output logic          out_valid [5],
  output logic [FW-1:0] out_flit  [5],
  input  logic          out_ready [5],
  input  logic          err_clear,
  output logic          parity_err_sticky,
  input  logic          dv_flit_flip,
  input  logic          dv_valid_clear,
  input  logic [2:0]    dv_port,
  input  logic [FW-1:0] dv_mask
);
  logic [FW-1:0] fifo [5][2];
  logic [1:0]    count [5];
  logic          rd    [5];
  logic          wr    [5];
  logic [FW-1:0] head  [5];
  logic          head_valid [5];
  logic [2:0]    req   [5];
  logic          head_bad [5];

  generate
    for (genvar i = 0; i < 5; i++) begin : g_in
      assign in_ready[i]   = (count[i] != 2'd2);
      assign head[i]       = fifo[i][rd[i]];
      assign head_valid[i] = (count[i] != 2'd0);
      assign head_bad[i]   = (^head[i]) != 1'b0;
      logic [XW-1:0] dx;
      logic [YW-1:0] dy;
      assign dx = head[i][FW-2-YW -: XW];
      assign dy = head[i][FW-2 -: YW];
      always_comb begin
        if (dx > my_x)      req[i] = 3'd1;
        else if (dx < my_x) req[i] = 3'd3;
        else if (dy > my_y) req[i] = 3'd2;
        else if (dy < my_y) req[i] = 3'd0;
        else                req[i] = 3'd4;
      end
    end
  endgenerate

  logic [2:0] rr   [5];
  logic [2:0] sel  [5];
  logic       fire [5];
  logic       pop  [5];
  logic       drop [5];

  always_comb begin
    for (int o = 0; o < 5; o++) begin
      sel[o] = 3'd5;
      for (int k = 0; k < 5; k++) begin
        logic [2:0] c;
        c = 3'((32'(rr[o]) + 1 + k) % 5);
        if (sel[o] == 3'd5 && head_valid[c] && !head_bad[c] && req[c] == 3'(o)) sel[o] = c;
      end
      fire[o] = (sel[o] != 3'd5) && (!out_valid[o] || out_ready[o]);
    end
    for (int i = 0; i < 5; i++) begin
      drop[i] = head_valid[i] && head_bad[i];
      pop[i]  = drop[i] || (fire[req[i]] && (sel[req[i]] == 3'(i)));
    end
  end

  logic          dv_flip_o  [5];
  logic          dv_clear_o [5];
  generate
    for (genvar o = 0; o < 5; o++) begin : g_dv
      assign dv_flip_o[o]  = (DV_HOOKS != 0) && dv_flit_flip  && (dv_port == 3'(o));
      assign dv_clear_o[o] = (DV_HOOKS != 0) && dv_valid_clear && (dv_port == 3'(o));
    end
  endgenerate

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int i = 0; i < 5; i++) begin
        count[i] <= '0; rd[i] <= 1'b0; wr[i] <= 1'b0; rr[i] <= 3'd4;
        out_valid[i] <= 1'b0; out_flit[i] <= '0;
      end
      parity_err_sticky <= 1'b0;
    end else begin
      for (int i = 0; i < 5; i++) begin
        if (in_valid[i] && in_ready[i]) begin
          fifo[i][wr[i]] <= in_flit[i];
          wr[i] <= !wr[i];
        end
        if (pop[i]) rd[i] <= !rd[i];
        if ((in_valid[i] && in_ready[i]) && !pop[i])      count[i] <= count[i] + 2'd1;
        else if (!(in_valid[i] && in_ready[i]) && pop[i]) count[i] <= count[i] - 2'd1;
        if (fire[i]) begin
          out_valid[i] <= !dv_clear_o[i];
          out_flit[i]  <= head[sel[i]] ^ (dv_flip_o[i] ? dv_mask : FW'(0));
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


// the reference and the design under the same inputs; the outputs side by side (n_* the design, r_* the reference)
module noc_router_pair #(
  parameter int XW = 3,
  parameter int YW = 3,
  parameter int DW = 16,
  parameter int FW = 1 + 2 * (XW + YW) + 8 + DW,
  parameter int DV_HOOKS = 1
)(
  input  logic          clk,
  input  logic          rst_n,
  input  logic [XW-1:0] my_x,
  input  logic [YW-1:0] my_y,
  input  logic          in_valid  [5],
  input  logic [FW-1:0] in_flit   [5],
  input  logic          out_ready [5],
  input  logic          err_clear,
  input  logic          dv_flit_flip,
  input  logic          dv_valid_clear,
  input  logic [2:0]    dv_port,
  input  logic [FW-1:0] dv_mask,
  output logic          n_in_ready  [5],
  output logic          n_out_valid [5],
  output logic [FW-1:0] n_out_flit  [5],
  output logic          n_perr,
  output logic          r_in_ready  [5],
  output logic          r_out_valid [5],
  output logic [FW-1:0] r_out_flit  [5],
  output logic          r_perr
);
  logic [4:0] n_stage_en;                    // drop 0.43: the new router's priority map all zero (one class: the order under test),
  logic       unused_stage_en;               // its output stages' clock enables unobserved
  assign unused_stage_en = ^n_stage_en;
  noc_router #(.XW(XW), .YW(YW), .DW(DW), .FW(FW), .DV_HOOKS(DV_HOOKS)) u_new (
    .clk(clk), .rst_n(rst_n), .my_x(my_x), .my_y(my_y), .prio(16'd0),
    .in_valid(in_valid), .in_flit(in_flit), .in_ready(n_in_ready),
    .out_valid(n_out_valid), .out_flit(n_out_flit), .out_ready(out_ready),
    .err_clear(err_clear), .parity_err_sticky(n_perr), .stage_en(n_stage_en),
    .dv_flit_flip(dv_flit_flip), .dv_valid_clear(dv_valid_clear), .dv_port(dv_port), .dv_mask(dv_mask));
  noc_router_ref #(.XW(XW), .YW(YW), .DW(DW), .FW(FW), .DV_HOOKS(DV_HOOKS)) u_ref (
    .clk(clk), .rst_n(rst_n), .my_x(my_x), .my_y(my_y),
    .in_valid(in_valid), .in_flit(in_flit), .in_ready(r_in_ready),
    .out_valid(r_out_valid), .out_flit(r_out_flit), .out_ready(out_ready),
    .err_clear(err_clear), .parity_err_sticky(r_perr),
    .dv_flit_flip(dv_flit_flip), .dv_valid_clear(dv_valid_clear), .dv_port(dv_port), .dv_mask(dv_mask));
endmodule
