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
//   Drop 0.43 -- the check PE's product as two half products (WW > XW: the ABFT check columns' WW = WCW-bit weight, 14 bits
//   at 32 rows, 13 at 16): w = w_hi * 2^WL + w_lo with w_lo the low WL = floor(WW / 2) bits unsigned and w_hi the rest
//   signed, the product stage registers x * w_lo and x * w_hi side by side (two 8 x 7 multipliers at 14 bits, the data
//   PE's size), and the add stage sums p_in + x * w_lo + (x * w_hi << WL) as one carry-save row more in front of its
//   adder. The sum is p_in + x * w modulo 2^PW, the single product's bit for bit (the ABFT verdict does not change); what
//   changes is the product stage's depth: the 8 x 14 multiplier's path from x was 1,240 ps from the pin (its Booth rows
//   one level deeper than the data PE's 8 x 8, the select lines fanned across 14-bit rows), the array's slowest path (the
//   last data PE's x_out into the check PE: 1,421 ps on the 32 x 32 array in run 180) -- now 1,061 ps, under the data
//   PE's own 1,095 (synth_report_drop043.md). The data PE (WW = XW) is the always block of drop 0.42 unchanged.
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

  // the product and add stages: one product (the data PE, WW = XW: the always block of drop 0.42 as it was), or two half
  // products (the check PE, WW > XW: drop 0.43)
  localparam int WL = (WW > XW) ? WW / 2 : 0;           // the low half's width (0: one product)
  generate
    if (WL == 0) begin : g_one
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
    end else begin : g_half
      // w = w_hi * 2^WL + w_lo (w_lo the low WL bits unsigned, w_hi the upper WW - WL bits signed): the product stage forms
      // x * w_lo (NL bits signed) and x * w_hi (NH bits signed) side by side and registers each with its sign bit inverted
      // -- a signed N-bit v equals (v ^ 2^(N-1)) - 2^(N-1) with the first term zero-extended -- so the add stage sums p_in,
      // the two products without sign extension (no sign bit fanned out across the upper columns of its carry-save row)
      // less one constant KSN (the two 2^(N-1), folded); prod_q holds the low half, ph_q the high half
      localparam int NL = XW + WL + 1;                  // x * w_lo: signed x unsigned -> 16 bits at WW = 14
      localparam int NH = XW + WW - WL;                 // x * w_hi: signed x signed -> 15 bits at WW = 14
      localparam int KSN = (1 << (NL - 1)) + (1 << (NH - 1 + WL));
      logic signed [WL:0]      w_lo;                    // {0, the low WL bits}: unsigned, as a signed operand
      logic signed [WW-WL-1:0] w_hi;                    // the upper bits, signed
      logic signed [NL-1:0]    pl;
      logic signed [NH-1:0]    ph;
      logic [NL-1:0]           pl_i;                    // the two products, sign bits inverted
      logic [NH-1:0]           ph_i, ph_q;
      assign w_lo = {1'b0, w_q[WL-1:0]};                // the halves as signed variables, not $signed() of a
      assign w_hi = w_q[WW-1:WL];                       // part-select: every simulator extends them the same way
      assign pl   = x_sel * w_lo;
      assign ph   = x_sel * w_hi;
      assign pl_i = {~pl[NL-1], pl[NL-2:0]};
      assign ph_i = {~ph[NH-1], ph[NH-2:0]};
      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
          w_q    <= '0;
          w_sh   <= '0;
          x_out  <= '0;
          s_out  <= 1'b0;
          prod_q <= '0;
          ph_q   <= '0;
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
          if (v_in) begin                               // product stage: the two half products, sign bits inverted
            x_out  <= x_in;
            prod_q <= PW'(pl_i);
            ph_q   <= ph_i;
            p_in_q <= p_in;
            inj_q  <= fault_inject;
          end
          if (v_q) begin                                // add stage: p_in + x * w_lo + x * w_hi * 2^WL (+ the hook's 1)
            p_out  <= p_in_q + prod_q + (PW'(ph_q) << WL) - PW'(KSN) + inj;
          end
        end
      end
    end
  endgenerate
endmodule
