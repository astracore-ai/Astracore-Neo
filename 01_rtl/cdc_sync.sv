// cdc_sync.sv -- request / acknowledge handshake synchronizer (drop 0.42, the chip top): carries the control plane's
//   register accesses from the AXI clock (top_ctrl) to the mesh clock (mesh_ctrl, the tiles' register buses) and their
//   responses back, one access in flight (neo_top.sv).
//   Mechanism (two-phase toggle handshake with held payloads): side A loads the request payload into a_hold and flips
//   a_tgl in the same cycle; a_hold is not touched again until the access completes. Side B synchronizes a_tgl through
//   two flops (cdc_bits); when the synchronized toggle differs from the last one it acted on, b_req pulses for one cycle
//   and side B serves the request, reading b_req_data = a_hold -- which has been stable for at least two B-clock edges by
//   then, so the payload needs no synchronizer of its own (a multicycle path by construction, constrained as such in the
//   integration note). When side B is done it loads the response into b_hold and flips b_tgl in the same cycle; side A
//   synchronizes b_tgl the same way, takes a_rsp_data from b_hold (stable for two A-clock edges) and pulses a_done. A busy
//   side A refuses new requests (a_busy), so neither held payload changes while the other side may read it. Latency: about
//   two synchronizer cycles each way plus the service time.
//   Reset: side A with a_rst_n, side B with b_rst_n, every toggle and seen-flag 0 -- the two resets are asserted together
//   (neo_top's reset rule); at power-up side A may come out of reset first: a request issued then waits for side B.
module cdc_sync #(
  parameter int RW = 32,                   // request payload width
  parameter int SW = 32                    // response payload width
)(
  // side A (requesting; the AXI clock)
  input  logic          a_clk,
  input  logic          a_rst_n,
  input  logic          a_req,             // start an access with a_req_data (taken only while !a_busy)
  input  logic [RW-1:0] a_req_data,
  output logic          a_busy,            // an access is in flight
  output logic          a_done,            // one cycle: the access completed, a_rsp_data holds its response
  output logic [SW-1:0] a_rsp_data,
  // side B (serving; the mesh clock)
  input  logic          b_clk,
  input  logic          b_rst_n,
  output logic          b_req,             // one cycle: an access arrived; b_req_data holds it until b_done
  output logic [RW-1:0] b_req_data,
  input  logic          b_done,            // one cycle: the access is served, b_rsp_data is its response
  input  logic [SW-1:0] b_rsp_data
);
  // ---- side A ----
  logic          a_tgl, a_ack_s, a_ack_seen;
  logic [RW-1:0] a_hold;
  logic          b_tgl;
  logic [SW-1:0] b_hold;
  always_ff @(posedge a_clk or negedge a_rst_n) begin
    if (!a_rst_n) begin
      a_tgl <= 1'b0; a_hold <= '0; a_ack_seen <= 1'b0; a_done <= 1'b0; a_rsp_data <= '0;
    end else begin
      a_done <= 1'b0;
      if (a_req && !a_busy) begin a_tgl <= !a_tgl; a_hold <= a_req_data; end
      if (a_ack_s != a_ack_seen) begin                 // b_hold has been stable for two a_clk edges
        a_ack_seen <= a_ack_s; a_done <= 1'b1; a_rsp_data <= b_hold;
      end
    end
  end
  assign a_busy = (a_tgl != a_ack_seen);
  cdc_bits #(.W(1)) u_ack_sync (.clk(a_clk), .rst_n(a_rst_n), .d(b_tgl), .q(a_ack_s));

  // ---- side B ----
  logic b_req_s, b_seen;
  cdc_bits #(.W(1)) u_req_sync (.clk(b_clk), .rst_n(b_rst_n), .d(a_tgl), .q(b_req_s));
  always_ff @(posedge b_clk or negedge b_rst_n) begin
    if (!b_rst_n) begin
      b_seen <= 1'b0; b_tgl <= 1'b0; b_hold <= '0;
    end else begin
      if (b_req) b_seen <= b_req_s;
      if (b_done) begin b_tgl <= !b_tgl; b_hold <= b_rsp_data; end
    end
  end
  assign b_req      = (b_req_s != b_seen);
  assign b_req_data = a_hold;              // stable from before the toggle until side A sees the acknowledge
endmodule
