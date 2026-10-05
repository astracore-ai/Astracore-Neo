// delay_line.sv -- DEPTH-register delay for a W-bit signed value (DEPTH may be 0 = pass-through).
// Used by skew_in (row i delayed i cycles) and deskew_out (column j delayed CT-1-j cycles).
module delay_line #(
  parameter int W     = 8,
  parameter int DEPTH = 1
)(
  input  logic                clk,
  input  logic                rst_n,
  input  logic signed [W-1:0] d,
  output logic signed [W-1:0] q
);
  generate
    if (DEPTH == 0) begin : g_pass
      assign q = d;
    end else begin : g_delay
      logic signed [W-1:0] pipe [DEPTH];
      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
          for (int k = 0; k < DEPTH; k++) pipe[k] <= '0;
        end else begin
          pipe[0] <= d;
          for (int k = 1; k < DEPTH; k++) pipe[k] <= pipe[k-1];
        end
      end
      assign q = pipe[DEPTH-1];
    end
  endgenerate
endmodule
