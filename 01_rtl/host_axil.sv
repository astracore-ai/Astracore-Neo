// host_axil.sv -- AXI4-Lite slave wrapper for the tile host register bus (drop 0.15).
//   32-bit data, 10-bit address window: ADDR[9:2] is the host_if word address (0x00..0xFF), so a tile occupies
//   1 KB of the licensee's register space. Single outstanding transaction per channel; a write completes in
//   two cycles (address/data accepted together, then BVALID), a read in two cycles (ARREADY, then RVALID with
//   the data host_if returns one cycle after h_re). RRESP/BRESP are always OKAY: host_if ignores unknown
//   addresses on write and returns zero on read, which is the behaviour an integrator expects of a register block.
module host_axil #(
  parameter int AW = 10
)(
  input  logic          aclk,
  input  logic          aresetn,
  // AXI4-Lite
  input  logic [AW-1:0] s_awaddr,
  input  logic          s_awvalid,
  output logic          s_awready,
  input  logic [31:0]   s_wdata,
  input  logic [3:0]    s_wstrb,
  input  logic          s_wvalid,
  output logic          s_wready,
  output logic [1:0]    s_bresp,
  output logic          s_bvalid,
  input  logic          s_bready,
  input  logic [AW-1:0] s_araddr,
  input  logic          s_arvalid,
  output logic          s_arready,
  output logic [31:0]   s_rdata,
  output logic [1:0]    s_rresp,
  output logic          s_rvalid,
  input  logic          s_rready,
  // tile host register bus (host_if inside neo_tile)
  output logic          h_we,
  output logic          h_re,
  output logic [7:0]    h_addr,
  output logic [31:0]   h_wdata,
  input  logic [31:0]   h_rdata
);
  logic unused_byte_lanes;                          // the word-aligned window: ADDR[1:0] select nothing (lint)
  assign unused_byte_lanes = ^{s_awaddr[1:0], s_araddr[1:0]};
  // ---- write channel: accept address and data together, pulse h_we for one cycle, then respond ----
  logic wr_pend;
  assign s_awready = !wr_pend && !s_bvalid && s_awvalid && s_wvalid;
  assign s_wready  = s_awready;
  assign s_bresp   = 2'b00;
  // ---- read channel: accept, pulse h_re, capture h_rdata the cycle after ----
  logic rd_pend;
  assign s_arready = !rd_pend && !s_rvalid && !s_awready;      // writes have priority on the single bus
  assign s_rresp   = 2'b00;

  always_ff @(posedge aclk or negedge aresetn) begin
    if (!aresetn) begin
      wr_pend <= 1'b0; s_bvalid <= 1'b0; rd_pend <= 1'b0; s_rvalid <= 1'b0; s_rdata <= '0;
      h_we <= 1'b0; h_re <= 1'b0; h_addr <= '0; h_wdata <= '0;
    end else begin
      h_we <= 1'b0; h_re <= 1'b0;
      // write
      if (s_awready) begin                                   // address and data accepted this cycle
        h_we <= 1'b1; h_addr <= s_awaddr[AW-1:2];
        h_wdata <= {s_wstrb[3] ? s_wdata[31:24] : 8'd0, s_wstrb[2] ? s_wdata[23:16] : 8'd0,
                    s_wstrb[1] ? s_wdata[15:8] : 8'd0, s_wstrb[0] ? s_wdata[7:0] : 8'd0};
        wr_pend <= 1'b1;
      end else if (wr_pend) begin
        wr_pend <= 1'b0; s_bvalid <= 1'b1;
      end
      if (s_bvalid && s_bready) s_bvalid <= 1'b0;
      // read
      if (s_arready && s_arvalid) begin
        h_re <= 1'b1; h_addr <= s_araddr[AW-1:2]; rd_pend <= 1'b1;
      end else if (rd_pend && !h_re) begin                   // h_rdata is valid one cycle after h_re
        rd_pend <= 1'b0; s_rvalid <= 1'b1; s_rdata <= h_rdata;
      end
      if (s_rvalid && s_rready) s_rvalid <= 1'b0;
    end
  end
endmodule
