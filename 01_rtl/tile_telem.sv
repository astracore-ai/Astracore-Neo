// tile_telem.sv -- the tile's telemetry counters (drop 0.43, stream B2): what an integrator measures a layer with before
//   silicon and a scheduler watches after it. NCNT 32-bit saturating counters, one per event bit (ev[k] high in a cycle
//   adds one to counter k; bit 0 is tied high by the tile: the cycle count of the window), and a snapshot register per
//   counter. The host reads snapshots, never the running counters, so one snapshot is a consistent set: every counter
//   sampled at the same edge.
//   Control (host_if's TEL_CTRL, 0x40, one-cycle pulses): snap copies every counter into its snapshot; clear zeroes the
//   counters; both at once take the snapshot of the window that ends and start the next one at zero. Reads: rsel picks
//   snapshot rsel - 1 (host_if's 0x41 .. 0x40 + NCNT; rsel 0 reads NCNT in bits 15:8, the count of counters).
//   The events of neo_tile (bit: event): 0 cycles, 1 array valid (a row enters the array: MAC utilization = 1 / 0), 2 feeder
//   stall (the sequencer in a load, start or run state with no row entering), 3 fetch wait (the DMA engine waiting on a
//   FETCH's response), 4 drain (the NIC's engine sending a drain message), 5 .. 9 link stall of router output N, E, S, W, L
//   (valid and not ready), 10 bank read port busy, 11 bank write port busy.
//   Counters and snapshots are flops (24 x 32 = 768 in the tile); they reset to zero with rst_n.
module tile_telem #(
  parameter int NCNT = 12
)(
  input  logic             clk,
  input  logic             rst_n,
  input  logic [NCNT-1:0]  ev,
  input  logic             snap,
  input  logic             clear,
  input  logic [3:0]       rsel,
  output logic [31:0]      rdata
);
  logic [31:0] cnt  [NCNT];
  logic [31:0] shot [NCNT];
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int k = 0; k < NCNT; k++) begin cnt[k] <= '0; shot[k] <= '0; end
    end else begin
      for (int k = 0; k < NCNT; k++) begin
        if (snap)       shot[k] <= cnt[k];
        if (clear)      cnt[k] <= '0;
        else if (ev[k]) cnt[k] <= (cnt[k] == 32'hFFFF_FFFF) ? cnt[k] : cnt[k] + 32'd1;
      end
    end
  end
  always_comb begin
    rdata = {16'd0, 8'(NCNT), 8'd0};
    for (int k = 0; k < NCNT; k++) if (32'(rsel) == k + 1) rdata = shot[k];
  end
endmodule
