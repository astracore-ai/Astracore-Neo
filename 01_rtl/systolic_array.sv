// systolic_array.sv -- ROWS x (COLS+1) weight-stationary systolic array (v0.2: double-buffered).
//   Columns 0..COLS-1 hold W[i][j] (WW bits). Column COLS is the ABFT check column and
//   holds sum_j W[i][j] (WCW bits), so its output equals the sum of the data outputs for
//   every activation row. Any single-point fault in a MAC, a stored weight or the psum
//   chain breaks that equality (see abft_checker).
//   Y[m][j] = sum_i X[m][i] * W[i][j]; x enters pre-skewed (row i delayed i cycles).
//   Bottom output of column j for row m appears ROWS + j cycles after row m entered row 0.
//   Weights: w_load shifts the shadow registers down each column; the swap token s_in
//   (pre-skewed like x) makes each PE adopt its shadow one cycle before the next tile's
//   first row reaches it. Shadow loads must not start until a token has left the array.
module systolic_array #(
  parameter int ROWS      = 32,
  parameter int COLS      = 32,
  parameter int XW        = 8,
  parameter int WW        = 8,
  parameter int PW        = 32,
  parameter int WCW       = WW + $clog2(ROWS) + 1,   // check-column weight width
  parameter int FAULT_ROW = 0,                       // PE that honours fault_inject (DV only)
  parameter int FAULT_COL = 0
)(
  input  logic                  clk,
  input  logic                  rst_n,
  input  logic                  w_load,
  input  logic signed [WW-1:0]  w_in  [COLS],   // weights entering the top of data columns
  input  logic signed [WCW-1:0] wc_in,          // check weight entering the top of column COLS
  input  logic signed [XW-1:0]  x_in  [ROWS],   // pre-skewed activations, one per row
  input  logic                  s_in  [ROWS],   // pre-skewed swap token, one per row
  input  logic                  v_in  [ROWS],   // pre-skewed valid, one per row (clock gating of the datapath)
  input  logic                  fault_inject,
  output logic signed [PW-1:0]  p_out [COLS+1]  // bottom-of-column outputs, data then check
);
  localparam int CT = COLS + 1;

  logic signed [XW-1:0]  x_w  [ROWS][CT];   // x leaving PE(i,j)
  logic                  s_w  [ROWS][CT];   // swap token leaving PE(i,j)
  logic signed [PW-1:0]  p_w  [ROWS][CT];   // psum leaving PE(i,j)
  logic signed [WW-1:0]  w_w  [ROWS][COLS]; // shadow weight held by data PE(i,j)
  logic signed [WCW-1:0] wc_w [ROWS];       // shadow weight held by check PE(i,COLS)
  logic                  v_w  [ROWS][CT];   // valid leaving PE(i,j), travels with x

  generate
    for (genvar i = 0; i < ROWS; i++) begin : g_row
      for (genvar j = 0; j < CT; j++) begin : g_col
        logic signed [XW-1:0] x_src;
        logic                 s_src;
        logic                 v_src;
        logic signed [PW-1:0] p_src;
        logic                 f_src;
        if (j == 0) begin : g_x0
          assign x_src = x_in[i];
          assign s_src = s_in[i];
          assign v_src = v_in[i];
        end else begin : g_xn
          assign x_src = x_w[i][j-1];
          assign s_src = s_w[i][j-1];
          assign v_src = v_w[i][j-1];
        end
        if (i == 0) begin : g_p0
          assign p_src = '0;
        end else begin : g_pn
          assign p_src = p_w[i-1][j];
        end
        assign f_src = ((i == FAULT_ROW) && (j == FAULT_COL)) ? fault_inject : 1'b0;

        if (j < COLS) begin : g_data
          logic signed [WW-1:0] w_src;
          if (i == 0) begin : g_w0
            assign w_src = w_in[j];
          end else begin : g_wn
            assign w_src = w_w[i-1][j];
          end
          mac_pe #(.XW(XW), .WW(WW), .PW(PW)) u_pe (
            .clk(clk), .rst_n(rst_n), .w_load(w_load),
            .w_in(w_src), .w_out(w_w[i][j]),
            .s_in(s_src), .s_out(s_w[i][j]),
            .v_in(v_src), .v_out(v_w[i][j]),
            .x_in(x_src), .x_out(x_w[i][j]),
            .p_in(p_src), .p_out(p_w[i][j]),
            .fault_inject(f_src));
        end else begin : g_check
          logic signed [WCW-1:0] wc_src;
          if (i == 0) begin : g_w0
            assign wc_src = wc_in;
          end else begin : g_wn
            assign wc_src = wc_w[i-1];
          end
          mac_pe #(.XW(XW), .WW(WCW), .PW(PW)) u_pe (
            .clk(clk), .rst_n(rst_n), .w_load(w_load),
            .w_in(wc_src), .w_out(wc_w[i]),
            .s_in(s_src), .s_out(s_w[i][j]),
            .v_in(v_src), .v_out(v_w[i][j]),
            .x_in(x_src), .x_out(x_w[i][j]),
            .p_in(p_src), .p_out(p_w[i][j]),
            .fault_inject(f_src));
        end
      end
    end
    for (genvar j = 0; j < CT; j++) begin : g_out
      assign p_out[j] = p_w[ROWS-1][j];
    end
  endgenerate
endmodule
