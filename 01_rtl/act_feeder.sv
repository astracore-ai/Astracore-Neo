// act_feeder.sv -- sliding-window activation feeder (implicit im2col), v0.5 (drop 0.41: incremental addresses, synchronous
//   read; drop 0.42: 2:4 sparse layers, a pixel per pair word -- see the end of this header).
//   Holds channel tiles of one input in a local buffer of REGIONS regions (a power of two):
//   abuf[(ct mod regions)*tile_pixels + iy*w + ix][channel]; the DMA refills a region after the
//   sequencer's ct_free for the tile that used it,
//   ROWS channels per entry. For a run at kernel position (cfg_ky, cfg_kx) over channel tile cfg_ct
//   it walks the output grid (oy, ox) and emits, one row per cycle, the ROWS channels of input pixel
//   (oy*s + ky - p, ox*s + kx - p), or zeros outside the tile (padding). Each output row is tagged
//   with its accumulator index (row_idx) and the acc_first flag sampled at start.
//   Safety (R7 from the FMEDA): the address generator and run FSM are duplicated; the two copies
//   see the same inputs and a comparator flags any disagreement on address, bounds, valid, swap or
//   row index (ctrl_err, sticky), because a consistently wrong input is invisible to ABFT.
//   ctrl_fault_inject (DV only) corrupts bit 0 of the primary address to prove the comparator.
//   Since drop 0.32 the buffer is organised in pairs of entries -- ABUF_DEPTH/2 words of two entries, the word address
//   abuf_waddr[AW-1:1], the entry inside the word abuf_waddr[0] -- and its one write port is a word wide: abuf_we writes
//   the entry at abuf_waddr, abuf_we2 the upper entry of the same word (entry abuf_waddr + 1; the DMA asserts it only at
//   an even abuf_waddr), so a 16-word beat of two 8-word entries lands in one cycle. The read is one entry per cycle.
//   Drop 0.40: the buffer is a neo_mem_rf leaf of ABUF_DEPTH/2 words, each a pair of entries (2 x ROWS x XW bits) with one
//   write-enable group per entry -- the module the synthesis flow black-boxes and a memory compiler replaces.
//
//   Drop 0.41 -- the address without multipliers in the per-pixel path, and the read through the leaf's registered port B
//   (an SRAM macro's read: address at the edge, data the cycle after). Until drop 0.40 the address was a combinational
//   function of the loop counters, addr = (ct & regions_m1)*tile_pixels + (oy*s + ky - p - iy0)*w + ox*s + kx - p (three
//   18-bit multipliers and an adder chain) feeding the asynchronous port A, the entry select and the array's skew
//   registers in one cycle (3.0 ns on ASAP7 SS at the unit, 3.4 ns at the core). Now:
//   * registers iy, ix (18 bits, signed: the input pixel's coordinates), arow (AW bits: the address of the row's first
//     pixel, region base + (iy - iy0)*w + kx - p) and a (the address of the pixel generated, arow + ix - (kx - p) -- the
//     brief's rowb + ix held in a register), updated by additions only: per output pixel ix += s, a += s; at a row end
//     (ox == wo - 1) ix = kx - p, iy += s, arow = a = arow + sw. inb = iy in [0, h) and ix in [0, w) (compares against
//     the descriptor), addr = inb ? a : 0 (no add in the address path). oy / ox stay as the loop counters (row end, run
//     end); they are not in the address. Every quantity is the old expression's value modulo 2^18 (iy, ix) or 2^AW (arow,
//     a, addr), so the stream is bit-identical to drop 0.40's.
//   * descriptor constants: iyb = oy0*s - p, sw = s*w, q = (oy0*s - p - iy0)*w, 3*tile_pixels and 3*w, computed from the
//     ports (static: written before go, held for the layer -- multicycle paths by protocol) into registers.
//   * the start-time terms. cfg_ct / cfg_ky / cfg_kx are NOT descriptor fields: they are the sequencer's run registers,
//     which change at the edge into its START state, one cycle before start -- a single-cycle path that no multicycle
//     constraint may cover. The products (ct & regions_m1)*tile_pixels and ky*w therefore take the two cycles the
//     pipeline has, and neither cycle holds a multiplier: in the start cycle each radix-4 digit of the per-run operand
//     selects 0, x, 2x or 3x of the static factor (shifted by its position), and a carry-save tree reduces the digit
//     products -- with kx - p and q -- to two rows per product (s1 / c1, s2 / c2, registered at the start edge); the token
//     cycle adds the four rows into arow and a; the first address is formed in the cycle after the token. start itself
//     sets only running and the token: every generator register (oy, ox, m, first, iy = iyb + ky, ix = kx - p, arow, a)
//     loads in the token cycle, the sequencer holding ct / ky / kx and first through the run -- so the sequencer's
//     start strobe drives two registers per copy, not the whole generator. ABUF_DEPTH <= 4096 (AW <= 12: six digits
//     fill the tree; a deeper buffer collides with the tree's fixed operands and fails lint and synthesis as a
//     multiply-driven net).
//   * the read: rb_en = the generated valid, rb_addr = addr[AW-1:1]; the pair arrives one cycle later. Every output moves
//     one cycle with it: valid, swap, row_idx and first are the generated values delayed by one register; x_vec is the
//     entry of rb_data selected by the delayed addr[0], zeroed when the delayed inb or the delayed valid is 0. busy =
//     running || out_pending, so the sequencer sees the run end when the last row has left. Port A is tied off.
//   Timing of a run: start at the edge ending cycle T; generated token at T+1, generated rows m = 0.. at T+2..; outputs one
//   cycle later -- swap at T+2, row m at T+3+m (drop 0.40: T+1 and T+2+m); busy falls the cycle after the last row.
//   * the duplicate (R7) mirrors every register: its own iy, ix, arow, a, constants, start-time rows, counters and the
//     output registers of valid / swap / row_idx. The constant and start-time registers of both copies load while their
//     generator is idle and hold while it runs, so each depends on its own copy's state (two registers fed by the same
//     port logic would be merged into one by synthesis). The comparator checks valid, swap and row_idx on the delayed
//     outputs of both copies and addr / inb in the generation cycle, as before; the fault hook stays on the primary's addr.
//
//   Drop 0.42 -- 2:4 sparse layers on the NCHK = 4 core (NCHK = 1 is the feeder of drop 0.41 bit for bit). A sparse
//   channel tile is 64 channels and a pixel's 64 channels are one pair word of the buffer: entry 2k holds channels
//   0..31, entry 2k + 1 channels 32..63 (the compiler's activation layout for sparse layers). The generator is A1c's and
//   counts pixels as before -- cfg_tile_pixels, the region base (ct & regions_m1)*tile_pixels, iy0, w, s are all in
//   pixels in both modes -- and in sparse mode every address is doubled: the per-pixel step is 2s, the row step 2sw
//   (registered while idle, like sw), and the token cycle shifts the start-time sum left by one, so the entry address
//   a = 2*(region base + (iy - iy0)*w + ix) is the pair's even entry and addr[AW-1:1] -- the leaf's word address,
//   unchanged -- is the pixel; nothing new on the address path to the leaf. The row presented is 2*ROWS bytes (x_vec):
//   bytes 0..ROWS-1 are the selected entry as before (in sparse mode addr[0] is 0, so the pair's lower entry: channels
//   0..31), bytes ROWS..2*ROWS-1 the pair's upper entry in sparse mode (channels 32..63) and zero in dense mode -- the
//   whole pair word per cycle; skew_in regroups it into four-byte row buses (channels 4g .. 4g + 3 to rows 2g and
//   2g + 1). The duplicate generator doubles its own addresses, so the comparator still checks every address.
//
//   Drop 0.43 (D2) -- the storage drop 0.41's synchronous read put behind the comparator, compared now. The registers
//   between the generation cycle and the row presented -- inb_q (the row is inside the tile), a0_q (the entry of the pair)
//   and first_oq / first_q (the run's acc_first) -- had no copy, and the leaf's port-B address latch (which word of the
//   buffer is read) none either: a fault in any of them presents a consistently wrong row (a zeroed row, the other entry
//   of the pair, another pixel, an overwrite instead of an accumulate), which the ABFT check column sees exactly as the
//   data columns do, so it was undetected. The duplicate now has its own inb_dq, a0_dq, first_dq / first_doq and addr_dq
//   (its generated pair address, registered with its valid), and the comparator adds first_oq against first_doq every
//   cycle and, in the cycle a row is presented (valid_q), inb_q / a0_q against inb_dq / a0_dq and the leaf's latched
//   address (neo_mem_rf RBQ = 1: rb_addr_q, the word rb_data shows) against addr_dq. A flip in any of them raises ctrl_err
//   in the cycle the row is presented.
module act_feeder #(
  parameter int ROWS       = 32,
  parameter int XW         = 8,
  parameter int ABUF_DEPTH = 4096,
  parameter int AW         = $clog2(ABUF_DEPTH),
  parameter int IDXW       = 9,
  parameter int NCHK       = 1,                       // the core's check columns: 4 = the sparse-capable core (drop 0.42)
  parameter int XB         = (NCHK > 1) ? 2 * ROWS : ROWS   // bytes presented per row (derived)
)(
  input  logic                 clk,
  input  logic                 rst_n,
  // buffer write port (DMA)
  input  logic                 abuf_we,
  input  logic [AW-1:0]        abuf_waddr,
  input  logic signed [XW-1:0] abuf_wdata [ROWS],
  input  logic                 abuf_we2,       // the upper entry of the same pair (abuf_waddr even), drop 0.32
  input  logic signed [XW-1:0] abuf_wdata2 [ROWS],
  // configuration (static during a run; cfg_ct / cfg_ky / cfg_kx are the sequencer's run registers, see the header)
  input  logic signed [15:0]   cfg_h,          // input tile height (rows of pixels)
  input  logic signed [15:0]   cfg_w,          // input tile width
  input  logic signed [15:0]   cfg_ho,         // output rows of the whole layer
  input  logic signed [15:0]   cfg_wo,         // output columns
  input  logic signed [15:0]   cfg_oy0,        // first output row of this descriptor's M-chunk
  input  logic signed [15:0]   cfg_oy_n,       // output rows in this M-chunk
  input  logic signed [15:0]   cfg_iy0,        // first input row present in the activation region
  input  logic signed [15:0]   cfg_s,          // stride
  input  logic signed [15:0]   cfg_p,          // padding
  input  logic signed [15:0]   cfg_tile_pixels,// entries per channel tile (= h*w)
  input  logic signed [15:0]   cfg_regions_m1, // regions in the buffer minus one (regions are a power of two)
  input  logic signed [15:0]   cfg_ct,         // channel tile of this run
  input  logic signed [15:0]   cfg_ky,         // kernel position of this run
  input  logic signed [15:0]   cfg_kx,
  input  logic                 acc_first,      // 1: this run overwrites the accumulator rows
  input  logic                 cfg_sparse,     // NCHK = 4: the layer is 2:4 sparse (64-channel tiles, a pixel per pair word)
  // control
  input  logic                 start,
  output logic                 busy,
  // stream out
  output logic [XB*XW-1:0]     x_vec,          // byte c at [c*XW +: XW]: dense, the entry in bytes 0..ROWS-1; sparse, the pair word
  output logic                 valid,
  output logic                 swap,
  output logic [IDXW-1:0]      row_idx,
  output logic                 first,
  // safety
  input  logic                 ctrl_clear,
  output logic                 ctrl_err,
  output logic                 ctrl_err_sticky,
  input  logic                 ctrl_fault_inject
);
  logic unused_cfg;                                 // the layer's output height is not needed here: cfg_oy0 / cfg_oy_n bound the run;
  assign unused_cfg = ^{cfg_ho, cfg_tile_pixels};   // of the tile size only the bits below AW reach the address (modulo 2^AW) (lint)
  localparam int EWB = ROWS * XW;                   // one entry: ROWS channels
  localparam int BWD = 2 * EWB;                     // one word of the leaf: the pair of entries
  logic [BWD-1:0] pair_wdata, pair_rdata;           // the leaf's write word and the pair read (port B, one cycle after the address)
  logic [1:0]     pair_we;
  logic [EWB-1:0] ent_rd;                           // the entry read (the delayed addr[0] selects it inside the pair)
  logic [BWD-1:0] unused_ra, unused_bd;             // the leaf's unused read ports (port A tied off, no hooks here)
  logic [AW-2:0]  rb_addr_q;                        // drop 0.43: the leaf's latched port-B address (the pair now on pair_rdata)
  logic           sp;                               // drop 0.42: a sparse layer (NCHK = 4 only): addresses doubled, the pair presented
  generate
    if (NCHK > 1) begin : g_sp
      assign sp = cfg_sparse;
    end else begin : g_nosp
      assign sp = 1'b0;
      logic unused_sparse;
      assign unused_sparse = cfg_sparse;
    end
  endgenerate

  // ---- the start-time arithmetic of both generators ----
  // The radix-4 digit products of the per-run operands and their carry-save sums, four trees: [0] / [1] the primary's
  // p1 / p2 terms, [2] / [3] the duplicate's. Tree u sums its eight operands cso[u][0..7] into two rows css[u] + csc[u]
  // (modulo 2^AW) through four levels of 3:2 compressors; unused operands are zero and fold away in synthesis.
  localparam int ND  = (AW + 1) / 2;                // radix-4 digits of an AW-bit per-run operand: ND <= 6 (AW <= 12, ABUF_DEPTH <= 4096)
  localparam int DWP = 2 * ND;                      // the operand padded to whole digits
  logic [DWP-1:0]       ctm, kyv;                   // the per-run operands: (ct & regions_m1) and ky, padded to ND digits
  logic [AW-1:0]        cso [4][8];
  logic [AW-1:0]        css [4];
  logic [AW-1:0]        csc [4];
  assign ctm = DWP'(cfg_ct & cfg_regions_m1);
  assign kyv = DWP'(cfg_ky);
  generate
    for (genvar u = 0; u < 4; u++) begin : g_csa
      logic [AW-1:0] s10, c10, s11, c11, s20, c20, s21, c21, s3, c3;
      assign s10    = cso[u][0] ^ cso[u][1] ^ cso[u][2];
      assign c10    = ((cso[u][0] & cso[u][1]) | (cso[u][0] & cso[u][2]) | (cso[u][1] & cso[u][2])) << 1;
      assign s11    = cso[u][3] ^ cso[u][4] ^ cso[u][5];
      assign c11    = ((cso[u][3] & cso[u][4]) | (cso[u][3] & cso[u][5]) | (cso[u][4] & cso[u][5])) << 1;
      assign s20    = s10 ^ c10 ^ s11;
      assign c20    = ((s10 & c10) | (s10 & s11) | (c10 & s11)) << 1;
      assign s21    = c11 ^ cso[u][6] ^ cso[u][7];
      assign c21    = ((c11 & cso[u][6]) | (c11 & cso[u][7]) | (cso[u][6] & cso[u][7])) << 1;
      assign s3     = s20 ^ c20 ^ s21;
      assign c3     = ((s20 & c20) | (s20 & s21) | (c20 & s21)) << 1;
      assign css[u] = s3 ^ c3 ^ c21;
      assign csc[u] = ((s3 & c3) | (s3 & c21) | (c3 & c21)) << 1;
    end
  endgenerate

  // ---- primary address generator / run FSM ----
  logic                 running;
  logic                 tok;
  logic                 valid_g;                    // a row is generated this cycle (its address goes to the leaf)
  logic signed [15:0]   oy;
  logic signed [15:0]   ox;
  logic [IDXW-1:0]      m;
  logic                 first_q;
  logic signed [17:0]   iy;                         // input row of the pixel generated (oy*s + ky - p)
  logic signed [17:0]   ix;                         // input column (ox*s + kx - p)
  logic [AW-1:0]        arow;                       // address of the row's first pixel: region base + (iy - iy0)*w + kx - p (mod 2^AW)
  logic [AW-1:0]        a;                          // address of the pixel generated: arow + (ix - (kx - p)) = region base + (iy - iy0)*w + ix
  logic [AW-1:0]        arow_nx;                    // arow + sw: the next row's first pixel
  // descriptor constants (from the ports, multicycle by protocol) and the start-time terms; every one of these registers
  // loads while the generator is idle and holds while it runs (so the duplicate's copies stay registers of their own)
  logic signed [17:0]   iyb;                        // oy0*s - p
  logic [AW-1:0]        sw;                         // s*w
  logic [AW-1:0]        q;                          // (oy0*s - p - iy0)*w
  logic [AW-1:0]        tp3;                        // 3*tile_pixels, 3*w: the radix-4 digit multiples
  logic [AW-1:0]        w3;
  logic [AW-1:0]        s1, c1;                     // (ct & regions_m1)*tile_pixels + kx - p, carry-save (the start cycle's)
  logic [AW-1:0]        s2, c2;                     // ky*w + q, carry-save
  logic                 inb;
  logic                 row_end;
  logic [AW-1:0]        addr_raw;
  logic [AW-1:0]        addr;
  // the output register stage (one cycle behind the generation: the leaf's read latency)
  logic                 valid_q;
  logic                 swap_q;
  logic [IDXW-1:0]      idx_q;
  logic                 first_oq;
  logic                 inb_q;
  logic                 a0_q;                       // addr[0] of the row read: the entry inside the pair

  assign valid_g  = running && !tok;
  assign row_end  = (ox == cfg_wo - 1);
  assign inb      = (iy >= 0) && (iy < 18'(cfg_h)) && (ix >= 0) && (ix < 18'(cfg_w));
  assign addr_raw = inb ? a : '0;
  assign addr     = addr_raw ^ {{(AW-1){1'b0}}, ctrl_fault_inject};
  assign arow_nx  = arow + sw;

  assign busy    = running || valid_q;              // out_pending: the last row is still in the output register
  assign swap    = swap_q;
  assign valid   = valid_q;
  assign row_idx = idx_q;
  assign first   = first_oq;

  // the leaf (drop 0.40): abuf_we writes the entry abuf_waddr[0] of the pair abuf_waddr[AW-1:1], abuf_we2 the pair's
  // upper entry in the same cycle (the upper entry takes abuf_we2's data when both address it, as before). The two
  // entries are packed in their own blocks, each variable with exactly one driver and a default (the pack / unpack
  // pattern of wbuf_mem: a loop that assigns slices of one vector from an unpacked array port is what every simulator
  // evaluates alike). Drop 0.41: read on port B (registered), port A tied off.
  logic [EWB-1:0] ent_w, ent_w2;                    // the two entries packed
  generate
    for (genvar c = 0; c < ROWS; c++) begin : g_pack
      assign ent_w[c*XW +: XW]  = abuf_wdata[c];
      assign ent_w2[c*XW +: XW] = abuf_wdata2[c];
    end
  endgenerate
  assign pair_wdata = {abuf_we2 ? ent_w2 : ent_w, ent_w};
  assign pair_we    = {abuf_we2 || (abuf_we && abuf_waddr[0]), abuf_we && !abuf_waddr[0]};
  neo_mem_rf #(.W(BWD), .D(ABUF_DEPTH/2), .G(2), .RB(1), .RBQ(1), .DV_HOOKS(0)) u_mem (
    .clk(clk),
    .we(pair_we), .waddr(abuf_waddr[AW-1:1]), .wdata(pair_wdata),
    .ra_addr('0), .ra_data(unused_ra),
    .rb_en(valid_g), .rb_addr(addr[AW-1:1]), .rb_data(pair_rdata), .rb_addr_q(rb_addr_q),
    .bd_we('0), .bd_addr('0), .bd_wdata('0), .bd_rdata(unused_bd),
    .fi_en(1'b0), .fi_or(1'b0), .fi_addr('0), .fi_mask('0));
  assign ent_rd = a0_q ? pair_rdata[BWD-1:EWB] : pair_rdata[EWB-1:0];
  generate
    for (genvar c = 0; c < ROWS; c++) begin : g_out
      assign x_vec[c*XW +: XW] = (valid_q && inb_q) ? ent_rd[c*XW +: XW] : '0;
    end
    for (genvar c = ROWS; c < XB; c++) begin : g_out_hi      // NCHK = 4: the pair's upper entry, in sparse mode only
      assign x_vec[c*XW +: XW] = (valid_q && inb_q && sp) ? pair_rdata[c*XW +: XW] : '0;
    end
  endgenerate

  // the primary's tree operands: digit k of the per-run operand selects 0, x, 2x or 3x of the static factor x (shifted by
  // 2k); tree 0 adds kx - p (as kx + ~p, the +1 in the carry row's free bit 0 below), tree 1 adds q
  generate
    for (genvar k = 0; k < ND; k++) begin : g_dig
      logic [1:0] d1, d2;
      assign d1 = ctm[2*k +: 2];
      assign d2 = kyv[2*k +: 2];
      assign cso[0][k] = ((d1 == 2'd0) ? '0 : (d1 == 2'd1) ? AW'(cfg_tile_pixels) : (d1 == 2'd2) ? AW'(cfg_tile_pixels) << 1 : tp3) << (2*k);
      assign cso[1][k] = ((d2 == 2'd0) ? '0 : (d2 == 2'd1) ? AW'(cfg_w) : (d2 == 2'd2) ? AW'(cfg_w) << 1 : w3) << (2*k);
    end
    for (genvar k = ND; k < 6; k++) begin : g_dig_z
      assign cso[0][k] = '0;
      assign cso[1][k] = '0;
    end
  endgenerate
  assign cso[0][6] = AW'(cfg_kx);
  assign cso[0][7] = ~AW'(cfg_p);
  assign cso[1][6] = q;
  assign cso[1][7] = '0;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      iyb <= '0; sw <= '0; q <= '0; tp3 <= '0; w3 <= '0; s1 <= '0; c1 <= '0; s2 <= '0; c2 <= '0;
    end else if (!running) begin
      iyb <= 18'(cfg_oy0) * 18'(cfg_s) - 18'(cfg_p);
      sw  <= (AW'(cfg_s) * AW'(cfg_w)) << sp;      // sparse: in entries, two per pixel
      q   <= AW'(18'(cfg_oy0) * 18'(cfg_s) - 18'(cfg_p) - 18'(cfg_iy0)) * AW'(cfg_w);
      tp3 <= AW'(cfg_tile_pixels) + (AW'(cfg_tile_pixels) << 1);
      w3  <= AW'(cfg_w) + (AW'(cfg_w) << 1);
      s1  <= css[0];
      c1  <= csc[0] | AW'(1);                       // the +1 of -p (bit 0 of a carry row is free)
      s2  <= css[1];
      c2  <= csc[1];
    end
  end

  // start sets running and the token; the generator registers load in the token cycle (the sequencer holds cfg_ct /
  // cfg_ky / cfg_kx and acc_first from the cycle before start until the run has ended), so start drives two registers
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      running <= 1'b0;
      tok     <= 1'b0;
    end else begin
      tok <= start && !running;
      if (start && !running) running <= 1'b1;
      else if (valid_g && row_end && (oy == cfg_oy0 + cfg_oy_n - 1)) running <= 1'b0;
    end
  end
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      oy      <= '0;
      ox      <= '0;
      m       <= '0;
      first_q <= 1'b0;
      iy      <= '0;
      ix      <= '0;
      arow    <= '0;
      a       <= '0;
    end else if (tok) begin                         // the token cycle: the run's first output row
      oy      <= cfg_oy0;
      ox      <= '0;
      m       <= '0;
      first_q <= acc_first;
      iy      <= iyb + 18'(cfg_ky);
      ix      <= 18'(cfg_kx) - 18'(cfg_p);
      arow    <= (s1 + c1 + s2 + c2) << sp;        // sparse: the pixel's even entry
      a       <= (s1 + c1 + s2 + c2) << sp;
    end else if (valid_g) begin
      m <= m + 1;
      if (row_end) begin
        ox   <= '0;
        ix   <= 18'(cfg_kx) - 18'(cfg_p);
        iy   <= iy + 18'(cfg_s);
        arow <= arow_nx;
        a    <= arow_nx;
        if (oy != cfg_oy0 + cfg_oy_n - 1) oy <= oy + 1;
      end else begin
        ox <= ox + 1;
        ix <= ix + 18'(cfg_s);
        a  <= a + (AW'(cfg_s) << sp);
      end
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      valid_q  <= 1'b0;
      swap_q   <= 1'b0;
      idx_q    <= '0;
      first_oq <= 1'b0;
      inb_q    <= 1'b0;
      a0_q     <= 1'b0;
    end else begin
      valid_q  <= valid_g;
      swap_q   <= tok;
      idx_q    <= m;
      first_oq <= first_q;
      inb_q    <= inb;
      a0_q     <= addr[0];
    end
  end

  // ---- duplicate address generator / run FSM (R7) and comparator: every register mirrored ----
  logic                 running_d;
  logic                 tok_d;
  logic                 valid_gd;
  logic signed [15:0]   oy_d;
  logic signed [15:0]   ox_d;
  logic [IDXW-1:0]      m_d;
  logic signed [17:0]   iy_d;
  logic signed [17:0]   ix_d;
  logic [AW-1:0]        arow_d;
  logic [AW-1:0]        a_d;
  logic [AW-1:0]        arow_nx_d;
  logic signed [17:0]   iyb_d;
  logic [AW-1:0]        sw_d, q_d, tp3_d, w3_d, s1_d, c1_d, s2_d, c2_d;
  logic                 inb_d;
  logic                 row_end_d;
  logic [AW-1:0]        addr_d;
  logic                 valid_dq;
  logic                 swap_dq;
  logic [IDXW-1:0]      idx_dq;
  // drop 0.43: the duplicate's copies of the registers behind the generation cycle (the header's D2 paragraph)
  logic                 first_dq;                   // acc_first of the run (the primary's first_q)
  logic                 first_doq;                  // and its output register (first_oq)
  logic                 inb_dq;                     // inb_q
  logic                 a0_dq;                      // a0_q
  logic [AW-2:0]        addr_dq;                    // the generated pair address, registered with the duplicate's valid: the leaf's rb_addr_q

  assign valid_gd  = running_d && !tok_d;
  assign row_end_d = (ox_d == cfg_wo - 1);
  assign inb_d     = (iy_d >= 0) && (iy_d < 18'(cfg_h)) && (ix_d >= 0) && (ix_d < 18'(cfg_w));
  assign addr_d    = inb_d ? a_d : '0;
  assign arow_nx_d = arow_d + sw_d;

  generate
    for (genvar k = 0; k < ND; k++) begin : g_dig_d
      logic [1:0] d1, d2;
      assign d1 = ctm[2*k +: 2];
      assign d2 = kyv[2*k +: 2];
      assign cso[2][k] = ((d1 == 2'd0) ? '0 : (d1 == 2'd1) ? AW'(cfg_tile_pixels) : (d1 == 2'd2) ? AW'(cfg_tile_pixels) << 1 : tp3_d) << (2*k);
      assign cso[3][k] = ((d2 == 2'd0) ? '0 : (d2 == 2'd1) ? AW'(cfg_w) : (d2 == 2'd2) ? AW'(cfg_w) << 1 : w3_d) << (2*k);
    end
    for (genvar k = ND; k < 6; k++) begin : g_dig_d_z
      assign cso[2][k] = '0;
      assign cso[3][k] = '0;
    end
  endgenerate
  assign cso[2][6] = AW'(cfg_kx);
  assign cso[2][7] = ~AW'(cfg_p);
  assign cso[3][6] = q_d;
  assign cso[3][7] = '0;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      iyb_d <= '0; sw_d <= '0; q_d <= '0; tp3_d <= '0; w3_d <= '0; s1_d <= '0; c1_d <= '0; s2_d <= '0; c2_d <= '0;
    end else if (!running_d) begin
      iyb_d <= 18'(cfg_oy0) * 18'(cfg_s) - 18'(cfg_p);
      sw_d  <= (AW'(cfg_s) * AW'(cfg_w)) << sp;
      q_d   <= AW'(18'(cfg_oy0) * 18'(cfg_s) - 18'(cfg_p) - 18'(cfg_iy0)) * AW'(cfg_w);
      tp3_d <= AW'(cfg_tile_pixels) + (AW'(cfg_tile_pixels) << 1);
      w3_d  <= AW'(cfg_w) + (AW'(cfg_w) << 1);
      s1_d  <= css[2];
      c1_d  <= csc[2] | AW'(1);
      s2_d  <= css[3];
      c2_d  <= csc[3];
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      running_d <= 1'b0;
      tok_d     <= 1'b0;
    end else begin
      tok_d <= start && !running_d;
      if (start && !running_d) running_d <= 1'b1;
      else if (valid_gd && row_end_d && (oy_d == cfg_oy0 + cfg_oy_n - 1)) running_d <= 1'b0;
    end
  end
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      oy_d   <= '0;
      ox_d   <= '0;
      m_d    <= '0;
      iy_d   <= '0;
      ix_d   <= '0;
      arow_d <= '0;
      a_d    <= '0;
      first_dq <= 1'b0;
    end else if (tok_d) begin
      oy_d   <= cfg_oy0;
      ox_d   <= '0;
      m_d    <= '0;
      first_dq <= acc_first;
      iy_d   <= iyb_d + 18'(cfg_ky);
      ix_d   <= 18'(cfg_kx) - 18'(cfg_p);
      arow_d <= (s1_d + c1_d + s2_d + c2_d) << sp;
      a_d    <= (s1_d + c1_d + s2_d + c2_d) << sp;
    end else if (valid_gd) begin
      m_d <= m_d + 1;
      if (row_end_d) begin
        ox_d   <= '0;
        ix_d   <= 18'(cfg_kx) - 18'(cfg_p);
        iy_d   <= iy_d + 18'(cfg_s);
        arow_d <= arow_nx_d;
        a_d    <= arow_nx_d;
        if (oy_d != cfg_oy0 + cfg_oy_n - 1) oy_d <= oy_d + 1;
      end else begin
        ox_d <= ox_d + 1;
        ix_d <= ix_d + 18'(cfg_s);
        a_d  <= a_d + (AW'(cfg_s) << sp);
      end
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      valid_dq <= 1'b0;
      swap_dq  <= 1'b0;
      idx_dq   <= '0;
      first_doq <= 1'b0;
      inb_dq    <= 1'b0;
      a0_dq     <= 1'b0;
      addr_dq   <= '0;
    end else begin
      valid_dq <= valid_gd;
      swap_dq  <= tok_d;
      idx_dq   <= m_d;
      first_doq <= first_dq;
      inb_dq    <= inb_d;
      a0_dq     <= addr_d[0];
      if (valid_gd) addr_dq <= addr_d[AW-1:1];
    end
  end

  // drop 0.43: first_oq every cycle; inb_q, a0_q and the leaf's latched address in the cycle the row is presented
  assign ctrl_err = (valid_q != valid_dq) || (swap_q != swap_dq) || (idx_q != idx_dq) ||
                    (valid_g && ((addr != addr_d) || (inb != inb_d))) ||
                    (first_oq != first_doq) ||
                    (valid_q && ((inb_q != inb_dq) || (a0_q != a0_dq) || (rb_addr_q != addr_dq)));

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n)          ctrl_err_sticky <= 1'b0;
    else if (ctrl_clear) ctrl_err_sticky <= 1'b0;
    else if (ctrl_err)   ctrl_err_sticky <= 1'b1;
  end
endmodule
