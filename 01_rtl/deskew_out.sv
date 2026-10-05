// deskew_out.sv -- delays column j by (CT-1-j) cycles so that all CT column outputs
// of one activation row leave the block in the same cycle.
module deskew_out #(
  parameter int CT = 33,
  parameter int PW = 32
)(
  input  logic                 clk,
  input  logic                 rst_n,
  input  logic signed [PW-1:0] p_in  [CT],
  output logic signed [PW-1:0] p_out [CT]
);
  generate
    for (genvar j = 0; j < CT; j++) begin : g_col
      delay_line #(.W(PW), .DEPTH(CT-1-j)) u_dl (.clk(clk), .rst_n(rst_n), .d(p_in[j]), .q(p_out[j]));
    end
  endgenerate
endmodule
