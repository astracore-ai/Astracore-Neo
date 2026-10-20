// neo_mac_core.sv -- AstraCore Neo streaming MAC core v0.3 (drop 0.42: 2:4 structured sparsity)
//   One 32x32 weight-stationary INT8 systolic core (1024 MACs) with ABFT check columns.
//   Streaming interface: present one unskewed activation row per cycle with valid_in; the matching output row
//   Y[m][0..COLS-1] appears on y with valid_out exactly LATENCY = PE_LAT*ROWS + COLS cycles later; the ABFT verdict for
//   that row follows two cycles after valid_out on abft_err, marked by abft_err_valid (drop 0.41: the checker's sum in
//   two stages).
//   Weight load: hold w_load for ROWS cycles, feeding row ROWS-1-t of the tile (and its check weights) on cycle t into
//   the SHADOW registers; then pulse swap_in for one cycle immediately before the first row of the tile. The token
//   travels with the data, so the next tile's shadow load may run while this tile streams (start it >= LATENCY cycles
//   after the token). The check weights are precomputed by the compiler.
//   Drop 0.42 -- NCHK = 4, the sparse-capable silicon core (NCHK = 1 is the core of drop 0.41 bit for bit):
//   * the activation row is 2*ROWS bytes (x_in, packed: byte c at [c*XW +: XW]; at NCHK = 1 ROWS bytes as before, now
//     packed too): dense layers present channel r in byte r (r < ROWS), 2:4 sparse layers (sparse = 1) a 64-channel
//     tile, channel c in byte c; skew_in builds four-byte row buses from it (rows 2g and 2g + 1 both get channels
//     4g .. 4g + 3 in sparse mode);
//   * w_in[j] is the shadow-chain entry {index, weight} of column j (WW + 2 bits): each PE multiplies the byte of its
//     row bus that its index selects (dense: index 0);
//   * wc_in holds the NCHK check weights of the row (column k at [k*WCW +: WCW]: the sum of the row's weights whose
//     index is k; dense: column 0 the row sum, columns 1..3 zero); the NCHK check columns sit side by side at the old
//     check column's position, and y_chk is the sum of their outputs (wrapped at PW bits like every accumulator), so
//     the checker, the accumulator's check word and the drain formats are unchanged; LATENCY does not change either.
//   Not yet: FP8/INT4 modes, output requantization (neo_core). The activation feeder and the accumulator live in
//   neo_mac_core_v02.sv.
//   Drop 0.44 (S1) -- the activation path's byte parity checked at the array's row inputs. The row arrives with one even
//   parity bit per byte (x_par, formed by the feeder from the decoded buffer word, registered with the row) and skew_in
//   carries the bits beside the bytes (p_row). In the cycle a row is valid at row i (v_row[i]) every byte of x_row[i] is
//   checked against its bit; a mismatch raises xpar_err (sticky xpar_err_sticky, the tile's act_perr cause, cleared with
//   abft_clear). This is the check the ABFT cannot make: a byte corrupted before the array's first column feeds every
//   data column and the check column the same wrong value (FMEDA v2: the skew pipes' 79 undetected flips).
module neo_mac_core #(
  parameter int ROWS      = 32,
  parameter int COLS      = 32,
  parameter int XW        = 8,
  parameter int WW        = 8,
  parameter int PW        = 32,
  parameter int WCW       = WW + $clog2(ROWS) + 1,
  parameter int PE_LAT    = 2,                      // psum latency of mac_pe
  parameter int NCHK      = 1,                      // check columns: 1 (dense-only), 4 (2:4 sparse-capable, drop 0.42)
  parameter int IW        = (NCHK > 1) ? 2 : 0,     // index bits per weight (derived)
  parameter int XB        = (NCHK > 1) ? 2 * ROWS : ROWS,   // activation bytes per row (derived)
  parameter int FAULT_ROW = 0,
  parameter int FAULT_COL = 0
)(
  input  logic                  clk,
  input  logic                  rst_n,
  // weight load
  input  logic                  w_load,
  input  logic [WW+IW-1:0]      w_in  [COLS],  // {index, weight} per column
  input  logic [NCHK*WCW-1:0]   wc_in,         // the row's check weights, column k at [k*WCW +: WCW]
  // activation stream in (drop 0.42: a packed row, byte c at [c*XW +: XW]; skew_in's header says why)
  input  logic [XB*XW-1:0]      x_in,
  input  logic [XB-1:0]         x_par,       // drop 0.44: even parity of byte c of x_in, checked at the row inputs
  input  logic                  valid_in,
  input  logic                  swap_in,     // one-cycle token before the first row of a tile
  input  logic                  sparse,      // NCHK = 4: the rows are 64-channel 2:4 tiles (static during a layer)
  // result stream out (aligned)
  output logic signed [PW-1:0]  y     [COLS],
  output logic signed [PW-1:0]  y_chk,
  output logic                  valid_out,
  // safety
  input  logic                  abft_clear,
  output logic                  abft_err,
  output logic                  abft_err_valid,   // drop 0.41: abft_err is the verdict of the row valid_out showed two cycles before
  output logic                  abft_err_sticky,
  output logic                  xpar_err,         // drop 0.44: a byte of a valid row reached the array with the wrong parity
  output logic                  xpar_err_sticky,
  // DV only
  input  logic                  fault_inject
);
  localparam int CT      = COLS + 1;                 // aligned outputs: the data columns and the summed check columns
  localparam int LATENCY = PE_LAT * ROWS + CT - 1;   // = PE_LAT*ROWS + COLS
  localparam int NB      = NCHK;                     // bytes per row bus

  // drop 0.43: an unpacked array that receives a child's unpacked output port is a net (wire): CI's Icarus 12 does not
  // propagate such a port into a variable (x_row, s_row, p_arr and p_algn read X there -- the standing T1 / T2 failures of
  // `make iverilog`); every other simulator treats the two alike
  wire [NB*XW-1:0]      x_row  [ROWS];
  wire [NB-1:0]         p_row  [ROWS];                 // drop 0.44: the bytes' parity bits, skewed with them
  wire                  s_row  [ROWS];
  logic                 v_row  [ROWS];                 // valid skewed like the data rows
  wire signed [PW-1:0]  p_arr  [COLS+NCHK];            // the array's bottom outputs: data, then the check columns
  logic signed [PW-1:0] p_bot  [CT];
  wire signed [PW-1:0]  p_algn [CT];

  skew_in #(.ROWS(ROWS), .XW(XW), .PE_LAT(PE_LAT), .NB(NB)) u_skew (
    .clk(clk), .rst_n(rst_n), .x_vec(x_in), .x_par(x_par), .sparse(sparse), .s_in(swap_in), .x_row(x_row), .p_row(p_row), .s_row(s_row));

  systolic_array #(.ROWS(ROWS), .COLS(COLS), .XW(XW), .WW(WW), .PW(PW), .WCW(WCW), .NCHK(NCHK),
                   .FAULT_ROW(FAULT_ROW), .FAULT_COL(FAULT_COL)) u_array (
    .clk(clk), .rst_n(rst_n), .w_load(w_load), .w_in(w_in), .wc_in(wc_in),
    .x_in(x_row), .s_in(s_row), .v_in(v_row), .fault_inject(fault_inject), .p_out(p_arr));

  generate
    for (genvar r = 0; r < ROWS; r++) begin : g_vskew
      if (r == 0) begin : g_v0
        assign v_row[r] = valid_in;
      end else begin : g_vd
        logic signed [0:0] vq;
        delay_line #(.W(1), .DEPTH(PE_LAT * r)) u_vd (.clk(clk), .rst_n(rst_n), .d(valid_in), .q(vq));
        assign v_row[r] = vq[0];
      end
    end
    for (genvar j = 0; j < COLS; j++) begin : g_pbot
      assign p_bot[j] = p_arr[j];
    end
    // the check side: the sum of the check columns' outputs (they leave the array in the same cycle), wrapped at PW bits;
    // NCHK is 1 or 4 -- a balanced pair of adders, between the check PEs' output registers and the checker's / the
    // accumulator's input registers
    if (NCHK == 1) begin : g_chk1
      assign p_bot[COLS] = p_arr[COLS];
    end else begin : g_chk4
      logic signed [PW-1:0] cs01, cs23;
      assign cs01 = p_arr[COLS] + p_arr[COLS + 1];
      assign cs23 = p_arr[COLS + 2] + p_arr[COLS + 3];
      assign p_bot[COLS] = cs01 + cs23;
    end
  endgenerate

  deskew_out #(.CT(CT), .PW(PW)) u_deskew (
    .clk(clk), .rst_n(rst_n), .p_in(p_bot), .p_out(p_algn));

  generate
    for (genvar j = 0; j < COLS; j++) begin : g_y
      assign y[j] = p_algn[j];
    end
  endgenerate
  assign y_chk = p_algn[COLS];

  // valid travels the same LATENCY as the data
  logic [LATENCY-1:0] valid_pipe;                          // packed shift register: bit 0 newest
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) valid_pipe <= '0;
    else        valid_pipe <= {valid_pipe[LATENCY-2:0], valid_in};
  end
  assign valid_out = valid_pipe[LATENCY-1];

  // drop 0.44: the row-input parity check -- every byte of a valid row against the bit that travelled with it
  logic [ROWS-1:0] xp_bad;
  generate
    for (genvar r = 0; r < ROWS; r++) begin : g_xpar
      logic [NB-1:0] pr;                                   // the parity of each byte of the row bus as it arrives
      for (genvar b = 0; b < NB; b++) begin : g_b
        assign pr[b] = ^x_row[r][b*XW +: XW];
      end
      assign xp_bad[r] = v_row[r] && (pr != p_row[r]);
    end
  endgenerate
  assign xpar_err = (xp_bad != '0);
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n)          xpar_err_sticky <= 1'b0;
    else if (abft_clear) xpar_err_sticky <= 1'b0;
    else if (xpar_err)   xpar_err_sticky <= 1'b1;
  end

  abft_checker #(.COLS(COLS), .PW(PW)) u_abft (
    .clk(clk), .rst_n(rst_n), .valid(valid_out), .y(y), .y_chk(y_chk),
    .abft_clear(abft_clear), .err(abft_err), .err_valid(abft_err_valid), .err_sticky(abft_err_sticky));
endmodule
