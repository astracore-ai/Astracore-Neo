// tile_mesh_cocotb.sv -- tile_mesh with packed host-bus and status ports, a bank backdoor, cycle-exact fault
//   injection and router observation for cocotb (drop 0.28: bank backdoors by lane and row).
//   Ports: the simulator presents unpacked arrays of 1-bit ports to VPI as one packed register, so a Python
//   testbench cannot index them; every per-tile port is packed into a vector: tile n occupies bit n (1-bit ports)
//   or bits [n*W +: W] (W-bit ports).
//   Internals: cocotb's VPI layer cannot enumerate generate scopes, cannot index unpacked arrays of 1-bit elements
//   and does not see two-dimensional unpacked arrays, so everything the tests touch inside the mesh goes through
//   this wrapper: the bank backdoor (bd_*), the fault ports (fi_*) and the router observation ports (ob_*).
//   Faults are applied at the FALLING clock edge, so they never race the design's own rising-edge updates: the
//   corrupted value is what the design samples at the next rising edge, exactly as the neosim M-tests poke a cell
//   between two ticks. (A comment must not begin with the simulator's name: it would be read as a directive.)
module tile_mesh_cocotb #(
  parameter int NX = 2, NY = 2, ROWS = 16, COLS = 8, PW = 32,
  parameter int ACC_ROWS = 64, ABUF_DEPTH = 256, WBUF_DEPTH = 512, BANK_DEPTH = 4096,
  parameter int FETCH_TIMEOUT = 8192, BIST_WORDS = 16,
  parameter int PDEPTH = 32,                 // program memory entries, as neo_tile's default (drop 0.22)
  parameter int PAW = $clog2(PDEPTH),
  parameter int N = NX * NY,
  parameter int NW = (N > 1) ? $clog2(N) : 1,
  parameter int XW = (NX > 1) ? $clog2(NX) : 1,
  parameter int YW = (NY > 1) ? $clog2(NY) : 1,
  parameter int DW = 64,
  parameter int WPF = 1,                     // words per link flit (drop 0.25)
  parameter int DWL = 32 + 32 * WPF,
  parameter int FW = 1 + 2 * (XW + YW) + 8 + DWL,     // the link flit, as the router sees it
  parameter int BAW = $clog2(BANK_DEPTH)
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
  //          7  NIC serve_busy held at 1                            (requests are never served; hold fi_en)
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
  logic [7:0]    h_addr_a [N];
  logic [31:0]   h_wdata_a [N], h_rdata_a [N];
  logic [38:0]   bd_rd  [N];
  logic [4:0]    ob_rv  [N];
  logic [FW-1:0] ob_rf4 [N];

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

  tile_mesh #(.NX(NX), .NY(NY), .XW(XW), .YW(YW), .DW(DW), .WPF(WPF), .ROWS(ROWS), .COLS(COLS), .PW(PW), .ACC_ROWS(ACC_ROWS),
              .ABUF_DEPTH(ABUF_DEPTH), .WBUF_DEPTH(WBUF_DEPTH), .BANK_DEPTH(BANK_DEPTH), .FETCH_TIMEOUT(FETCH_TIMEOUT),
              .BIST_WORDS(BIST_WORDS)) u_mesh (
    .clk(clk), .rst_n(rst_n),
    .h_we(h_we_a), .h_re(h_re_a), .h_addr(h_addr_a), .h_wdata(h_wdata_a), .h_rdata(h_rdata_a), .err_pin(err_pin_a),
    .prog_done(prog_done_a), .drain_busy(drain_busy_a), .done(done_a), .crc_err(crc_err_a), .parity_err(parity_err_a));

  // backdoor, faults and observation per tile; hierarchical references into the mesh (testbench only)
  generate
    for (genvar gy = 0; gy < NY; gy++) begin : g_ty
      for (genvar gx = 0; gx < NX; gx++) begin : g_tx
        localparam int tn = gy * NX + gx;
        // bank backdoor
        always_ff @(posedge clk) begin
          if (bd_we && (bd_node == tn))
            u_mesh.g_y[gy].g_x[gx].u_t.u_bank.u_bank.mem[bd_addr[3:0]][bd_addr[BAW-1:4]] <= bd_wdata;   // lane, row (drop 0.28)
        end
        assign bd_rd[tn] = u_mesh.g_y[gy].g_x[gx].u_t.u_bank.u_bank.mem[bd_addr[3:0]][bd_addr[BAW-1:4]];
        // router observation
        assign ob_rv[tn] = {u_mesh.g_y[gy].g_x[gx].u_t.u_router.out_valid[4], u_mesh.g_y[gy].g_x[gx].u_t.u_router.out_valid[3],
                            u_mesh.g_y[gy].g_x[gx].u_t.u_router.out_valid[2], u_mesh.g_y[gy].g_x[gx].u_t.u_router.out_valid[1],
                            u_mesh.g_y[gy].g_x[gx].u_t.u_router.out_valid[0]};
        assign ob_rf4[tn] = u_mesh.g_y[gy].g_x[gx].u_t.u_router.out_flit[4];
        // faults, falling edge
        always_ff @(negedge clk) begin
          if (fi_en && (fi_node == tn)) begin
            case (fi_sel)
              3'd1: u_mesh.g_y[gy].g_x[gx].u_t.u_bank.u_bank.mem[fi_idx[3:0]][fi_idx[BAW-1:4]] <=
                      u_mesh.g_y[gy].g_x[gx].u_t.u_bank.u_bank.mem[fi_idx[3:0]][fi_idx[BAW-1:4]] | fi_mask[38:0];
              3'd2: u_mesh.g_y[gy].g_x[gx].u_t.u_prog.mem[fi_idx[PAW-1:0]][0] <=
                      u_mesh.g_y[gy].g_x[gx].u_t.u_prog.mem[fi_idx[PAW-1:0]][0] ^ fi_mask[38:0];
              3'd3: u_mesh.g_y[gy].g_x[gx].u_t.u_host.cfg[fi_idx[4:0]] <=
                      u_mesh.g_y[gy].g_x[gx].u_t.u_host.cfg[fi_idx[4:0]] ^ fi_mask[15:0];
              3'd4: u_mesh.g_y[gy].g_x[gx].u_t.u_dma.pc <= u_mesh.g_y[gy].g_x[gx].u_t.u_dma.pc ^ fi_mask[3:0];
              3'd5: u_mesh.g_y[gy].g_x[gx].u_t.u_router.out_flit[fi_idx[2:0]] <=
                      u_mesh.g_y[gy].g_x[gx].u_t.u_router.out_flit[fi_idx[2:0]] ^ FW'(fi_mask);
              3'd6: u_mesh.g_y[gy].g_x[gx].u_t.u_router.out_valid[fi_idx[2:0]] <= 1'b0;
              3'd7: u_mesh.g_y[gy].g_x[gx].u_t.u_nic.serve_busy <= 1'b1;
              default: ;
            endcase
          end
        end
      end
    end
  endgenerate
  assign bd_rdata     = bd_rd[bd_node];
  assign ob_out_valid = ob_rv[fi_node];
  assign ob_out_flit4 = ob_rf4[fi_node];
endmodule
