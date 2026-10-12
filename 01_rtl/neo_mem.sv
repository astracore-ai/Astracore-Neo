/* verilator lint_off DECLFILENAME */        // two modules, neo_mem_sp and neo_mem_rf, in one file by design
// neo_mem.sv -- the design's memory arrays as leaf modules (drop 0.40).
//   Every array that a memory compiler replaces in silicon (09_synthesis/memories.md: the bank's 16 lanes, the weight
//   buffer, the activation buffer, the accumulator, the NIC's drain FIFO) lives in one of the two modules below, so that one swap -- the
//   macro wrapper at implementation, a black box with the macro's area and timing in the open-PDK synthesis job
//   (07_ci/synth) -- covers the whole design, while every simulator runs the behavioural array here. The program
//   memory (prog_mem), the requantization tables and the router FIFOs stay flops in their own modules.
//
//   neo_mem_sp -- single-port style: one write port of G groups (we[g] writes bits [g*GW +: GW] of the word at waddr,
//     GW = W / G) and one registered read port (rdata holds the word at raddr from the cycle after re). The bank's lanes.
//   neo_mem_rf -- register-file style: the same write port, an asynchronous read port A (ra_data is the word at ra_addr
//     in the same cycle) and, with RB = 1, a registered read port B (rb_en / rb_addr -> rb_data the cycle after). The
//     weight buffer, the activation buffer and the NIC's drain FIFO use port A; the accumulator uses A for its
//     read-modify-write and B for the drain. A write and a read of the same word in one cycle: the read returns the old
//     word (nonblocking write).
//
//   Testbench hooks (DV_HOOKS = 1; the ports are tied off at 0, the default): a backdoor write at the rising edge
//   (bd_we[g]: group g of the word at bd_addr <= the same group of bd_wdata, independent of the functional write of the
//   other groups in the same cycle), a combinational backdoor read (bd_rdata = the word at bd_addr) and a fault at the
//   falling edge while fi_en: the word at fi_addr |= fi_mask when fi_or is 1 (a stuck-at), ^= fi_mask when 0 (a flip).
//   These are the semantics the wrapper's hooks had inside sram_bank and wbuf_mem (drops 0.35 / 0.36).
module neo_mem_sp #(
  parameter int W        = 39,                  // word width in bits
  parameter int D        = 256,                 // words
  parameter int G        = 1,                   // write-enable groups per word (W divisible by G)
  parameter int AW       = (D > 1) ? $clog2(D) : 1,
  parameter int DV_HOOKS = 0
)(
  input  logic          clk,
  input  logic [G-1:0]  we,
  input  logic [AW-1:0] waddr,
  input  logic [W-1:0]  wdata,
  input  logic          re,
  input  logic [AW-1:0] raddr,
  output logic [W-1:0]  rdata,
  // testbench hooks (DV_HOOKS = 1)
  input  logic [G-1:0]  bd_we,
  input  logic [AW-1:0] bd_addr,
  input  logic [W-1:0]  bd_wdata,
  output logic [W-1:0]  bd_rdata,
  input  logic          fi_en,
  input  logic          fi_or,
  input  logic [AW-1:0] fi_addr,
  input  logic [W-1:0]  fi_mask
);
  localparam int GW = W / G;
  logic [W-1:0] mem [D];

  always_ff @(posedge clk) begin
    for (int g = 0; g < G; g++) begin
      if (we[g]) mem[waddr][g*GW +: GW] <= wdata[g*GW +: GW];
    end
    if (re) rdata <= mem[raddr];
  end

  generate
    if (DV_HOOKS != 0) begin : g_dv
      always_ff @(posedge clk) begin
        for (int g = 0; g < G; g++) begin
          if (bd_we[g]) mem[bd_addr][g*GW +: GW] <= bd_wdata[g*GW +: GW];
        end
      end
      assign bd_rdata = mem[bd_addr];
      always_ff @(negedge clk) if (fi_en) mem[fi_addr] <= fi_or ? (mem[fi_addr] | fi_mask) : (mem[fi_addr] ^ fi_mask);
    end else begin : g_nodv
      assign bd_rdata = '0;
      logic unused_dv;
      assign unused_dv = ^{bd_we, bd_addr, bd_wdata, fi_en, fi_or, fi_addr, fi_mask};
    end
  endgenerate
endmodule

module neo_mem_rf #(
  parameter int W        = 256,
  parameter int D        = 1024,
  parameter int G        = 1,
  parameter int RB       = 0,                   // 1: the registered read port B exists
  parameter int AW       = (D > 1) ? $clog2(D) : 1,
  parameter int DV_HOOKS = 0
)(
  input  logic          clk,
  input  logic [G-1:0]  we,
  input  logic [AW-1:0] waddr,
  input  logic [W-1:0]  wdata,
  input  logic [AW-1:0] ra_addr,
  output logic [W-1:0]  ra_data,             // combinational
  input  logic          rb_en,
  input  logic [AW-1:0] rb_addr,
  output logic [W-1:0]  rb_data,             // registered (RB = 1), else 0
  // testbench hooks (DV_HOOKS = 1)
  input  logic [G-1:0]  bd_we,
  input  logic [AW-1:0] bd_addr,
  input  logic [W-1:0]  bd_wdata,
  output logic [W-1:0]  bd_rdata,
  input  logic          fi_en,
  input  logic          fi_or,
  input  logic [AW-1:0] fi_addr,
  input  logic [W-1:0]  fi_mask
);
  localparam int GW = W / G;
  logic [W-1:0] mem [D];

  always_ff @(posedge clk) begin
    for (int g = 0; g < G; g++) begin
      if (we[g]) mem[waddr][g*GW +: GW] <= wdata[g*GW +: GW];
    end
  end
  assign ra_data = mem[ra_addr];

  generate
    if (RB != 0) begin : g_rb
      always_ff @(posedge clk) if (rb_en) rb_data <= mem[rb_addr];
    end else begin : g_norb
      assign rb_data = '0;
      logic unused_rb;
      assign unused_rb = ^{rb_en, rb_addr};
    end
    if (DV_HOOKS != 0) begin : g_dv
      always_ff @(posedge clk) begin
        for (int g = 0; g < G; g++) begin
          if (bd_we[g]) mem[bd_addr][g*GW +: GW] <= bd_wdata[g*GW +: GW];
        end
      end
      assign bd_rdata = mem[bd_addr];
      always_ff @(negedge clk) if (fi_en) mem[fi_addr] <= fi_or ? (mem[fi_addr] | fi_mask) : (mem[fi_addr] ^ fi_mask);
    end else begin : g_nodv
      assign bd_rdata = '0;
      logic unused_dv;
      assign unused_dv = ^{bd_we, bd_addr, bd_wdata, fi_en, fi_or, fi_addr, fi_mask};
    end
  endgenerate
endmodule
