// fpga_top.sv -- one neo_tile_axil with the mesh links tied off, for the ZCU102 block design (skeleton).
module fpga_top (
  input  logic        clk, input logic rst_n,
  input  logic [9:0]  s_axi_awaddr, input logic s_axi_awvalid, output logic s_axi_awready,
  input  logic [31:0] s_axi_wdata, input logic [3:0] s_axi_wstrb, input logic s_axi_wvalid, output logic s_axi_wready,
  output logic [1:0]  s_axi_bresp, output logic s_axi_bvalid, input logic s_axi_bready,
  input  logic [9:0]  s_axi_araddr, input logic s_axi_arvalid, output logic s_axi_arready,
  output logic [31:0] s_axi_rdata, output logic [1:0] s_axi_rresp, output logic s_axi_rvalid, input logic s_axi_rready,
  output logic        err_pin_led
);
  localparam int FW = 1 + 2 * (2 + 2) + 8 + 64;
  logic        in_valid [4], in_ready [4], out_valid [4], out_ready [4];
  logic [FW-1:0] in_flit [4], out_flit [4];
  for (genvar i = 0; i < 4; i++) begin : g_tie
    assign in_valid[i] = 1'b0; assign in_flit[i] = '0; assign out_ready[i] = 1'b1;
  end
  neo_tile_axil #(.ROWS(16), .COLS(8), .ACC_ROWS(64), .ABUF_DEPTH(256), .WBUF_DEPTH(512), .BANK_DEPTH(4096)) u_tile (
    .clk(clk), .rst_n(rst_n),
    .s_awaddr(s_axi_awaddr), .s_awvalid(s_axi_awvalid), .s_awready(s_axi_awready), .s_wdata(s_axi_wdata), .s_wstrb(s_axi_wstrb),
    .s_wvalid(s_axi_wvalid), .s_wready(s_axi_wready), .s_bresp(s_axi_bresp), .s_bvalid(s_axi_bvalid), .s_bready(s_axi_bready),
    .s_araddr(s_axi_araddr), .s_arvalid(s_axi_arvalid), .s_arready(s_axi_arready), .s_rdata(s_axi_rdata), .s_rresp(s_axi_rresp),
    .s_rvalid(s_axi_rvalid), .s_rready(s_axi_rready), .err_pin(err_pin_led),
    .in_valid(in_valid), .in_flit(in_flit), .in_ready(in_ready), .out_valid(out_valid), .out_flit(out_flit), .out_ready(out_ready),
    .prog_done(), .drain_busy(), .done(), .parity_err(), .crc_err(), .array_abft_sticky(), .acc_abft_sticky(), .ctrl_err_sticky(),
    .seq_err_sticky(), .rq_err_sticky(), .ecc_ce(), .ecc_ue(), .wbuf_ce(), .wbuf_ue(), .rq_tbl_perr(), .lost_err(), .fetch_timeout());
endmodule
