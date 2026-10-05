// delay_line.sv -- DEPTH-cycle delay of a W-bit value, reset to zero.
//   DEPTH = 0 is a wire. No generate-if: the pipe is declared unconditionally (at least one stage) and
//   the stage always-blocks come from a generate-for that has no iterations when DEPTH = 0, which every
//   simulator elaborates the same way (a generate-if on an overridden parameter did not, on Icarus).
module delay_line #(
  parameter int W     = 8,
  parameter int DEPTH = 1
)(
  input  logic                clk,
  input  logic                rst_n,
  input  logic signed [W-1:0] d,
  output logic signed [W-1:0] q
);
  localparam int N = (DEPTH > 0) ? DEPTH : 1;
  logic signed [W-1:0] pipe [N];
  generate
    for (genvar k = 0; k < DEPTH; k++) begin : g_st
      if (k == 0) begin : g_first
        always_ff @(posedge clk or negedge rst_n) begin
          if (!rst_n) pipe[k] <= '0;
          else        pipe[k] <= d;
        end
      end else begin : g_next
        always_ff @(posedge clk or negedge rst_n) begin
          if (!rst_n) pipe[k] <= '0;
          else        pipe[k] <= pipe[k-1];
        end
      end
    end
  endgenerate
  assign q = (DEPTH == 0) ? d : pipe[N-1];
endmodule
