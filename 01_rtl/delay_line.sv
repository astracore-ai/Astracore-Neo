// delay_line.sv -- DEPTH-cycle delay of a W-bit value, reset to zero.
//   DEPTH = 0 is a wire. No generate-if: the pipe is declared unconditionally (at least one stage) and
//   the stage always-blocks come from a generate-for that has no iterations when DEPTH = 0, which every
//   simulator elaborates the same way (a generate-if on an overridden parameter did not, on Icarus).
//   Drop 0.44: RSTV = 1 resets every stage to all ones -- a line that carries the complement of another line's value as
//   its check copy (neo_mac_core's valid / token checks): synthesis merges flops with the same D input into one, so a
//   plain second line of the same signal would fold back into the first; the inverted D with the inverted reset is a
//   distinct register at every stage (acc_bank's complemented copies, drop 0.43).
module delay_line #(
  parameter int W     = 8,
  parameter int DEPTH = 1,
  parameter int RSTV  = 0
)(
  input  logic                clk,
  input  logic                rst_n,
  input  logic signed [W-1:0] d,
  output logic signed [W-1:0] q
);
  localparam int N = (DEPTH > 0) ? DEPTH : 1;
  localparam logic signed [W-1:0] RST = (RSTV != 0) ? {W{1'b1}} : {W{1'b0}};
  logic signed [W-1:0] pipe [N];
  generate
    for (genvar k = 0; k < DEPTH; k++) begin : g_st
      if (k == 0) begin : g_first
        always_ff @(posedge clk or negedge rst_n) begin
          if (!rst_n) pipe[k] <= RST;
          else        pipe[k] <= d;
        end
      end else begin : g_next
        always_ff @(posedge clk or negedge rst_n) begin
          if (!rst_n) pipe[k] <= RST;
          else        pipe[k] <= pipe[k-1];
        end
      end
    end
  endgenerate
  assign q = (DEPTH == 0) ? d : pipe[N-1];
endmodule
