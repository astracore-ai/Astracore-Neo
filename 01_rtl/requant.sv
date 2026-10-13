// requant.sv -- per-channel requantization of INT32 accumulator rows to INT8 (drop 0.5; drop 0.41: three stages).
//   q = sat8( round( acc * mult >> shift ) + zp ), then optional ReLU in the quantized domain
//   (max with zp). mult is an unsigned 16-bit multiplier and shift a 5-bit right shift, i.e. the
//   output scale is scale_in * mult / 2^shift, chosen by the compiler per output channel
//   (TFLite-style fixed-point requantization). Rounding is round-half-up on the shifted value.
//   Tables written by DMA.
//   Three pipeline stages since drop 0.41 (two before, the multiply and the shift / round / offset / saturate, 1.85 ns and
//   2.97 ns on the ASAP7 SS proxy): out_valid / out_q are THREE cycles after in_valid / in_acc.
//     1. s = acc * mult + c in carry-save form, c = 2^(shift-1) + zp * 2^shift (the rounding bias, 0 when shift = 0, and
//        the zero point moved above the shift: the two do not overlap, so c is two shifts and an OR). The rows: acc where
//        mult[k] is set, shifted by k, for each of mult's MW bits, each PW bits wide with its sign bit inverted (the sign
//        extensions become one constant, -x * 2^n = (1 - x) * 2^n - 2^n, folded into row 0's free top bits), and c; a
//        tree of 3:2 compressors (full adders, rows taken three at a time in order, c joining after the first level)
//        reduces the MW + 1 rows to two, which are registered: no carry-propagate adder in the stage. Beside them, acc's
//        sign for the ReLU;
//     2. s = the sum of the two rows: the one carry-propagate adder;
//     3. v = s >>> shift, which is round(acc * mult >> shift) + zp exactly (floor((x + zp * 2^shift) / 2^shift) =
//        floor(x / 2^shift) + zp, so the zero point needs no adder of its own); v fits in 8 signed bits when s's bits from
//        7 + shift up all equal its sign, and is then s's byte at shift, otherwise s's sign saturates it (+127 / -128).
//        ReLU (max(v, zp)) gives zp when acc < 0: mult >= 0, so v <= zp then (v = zp when mult = 0 or the rounding
//        brings the product to 0) and v >= zp otherwise -- no compare.
//   Bit-identical to the two-stage form wherever that one does not wrap (PW + MW >= 32, the core's 32 and 16; the sums
//   here are SW bits wide and never wrap; MW >= 2); only the latency changed. The table parity check is on the table
//   read (in_valid's cycle), not in the pipeline, and is unchanged.
//   Drop 0.43 (stream B2): the whole block runs on a gated clock (neo_icg), enabled while a row is in the pipeline (in_valid,
//   v1, v2, out_valid: the edge that clears out_valid included), a table entry is written, the sticky is cleared, and
//   through the reset (cg_en, also an output: the tile's observation of its gated fraction). Every register that a reader
//   samples -- the valids, the tables, the sticky, out_q under out_valid -- changes only in those cycles; the stage
//   registers that load the row unconditionally hold their last row instead of the idle input in the others, which no one
//   reads (out_q is read under out_valid, the comparator and the drain FIFO likewise).
module requant #(
  parameter int COLS = 32,
  parameter int PW   = 32,
  parameter int MW   = 16,
  parameter int CW   = $clog2(COLS)
)(
  input  logic                 clk,
  input  logic                 rst_n,
  // per-column table write
  input  logic                 tbl_we,
  input  logic [CW-1:0]        tbl_addr,
  input  logic [MW-1:0]        tbl_mult,
  input  logic [4:0]           tbl_shift,
  input  logic signed [7:0]    tbl_zp,
  input  logic                 relu,
  // stream
  input  logic                 in_valid,
  input  logic signed [PW-1:0] in_acc [COLS],
  output logic                 out_valid,
  output logic signed [7:0]    out_q [COLS],
  // table parity (drop 0.12): each entry stores odd parity over {mult, shift, zp}; checked on every use
  input  logic                 err_clear,
  output logic                 tbl_perr_sticky,
  input  logic                 tbl_fault_inject,   // DV: corrupt bit 0 of column 0's multiplier
  output logic                 cg_en               // drop 0.43: the block's clock enable (observation)
);
  localparam int BW   = PW + MW + 1;               // product width
  localparam int CTW  = 40;                        // c = 2^(shift-1) + zp * 2^shift: |zp * 2^31| <= 2^38
  localparam int SW   = (BW > CTW) ? BW : CTW + 1; // the sums: |acc * mult + c| < 2^(SW-1) (49 bits at PW 32, MW 16)
  localparam int NIN  = MW + 1;                    // the tree's rows: the MW partial products and c
  localparam int NC   = NIN - 2;                   // the 3:2 compressors: each takes one row away, two remain
  localparam int QI   = ((MW / 3) < NC) ? MW / 3 : NC;   // c joins after the first QI compressors (the first level)
  localparam int NROW = NIN + 2 * NC;              // all the rows: compressor i takes rows 3i .. 3i + 2, makes two

  logic [MW-1:0]      mult  [COLS];
  logic [4:0]         shft  [COLS];
  logic signed [7:0]  zp    [COLS];
  logic               par   [COLS];
  logic               wrt   [COLS];                  // entry has been written (parity meaningful)
  logic [MW-1:0]      mult_p [COLS];                 // multiplier as used (with the DV fault on column 0)
  logic               perr;
  // the gated clock (drop 0.43); v1 / v2 are the stage-1 and stage-2 valids (the stages below)
  logic               gclk;
  logic               v1, v2;
  assign cg_en = in_valid || v1 || v2 || out_valid || tbl_we || err_clear || !rst_n;
  neo_icg u_icg (.clk(clk), .en(cg_en), .te(1'b0), .gclk(gclk));

  always_ff @(posedge gclk) begin
    if (tbl_we) begin
      mult[tbl_addr] <= tbl_mult;
      shft[tbl_addr] <= tbl_shift;
      zp[tbl_addr]   <= tbl_zp;
      par[tbl_addr]  <= ~(^{tbl_mult, tbl_shift, tbl_zp});     // odd parity
    end
  end
  always_ff @(posedge gclk or negedge rst_n) begin
    if (!rst_n) begin
      for (int j = 0; j < COLS; j++) wrt[j] <= 1'b0;
    end else if (tbl_we) begin
      wrt[tbl_addr] <= 1'b1;
    end
  end
  generate
    for (genvar j = 0; j < COLS; j++) begin : g_mp
      if (j == 0) begin : g_f
        assign mult_p[j] = mult[j] ^ {{(MW-1){1'b0}}, tbl_fault_inject};
      end else begin : g_n
        assign mult_p[j] = mult[j];
      end
    end
  endgenerate
  always_comb begin
    perr = 1'b0;
    if (in_valid) begin
      for (int j = 0; j < COLS; j++) begin
        if (wrt[j] && ((^{mult_p[j], shft[j], zp[j]} ^ par[j]) != 1'b1)) perr = 1'b1;
      end
    end
  end
  always_ff @(posedge gclk or negedge rst_n) begin
    if (!rst_n)         tbl_perr_sticky <= 1'b0;
    else if (err_clear) tbl_perr_sticky <= 1'b0;
    else if (perr)      tbl_perr_sticky <= 1'b1;
  end

  // stage 1: the tree (SW-bit rows, totals modulo 2^SW). Row k < MW: acc's low PW-1 bits where mult[k] is set and
  // ~(acc[PW-1] & mult[k]) above them, shifted by k; row 0 carries the sign constant, the sum over k of -2^(PW-1+k)
  // (2^(PW-1) plus the bits from PW+MW-1 up): its bit PW-1 plus ~x makes x at PW-1 and ~x at PW. Row MW + 2 QI is c;
  // compressor i takes rows 3i, 3i + 1, 3i + 2 and makes the sum and carry rows MW + 2i (+ 1 from i = QI on, past c)
  // and the next; the last two rows remain.
  logic [SW-1:0]            row  [COLS][NROW];
  always_comb begin
    for (int j = 0; j < COLS; j++) begin
      row[j][0] = SW'({(in_acc[j][PW-1] & mult_p[j][0]) ^ 1'b1, in_acc[j][PW-1] & mult_p[j][0],
                       in_acc[j][PW-2:0] & {(PW-1){mult_p[j][0]}}}) | ({SW{1'b1}} << (PW + MW - 1));
      for (int k = 1; k < MW; k++)
        row[j][k] = SW'({(in_acc[j][PW-1] & mult_p[j][k]) ^ 1'b1, in_acc[j][PW-2:0] & {(PW-1){mult_p[j][k]}}}) << k;
      row[j][MW + 2*QI] = ({{(SW-8){zp[j][7]}}, zp[j]} << shft[j]) | ((SW'(1) << shft[j]) >> 1);
      for (int i = 0; i < NC; i++) begin
        row[j][MW + 2*i + ((i >= QI) ? 1 : 0)]     = row[j][3*i] ^ row[j][3*i + 1] ^ row[j][3*i + 2];
        row[j][MW + 2*i + ((i >= QI) ? 1 : 0) + 1] = ((row[j][3*i] & row[j][3*i + 1]) | (row[j][3*i] & row[j][3*i + 2]) |
                                                      (row[j][3*i + 1] & row[j][3*i + 2])) << 1;
      end
    end
  end
  logic [SW-1:0]            ra1  [COLS];             // the two rows (stage 1; v1 above)
  logic [SW-1:0]            rb1  [COLS];
  logic                     n1   [COLS];             // acc < 0
  logic [4:0]               sh1  [COLS];
  logic signed [7:0]        zp1  [COLS];
  // stage 2: s = acc * mult + 2^(shift-1) + zp * 2^shift (v2 above)
  logic [SW-1:0]            sum2 [COLS];
  logic                     n2   [COLS];
  logic [4:0]               sh2  [COLS];
  logic signed [7:0]        zp2  [COLS];

  always_ff @(posedge gclk or negedge rst_n) begin
    if (!rst_n) begin
      v1 <= 1'b0; v2 <= 1'b0;
      out_valid <= 1'b0;
      for (int j = 0; j < COLS; j++) begin
        ra1[j] <= '0; rb1[j] <= '0; n1[j] <= 1'b0; sh1[j] <= '0; zp1[j] <= '0;
        sum2[j] <= '0; n2[j] <= 1'b0; sh2[j] <= '0; zp2[j] <= '0; out_q[j] <= '0;
      end
    end else begin
      // stage 1
      v1 <= in_valid;
      for (int j = 0; j < COLS; j++) begin
        ra1[j] <= row[j][NROW-2];
        rb1[j] <= row[j][NROW-1];
        n1[j]  <= in_acc[j][PW-1];
        sh1[j] <= shft[j];
        zp1[j] <= zp[j];
      end
      // stage 2
      v2 <= v1;
      for (int j = 0; j < COLS; j++) begin
        sum2[j] <= ra1[j] + rb1[j];
        n2[j]   <= n1[j];
        sh2[j]  <= sh1[j];
        zp2[j]  <= zp1[j];
      end
      // stage 3: v = s >>> shift; v fits in 8 signed bits when s's bits from 7 + shift up all equal its sign (ss: 1 where
      // one differs); ReLU: zp when acc < 0; saturation
      out_valid <= v2;
      for (int j = 0; j < COLS; j++) begin
        logic [7:0]           v8;                          // v's low byte: s's bits shift + 7 .. shift
        logic [SW-8:0]        ss;
        logic                 neg, fits;
        v8   = 8'(sum2[j] >> sh2[j]);
        neg  = sum2[j][SW-1:SW-1] != 1'b0;
        ss   = sum2[j][SW-1:7] ^ {(SW-7){sum2[j][SW-1:SW-1]}};
        fits = (ss & ({(SW-7){1'b1}} << sh2[j])) == '0;
        if (relu && n2[j])  out_q[j] <= zp2[j];
        else if (fits)      out_q[j] <= v8;
        else                out_q[j] <= neg ? -8'sd128 : 8'sd127;
      end
    end
  end
endmodule
