// tile_mesh_cocotb.sv -- tile_mesh with packed host-bus and status ports for cocotb (drop 0.19).
//   Verilator presents unpacked arrays of 1-bit ports to VPI as one packed register, so a Python testbench
//   cannot index them; this wrapper packs every per-tile port into a vector: tile n occupies bit n (1-bit
//   ports) or bits [n*W +: W] (W-bit ports). Internals are reached through u_mesh.
module tile_mesh_cocotb #(
  parameter int NX = 2, NY = 2, ROWS = 16, COLS = 8, PW = 32,
  parameter int ACC_ROWS = 64, ABUF_DEPTH = 256, WBUF_DEPTH = 512, BANK_DEPTH = 4096,
  parameter int FETCH_TIMEOUT = 600, BIST_WORDS = 16,
  parameter int N = NX * NY
)(
  input  logic          clk,
  input  logic          rst_n,
  input  logic [N-1:0]  h_we,
  input  logic [N-1:0]  h_re,
  input  logic [N*8-1:0]  h_addr,
  input  logic [N*32-1:0] h_wdata,
  output logic [N*32-1:0] h_rdata,
  output logic [N-1:0]  err_pin,
  output logic [N-1:0]  prog_done,
  output logic [N-1:0]  drain_busy,
  output logic [N-1:0]  done,
  output logic [N-1:0]  crc_err,
  output logic [N-1:0]  parity_err
);
  logic          h_we_a [N], h_re_a [N], err_pin_a [N], prog_done_a [N], drain_busy_a [N], done_a [N], crc_err_a [N], parity_err_a [N];
  logic [7:0]    h_addr_a [N];
  logic [31:0]   h_wdata_a [N], h_rdata_a [N];
  generate
    for (genvar n = 0; n < N; n++) begin : g_pack
      assign h_we_a[n]    = h_we[n];
      assign h_re_a[n]    = h_re[n];
      assign h_addr_a[n]  = h_addr[n*8 +: 8];
      assign h_wdata_a[n] = h_wdata[n*32 +: 32];
      assign h_rdata[n*32 +: 32] = h_rdata_a[n];
      assign err_pin[n]    = err_pin_a[n];
      assign prog_done[n]  = prog_done_a[n];
      assign drain_busy[n] = drain_busy_a[n];
      assign done[n]       = done_a[n];
      assign crc_err[n]    = crc_err_a[n];
      assign parity_err[n] = parity_err_a[n];
    end
  endgenerate
  tile_mesh #(.NX(NX), .NY(NY), .ROWS(ROWS), .COLS(COLS), .PW(PW), .ACC_ROWS(ACC_ROWS), .ABUF_DEPTH(ABUF_DEPTH),
              .WBUF_DEPTH(WBUF_DEPTH), .BANK_DEPTH(BANK_DEPTH), .FETCH_TIMEOUT(FETCH_TIMEOUT), .BIST_WORDS(BIST_WORDS)) u_mesh (
    .clk(clk), .rst_n(rst_n),
    .h_we(h_we_a), .h_re(h_re_a), .h_addr(h_addr_a), .h_wdata(h_wdata_a), .h_rdata(h_rdata_a), .err_pin(err_pin_a),
    .prog_done(prog_done_a), .drain_busy(drain_busy_a), .done(done_a), .crc_err(crc_err_a), .parity_err(parity_err_a));
endmodule
