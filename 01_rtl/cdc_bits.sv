// cdc_bits.sv -- two-flop synchronizer of a vector of levels (drop 0.44, the chip top): every flop of neo_top that samples a
//   signal of the other clock domain is the first stage of one of these, so the design's synchronizers are one module a
//   licensee swaps for its synchronizer cell and its CDC tool recognizes. Uses in neo_top: the tiles' status levels (done,
//   err_pin), the effective tile-enable map and the bridge routers' parity flags into the AXI clock; the tile-enable request
//   into the mesh clock; the gray-coded pointers of cdc_fifo. Each bit is synchronized on its own -- a multi-bit value may
//   be read in a mix of its old and new bits for a cycle, so it must be a set of independent levels or a gray code (one bit
//   changes at a time), never a binary count: every use above is one or the other.
//   RESET = 1: both flops reset to 0 with rst_n (asynchronous assertion; the destination domain's reset). RESET = 0: no reset,
//   the flops sample through the destination domain's reset -- the tile-enable request into the mesh clock uses it, so that
//   the map written while the mesh is held in reset is the map the tiles leave reset with (neo_top.sv, mesh_ctrl.sv).
module cdc_bits #(
  parameter int W     = 1,
  parameter int RESET = 1
)(
  input  logic         clk,          // the destination clock
  input  logic         rst_n,        // the destination domain's reset (unused when RESET = 0)
  input  logic [W-1:0] d,            // levels from the source domain, each driven by a flop of that domain
  output logic [W-1:0] q
);
  logic [W-1:0] sync1, sync2;        // the two stages: sync1 may go metastable, sync2 is the synchronized level
  generate
    if (RESET != 0) begin : g_rst
      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin sync1 <= '0; sync2 <= '0; end
        else begin sync1 <= d; sync2 <= sync1; end
      end
    end else begin : g_free
      always_ff @(posedge clk) begin
        sync1 <= d; sync2 <= sync1;
      end
      logic unused_rst;
      assign unused_rst = rst_n;
    end
  endgenerate
  assign q = sync2;
endmodule
