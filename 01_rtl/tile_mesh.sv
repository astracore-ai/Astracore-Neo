// tile_mesh.sv -- NX x NY neo_tile mesh (drop 0.7); host ports exposed per tile, edges terminated.
module tile_mesh #(
  parameter int NX = 2, NY = 2, XW = 2, YW = 2,
  parameter int WPF = 1,                     // words per link flit (drop 0.25): 32 = the 1024-bit links
  parameter int DWL = 32 + 32 * WPF,
  parameter int FW = 1 + 2 * (XW + YW) + 8 + DWL,     // the link flit
  parameter int ROWS = 16, COLS = 8, PW = 32,
  parameter int ACC_ROWS = 64, ABUF_DEPTH = 256, WBUF_DEPTH = 512, BANK_DEPTH = 4096,
  parameter int FETCH_TIMEOUT = 1048575,     // silicon default (drop 0.24): 2^20 - 1, see tile_nic.sv
  parameter int BIST_WORDS = BANK_DEPTH,
  parameter int DV_HOOKS = 0,                // 1: the testbench hooks of the tiles exist (drop 0.35); 0: tied off
  parameter int NW = (NX * NY > 1) ? $clog2(NX * NY) : 1,   // node index width of the hooks
  parameter int BAW = $clog2(BANK_DEPTH)
)(
  input  logic          clk,
  input  logic          rst_n,
  input  logic          h_we    [NX*NY],
  input  logic          h_re    [NX*NY],
  input  logic [7:0]    h_addr  [NX*NY],
  input  logic [31:0]   h_wdata [NX*NY],
  output logic [31:0]   h_rdata [NX*NY],
  output logic          err_pin [NX*NY],
  output logic          prog_done  [NX*NY],
  output logic          drain_busy [NX*NY],
  output logic          done [NX*NY],
  output logic          parity_err [NX*NY],
  output logic          crc_err    [NX*NY],
  output logic          array_abft_sticky [NX*NY],
  output logic          acc_abft_sticky   [NX*NY],
  output logic          ctrl_err_sticky   [NX*NY],
  output logic          seq_err_sticky    [NX*NY],
  output logic          rq_err_sticky     [NX*NY],
  output logic          ecc_ce            [NX*NY],
  output logic          ecc_ue            [NX*NY],
  output logic          wbuf_ce           [NX*NY],
  output logic          wbuf_ue           [NX*NY],
  output logic          rq_tbl_perr       [NX*NY],
  output logic          lost_err          [NX*NY],
  output logic          fetch_timeout     [NX*NY],
  // testbench hooks (drop 0.35; DV_HOOKS = 1): one tile at a time, selected by node index y * NX + x -- the bank backdoor
  // (dv_bd_*), the fault injection (dv_fi_*: see neo_tile) and the observation of node dv_fi_node's router output registers
  input  logic [NW-1:0] dv_bd_node,
  input  logic          dv_bd_we,
  input  logic [19:0]   dv_bd_addr,
  input  logic [38:0]   dv_bd_wdata,
  output logic [38:0]   dv_bd_rdata,
  input  logic [NW-1:0] dv_fi_node,
  input  logic [2:0]    dv_fi_sel,
  input  logic          dv_fi_en,
  input  logic [19:0]   dv_fi_idx,
  input  logic [DWL-1:0] dv_fi_mask,
  output logic [4:0]    dv_rt_valid,
  output logic [FW-1:0] dv_rt_flit4
);
  logic          o_valid [NX*NY][4];
  logic [FW-1:0] o_flit  [NX*NY][4];
  logic          o_ready [NX*NY][4];
  logic          i_valid [NX*NY][4];
  logic [FW-1:0] i_flit  [NX*NY][4];
  logic          i_ready [NX*NY][4];
  logic [38:0]   dv_bd_rd [NX*NY];
  logic [4:0]    dv_rv    [NX*NY];
  logic [FW-1:0] dv_rf4   [NX*NY];
  assign dv_bd_rdata = dv_bd_rd[dv_bd_node];
  assign dv_rt_valid = dv_rv[dv_fi_node];
  assign dv_rt_flit4 = dv_rf4[dv_fi_node];
  logic unused_mesh_bits;                                   // the backdoor address bits above the bank's width (lint)
  assign unused_mesh_bits = ^dv_bd_addr;

  generate
    for (genvar y = 0; y < NY; y++) begin : g_y
      for (genvar x = 0; x < NX; x++) begin : g_x
        localparam int n = y * NX + x;
        // the same module in every position (drop 0.35): the coordinates are strapped on ports, not parameters, so the
        // simulator elaborates one tile and instantiates it NX*NY times (64 parameter-distinct copies did not fit a 16 GB build)
        // the tile's link ports are packed vectors (port p at bit p / bits [p*FW +: FW]); the mesh wiring below stays per port
        logic [3:0]      t_in_valid, t_in_ready, t_out_valid, t_out_ready;
        logic [4*FW-1:0] t_in_flit, t_out_flit;
        for (genvar p = 0; p < 4; p++) begin : g_lp
          assign t_in_valid[p]           = i_valid[n][p];
          assign t_in_flit[p*FW +: FW]   = i_flit[n][p];
          assign i_ready[n][p]           = t_in_ready[p];
          assign o_valid[n][p]           = t_out_valid[p];
          assign o_flit[n][p]            = t_out_flit[p*FW +: FW];
          assign t_out_ready[p]          = o_ready[n][p];
        end
        neo_tile #(.XW(XW), .YW(YW), .NX(NX), .NY(NY), .WPF(WPF),
                   .ROWS(ROWS), .COLS(COLS), .PW(PW), .ACC_ROWS(ACC_ROWS), .ABUF_DEPTH(ABUF_DEPTH),
                   .WBUF_DEPTH(WBUF_DEPTH), .BANK_DEPTH(BANK_DEPTH), .FETCH_TIMEOUT(FETCH_TIMEOUT), .BIST_WORDS(BIST_WORDS),
                   .DV_HOOKS(DV_HOOKS)) u_t (
          .clk(clk), .rst_n(rst_n), .my_x(XW'(x)), .my_y(YW'(y)),
          .in_valid(t_in_valid), .in_flit(t_in_flit), .in_ready(t_in_ready),
          .out_valid(t_out_valid), .out_flit(t_out_flit), .out_ready(t_out_ready),
          .h_we(h_we[n]), .h_re(h_re[n]), .h_addr(h_addr[n]), .h_wdata(h_wdata[n]), .h_rdata(h_rdata[n]), .err_pin(err_pin[n]),
          .prog_done(prog_done[n]), .drain_busy(drain_busy[n]), .done(done[n]),
          .parity_err(parity_err[n]), .crc_err(crc_err[n]),
          .array_abft_sticky(array_abft_sticky[n]), .acc_abft_sticky(acc_abft_sticky[n]), .ctrl_err_sticky(ctrl_err_sticky[n]),
          .seq_err_sticky(seq_err_sticky[n]), .rq_err_sticky(rq_err_sticky[n]), .ecc_ce(ecc_ce[n]), .ecc_ue(ecc_ue[n]),
          .wbuf_ce(wbuf_ce[n]), .wbuf_ue(wbuf_ue[n]), .rq_tbl_perr(rq_tbl_perr[n]),
          .lost_err(lost_err[n]), .fetch_timeout(fetch_timeout[n]),
          .dv_bd_we(dv_bd_we && (dv_bd_node == NW'(n))), .dv_bd_addr(dv_bd_addr[BAW-1:0]), .dv_bd_wdata(dv_bd_wdata), .dv_bd_rdata(dv_bd_rd[n]),
          .dv_fi_en(dv_fi_en && (dv_fi_node == NW'(n))), .dv_fi_sel(dv_fi_sel), .dv_fi_idx(dv_fi_idx), .dv_fi_mask(dv_fi_mask),
          .dv_rt_valid(dv_rv[n]), .dv_rt_flit4(dv_rf4[n]));
        // north input (0) <- south output (2) of the node above; south input (2) <- north output (0) of the node below
        if (y > 0) begin : g_n_in
          assign i_valid[n][0] = o_valid[n-NX][2]; assign i_flit[n][0] = o_flit[n-NX][2]; assign o_ready[n-NX][2] = i_ready[n][0];
        end else begin : g_n_edge
          assign i_valid[n][0] = 1'b0; assign i_flit[n][0] = '0; assign o_ready[n][0] = 1'b1;
        end
        if (y < NY-1) begin : g_s_in
          assign i_valid[n][2] = o_valid[n+NX][0]; assign i_flit[n][2] = o_flit[n+NX][0]; assign o_ready[n+NX][0] = i_ready[n][2];
        end else begin : g_s_edge
          assign i_valid[n][2] = 1'b0; assign i_flit[n][2] = '0; assign o_ready[n][2] = 1'b1;
        end
        // west input (3) <- east output (1) of the node to the left; east input (1) <- west output (3) of the node to the right
        if (x > 0) begin : g_w_in
          assign i_valid[n][3] = o_valid[n-1][1]; assign i_flit[n][3] = o_flit[n-1][1]; assign o_ready[n-1][1] = i_ready[n][3];
        end else begin : g_w_edge
          assign i_valid[n][3] = 1'b0; assign i_flit[n][3] = '0; assign o_ready[n][3] = 1'b1;
        end
        if (x < NX-1) begin : g_e_in
          assign i_valid[n][1] = o_valid[n+1][3]; assign i_flit[n][1] = o_flit[n+1][3]; assign o_ready[n+1][3] = i_ready[n][1];
        end else begin : g_e_edge
          assign i_valid[n][1] = 1'b0; assign i_flit[n][1] = '0; assign o_ready[n][1] = 1'b1;
        end
      end
    end
  endgenerate
endmodule
