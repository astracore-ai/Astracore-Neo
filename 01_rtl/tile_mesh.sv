// tile_mesh.sv -- NX x NY neo_tile mesh (drop 0.7); host ports exposed per tile, edges terminated.
module tile_mesh #(
  parameter int NX = 2, NY = 2, XW = 2, YW = 2, DW = 64,
  parameter int FW = 1 + 2 * (XW + YW) + 8 + DW,
  parameter int ROWS = 16, COLS = 8, PW = 32,
  parameter int ACC_ROWS = 64, ABUF_DEPTH = 256, WBUF_DEPTH = 512, BANK_DEPTH = 4096,
  parameter int IDXW = $clog2(ACC_ROWS),
  parameter int FETCH_TIMEOUT = 1048575,     // silicon default (drop 0.24): 2^20 - 1, see tile_nic.sv
  parameter int BIST_WORDS = BANK_DEPTH
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
  output logic          fetch_timeout     [NX*NY]
);
  logic          o_valid [NX*NY][4];
  logic [FW-1:0] o_flit  [NX*NY][4];
  logic          o_ready [NX*NY][4];
  logic          i_valid [NX*NY][4];
  logic [FW-1:0] i_flit  [NX*NY][4];
  logic          i_ready [NX*NY][4];

  generate
    for (genvar y = 0; y < NY; y++) begin : g_y
      for (genvar x = 0; x < NX; x++) begin : g_x
        localparam int n = y * NX + x;
        neo_tile #(.XW(XW), .YW(YW), .NX(NX), .NY(NY), .DW(DW), .FW(FW), .MY_X(x), .MY_Y(y),
                   .ROWS(ROWS), .COLS(COLS), .PW(PW), .ACC_ROWS(ACC_ROWS), .ABUF_DEPTH(ABUF_DEPTH),
                   .WBUF_DEPTH(WBUF_DEPTH), .BANK_DEPTH(BANK_DEPTH), .FETCH_TIMEOUT(FETCH_TIMEOUT), .BIST_WORDS(BIST_WORDS)) u_t (
          .clk(clk), .rst_n(rst_n),
          .in_valid(i_valid[n]), .in_flit(i_flit[n]), .in_ready(i_ready[n]),
          .out_valid(o_valid[n]), .out_flit(o_flit[n]), .out_ready(o_ready[n]),
          .h_we(h_we[n]), .h_re(h_re[n]), .h_addr(h_addr[n]), .h_wdata(h_wdata[n]), .h_rdata(h_rdata[n]), .err_pin(err_pin[n]),
          .prog_done(prog_done[n]), .drain_busy(drain_busy[n]), .done(done[n]),
          .parity_err(parity_err[n]), .crc_err(crc_err[n]),
          .array_abft_sticky(array_abft_sticky[n]), .acc_abft_sticky(acc_abft_sticky[n]), .ctrl_err_sticky(ctrl_err_sticky[n]),
          .seq_err_sticky(seq_err_sticky[n]), .rq_err_sticky(rq_err_sticky[n]), .ecc_ce(ecc_ce[n]), .ecc_ue(ecc_ue[n]),
          .wbuf_ce(wbuf_ce[n]), .wbuf_ue(wbuf_ue[n]), .rq_tbl_perr(rq_tbl_perr[n]),
          .lost_err(lost_err[n]), .fetch_timeout(fetch_timeout[n]));
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
