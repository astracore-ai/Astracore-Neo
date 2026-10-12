// systolic_array.sv -- ROWS x (COLS + NCHK) weight-stationary systolic array (v0.3, drop 0.46: 2:4 sparsity).
//   Columns 0..COLS-1 hold W[i][j] (WW bits). The ABFT check columns COLS .. COLS + NCHK - 1 hold check weights (WCW
//   bits), so the sum of their outputs equals the sum of the data outputs for every activation row. Any single-point
//   fault in a MAC, a stored weight or the psum chain breaks that equality (see abft_checker).
//   Y[m][j] = sum_i X[m][i] * W[i][j]; x enters pre-skewed (row i delayed PE_LAT*i cycles).
//   Bottom output of column j for row m appears PE_LAT*ROWS + j cycles after row m entered row 0.
//   Weights: w_load shifts the shadow registers down each column; the swap token s_in
//   (pre-skewed like x) makes each PE adopt its shadow one cycle before the next tile's
//   first row reaches it. Shadow loads must not start until a token has left the array.
//   Drop 0.46 -- NCHK = 4, the sparse-capable silicon array (NCHK = 1 is the array of drop 0.41 bit for bit):
//   * every row bus is NB = NCHK bytes wide (x_in [i], NB*XW bits) and every data PE selects its byte by a 2-bit index
//     that shifts down the column with its weight: the shadow chain entry is {index, weight} (w_in, WW + 2 bits);
//   * NCHK = 4 check columns with hard-wired indices: check PE (i, k) multiplies byte k of row i's bus -- the byte is
//     wired, so the check PE is the NB = 1 PE of drop 0.41 -- by the row's check weight for index k, the sum over the
//     data columns of the weights of row i whose index is k (wc_in, column k at [k*WCW +: WCW]; a compiler-side
//     precomputation, as the single check weight was). The four check PEs of a row sit side by side at the position of
//     the old check column: they take x, the swap token and the valid from the last data PE's registers, so their
//     outputs leave the array in the cycle the single check column's did and the core's latency does not change. The
//     core sums the four (neo_mac_core). Dense layers store index 0 everywhere, today's check weight in column 0 and
//     zeros in columns 1..3: check columns 1..3 then add 0 whatever bytes 1..3 of a bus carry;
//   * coverage (the FMEDA argument): a single-point fault in a data PE (its product, its stored weight or index, the
//     byte it selects) or in the psum chain of one column changes that column's output only, so it flips the data side
//     of the equality; a fault in a check PE or its chain flips the check side only.
//   The fault hook selects any PE: FAULT_COL = COLS + k is check column k.
module systolic_array #(
  parameter int ROWS      = 32,
  parameter int COLS      = 32,
  parameter int XW        = 8,
  parameter int WW        = 8,
  parameter int PW        = 32,
  parameter int WCW       = WW + $clog2(ROWS) + 1,   // check-column weight width
  parameter int NCHK      = 1,                       // check columns: 1 (the dense-only array), 4 (2:4 sparse-capable, drop 0.46)
  parameter int NB        = NCHK,                    // bytes per row bus (derived)
  parameter int IW        = (NCHK > 1) ? 2 : 0,      // index bits per weight (derived)
  parameter int FAULT_ROW = 0,                       // PE that honours fault_inject (DV only)
  parameter int FAULT_COL = 0
)(
  input  logic                  clk,
  input  logic                  rst_n,
  input  logic                  w_load,
  input  logic [WW+IW-1:0]      w_in  [COLS],   // {index, weight} entering the top of data columns
  input  logic [NCHK*WCW-1:0]   wc_in,          // check weights entering the top of the check columns, column k at [k*WCW +: WCW]
  input  logic [NB*XW-1:0]      x_in  [ROWS],   // pre-skewed row buses, one per row (NB bytes each)
  input  logic                  s_in  [ROWS],   // pre-skewed swap token, one per row
  input  logic                  v_in  [ROWS],   // pre-skewed valid, one per row (clock gating of the datapath)
  input  logic                  fault_inject,
  output logic signed [PW-1:0]  p_out [COLS+NCHK]  // bottom-of-column outputs, data then the check columns
);
  localparam int CT = COLS + NCHK;

  logic [NB*XW-1:0]      x_w  [ROWS][COLS];   // x leaving data PE(i,j)
  logic                  s_w  [ROWS][COLS];   // swap token leaving data PE(i,j)
  logic                  v_w  [ROWS][COLS];   // valid leaving data PE(i,j), travels with x
  logic signed [PW-1:0]  p_w  [ROWS][CT];     // psum leaving PE(i,j), data then check columns
  logic [WW+IW-1:0]      w_w  [ROWS][COLS];   // shadow {index, weight} held by data PE(i,j)
  logic [WCW-1:0]        wc_w [ROWS][NCHK];   // shadow weight held by check PE(i, COLS + k)
  // the check PEs' x / token / valid outputs: no column follows them
  logic [XW-1:0]         unused_cx [ROWS][NCHK];
  logic                  unused_cs [ROWS][NCHK];
  logic                  unused_cv [ROWS][NCHK];

  generate
    for (genvar i = 0; i < ROWS; i++) begin : g_row
      for (genvar j = 0; j < CT; j++) begin : g_col
        logic signed [PW-1:0] p_src;
        logic                 f_src;
        if (i == 0) begin : g_p0
          assign p_src = '0;
        end else begin : g_pn
          assign p_src = p_w[i-1][j];
        end
        assign f_src = ((i == FAULT_ROW) && (j == FAULT_COL)) ? fault_inject : 1'b0;

        if (j < COLS) begin : g_data
          logic [NB*XW-1:0] x_src;
          logic             s_src;
          logic             v_src;
          logic [WW+IW-1:0] w_src;
          if (j == 0) begin : g_x0
            assign x_src = x_in[i];
            assign s_src = s_in[i];
            assign v_src = v_in[i];
          end else begin : g_xn
            assign x_src = x_w[i][j-1];
            assign s_src = s_w[i][j-1];
            assign v_src = v_w[i][j-1];
          end
          if (i == 0) begin : g_w0
            assign w_src = w_in[j];
          end else begin : g_wn
            assign w_src = w_w[i-1][j];
          end
          mac_pe #(.XW(XW), .WW(WW), .PW(PW), .NB(NB)) u_pe (
            .clk(clk), .rst_n(rst_n), .w_load(w_load),
            .w_in(w_src), .w_out(w_w[i][j]),
            .s_in(s_src), .s_out(s_w[i][j]),
            .v_in(v_src), .v_out(v_w[i][j]),
            .x_in(x_src), .x_out(x_w[i][j]),
            .p_in(p_src), .p_out(p_w[i][j]),
            .fault_inject(f_src));
        end else begin : g_check
          // check column k = j - COLS: byte k of the row bus as the last data PE passes it on (hard-wired index k)
          logic [XW-1:0]  xc_src;
          logic [WCW-1:0] wc_src;
          assign xc_src = x_w[i][COLS-1][(j-COLS)*XW +: XW];
          if (i == 0) begin : g_w0
            assign wc_src = wc_in[(j-COLS)*WCW +: WCW];
          end else begin : g_wn
            assign wc_src = wc_w[i-1][j-COLS];
          end
          mac_pe #(.XW(XW), .WW(WCW), .PW(PW)) u_pe (
            .clk(clk), .rst_n(rst_n), .w_load(w_load),
            .w_in(wc_src), .w_out(wc_w[i][j-COLS]),
            .s_in(s_w[i][COLS-1]), .s_out(unused_cs[i][j-COLS]),
            .v_in(v_w[i][COLS-1]), .v_out(unused_cv[i][j-COLS]),
            .x_in(xc_src), .x_out(unused_cx[i][j-COLS]),
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
