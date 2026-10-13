// cdc_fifo.sv -- asynchronous FIFO between two unrelated clocks (drop 0.42, the chip top): the data path of every
//   noc_axi_bridge crosses the mesh clock / AXI clock boundary through two of these, one per direction (neo_top.sv).
//   Mechanism (the classic dual-clock FIFO): DEPTH entries of W bits in flops (a router-FIFO-sized array, not a memory leaf),
//   written at the write side's binary pointer on the write clock and read combinationally at the read side's binary
//   pointer; each pointer is AW + 1 bits (the extra bit tells full from empty) and is kept beside its binary form as a
//   registered gray code, which is the only thing that crosses: the read pointer's gray code into the write clock, the write
//   pointer's into the read clock, each through a two-flop synchronizer (cdc_bits). A gray code changes one bit per
//   increment, so the synchronized copy is the old pointer or the new one, never a mix; the copy lags the real pointer,
//   which only makes the other side see the FIFO fuller (write side) or emptier (read side) than it is -- never an entry
//   that is not written yet, never a free slot that is not yet read. The entry under the read pointer was written at least
//   two read-clock edges before the synchronized write pointer showed it, so the read mux sees settled data (the timing
//   constraint between the domains is a maximum delay of one read-clock period on mem -> r_data, the integration note).
//   Throughput: a push and a pop per cycle each side; DEPTH covers the pointer round trip (about two cycles of
//   synchronizer, one register each side) at DEPTH = 8 when the clocks are equal.
//   Reset: the write side with wrst_n, the read side with rrst_n; both pointers start at 0, so the two resets must be
//   asserted together (neo_top's reset rule: rst_n and aresetn asserted together, aresetn released first) -- a side reset
//   alone would leave its pointer at 0 against the other side's.
module cdc_fifo #(
  parameter int W     = 8,
  parameter int DEPTH = 8,                 // a power of two, at least 4
  parameter int AW    = $clog2(DEPTH)
)(
  // write side
  input  logic         wclk,
  input  logic         wrst_n,
  input  logic         w_valid,
  input  logic [W-1:0] w_data,
  output logic         w_ready,           // not full
  // read side
  input  logic         rclk,
  input  logic         rrst_n,
  output logic         r_valid,           // not empty
  output logic [W-1:0] r_data,
  input  logic         r_ready
);
  localparam int PW = AW + 1;              // pointer width: the entry index and the lap bit
  logic [W-1:0]  mem [DEPTH];
  logic          push, pop;
  logic [PW-1:0] wbin, wbin_next, wgray, wgray_next;   // write side (wclk)
  logic [PW-1:0] rbin, rbin_next, rgray, rgray_next;   // read side (rclk)
  logic [PW-1:0] rgray_w;                  // the read pointer's gray code, synchronized into wclk
  logic [PW-1:0] wgray_r;                  // the write pointer's gray code, synchronized into rclk

  // ---- write side (wclk) ----
  assign push       = w_valid && w_ready;
  assign wbin_next  = wbin + PW'(push);
  assign wgray_next = (wbin_next >> 1) ^ wbin_next;
  always_ff @(posedge wclk or negedge wrst_n) begin
    if (!wrst_n) begin wbin <= '0; wgray <= '0; end
    else begin wbin <= wbin_next; wgray <= wgray_next; end
  end
  always_ff @(posedge wclk) begin
    if (push) mem[wbin[AW-1:0]] <= w_data;
  end
  cdc_bits #(.W(PW)) u_rsync (.clk(wclk), .rst_n(wrst_n), .d(rgray), .q(rgray_w));
  // full: the write pointer is one lap ahead of the read pointer (gray: the two top bits inverted, the rest equal)
  assign w_ready = (wgray != {~rgray_w[AW:AW-1], rgray_w[AW-2:0]});

  // ---- read side (rclk) ----
  assign pop        = r_valid && r_ready;
  assign rbin_next  = rbin + PW'(pop);
  assign rgray_next = (rbin_next >> 1) ^ rbin_next;
  always_ff @(posedge rclk or negedge rrst_n) begin
    if (!rrst_n) begin rbin <= '0; rgray <= '0; end
    else begin rbin <= rbin_next; rgray <= rgray_next; end
  end
  cdc_bits #(.W(PW)) u_wsync (.clk(rclk), .rst_n(rrst_n), .d(wgray), .q(wgray_r));
  assign r_valid = (rgray != wgray_r);
  assign r_data  = mem[rbin[AW-1:0]];
endmodule
