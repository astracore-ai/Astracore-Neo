// tile_mesh_cocotb.sv -- tile_mesh with packed host-bus and status ports, a bank backdoor, cycle-exact fault
//   injection and router observation for cocotb (drop 0.28: bank backdoors by lane and row; drop 0.29: the dead-server fault).
//   Ports: the simulator presents unpacked arrays of 1-bit ports to VPI as one packed register, so a Python
//   testbench cannot index them; every per-tile port is packed into a vector: tile n occupies bit n (1-bit ports)
//   or bits [n*W +: W] (W-bit ports).
//   Internals: cocotb's VPI layer cannot enumerate generate scopes, cannot index unpacked arrays of 1-bit elements
//   and does not see two-dimensional unpacked arrays, so everything the tests touch inside the mesh goes through
//   this wrapper: the bank backdoor (bd_*), the fault ports (fi_*) and the router observation ports (ob_*).
//   Since drop 0.35 these are ports of tile_mesh and neo_tile (DV_HOOKS = 1), no longer hierarchical references from
//   here: neo_tile is a hierarchical block of the Verilator build (verilated once, instantiated NX*NY times), and a
//   hierarchical block admits no references into it. The semantics are the tile's: the backdoor writes at the rising edge
//   and reads combinationally; faults are applied at the FALLING clock edge, so they never race the design's own
//   rising-edge updates: the corrupted value is what the design samples at the next rising edge, exactly as the neosim
//   M-tests poke a cell between two ticks. (A comment must not begin with the simulator's name: it would be read as a directive.)
module tile_mesh_cocotb #(
  parameter int NX = 2, NY = 2, ROWS = 16, COLS = 8, PW = 32,
  parameter int ACC_ROWS = 64, ABUF_DEPTH = 256, WBUF_DEPTH = 512, BANK_DEPTH = 4096,
  parameter int FETCH_TIMEOUT = 8192, BIST_WORDS = 16,
  parameter int N = NX * NY,
  parameter int NW = (N > 1) ? $clog2(N) : 1,
  parameter int XW = (NX > 1) ? $clog2(NX) : 1,
  parameter int YW = (NY > 1) ? $clog2(NY) : 1,
  parameter int WPF = 1,                     // words per link flit (drop 0.25)
  parameter int DWL = 32 + 32 * WPF,
  parameter int FW = 1 + 2 * (XW + YW) + 8 + DWL      // the link flit, as the router sees it
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
  output logic [N-1:0]  parity_err,
  // the twelve sticky flags of tile n in bits [n*12 +: 12] (drop 0.37; the mesh's flag ports were unconnected before, twelve
  // PINMISSING warnings per build), in the order of the tile's cause register from its bit 2: array_abft, acc_abft, ctrl_err,
  // seq_err, rq_err, ecc_ue, ecc_ce, wbuf_ce, wbuf_ue, rq_tbl_perr, lost_err, fetch_timeout (flags[n*12 + k] = cause bit k + 2)
  output logic [N*12-1:0] flags,
  // bank backdoor, raw (39,32) codewords: on bd_we the next rising edge writes bd_wdata into node bd_node's bank
  // at bd_addr; bd_rdata shows node bd_node's word bd_addr combinationally
  input  logic [NW-1:0] bd_node,
  input  logic          bd_we,
  input  logic [19:0]   bd_addr,            // 20 bits: the silicon bank has 512K words
  input  logic [38:0]   bd_wdata,
  output logic [38:0]   bd_rdata,
  // fault injection into node fi_node, applied at every falling edge while fi_en is high (a one-clock pulse of fi_en
  // applies it exactly once):
  //   fi_sel 1  bank word fi_idx OR fi_mask[38:0]            (a stuck-at bit; hold fi_en for the duration)
  //          2  program memory word fi_idx lane 0 XOR fi_mask[38:0]
  //          3  descriptor register cfg[fi_idx] XOR fi_mask[15:0]   (the primary copy only)
  //          4  primary DMA engine pc XOR fi_mask[3:0]
  //          5  router output register out_flit[fi_idx] XOR fi_mask  (payload bits, any of the WPF words; fi_idx = port, 4 = local)
  //          6  router output register out_valid[fi_idx] cleared     (the flit vanishes)
  //          7  NIC transmit engine held in X_SERVE_RD (4'd2)        (a dead server: a request is taken and its response never
//                                                                 comes, whatever the request's length; hold fi_en. Drop 0.29:
//                                                                 was serve_busy held at 1, which only starved the requester
//                                                                 because the old engine streamed words without ever closing)
  input  logic [NW-1:0] fi_node,
  input  logic [2:0]    fi_sel,
  input  logic          fi_en,
  input  logic [19:0]   fi_idx,
  input  logic [DWL-1:0] fi_mask,                 // as wide as the link payload (drop 0.27): a flit fault can hit any word
  // observation of node fi_node's router output stage: valid of port k in bit k, and the local-port (4) flit
  output logic [4:0]    ob_out_valid,
  output logic [FW-1:0] ob_out_flit4
);
  logic          h_we_a [N], h_re_a [N], err_pin_a [N], prog_done_a [N], drain_busy_a [N], done_a [N], crc_err_a [N], parity_err_a [N];
  logic          array_abft_a [N], acc_abft_a [N], ctrl_err_a [N], seq_err_a [N], rq_err_a [N], ecc_ce_a [N], ecc_ue_a [N],
                 wbuf_ce_a [N], wbuf_ue_a [N], rq_tbl_perr_a [N], lost_err_a [N], fetch_timeout_a [N];
  logic [7:0]    h_addr_a [N];
  logic [31:0]   h_wdata_a [N], h_rdata_a [N];

  generate
    for (genvar n = 0; n < N; n++) begin : g_pack
      assign flags[n*12 +: 12] = {fetch_timeout_a[n], lost_err_a[n], rq_tbl_perr_a[n], wbuf_ue_a[n], wbuf_ce_a[n], ecc_ce_a[n],
                                  ecc_ue_a[n], rq_err_a[n], seq_err_a[n], ctrl_err_a[n], acc_abft_a[n], array_abft_a[n]};
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

  tile_mesh #(.NX(NX), .NY(NY), .XW(XW), .YW(YW), .WPF(WPF), .ROWS(ROWS), .COLS(COLS), .PW(PW), .ACC_ROWS(ACC_ROWS),
              .ABUF_DEPTH(ABUF_DEPTH), .WBUF_DEPTH(WBUF_DEPTH), .BANK_DEPTH(BANK_DEPTH), .FETCH_TIMEOUT(FETCH_TIMEOUT),
              .BIST_WORDS(BIST_WORDS), .DV_HOOKS(1)) u_mesh (
    .clk(clk), .rst_n(rst_n),
    .h_we(h_we_a), .h_re(h_re_a), .h_addr(h_addr_a), .h_wdata(h_wdata_a), .h_rdata(h_rdata_a), .err_pin(err_pin_a),
    .prog_done(prog_done_a), .drain_busy(drain_busy_a), .done(done_a), .crc_err(crc_err_a), .parity_err(parity_err_a),
    .array_abft_sticky(array_abft_a), .acc_abft_sticky(acc_abft_a), .ctrl_err_sticky(ctrl_err_a), .seq_err_sticky(seq_err_a),
    .rq_err_sticky(rq_err_a), .ecc_ce(ecc_ce_a), .ecc_ue(ecc_ue_a), .wbuf_ce(wbuf_ce_a), .wbuf_ue(wbuf_ue_a),
    .rq_tbl_perr(rq_tbl_perr_a), .lost_err(lost_err_a), .fetch_timeout(fetch_timeout_a),
    .dv_bd_node(bd_node), .dv_bd_we(bd_we), .dv_bd_addr(bd_addr), .dv_bd_wdata(bd_wdata), .dv_bd_rdata(bd_rdata),
    .dv_fi_node(fi_node), .dv_fi_sel(fi_sel), .dv_fi_en(fi_en), .dv_fi_idx(fi_idx), .dv_fi_mask(fi_mask),
    .dv_rt_valid(ob_out_valid), .dv_rt_flit4(ob_out_flit4));

endmodule
