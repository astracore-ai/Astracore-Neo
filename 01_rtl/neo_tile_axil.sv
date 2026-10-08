// neo_tile_axil.sv -- neo_tile with an AXI4-Lite host port (drop 0.15): the integration view of one tile.
module neo_tile_axil #(
  parameter int XW = 2, YW = 2, NX = 2, NY = 2, WPF = 1,            // WPF words per link flit (drop 0.25)
  parameter int FW = 1 + 2 * (XW + YW) + 8 + 32 + 32 * WPF,          // the link flit
  parameter int ROWS = 16, COLS = 8, PW = 32,
  parameter int ACC_ROWS = 64, ABUF_DEPTH = 256, WBUF_DEPTH = 512, BANK_DEPTH = 4096,
  parameter int FETCH_TIMEOUT = 1048575, BIST_WORDS = BANK_DEPTH,  // silicon default (drop 0.24): 2^20 - 1, see tile_nic.sv
  parameter int DV_HOOKS = 0, BAW = $clog2(BANK_DEPTH), DWL = 32 + 32 * WPF
)(
  input  logic          clk,
  input  logic          rst_n,
  input  logic [XW-1:0] my_x,          // the tile's mesh coordinates, strapped at integration (drop 0.35)
  input  logic [YW-1:0] my_y,
  // AXI4-Lite host port
  input  logic [9:0]    s_awaddr, input logic s_awvalid, output logic s_awready,
  input  logic [31:0]   s_wdata, input logic [3:0] s_wstrb, input logic s_wvalid, output logic s_wready,
  output logic [1:0]    s_bresp, output logic s_bvalid, input logic s_bready,
  input  logic [9:0]    s_araddr, input logic s_arvalid, output logic s_arready,
  output logic [31:0]   s_rdata, output logic [1:0] s_rresp, output logic s_rvalid, input logic s_rready,
  output logic          err_pin,
  // mesh links (N, E, S, W), packed as neo_tile's: port p at bit p / bits [p*FW +: FW] (drop 0.35)
  input  logic [3:0]    in_valid, input logic [4*FW-1:0] in_flit, output logic [3:0] in_ready,
  output logic [3:0]    out_valid, output logic [4*FW-1:0] out_flit, input logic [3:0] out_ready,
  // status
  output logic          prog_done, drain_busy, done, parity_err, crc_err, array_abft_sticky, acc_abft_sticky,
  output logic          ctrl_err_sticky, seq_err_sticky, rq_err_sticky, ecc_ce, ecc_ue, wbuf_ce, wbuf_ue, rq_tbl_perr,
  output logic          lost_err, fetch_timeout,
  // testbench hooks of the tile (drop 0.35), tied off when DV_HOOKS = 0
  input  logic          dv_bd_we, input logic [BAW-1:0] dv_bd_addr, input logic [38:0] dv_bd_wdata, output logic [38:0] dv_bd_rdata,
  input  logic          dv_fi_en, input logic [2:0] dv_fi_sel, input logic [19:0] dv_fi_idx, input logic [DWL-1:0] dv_fi_mask,
  output logic [4:0]    dv_rt_valid, output logic [FW-1:0] dv_rt_flit4
);
  logic h_we, h_re; logic [7:0] h_addr; logic [31:0] h_wdata, h_rdata;
  host_axil #(.AW(10)) u_axil (
    .aclk(clk), .aresetn(rst_n),
    .s_awaddr(s_awaddr), .s_awvalid(s_awvalid), .s_awready(s_awready), .s_wdata(s_wdata), .s_wstrb(s_wstrb), .s_wvalid(s_wvalid),
    .s_wready(s_wready), .s_bresp(s_bresp), .s_bvalid(s_bvalid), .s_bready(s_bready),
    .s_araddr(s_araddr), .s_arvalid(s_arvalid), .s_arready(s_arready), .s_rdata(s_rdata), .s_rresp(s_rresp), .s_rvalid(s_rvalid), .s_rready(s_rready),
    .h_we(h_we), .h_re(h_re), .h_addr(h_addr), .h_wdata(h_wdata), .h_rdata(h_rdata));
  neo_tile #(.XW(XW), .YW(YW), .NX(NX), .NY(NY), .WPF(WPF), .ROWS(ROWS), .COLS(COLS), .PW(PW),
             .ACC_ROWS(ACC_ROWS), .ABUF_DEPTH(ABUF_DEPTH), .WBUF_DEPTH(WBUF_DEPTH), .BANK_DEPTH(BANK_DEPTH),
             .FETCH_TIMEOUT(FETCH_TIMEOUT), .BIST_WORDS(BIST_WORDS), .DV_HOOKS(DV_HOOKS)) u_tile (
    .clk(clk), .rst_n(rst_n), .my_x(my_x), .my_y(my_y),
    .in_valid(in_valid), .in_flit(in_flit), .in_ready(in_ready), .out_valid(out_valid), .out_flit(out_flit), .out_ready(out_ready),
    .h_we(h_we), .h_re(h_re), .h_addr(h_addr), .h_wdata(h_wdata), .h_rdata(h_rdata), .err_pin(err_pin),
    .prog_done(prog_done), .drain_busy(drain_busy), .done(done), .parity_err(parity_err), .crc_err(crc_err),
    .array_abft_sticky(array_abft_sticky), .acc_abft_sticky(acc_abft_sticky), .ctrl_err_sticky(ctrl_err_sticky),
    .seq_err_sticky(seq_err_sticky), .rq_err_sticky(rq_err_sticky), .ecc_ce(ecc_ce), .ecc_ue(ecc_ue),
    .wbuf_ce(wbuf_ce), .wbuf_ue(wbuf_ue), .rq_tbl_perr(rq_tbl_perr), .lost_err(lost_err), .fetch_timeout(fetch_timeout),
    .dv_bd_we(dv_bd_we), .dv_bd_addr(dv_bd_addr), .dv_bd_wdata(dv_bd_wdata), .dv_bd_rdata(dv_bd_rdata),
    .dv_fi_en(dv_fi_en), .dv_fi_sel(dv_fi_sel), .dv_fi_idx(dv_fi_idx), .dv_fi_mask(dv_fi_mask),
    .dv_rt_valid(dv_rt_valid), .dv_rt_flit4(dv_rt_flit4));
endmodule
