// mac_pe.sv -- weight-stationary processing element, pipelined multiply-accumulate (v0.4, drop 0.42: the 2:4 index path).
//   x flows left -> right (one register per PE); the partial-sum path is a two-stage pipeline
//   (stage 1: product and delayed psum, stage 2: add), so PE_LAT = 2 and the row skew in the
//   array is 2 cycles per row. This is the pipeline depth sized for the 2.0 GHz worst-case
//   corner in N5-class silicon (8x8 signed multiply in one stage, 32-bit add in the other).
//   Double-buffered weight: w_load shifts the SHADOW register down the column (w_out = w_sh),
//   and the swap token s_in (which travels with the data: skewed per row, +1 per column)
//   copies shadow -> active one cycle before the first row of the new tile arrives. So the
//   next weight tile loads while the current one streams, with no drain between tiles.
//   Drop 0.42 -- 2:4 structured sparsity (NB = 4, the sparse-capable array): the row bus carries NB activation bytes
//   (x_in / x_out, byte b at [b*XW +: XW]) and the PE multiplies the byte its 2-bit index selects -- a 4:1 byte mux in
//   front of the multiplier, the only logic the product stage gains. The index travels with its weight: the shadow chain
//   entry is {index, weight} (WW + 2 bits, w_in / w_out), and the swap token copies both into the active registers (w_q,
//   and sel_q, the index decoded one-hot). A dense layer stores index 0 (byte 0 of row r's bus is channel r, as before); in a sparse layer rows 2g and
//   2g + 1 both see channels 4g .. 4g + 3 of the 64-channel tile and each PE of the pair holds one of the group's two
//   nonzero weights with its position in the group (systolic_array, skew_in). NB = 1 is the PE of drop 0.41 bit for bit
//   (an XW-bit row bus, no index): the ABFT check columns are NB = 1 PEs with their byte wired by the array.
// fault_inject: adds +1 to this cycle's product (a single-point MAC fault) -- for safety
// verification of the ABFT checker only; tied to 0 on every PE except the one selected
// by FAULT_ROW/FAULT_COL in systolic_array. Remove or fuse off before tape-out.
module mac_pe #(
  parameter int XW = 8,                    // activation width (signed)
  parameter int WW = 8,                    // weight width (signed); wider for the ABFT check column
  parameter int PW = 32,                   // partial-sum width (signed)
  parameter int NB = 1,                    // bytes on the row bus: 1 (the dense-only PE), 4 (2:4 sparse-capable, drop 0.42)
  parameter int IW = (NB > 1) ? 2 : 0      // index bits stored with the weight (derived from NB, not overridden)
)(
  input  logic                 clk,
  input  logic                 rst_n,
  input  logic                 w_load,
  input  logic [WW+IW-1:0]     w_in,       // shadow-chain entry {index, weight} from the PE above
  output logic [WW+IW-1:0]     w_out,      // shadow entry, for the PE below (shift chain)
  input  logic                 s_in,       // swap token (shadow -> active)
  output logic                 s_out,
  input  logic                 v_in,       // valid, travels with x; datapath registers hold when 0 (clock gating)
  output logic                 v_out,
  input  logic [NB*XW-1:0]     x_in,       // the row bus: NB signed activation bytes, byte b at [b*XW +: XW]
  output logic [NB*XW-1:0]     x_out,
  input  logic signed [PW-1:0] p_in,
  output logic signed [PW-1:0] p_out,
  input  logic                 fault_inject
);
  // psum latency through this PE: 2 (stage 1 product, stage 2 add); the sequencer's PE_LAT parameter states it

  logic signed [WW-1:0] w_q;      // active weight
  logic [WW+IW-1:0]     w_sh;     // shadow {index, weight}
  logic signed [XW-1:0] x_sel;    // the activation byte multiplied this cycle
  logic signed [PW-1:0] prod_q;   // stage 1: product
  logic signed [PW-1:0] p_in_q;   // stage 1: incoming psum, delayed to meet the product
  logic                 inj_q;
  logic                 v_q;    // stage 1: fault hook, delayed with the product it affects
  logic signed [PW-1:0] inj;

  assign inj   = inj_q ? PW'(1) : PW'(0);
  assign w_out = w_sh;

  // the index path (NB = 4): the active index, adopted with the weight, selects one byte of the row bus (a 4:1 mux). The
  // swap decodes the shadow's 2-bit index into a one-hot select register (sel_q, 4 flops), so the mux is one AND-OR level
  // driven straight from flops: 1,095 ps for the product stage on the ASAP7 SS proxy against 1,159 ps with the binary
  // index and a mux tree (synth.py's mac_pe unit; the dense-only PE: 1,031 ps). A flipped select bit ORs two bytes or
  // selects none: the product is wrong, which the ABFT check columns see as for any data-PE fault
  generate
    if (NB > 1) begin : g_idx
      logic [3:0] sel_q;
      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)    sel_q <= 4'b0001;
        else if (s_in) sel_q <= 4'b0001 << w_sh[WW +: IW];
      end
      assign x_sel = (x_in[0 +: XW] & {XW{sel_q[0]}}) | (x_in[XW +: XW] & {XW{sel_q[1]}})
                   | (x_in[2*XW +: XW] & {XW{sel_q[2]}}) | (x_in[3*XW +: XW] & {XW{sel_q[3]}});
    end else begin : g_byte
      assign x_sel = x_in;
    end
  endgenerate

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      w_q    <= '0;
      w_sh   <= '0;
      x_out  <= '0;
      s_out  <= 1'b0;
      prod_q <= '0;
      p_in_q <= '0;
      inj_q  <= 1'b0;
      p_out  <= '0;
      v_out  <= 1'b0;
      v_q    <= 1'b0;
    end else begin
      if (w_load) w_sh <= w_in;
      if (s_in)   w_q  <= w_sh[WW-1:0];
      s_out  <= s_in;
      v_out  <= v_in;
      v_q    <= v_in;
      if (v_in) begin                               // product stage: enabled by the incoming valid
        x_out  <= x_in;
        prod_q <= x_sel * w_q;                      // all operands signed -> sign-extended product
        p_in_q <= p_in;
        inj_q  <= fault_inject;
      end
      if (v_q) begin                                // add stage: enabled by the valid one cycle later
        p_out  <= p_in_q + prod_q + inj;
      end
    end
  end
endmodule
