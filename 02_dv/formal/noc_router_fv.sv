// noc_router_fv.sv -- formal harness for rtl/noc_router (drop 0.41) at XW = YW = 3 and a payload of DW = 8 bits (no
//   property depends on the payload width), DV_HOOKS = 0 as in silicon. Every input is free every cycle -- the five
//   input ports (valid and flit, no protocol assumed), the five out_ready, err_clear -- and the strapped coordinates
//   (my_x, my_y) are free constants. Asserted every cycle after reset:
//     FIFO        count <= 2, in_ready == (count != 2), the write pointer one past the valid entries
//     XY          a flit in output register E has dst_x > my_x, W dst_x < my_x, S dst_x == my_x && dst_y > my_y,
//                 N dst_x == my_x && dst_y < my_y, L dst == (my_x, my_y)
//     parity      no output register ever holds a flit with odd parity; a FIFO head with odd parity is popped in
//                 the cycle it is the head, and parity_err_sticky is exactly "a bad head was dropped since the last
//                 err_clear" (err_clear has priority)
//     no invention  a flit leaves only if a flit of the same value was accepted before: for a free constant value F
//                 the harness remembers whether F was ever accepted, and every FIFO entry and output register holding
//                 F implies it (F free: every value)
//     one token   one accepted good-parity flit, picked by free inputs at its acceptance, is followed by position:
//                 while in its FIFO slot that slot is valid and holds it; the grant that pops it loads exactly one
//                 output register and a grant of its input always pops it (nothing lost, nothing left behind as a
//                 copy); in the output register it is unchanged until it leaves, and that register is its XY port
//     fairness    an input whose head requests output o is granted within 5 fires of o: at most 4 fires of o go to
//                 other inputs while it waits (with the round-robin distance as the inductive helper)
//   Internal names read: fifo, count, rd, wr (the FIFO state), rr (the round-robin pointer), fire, sel, pop (the grants).
module noc_router_fv #(
  parameter int XW = 3,
  parameter int YW = 3,
  parameter int DW = 8,
  parameter int FW = 1 + 2 * (XW + YW) + 8 + DW
)(
  input  logic            clk,
  input  logic [4:0]      in_valid_v,
  input  logic [5*FW-1:0] in_flit_v,            // port i at [FW*i +: FW]
  input  logic [4:0]      out_ready_v,
  input  logic            err_clear,
  input  logic            pick,                 // take the flit accepted on input pick_i as the token
  input  logic [2:0]      pick_i
);
  logic rst_n = 1'b0;
  always_ff @(posedge clk) rst_n <= 1'b1;

  // free constants: the coordinates the mesh straps, the value watched for invention
  logic [XW-1:0] my_x;
  logic [YW-1:0] my_y;
  logic [FW-1:0] F;
  always_ff @(posedge clk) begin
    my_x <= my_x;
    my_y <= my_y;
    F    <= F;
  end

  logic          in_valid  [5];
  logic [FW-1:0] in_flit   [5];
  logic          in_ready  [5];
  logic          out_valid [5];
  logic [FW-1:0] out_flit  [5];
  logic          out_ready [5];
  logic          sticky;
  generate
    for (genvar i = 0; i < 5; i++) begin : g_port
      assign in_valid[i]  = in_valid_v[i];
      assign in_flit[i]   = in_flit_v[FW*i +: FW];
      assign out_ready[i] = out_ready_v[i];
    end
  endgenerate
  noc_router #(.XW(XW), .YW(YW), .DW(DW), .DV_HOOKS(0)) u_dut (
    .clk(clk), .rst_n(rst_n), .my_x(my_x), .my_y(my_y),
    .in_valid(in_valid), .in_flit(in_flit), .in_ready(in_ready),
    .out_valid(out_valid), .out_flit(out_flit), .out_ready(out_ready),
    .err_clear(err_clear), .parity_err_sticky(sticky),
    .dv_flit_flip(1'b0), .dv_valid_clear(1'b0), .dv_port(3'd0), .dv_mask({FW{1'b0}}));

  // flit fields
  function automatic logic [XW-1:0] fdx(input logic [FW-1:0] f);
    return f[FW-2-YW -: XW];
  endfunction
  function automatic logic [YW-1:0] fdy(input logic [FW-1:0] f);
    return f[FW-2 -: YW];
  endfunction
  // the XY port of a flit at this router: N 0, E 1, S 2, W 3, L 4 (the specification of the routing relation)
  function automatic logic [2:0] xy_port(input logic [FW-1:0] f);
    if (fdx(f) > my_x)      return 3'd1;
    else if (fdx(f) < my_x) return 3'd3;
    else if (fdy(f) > my_y) return 3'd2;
    else if (fdy(f) < my_y) return 3'd0;
    else                    return 3'd4;
  endfunction

  // the FIFO heads, from the FIFO state
  logic [FW-1:0] hd   [5];
  logic          hv   [5];
  logic          hbad [5];
  logic          any_drop;
  always_comb begin
    any_drop = 1'b0;
    for (int i = 0; i < 5; i++) begin
      hd[i]   = u_dut.fifo[i][u_dut.rd[i]];
      hv[i]   = (u_dut.count[i] != 2'd0);
      hbad[i] = (^hd[i]) != 1'b0;
      if (hv[i] && hbad[i]) any_drop = 1'b1;
    end
  end

  // parity_err_sticky's model
  logic exp_sticky;
  always_ff @(posedge clk) begin
    if (!rst_n)         exp_sticky <= 1'b0;
    else if (err_clear) exp_sticky <= 1'b0;
    else if (any_drop)  exp_sticky <= 1'b1;
  end

  // no invention: was F ever accepted?
  logic seen_f;
  logic acc_f;
  always_comb begin
    acc_f = 1'b0;
    for (int i = 0; i < 5; i++) if (in_valid[i] && in_ready[i] && in_flit[i] == F) acc_f = 1'b1;
  end
  always_ff @(posedge clk) begin
    if (!rst_n)     seen_f <= 1'b0;
    else if (acc_f) seen_f <= 1'b1;
  end

  // the token: where the picked flit is (0 not picked yet, 1 FIFO tk_i slot tk_s, 2 output register tk_o, 3 left)
  logic [1:0]    tk_st;
  logic [2:0]    tk_i, tk_o;
  logic          tk_s;
  logic [FW-1:0] tk_v;
  logic          take, tk_head, tk_pop, tk_dep;
  logic [4:0]    tk_g;                              // outputs whose grant selects the token's input this cycle
  logic [2:0]    tk_go;                             // the lowest of them
  always_comb begin
    take    = (tk_st == 2'd0) && pick && (pick_i < 3'd5) && in_valid[pick_i] && in_ready[pick_i] &&
              ((^in_flit[pick_i]) == 1'b0);
    tk_head = (tk_st == 2'd1) && (u_dut.count[tk_i] != 2'd0) && (u_dut.rd[tk_i] == tk_s);
    tk_pop  = tk_head && u_dut.pop[tk_i];
    tk_dep  = (tk_st == 2'd2) && out_valid[tk_o] && out_ready[tk_o];
    tk_go   = 3'd0;
    for (int o = 4; o >= 0; o--) begin
      tk_g[o] = u_dut.fire[o] && (u_dut.sel[o] == tk_i);
      if (tk_g[o]) tk_go = 3'(o);
    end
  end
  always_ff @(posedge clk) begin
    if (!rst_n) tk_st <= 2'd0;
    else if (take) begin
      tk_st <= 2'd1; tk_i <= pick_i; tk_s <= u_dut.wr[pick_i]; tk_v <= in_flit[pick_i];
    end else if (tk_pop) begin
      tk_st <= 2'd2; tk_o <= tk_go;
    end else if (tk_dep) begin
      tk_st <= 2'd3;
    end
  end

  // fairness: fires of output o that went elsewhere while input i's head requested o
  logic [2:0] wt [5][5];
  logic       rq [5][5];                            // rq[o][i]: input i's head is valid, good and requests o
  always_comb begin
    for (int o = 0; o < 5; o++)
      for (int i = 0; i < 5; i++)
        rq[o][i] = hv[i] && !hbad[i] && (xy_port(hd[i]) == 3'(o));
  end
  always_ff @(posedge clk) begin
    for (int o = 0; o < 5; o++)
      for (int i = 0; i < 5; i++) begin
        if (!rst_n || !rq[o][i] || u_dut.pop[i]) wt[o][i] <= 3'd0;
        else if (u_dut.fire[o])                  wt[o][i] <= wt[o][i] + 3'd1;
      end
  end
  function automatic logic [2:0] rr_dist(input logic [2:0] r, input logic [2:0] i);   // i's place in the search after r: 1..5
    return (i > r) ? 3'(i - r) : 3'(i + 3'd5 - r);
  endfunction

  always_comb begin
    if (rst_n) begin
      for (int i = 0; i < 5; i++) begin
        // FIFO
        assert (u_dut.count[i] <= 2'd2);
        assert (in_ready[i] == (u_dut.count[i] != 2'd2));
        assert (u_dut.wr[i] == (u_dut.rd[i] ^ (u_dut.count[i] == 2'd1)));
        // parity: never in an output register
        if (out_valid[i]) assert ((^out_flit[i]) == 1'b0);
        // no invention: every copy of F inside was accepted
        if (u_dut.count[i] != 2'd0 && u_dut.fifo[i][u_dut.rd[i]] == F) assert (seen_f);
        if (u_dut.count[i] == 2'd2 && u_dut.fifo[i][!u_dut.rd[i]] == F) assert (seen_f);
        if (out_valid[i] && out_flit[i] == F) assert (seen_f);
      end
      // XY consistency of the output registers
      if (out_valid[1]) assert (fdx(out_flit[1]) > my_x);                                   // E
      if (out_valid[3]) assert (fdx(out_flit[3]) < my_x);                                   // W
      if (out_valid[2]) assert (fdx(out_flit[2]) == my_x && fdy(out_flit[2]) > my_y);       // S
      if (out_valid[0]) assert (fdx(out_flit[0]) == my_x && fdy(out_flit[0]) < my_y);       // N
      if (out_valid[4]) assert (fdx(out_flit[4]) == my_x && fdy(out_flit[4]) == my_y);      // L
      // the sticky is exactly its model
      assert (sticky == exp_sticky);
      // the token
      if (tk_st == 2'd1) begin
        assert (tk_i < 3'd5 && (^tk_v) == 1'b0);
        assert ((u_dut.count[tk_i] != 2'd0 && u_dut.rd[tk_i] == tk_s) || (u_dut.count[tk_i] == 2'd2));
        assert (u_dut.fifo[tk_i][tk_s] == tk_v);
        if (tk_pop)  assert (tk_g != 5'd0 && (tk_g & (tk_g - 5'd1)) == 5'd0);   // popped: into exactly one output
        if (tk_head && tk_g != 5'd0) assert (tk_pop);                          // granted: popped, no copy left
      end
      if (tk_st == 2'd2) begin
        assert (tk_o < 3'd5 && (^tk_v) == 1'b0);
        assert (out_valid[tk_o] && out_flit[tk_o] == tk_v);
        assert (tk_o == xy_port(tk_v));
      end
      // fairness
      for (int o = 0; o < 5; o++) begin
        assert (u_dut.rr[o] <= 3'd4);
        for (int i = 0; i < 5; i++) begin
          assert (wt[o][i] <= 3'd4);
          if (rq[o][i]) assert (4'(wt[o][i]) + 4'(rr_dist(u_dut.rr[o], 3'(i))) <= 4'd5);
        end
      end
    end
  end
  // a bad head is popped in the cycle it is the head
  logic       prev_valid = 1'b0;
  logic [4:0] prev_drop;
  logic [4:0] prev_rd;
  always_ff @(posedge clk) begin
    prev_valid <= rst_n;
    for (int i = 0; i < 5; i++) begin
      prev_drop[i] <= hv[i] && hbad[i];
      prev_rd[i]   <= u_dut.rd[i];
    end
  end
  always_comb begin
    if (rst_n && prev_valid)
      for (int i = 0; i < 5; i++) if (prev_drop[i]) assert (u_dut.rd[i] != prev_rd[i]);
  end
endmodule
