// noc_mesh.sv -- NX x NY mesh of noc_router, local ports exposed, edges terminated (drop 0.4).
module noc_mesh #(
  parameter int NX = 3,
  parameter int NY = 3,
  parameter int XW = 3,
  parameter int YW = 3,
  parameter int DW = 32,
  parameter int FW = 1 + 2 * (XW + YW) + 8 + DW
)(
  input  logic          clk,
  input  logic          rst_n,
  input  logic          l_in_valid  [NX*NY],
  input  logic [FW-1:0] l_in_flit   [NX*NY],
  output logic          l_in_ready  [NX*NY],
  output logic          l_out_valid [NX*NY],
  output logic [FW-1:0] l_out_flit  [NX*NY],
  input  logic          l_out_ready [NX*NY],
  input  logic          err_clear,
  output logic          parity_err  [NX*NY]
);
  // link signals indexed [node][port]: what node n drives OUT of port p, and what it accepts
  logic          o_valid [NX*NY][5];
  logic [FW-1:0] o_flit  [NX*NY][5];
  logic          o_ready [NX*NY][5];
  logic          i_valid [NX*NY][5];
  logic [FW-1:0] i_flit  [NX*NY][5];
  logic          i_ready [NX*NY][5];

  generate
    for (genvar y = 0; y < NY; y++) begin : g_y
      for (genvar x = 0; x < NX; x++) begin : g_x
        localparam int n = y * NX + x;
        noc_router #(.XW(XW), .YW(YW), .DW(DW), .FW(FW), .MY_X(x), .MY_Y(y)) u_r (
          .clk(clk), .rst_n(rst_n),
          .in_valid(i_valid[n]), .in_flit(i_flit[n]), .in_ready(i_ready[n]),
          .out_valid(o_valid[n]), .out_flit(o_flit[n]), .out_ready(o_ready[n]),
          .err_clear(err_clear), .parity_err_sticky(parity_err[n]));
        // local port 4
        assign i_valid[n][4]   = l_in_valid[n];
        assign i_flit[n][4]    = l_in_flit[n];
        assign l_in_ready[n]   = i_ready[n][4];
        assign l_out_valid[n]  = o_valid[n][4];
        assign l_out_flit[n]   = o_flit[n][4];
        assign o_ready[n][4]   = l_out_ready[n];
        // north input (port 0) <- SOUTH output (port 2) of the node above
        if (y > 0) begin : g_n_in
          assign i_valid[n][0]    = o_valid[n-NX][2];
          assign i_flit[n][0]     = o_flit[n-NX][2];
          assign o_ready[n-NX][2] = i_ready[n][0];
        end else begin : g_n_edge
          assign i_valid[n][0] = 1'b0;
          assign i_flit[n][0]  = '0;
          assign o_ready[n][0] = 1'b1;       // north output of a top-row node: no link, sink
        end
        // south input (port 2) <- NORTH output (port 0) of the node below
        if (y < NY-1) begin : g_s_in
          assign i_valid[n][2]    = o_valid[n+NX][0];
          assign i_flit[n][2]     = o_flit[n+NX][0];
          assign o_ready[n+NX][0] = i_ready[n][2];
        end else begin : g_s_edge
          assign i_valid[n][2] = 1'b0;
          assign i_flit[n][2]  = '0;
          assign o_ready[n][2] = 1'b1;
        end
        // west input (port 3) <- EAST output (port 1) of the node to the left
        if (x > 0) begin : g_w_in
          assign i_valid[n][3]   = o_valid[n-1][1];
          assign i_flit[n][3]    = o_flit[n-1][1];
          assign o_ready[n-1][1] = i_ready[n][3];
        end else begin : g_w_edge
          assign i_valid[n][3] = 1'b0;
          assign i_flit[n][3]  = '0;
          assign o_ready[n][3] = 1'b1;
        end
        // east input (port 1) <- WEST output (port 3) of the node to the right
        if (x < NX-1) begin : g_e_in
          assign i_valid[n][1]   = o_valid[n+1][3];
          assign i_flit[n][1]    = o_flit[n+1][3];
          assign o_ready[n+1][3] = i_ready[n][1];
        end else begin : g_e_edge
          assign i_valid[n][1] = 1'b0;
          assign i_flit[n][1]  = '0;
          assign o_ready[n][1] = 1'b1;
        end
      end
    end
  endgenerate
endmodule
