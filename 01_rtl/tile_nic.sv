// tile_nic.sv -- tile network interface (drop 0.7; widths for the silicon configuration in drop 0.21; 16-word beats on the
//   bank side in drop 0.29, stage 2 of the 512-bit bank port): the core's DMA and the bank's server.
//   Beat flit (interface <-> link packer/unpacker, drop 0.29): payload DW = 32 + 32*BW bits, {type[2:0], tag[8:0], hdr[19:0],
//   words[32*BW-1:0]} with BW = 16 words = one bank row, word i at [32*i +: 32]; hdr[13:8] = words carried - 1 on the data
//   types (RDRSP, WRDATA, PSUM: 1..BW words), every other type carries its one word in word 0. Message types:
//     RDREQ  hdr[7:4] = req_y, hdr[3:0] = req_x; word 0 = {len[11:0] words, addr[19:0]}   one flit, parity-protected
//     RDRSP  words in address order; the requester knows where they go                 (+ CRC flit)
//     WRHDR  word 0[19:0] = bank word address, then WRDATA beats                       (+ CRC flit)
//     PSUM   tag = accumulator row, hdr[7:0] = column of word 0, word i = column + i; column COLS = the check value (+ CRC)
//     CRC    word 0[15:0] = CRC-16 over the message's data words, [31:16] = number of data words sent (drop 0.12): a
//            receiver that counted a different number flags a lost or duplicated flit
//     RDY    one flit, owner -> contributor: "my reduce port is open" (flow control for PSUM, so a partial-sum stream can
//            never block the owner's own fetch responses behind it)
//   Bank side (drop 0.29): the row port of the 512-bit bank, 16 words = 64 B per cycle each way. Serving a read moves
//   whole rows: the first beat starts at the request's word offset inside its row (BW - offset words), every later beat is
//   a full row until the last, so a 4,095-word serve is 257 beats instead of 4,095 words; a zero-length request is answered
//   by its CRC flit alone (count 0: the requester completes cleanly; the toolchain refuses to emit one). A WRDATA beat is written as one
//   row with the lanes it covers masked in, or as two rows (two cycles) when it crosses a row boundary; the receive CRC
//   accumulates a whole beat per cycle (crc16_beat), the word count per word as before. Fetch responses (RDRSP) are
//   received at entry rate (drop 0.30, stage 3), two entries per cycle since drop 0.32: each cycle up to two activation
//   entries (WPA words) or weight entries (WPW words) are assembled from the words staged from earlier beats plus the
//   current beat and written to the buffer's word-wide port (a pair of entries at an even address; a single entry when
//   the address is odd or only one entry's words are there -- the odd trailing entry), so a 16-word beat of two 8-word
//   activation entries is taken in one cycle; words that do not complete an entry are staged for the next beat. PSUM beats are taken whole (drop 0.31): up to 16 columns per cycle into the row under
//   assembly, the row handed to the reduce port in the cycle its check word arrives.
//   Drain modes (drop 0.13): INT32 rows (COLS words + check word) for partial results and debug, or INT8 rows from the
//   requantization stage packed 4 channels per word (COLS/4 words per row, no check word): with COLS == ROWS one INT8 row
//   is exactly one activation entry of the next layer, so the written region is the next layer's input with no
//   reformatting. The drain sends 16-word beats (drop 0.31): an INT32 row (COLS + 1 words) is 16 + 16 + 1 at 32x32, an
//   INT8 row (COLS / 4 words) one beat; the packer merges beats into wide flits as before.
//   RX: per-source write pointers, PSUM row assembly and CRC accumulators (sources interleave at a destination; each
//   (source, destination) stream is in order on the XY mesh), each source's state in its own register block -- except the
//   rows under assembly, which are one neo_mem_rf leaf of NSRC rows since drop 0.41 (u_prow: 64 x 1,024 bits in silicon,
//   65,536 flops before). TX: one message at a time (RDREQ, serve a read, or drain), so a receiver never sees two of our
//   messages interleaved. Both beat CRCs (receive, transmit) are crc16_beat in its matrix form since drop 0.41: one XOR
//   tree per CRC bit instead of 16 chained word stages, the transmit beat's 7.7 ns path of drop 0.40.
//   Drop 0.42 (the paths behind the NIC's 2.3 ns on the ASAP7 SS proxy after drop 0.41, toward the PE's ~1 ns):
//   - RX: the beat is registered at the port (q_*) and, with it, the receive state of its source -- write pointer, CRC
//     accumulator, word count (st_*), read through the 64:1 muxes on the arriving beat's source field in the cycle the
//     beat is taken. Every action on the beat then reads registers only: the WRDATA lane mask came from behind the
//     wr_ptr[rx_src] mux (2.32 ns), the CRC from behind crc_acc[rx_src]. A source's next beat finds its state in st_*,
//     updated in place; a beat of another source loads its own while the outgoing state goes back to its registers
//     through one write-back register (wb3_*), so interleaved sources cost nothing. What the actions need of the beat
//     itself comes with it, formed from the arriving flit: its word count (and a copy for the CRC), a PSUM beat's
//     columns (its count's thermometer shifted by its first column) and that column's lane (one lane rotation now places
//     the WRDATA and the PSUM words), the fetch's word count below, and the partition compare (the configuration a beat
//     is checked against is the one at its arrival). Every received flit is one cycle later, the receive rate unchanged.
//   - RX: no arithmetic in the fetch-receive decision's loop (entries this cycle, stay on the beat or stash): the
//     staged words plus the beat's words left is a thermometer code, the staged count too, the beat position a one-hot,
//     so the compares against wpe, 2 wpe, 3 wpe are bits, "minus c wpe" a shift and the words' selects rotations by a
//     register, the next values formed for every case side by side -- where the chain of adders and compares behind
//     rx_ready was 2.28 ns register to register (rx_woff -> rx_cnt); bit-identical decisions. A beat is consumed per
//     type (fire_*), so the load enables of one type (the serve request, the reduce FIFO, the CRC flags) do not wait
//     for the fetch's; the WRDATA lane offset is a one-hot (st_rot, turned by the beat's count) and every value derived
//     from it an OR of its bits.
//   - TX: the drain FIFO's head is read on the leaf's registered port B (an SRAM macro's read, as drop 0.41 did for the
//     core's arrays), its INT8 rows packed four bytes per word at the push, and the drain beat is a select by beat
//     index (a row goes out in beats of BW words from word 0), its count, mask and last flag constants of the index; a
//     pushed row is sendable one cycle later than before. The FIFO's counts are pushed - popped (no up / down counter).
//   - TX: the CRC of a sent beat is accumulated in the cycle after it (the beat and its count registered: txc_*), so
//     the beat's assembly and the CRC are no longer one path; the CRC flit waits for the last beat's update, one cycle
//     per message.
//   - TX: the serve's position (row, words left, lanes left, the lane's one-hot and mask, the last-beat flag) in
//     registers, and the flit's parity formed from word parities -- the bank row's under the beat's lane mask for a
//     served beat, the beat's parity stored with the drain FIFO's row at the push for a drained one -- instead of one
//     XOR tree over the 560 bits behind the words' select.
//   Drop 0.43 (stream C2, transformer readiness):
//   - INT4 weights: FETCH_W with arg 1 fetches INT4 entries -- COLS nibbles in WP4 = COLS / 8 words, nibble j at bits
//     [4j +: 4] of the entry's words -- through the same entry assembly (the entry size WP4 beside WPA / WPW), and each
//     entry is expanded on its way to the weight buffer: every nibble sign-extended to the ordinary 8-bit weight, index 0,
//     the check weight of column 0 their sum (an adder per entry), the other check weights 0. wbuf_mem receives the
//     ordinary entry; the bank holds half (16x8: a third) of the words. The layer's per-output-channel scale is the
//     requantizer's (the compiler folds it into mult / shift); per-group scales along K are not supported.
//   - FETCH_A with arg 1 fetches activation-format entries (ROWS bytes, WPA words) into the vector unit's residual buffer
//     (rbuf_* -- the data on abuf_wdata / abuf_wdata2, the pair rule of the activation buffer).
//   - DRAIN_WR's base field, unused before, is the drain's format for INT8 rows (arg 1): [14] entry -- each row followed
//     by its check weight (the sum of its bytes, WCW bits, in the next word) and zero words up to WPW: a weight entry of
//     the next layer (the drained tensor fetched as weights: K or V of an attention head); [13] pad -- each row padded with
//     zero words to WPA, an activation entry whose upper ROWS - COLS channels are zero (the 16x8 core's COLS = 8 outputs
//     as a channel tile of 16); [15] transpose -- every block of COLS rows is drained as its transpose (a COLS x COLS byte
//     buffer written by rows and read by columns, the orientation alternating per block so a block is read out while the
//     next is written; COLS rows a block, the drain's length a multiple of COLS): the transpose by the DMA that turns
//     tokens x channels into the channels x tokens of K^T; [12:0] the group stride -- when nonzero, every group of COLS
//     rows starts at the address of the group before plus the stride (a WRHDR before each group), so a block's entries
//     land in the weight region of the output tile they belong to. The message stays one message (one CRC, the count of
//     its data words); the receiver takes a WRHDR in the middle of a message as it takes the first.
//   Commands from the host or sequencer: FETCH_A / FETCH_W pull `len` words from a remote bank into the activation /
//   weight buffer at entry `base`; DRAIN_WR / DRAIN_PSUM route the core's drained rows to a remote bank address / to the
//   owner core's reduce port. crc_err_sticky is the end-to-end protection the FMEDA counts on (row 12): it covers
//   everything between the words read from the source bank and the words written into the destination buffer or bank,
//   including NIC logic the link parity cannot see.
module tile_nic #(
  parameter int XW    = 2,
  parameter int YW    = 2,
  parameter int NX    = 2,
  parameter int NSRC  = 4,                  // NX * NY nodes
  parameter int BW    = 16,                 // words per beat = lanes of a bank row (drop 0.29)
  parameter int DW    = 32 + 32 * BW,       // beat payload
  parameter int FW    = 1 + 2 * (XW + YW) + 8 + DW,
  parameter int ROWS  = 16,
  parameter int COLS  = 8,
  parameter int WCW   = 8 + $clog2(ROWS) + 1,
  parameter int IDXW  = 6,
  parameter int AW    = 8,                  // activation buffer entry address width
  parameter int WAW   = 9,                  // weight buffer entry address width
  parameter int BAW   = 12,                 // bank word address width
  parameter int LW    = $clog2(BW),         // lane bits of a word address
  parameter int BRAW  = (BAW > LW) ? BAW - LW : 1,   // bank row address width
  parameter int PW    = 32,
  parameter int NCHK  = 1,                  // the core's check columns: 4 = the 2:4 sparse-capable core (drop 0.42)
  parameter int IW    = (NCHK > 1) ? 2 : 0, // index bits per weight (derived)
  parameter int WPA   = ROWS * 8 / 32,      // words per activation entry
  parameter int WPW   = (COLS * (8 + IW) + NCHK * WCW + 31) / 32,   // words per weight entry (12 at 32x32 with NCHK = 4, 9 before)
  parameter int DFD   = 64,                 // drain FIFO rows
  parameter int DV_HOOKS = 0,               // 1: the dead-server hook below exists (drop 0.35)
  parameter int FETCH_TIMEOUT = 1048575     // cycles a fetch may wait for its response. Silicon rule (drop 0.24): every tile has at
                                            // most one fetch outstanding and a fetch is at most 4,095 words, so the longest wait at one
                                            // bank is (NX*NY - 1) queued maximal fetches plus its own: FETCH_TIMEOUT >= NX*NY*(4,095 + H),
                                            // H the per-request overhead; 2^20 - 1 covers 64 tiles with H up to 12,000 cycles.
                                            // The simulation builds pass 8,192 / 65,535 so the timeout tests stay short.
)(
  input  logic                  clk,
  input  logic                  rst_n,
  input  logic [XW-1:0]         my_x,         // this tile's mesh coordinates (drop 0.35: ports strapped by the mesh, not
  input  logic [YW-1:0]         my_y,         // parameters, so every tile is the same module -- one elaboration, 64 instances)
  // link side: beats of up to BW words (link_unpacker / link_packer in neo_tile)
  input  logic                  rx_valid,
  input  logic [FW-1:0]         rx_flit,
  output logic                  rx_ready,
  output logic                  tx_valid,
  output logic [FW-1:0]         tx_flit,
  input  logic                  tx_ready,
  // local bank, row port (drop 0.29): 16 words per cycle, lane write mask
  output logic                  b_we,
  output logic [BRAW-1:0]       b_wrow,
  output logic [BW-1:0]         b_wmask,
  output logic [32*BW-1:0]      b_wdata,
  output logic                  b_re,
  output logic [BRAW-1:0]       b_rrow,
  input  logic [32*BW-1:0]      b_rdata,
  input  logic                  b_rvalid,
  // core buffers
  output logic                  abuf_we,
  output logic [AW-1:0]         abuf_waddr,
  output logic signed [7:0]     abuf_wdata [ROWS],
  output logic                  abuf_we2,                 // the entry at abuf_waddr + 1 (abuf_waddr even), drop 0.32
  output logic signed [7:0]     abuf_wdata2 [ROWS],
  output logic                  rbuf_we,                  // drop 0.43: FETCH_A with arg 1 -- the entry (abuf_wdata) goes to the
  output logic                  rbuf_we2,                 // vector unit's residual buffer at rbuf_waddr (and abuf_wdata2 to
  output logic [IDXW-1:0]       rbuf_waddr,               // rbuf_waddr + 1)
  output logic                  wbuf_we,
  output logic [WAW-1:0]        wbuf_waddr,
  output logic [8+IW-1:0]       wbuf_wdata [COLS],        // {index, weight} per column (drop 0.42: the entry of wbuf_mem)
  output logic [NCHK*WCW-1:0]   wcbuf_wdata,              // the check weights, column k at [k*WCW +: WCW]
  output logic                  wbuf_we2,
  output logic [8+IW-1:0]       wbuf_wdata2 [COLS],
  output logic [NCHK*WCW-1:0]   wcbuf_wdata2,
  // core reduce port
  output logic                  ext_valid,
  output logic [IDXW-1:0]       ext_idx,
  output logic signed [PW-1:0]  ext_y [COLS],
  output logic signed [PW-1:0]  ext_chk,
  input  logic                  ext_ready,
  // core drain
  input  logic                  rd_valid,
  input  logic                  rq_valid,     // requantized row (two cycles after rd_valid)
  input  logic signed [7:0]     rq_q [COLS],
  input  logic signed [PW-1:0]  rd_data [COLS],
  input  logic signed [PW-1:0]  rd_chk,
  // command port
  input  logic                  cmd_valid,
  input  logic [2:0]            cmd_op,       // 1 FETCH_A, 2 FETCH_W, 3 DRAIN_WR, 4 DRAIN_PSUM, 5 NOTIFY (send RDY to x,y)
  input  logic                  cmd_int8,     // with DRAIN_WR: drain the requantized INT8 rows instead of INT32; with FETCH_W
                                              // (drop 0.43): INT4 weight entries; with FETCH_A: into the vector unit's RBUF
  input  logic [XW-1:0]         cmd_x,
  input  logic [YW-1:0]         cmd_y,
  input  logic [19:0]           cmd_addr,
  input  logic [11:0]           cmd_len,      // words to fetch, or rows to drain
  input  logic [15:0]           cmd_base,     // local entry index for fetches; DRAIN_WR's INT8 format (drop 0.43): [15]
                                              // transpose, [14] entry, [13] pad, [12:0] group stride in words
  output logic                  cmd_busy,     // a fetch is outstanding
  output logic                  drain_busy,   // drain rows still to be sent
  output logic                  crc_err_sticky,
  input  logic                  err_clear,
  output logic                  rdy_seen,     // an RDY flit arrived since rdy_clear
  input  logic                  rdy_clear,
  output logic                  ntf_busy,     // a NOTIFY is pending transmission
  input  logic [3:0]            partition,           // spatial partition of this tile (drop 0.16)
  output logic                  iso_err_sticky,      // a flit from another partition arrived and was dropped
  output logic                  lost_err_sticky,     // word count at the CRC flit differed from the words received
  output logic                  fetch_timeout_sticky, // a fetch response did not complete within FETCH_TIMEOUT cycles
  input  logic                  dv_tx_hold           // testbench hook (drop 0.35; DV_HOOKS = 1): the transmit engine held in
                                                     // X_SERVE_RD at every falling edge while high -- a dead server
);
  localparam logic [2:0] T_RDREQ = 3'd1, T_RDRSP = 3'd2, T_WRHDR = 3'd3, T_WRDATA = 3'd4,
                         T_PSUM = 3'd5, T_CRC = 3'd6, T_RDY = 3'd7;
  localparam int VW = 32 * BW;                            // the words field of a beat
  localparam int CW = $clog2(BW + 1);                      // beat word count 0..BW
  localparam int RW = CW + 1;                              // lane + room, 0..2*BW-1 (drop 0.42)
  localparam int SEQ_LO = DW;                              // seq field above the payload
  localparam int SRCX_LO = DW + 8;
  localparam int SRCY_LO = DW + 8 + XW;

  // ------------------------------------------------------------------ RX stage (drop 0.42)
  // The beat from the unpacker is registered (q_*) before anything acts on it: every action of the receive side below is
  // on the registered beat -- the decode, the fetch's entries, the writeback, the PSUM row, the CRC, the counts, the
  // flags -- and q_ready (the old rx_ready) says it is consumed this cycle. The stage takes the next beat when it is
  // empty or as its beat is consumed, so the receive rate is unchanged and every flit is one cycle later.
  logic            q_valid;
  logic [FW-1:0]   q_flit;
  logic            q_ready;
  // formed from the arriving beat with it (so no adder of theirs is behind the register): its word count -- twice, the
  // copy for the CRC (whose 32-entry matrix select fans each bit out to hundreds of gates) stored inverted, as two flops
  // of the same input are one flop to the synthesis -- and modulo BW as a one-hot (the WRDATA pointer's turn), whether
  // it is a PSUM beat carrying its row's check word, the columns a PSUM beat covers and its first column's lane as a
  // one-hot (the select of the lane rotation below), and for the fetch, t of the beat's first cycle (q_tt: the staged
  // words of the cycle the beat is taken plus its words, a thermometer code; the fetch receive below)
  localparam int EW  = (WPA > WPW) ? WPA : WPW;           // words of the largest entry (4 at 16x8, 9 at 32x32; drop 0.21)
  localparam int WP4 = (COLS * 4 + 31) / 32;              // words of an INT4 weight entry (drop 0.43): 1 at 16x8, 4 at 32x32
  localparam int TW  = BW + EW;                           // the fetch's t <= BW + EW - 1: thermometer bits t > 0 .. t > BW + EW - 1
  logic [CW-1:0]   q_nw;
  logic [CW-1:0]   q_nwc_n;                               // ~q_nw, the CRC's copy
  logic [BW-1:0]   q_nwo;                                 // one-hot, bit k = (words mod BW == k)
  logic            q_pschk;
  logic [COLS-1:0] q_pcm;                                 // PSUM: column j in [col0, col0 + words)
  logic [BW-1:0]   q_c0;                                  // PSUM: one-hot, bit k = (col0 mod BW == k)
  logic [TW-1:0]   q_tt;                                  // the fetch's t of the beat's first cycle (thermometer)
  logic [EW-1:0]   sg_on;                                 // one-hot: the staged count of the next cycle (fetch receive)
  logic [2:0]      in_type;
  logic [13:0]     in_hdr;                               // hdr[13:0]: the count field and the column
  logic            in_data;
  logic [CW-1:0]   in_nw;
  logic [COLS:0]   in_pmask;                              // PSUM: the columns 0..COLS the beat covers
  logic [BW-1:0]   in_cnto;                               // one-hot of the count field (data types; words - 1)
  logic [BW-1:0]   in_nwt;                                // thermometer of the word count: bit k = (words > k)
  logic [TW-1:0]   in_tt;                                 // q_tt's next value
  logic [BW-1:0]   in_cge;                                // the count field >= m (m >= 1; data types or not)
  logic [BW-1:0]   q_nwt;                                 // thermometer of the word count (the WRDATA crossing test)
  logic            q_foreign;                             // the beat is of another partition (the compare with it)
  assign in_type  = rx_flit[DW-1 -: 3];
  assign in_hdr   = rx_flit[VW +: 14];
  assign in_data  = (in_type == T_RDRSP) || (in_type == T_WRDATA) || (in_type == T_PSUM);
  assign in_nw    = in_data ? CW'(in_hdr[13:8]) + CW'(1) : CW'(1);
  localparam int PMW = COLS + 1;                           // the columns and the check value
  assign in_pmask = PMW'(in_nwt) << in_hdr[7:0];          // column j: words > j - col0 (j >= col0)
  assign rx_ready = !q_valid || q_ready;
  always_comb begin
    for (int j = 0; j < BW; j++) in_cnto[j] = in_data && (in_hdr[13:8] == 6'(j));
    for (int m = 0; m < BW; m++) begin                    // count field >= m, by its one-hot (the type applied after)
      in_cge[m] = (m == 0);
      for (int j = m; j < BW; j++) if (m > 0) in_cge[m] = in_cge[m] | (in_hdr[13:8] == 6'(j));
    end
    for (int k = 0; k < BW; k++) in_nwt[k] = (k == 0) || (in_data && in_cge[k]);   // words = count + 1 (data), 1
    for (int k = 0; k < TW; k++) begin                    // staged + words > k: staged >= k, or the count >= k - staged
      in_tt[k] = 1'b0;
      for (int j = 0; j < EW; j++)
        if ((k <= j) || ((k - j < BW) && in_data && in_cge[(k - j) % BW])) in_tt[k] = in_tt[k] | sg_on[j];
    end
  end
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      q_valid <= 1'b0; q_flit <= '0; q_nw <= '0; q_nwc_n <= '1; q_nwo <= BW'(2); q_pschk <= 1'b0; q_pcm <= '0; q_c0 <= '0;
      q_tt <= '0; q_nwt <= BW'(1); q_foreign <= 1'b0;
    end else if (rx_ready) begin
      q_valid <= rx_valid;
      if (rx_valid) begin
        q_flit  <= rx_flit;
        q_nw    <= in_nw;
        q_nwc_n <= ~in_nw;
        for (int k = 0; k < BW; k++)                      // words mod BW = (count field + 1) mod BW; 1 for the other types
          q_nwo[k] <= in_data ? in_cnto[(k + BW - 1) % BW] : (k == 1);
        q_pschk <= (in_type == T_PSUM) && in_pmask[COLS];
        q_pcm   <= (in_type == T_PSUM) ? in_pmask[COLS-1:0] : '0;
        for (int k = 0; k < BW; k++)
          q_c0[k] <= in_hdr[LW-1:0] == LW'(k);
        q_tt    <= in_tt;
        q_nwt   <= in_nwt;
        q_foreign <= rx_flit[SEQ_LO +: 4] != partition;
      end
    end
  end

  // ------------------------------------------------------------------ RX decode (the registered beat)
  logic [2:0]      rx_type;
  logic [8:0]      rx_tag;
  logic [19:0]     rx_hdr;
  logic [VW-1:0]   rx_words;
  logic [CW-1:0]   rx_nw;                                 // words in the beat
  logic [31:0]     rx_word;                               // word 0: the word of the single-word types
  logic [51:0]     rx_data;                               // the one-word view {hdr, word}: the fields of RDREQ, WRHDR, CRC
  logic [XW-1:0]   rx_src_x;
  logic [YW-1:0]   rx_src_y;
  localparam int SRCW = (NSRC > 1) ? $clog2(NSRC) : 1;    // source node index width (drop 0.35: was a fixed 8 bits)
  logic [SRCW-1:0] rx_src;                                // source node index
  assign rx_type  = q_flit[DW-1 -: 3];
  assign rx_tag   = q_flit[DW-4 -: 9];
  assign rx_hdr   = q_flit[DW-13 -: 20];
  assign rx_words = q_flit[VW-1:0];
  assign rx_nw    = q_nw;                                 // (data types: hdr[13:8] + 1; the others 1)
  assign rx_word  = rx_words[31:0];
  assign rx_data  = {rx_hdr, rx_word};
  assign rx_src_x = q_flit[SRCX_LO +: XW];
  assign rx_src_y = q_flit[SRCY_LO +: YW];
  assign rx_src   = SRCW'(32'(rx_src_y) * NX + 32'(rx_src_x));

  // per-source receive state (each source's registers in its own block, g_src below; the partial-sum rows in the u_prow leaf)
  logic [BAW-1:0]       wr_ptr  [NSRC];
  logic [15:0]          crc_acc [NSRC];
  logic [15:0]          rx_cnt  [NSRC];                 // data words received in the current message
  // Drop 0.42: the state of the registered beat's source, st_* (write pointer, CRC accumulator, word count of source
  // st_src), is registered with the beat: read from the source's g_src registers in the cycle the stage takes the beat
  // (the 64:1 muxes select on the arriving beat's source field and end in st_*), then updated in place by every beat
  // of that source -- the g_src registers of st_src are stale meanwhile. A beat of another source loads its own state
  // and the outgoing one goes back to its g_src registers through the write-back register wb3_* (one cycle; a beat whose
  // source is in wb3_* takes the state from there). So the beat's actions read only registers: the WRDATA lane masks
  // from st_ptr (behind the 64:1 wr_ptr[rx_src] mux before, 2.32 ns), the CRC from st_crc (behind crc_acc[rx_src]).
  logic                 st_v;                             // st_* holds a source's state (after the first beat)
  logic [SRCW-1:0]      st_src;
  logic [BAW-1:0]       st_ptr;
  logic [15:0]          st_crc;
  logic [15:0]          st_cnt;
  logic                 wb3_v;                            // wb3_* goes back to source wb3_src's registers this cycle
  logic [SRCW-1:0]      wb3_src;
  logic [BAW-1:0]       wb3_ptr;
  logic [15:0]          wb3_crc;
  logic [15:0]          wb3_cnt;
  logic [SRCW-1:0]      in_src;                           // the source of the beat at the port
  logic                 st_load;                          // the stage takes a beat of another source than st_src
  logic [BAW-1:0]       lk_ptr, u_ptr;                    // in_src's state (lk: its registers or wb3); st after this cycle's beat (u)
  logic [15:0]          lk_crc, lk_cnt, u_crc, u_cnt;
  localparam int FTW = $clog2(FETCH_TIMEOUT + 1);          // timer width follows the parameter (drop 0.24: was a fixed 16 bits)
  logic [FTW-1:0]       fetch_timer;

  // outstanding fetch
  logic                 fetch_busy;
  logic                 fetch_is_w;
  logic                 fetch_is_w4;                      // drop 0.43: INT4 weight entries (FETCH_W, arg 1; fetch_is_w too)
  logic                 fetch_is_r;                       // drop 0.43: activation entries into the vector unit's RBUF (FETCH_A, arg 1)
  logic [SRCW-1:0]      fetch_src;                        // node serving our fetch
  logic [15:0]          fetch_entry;                      // the entry the next entry goes to (drop 0.42: a register, was
                                                          // fetch_base + fetch_ent)
  logic [31:0]          stage [EW];                       // words of the entry under assembly, from earlier beats

  // ext FIFO (reduced rows), depth 2
  logic                 efifo_valid [2];
  logic [IDXW-1:0]      efifo_idx   [2];
  logic signed [PW-1:0] efifo_y     [2][COLS];
  logic signed [PW-1:0] efifo_chk   [2];
  logic                 efifo_rd;                          // read pointer
  logic                 efifo_wr;
  logic                 efifo_full;
  assign efifo_full = efifo_valid[0] && efifo_valid[1];

  // serve request latch
  logic                 serve_done;                       // last flit of a served read accepted (TX engine)
  logic                 serve_busy;
  logic [XW-1:0]        serve_x;
  logic [YW-1:0]        serve_y;
  logic [11:0]          serve_len;

  // PSUM beat (drop 0.31): columns col0 .. col0 + words - 1 of row rx_tag (col0 = hdr[7:0]); column COLS is the check
  // value and completes the row (8-bit columns for COLS up to 255, drop 0.21). Drop 0.42: the columns covered (q_pcm),
  // the check word's presence (q_pschk) and col0's lane (q_c0) come with the beat from the stage's input; column j's word
  // is lane j mod BW of the beat rotated by col0 (bt_rot below), the check word lane COLS mod BW
  logic        ps_has_chk;                                // the beat carries the check word: the row goes to the reduce port
  assign ps_has_chk = q_pschk;                            // (PSUM, col0 <= COLS < col0 + words)
  logic rx_foreign;                                       // (drop 0.42: compared as the stage takes the beat)
  assign rx_foreign = q_valid && q_foreign;
  // the beat is consumed this cycle, by type (drop 0.42: each from its own condition, so a load enable of one type does
  // not wait for the fetch decision of another; rx_fire was q_ready of whatever type and the type compare after it)
  logic rx_ok;                                            // a beat of our partition is in the stage
  logic fire_rdreq, fire_rsp, fire_wrhdr, fire_wrdata, fire_psum, fire_crc, fire_rdy;
  assign rx_ok = q_valid && !rx_foreign;

  // ------------------------------------------------------------------ WRDATA writeback: beat -> bank rows
  // Drop 0.42: the beat's word address is st_ptr (registered with the beat), and the lane masks are written per lane
  // against the count field (hdr[13:8] = words - 1) -- the same masks as ((1 << n) - 1) << off without the shifts and
  // subtractions in series: lane l of the first row is in [off, off + n1) iff l >= off and l - off <= hdr[13:8]; lane l
  // of the second row (a beat that crosses the row boundary: nw > room, i.e. hdr[13:8] >= room) is below n2 = nw - room
  // iff l + room <= hdr[13:8]. What depends on the lane offset alone -- l >= off, l - off, l + room, and the offset as a
  // one-hot (the rotation's select) -- is registered beside st_ptr (st_m*, st_rot: rewritten whenever st_ptr is), so the
  // masks are one compare per lane against the count field, the crossing test (nw > BW - off) an AND-OR of st_rot and
  // the count's thermometer, and the rotation's 2,048-pin select a register. The rotation serves the PSUM beats as well
  // (their columns at col0 + i: the select q_c0 instead of st_rot).
  logic                 wb_active;                        // a WRDATA beat is in the stage
  logic                 wb_phase;                         // 1: writing the second row of a beat that crosses a row boundary
  logic                 wb_cross;
  logic [BRAW-1:0]      wb_row;
  logic [BW-1:0]        wb_mask1, wb_mask2;
  logic [BW-1:0]        bt_sel;                           // the rotation's select: st_rot (WRDATA), q_c0 (PSUM)
  logic [VW-1:0]        bt_rot;                           // the beat rotated: word i in lane (k + i) mod BW for bt_sel[k]
  logic [BW-1:0]        st_mge;                           // lane l >= off
  logic [LW-1:0]        st_md1 [BW];                      // l - off (lanes l >= off)
  logic [RW-1:0]        st_md2 [BW];                      // l + room (RW = CW + 1 bits: up to 2 BW - 1)
  logic [BW-1:0]        st_rot;                           // one-hot: bit k = (off == k)
  assign wb_active = rx_ok && (rx_type == T_WRDATA);
  assign wb_row    = st_ptr[BAW-1:LW];
  always_comb begin                                       // nw > room: room = BW - off, off one-hot in st_rot (BW: never)
    wb_cross = 1'b0;
    for (int k = 1; k < BW; k++) wb_cross = wb_cross | (st_rot[k] && q_nwt[BW - k]);
  end
  assign bt_sel    = (rx_type == T_PSUM) ? q_c0 : st_rot;
  always_comb begin
    for (int l = 0; l < BW; l++) begin
      wb_mask1[l] = st_mge[l] && (6'(st_md1[l]) <= rx_hdr[13:8]);
      wb_mask2[l] = 6'(st_md2[l]) <= rx_hdr[13:8];
    end
    for (int l = 0; l < BW; l++) begin                    // lane l takes word (l - k) mod BW
      bt_rot[32 * l +: 32] = '0;
      for (int k = 0; k < BW; k++)
        if (bt_sel[k]) bt_rot[32 * l +: 32] = bt_rot[32 * l +: 32] | rx_words[32 * ((l - k + BW) % BW) +: 32];
    end
  end
  assign fire_wrdata = wb_active && (!wb_cross || wb_phase);   // the whole beat, after its last row write
  assign b_we      = wb_active;
  assign b_wrow    = wb_phase ? wb_row + BRAW'(1) : wb_row;
  assign b_wmask   = wb_phase ? wb_mask2 : wb_mask1;
  assign b_wdata   = bt_rot;

  // ------------------------------------------------------------------ fetch receive at entry rate (drop 0.30; two entries per cycle, drop 0.32)
  // Each cycle up to two entries (WPE = WPA or WPW words each) are assembled from the words staged from earlier beats
  // followed by the current beat's words not yet taken, and written to the buffer's word-wide port: a pair when the
  // entry address is even and the words of both are there, otherwise one entry (the odd trailing entry, or the first
  // entry of a fetch at an odd base). When what is left of the beat still holds an entry the beat stays (fx_more),
  // otherwise the leftover words are staged and the beat is consumed. A response that arrives with no fetch outstanding
  // (abandoned by the watchdog) is consumed without storing anything.
  // Drop 0.42: no arithmetic in the decision's loop (the adders and compares in series behind rx_ready, rx_woff -> rx_cnt,
  // were 2.28 ns register to register after drop 0.41; the synthesis maps every adder and compare, constant operands
  // included, onto the library's weakest half-adder cells). With t = the staged words plus the beat's words not yet
  // taken -- an entry is written iff t >= wpe, a pair iff t >= 2 wpe (at an even address), the beat stays iff the words
  // after them still make an entry (t >= 2 wpe after one, t >= 3 wpe after a pair), and t - c wpe words remain after c
  // entries --, t is kept as a thermometer code (fx_tt[k] = t > k), so every compare is one of its bits and every
  // "minus c wpe" a shift by a constant; the staged count likewise (sg_t[w] = staged > w: "slot w is staged"); and the
  // beat word under entry word 0 one-hot (fx_bo), so entry word w is beat word base + w, the second entry's base + wpe
  // + w and a stash after c entries base + c wpe + w: rotations of the beat by a register (fx_bo, and fx_bo turned by
  // wpe, 2 wpe). t of a beat's first cycle (q_tt) is formed as the stage takes it: its word count's thermometer shifted
  // up by the staged count of that cycle; while it stays, fx_lt (t - c wpe of the cycle before). Bit-identical
  // decisions, entries and stash.
  localparam int TXW = (TW > 3 * EW) ? TW : 3 * EW + 1;   // ... zero-extended to every compare's bit (t >= 3 wpe)
  logic          fx_act;                                  // an RDRSP beat is in the stage
  logic          fx_held;                                 // the beat in the stage stays from the cycle before
  logic [TW-1:0] fx_lt;                                   // ... t then (thermometer)
  logic [TW-1:0] fx_tt;                                   // t (thermometer)
  logic [TXW-1:0] fx_tx;
  logic [EW-1:0] sg_t;                                    // the staged count (thermometer): slot w holds a staged word
  logic [BW-1:0] fx_bo, fx_bo1, fx_bo2;                   // one-hot: the beat word under entry word 0, + wpe, + 2 wpe
  logic          fx_ge1, fx_ge2, fx_ge3;                  // t >= wpe, 2 wpe, 3 wpe
  logic          fx_entry;                                // at least one entry is written this cycle
  logic          fx_two;                                  // two entries are written this cycle
  logic          fx_more;                                 // ... enough for another entry: stay on the beat
  logic          fx_stash;                                // ... fewer: they go to stage, the beat is consumed
  logic          fx_done;                                 // the beat is consumed this cycle
  logic [TW-1:0] fx_tc;                                   // t - c wpe after the c entries written (thermometer)
  logic [31:0]   fx_r0 [EW], fx_r1 [EW], fx_r2 [EW];     // beat words base + w, base + wpe + w, base + 2 wpe + w
  logic [EW-1:0] fx_swe;                                  // the stash writes slot w
  assign fx_act = rx_ok && (rx_type == T_RDRSP);
  assign fx_tt  = fx_held ? fx_lt : q_tt;
  assign fx_tx  = TXW'(fx_tt);
  always_comb begin                                       // the compares, the shifts and the turns by the entry size
    if (fetch_is_w4) begin                                // drop 0.43: the INT4 entry
      fx_ge1 = fx_tx[WP4 - 1]; fx_ge2 = fx_tx[2 * WP4 - 1]; fx_ge3 = fx_tx[3 * WP4 - 1];
    end else if (fetch_is_w) begin
      fx_ge1 = fx_tx[WPW - 1]; fx_ge2 = fx_tx[2 * WPW - 1]; fx_ge3 = fx_tx[3 * WPW - 1];
    end else begin
      fx_ge1 = fx_tx[WPA - 1]; fx_ge2 = fx_tx[2 * WPA - 1]; fx_ge3 = fx_tx[3 * WPA - 1];
    end
    for (int k = 0; k < BW; k++) begin
      fx_bo1[k] = fetch_is_w4 ? fx_bo[(k - WP4 % BW + BW) % BW] :
                  fetch_is_w  ? fx_bo[(k - WPW % BW + BW) % BW] : fx_bo[(k - WPA % BW + BW) % BW];
      fx_bo2[k] = fetch_is_w4 ? fx_bo[(k - (2 * WP4) % BW + BW) % BW] :
                  fetch_is_w  ? fx_bo[(k - (2 * WPW) % BW + BW) % BW] : fx_bo[(k - (2 * WPA) % BW + BW) % BW];
    end
  end
  assign fx_entry = fx_act && fetch_busy && fx_ge1;
  assign fx_two   = fx_entry && !fetch_entry[0] && fx_ge2;
  assign fx_more  = fx_entry && (fx_two ? fx_ge3 : fx_ge2);
  assign fx_stash = fx_act && fetch_busy && !fx_more;
  assign fx_done  = !fetch_busy || !fx_more;
  always_comb begin
    if (fx_two)        fx_tc = fetch_is_w4 ? fx_tt >> (2 * WP4) : fetch_is_w ? fx_tt >> (2 * WPW) : fx_tt >> (2 * WPA);
    else if (fx_entry) fx_tc = fetch_is_w4 ? fx_tt >> WP4 : fetch_is_w ? fx_tt >> WPW : fx_tt >> WPA;
    else               fx_tc = fx_tt;
    for (int w = 0; w < EW; w++) begin
      fx_r0[w] = '0; fx_r1[w] = '0; fx_r2[w] = '0;
      for (int k = 0; k < BW; k++) begin
        fx_r0[w] = fx_r0[w] | ({32{fx_bo[k]}}  & rx_words[32 * ((k + w) % BW) +: 32]);
        fx_r1[w] = fx_r1[w] | ({32{fx_bo1[k]}} & rx_words[32 * ((k + w) % BW) +: 32]);
        fx_r2[w] = fx_r2[w] | ({32{fx_bo2[k]}} & rx_words[32 * ((k + w) % BW) +: 32]);
      end
      // after c entries the slots below t - c wpe (c > 0), or from the staged count on (c = 0), take the beat's words
      fx_swe[w] = fx_stash && fx_tc[w] && (fx_entry || !sg_t[w]);
    end
  end
  // bits the interface does not read (lint): the flit's parity and destination (the router's), seq (its partition half
  // compared as the stage takes the beat, drop 0.42), the tag above IDXW, the header half of the one-word view, the
  // entry index above the buffer address widths
  logic unused_nic_bits;
  assign unused_nic_bits = ^{q_flit[FW-1 -: 1 + XW + YW], q_flit[SEQ_LO +: 8], rx_tag, rx_data, fetch_entry};

  // the next staged count, beat position and entry index (drop 0.42): each formed for the cases side by side and chosen
  // by the decision at the end; fx_held / fx_lt follow a beat that stays. The beat position after a stash or with no
  // beat is minus the staged count (the next beat's word 0 is entry word "staged"): one-hot from the thermometer.
  logic          fetch_cmd;                               // a fetch command is taken this cycle
  logic [EW-1:0] sg_tn;                                   // sg_t of the next cycle
  logic [BW-1:0] fx_bo_n, fx_bneg_c, fx_bneg_s;           // fx_bo next; one-hot of -(t - c wpe), of -staged (mod BW)
  logic [15:0]   fe_p1, fe_p2;                            // fetch_entry + 1, + 2
  assign fetch_cmd = cmd_valid && (cmd_op == 3'd1 || cmd_op == 3'd2) && !fetch_busy;
  assign sg_tn     = (fetch_cmd || fx_more) ? '0 : fx_stash ? EW'(fx_tc) : sg_t;   // (a stash leaves t - c wpe < wpe)
  always_comb begin
    for (int j = 0; j < EW; j++) sg_on[j] = ((j == 0) || sg_tn[(j + EW - 1) % EW]) && !sg_tn[j];
    for (int k = 0; k < BW; k++) begin
      fx_bneg_c[k] = 1'b0; fx_bneg_s[k] = 1'b0;
      for (int j = 0; j < EW; j++)
        if ((BW - j) % BW == k) begin
          fx_bneg_c[k] = ((j == 0) || fx_tc[(j + TW - 1) % TW]) && !fx_tc[j];
          fx_bneg_s[k] = ((j == 0) || sg_t[(j + EW - 1) % EW]) && !sg_t[j];
        end
    end
  end
  assign fx_bo_n = fx_more ? (fx_two ? fx_bo2 : fx_bo1) : fetch_cmd ? BW'(1) : fx_stash ? fx_bneg_c : fx_bneg_s;
  assign fe_p1   = fetch_entry + 16'd1;
  assign fe_p2   = fetch_entry + 16'd2;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      sg_t <= '0; fx_bo <= BW'(1); fx_held <= 1'b0; fx_lt <= '0; fetch_entry <= '0;
    end else begin
      sg_t    <= sg_tn;
      fx_bo   <= fx_bo_n;
      fx_held <= fx_more;                                 // the beat stays (q_ready is low): its words left next cycle
      fx_lt   <= fx_tc;
      if (fetch_cmd)     fetch_entry <= cmd_base;
      else if (fx_entry) fetch_entry <= fx_two ? fe_p2 : fe_p1;
    end
  end

  // entry assembly: the first entry from the staged words then the beat's words; the second entry from the beat's words
  // that follow (the staged words are fewer than one entry, so it never reaches into stage)
  logic [31:0]   ent  [EW];
  logic [31:0]   ent2 [EW];
  always_comb begin
    for (int w = 0; w < EW; w++) begin
      ent[w]  = sg_t[w] ? stage[w] : fx_r0[w];
      ent2[w] = fx_r1[w];
    end
  end

  // ------------------------------------------------------------------ RX handshake
  // RDRSP beats are consumed at entry rate (fx_done); a PSUM beat whole, held only while its check word finds the reduce
  // FIFO full; the other types, and a WRDATA beat once its last row write is on the port, whole. Drop 0.42: of the
  // registered beat (q_ready; the port's rx_ready is the stage's, above), and per type (fire_*: the beat is consumed).
  always_comb begin
    if (rx_foreign) q_ready = 1'b1;                        // dropped
    else begin
      case (rx_type)
        T_RDREQ:  q_ready = !serve_busy;
        T_RDRSP:  q_ready = fx_done;
        T_PSUM:   q_ready = !(ps_has_chk && efifo_full);
        T_WRDATA: q_ready = !wb_cross || wb_phase;
        default:  q_ready = 1'b1;
      endcase
    end
  end
  assign fire_rdreq = rx_ok && (rx_type == T_RDREQ) && !serve_busy;
  assign fire_rsp   = fx_act && fx_done;
  assign fire_wrhdr = rx_ok && (rx_type == T_WRHDR);
  assign fire_psum  = rx_ok && (rx_type == T_PSUM) && !(ps_has_chk && efifo_full);
  assign fire_crc   = rx_ok && (rx_type == T_CRC);
  assign fire_rdy   = rx_ok && (rx_type == T_RDY);

  // CRC update for what is received this cycle: the whole data beat (its first rx_nw words), otherwise the one word; on
  // the source's accumulator registered with the beat (drop 0.42: was crc_acc[rx_src]), the count from the CRC's copy
  logic [15:0]   rx_crc_next;
  logic [CW-1:0] rx_nwc;
  assign rx_nwc = ~q_nwc_n;
  crc16_beat #(.BW(BW)) u_rxcrc (.crc_in(st_crc), .words(rx_words), .n(rx_nwc), .crc_out(rx_crc_next));
  generate
    for (genvar c = 0; c < ROWS; c++) begin : g_a
      assign abuf_wdata[c]  = ent[c / 4][8 * (c % 4) +: 8];
      assign abuf_wdata2[c] = ent2[c / 4][8 * (c % 4) +: 8];
    end
    // a weight entry (drop 0.42: {idx[COLS-1..0], wc[NCHK-1..0], w[COLS-1..0]}, wbuf_mem's layout): its WPW words as one
    // vector, the fields sliced out of it (NCHK = 1: the weight bytes and the check weight where they were)
    logic [32*WPW-1:0] wev, wev2;
    for (genvar w = 0; w < WPW; w++) begin : g_wev
      assign wev[32*w +: 32]  = ent[w];
      assign wev2[32*w +: 32] = ent2[w];
    end
    // drop 0.43: an INT4 entry's nibbles, sign-extended (w4s), are the 8-bit weights of the entry written (index 0)
    logic [32*WP4-1:0]    w4v, w4v2;
    logic signed [7:0]    w4s [COLS];
    logic signed [7:0]    w4s2 [COLS];
    for (genvar w = 0; w < WP4; w++) begin : g_w4v
      assign w4v[32*w +: 32]  = ent[w];
      assign w4v2[32*w +: 32] = ent2[w];
    end
    for (genvar j = 0; j < COLS; j++) begin : g_w
      assign w4s[j]  = {{4{w4v[4*j+3]}}, w4v[4*j +: 4]};
      assign w4s2[j] = {{4{w4v2[4*j+3]}}, w4v2[4*j +: 4]};
      if (IW > 0) begin : g_wi
        assign wbuf_wdata[j]  = fetch_is_w4 ? {{IW{1'b0}}, w4s[j]} : {wev[COLS * 8 + NCHK * WCW + IW * j +: IW], wev[8 * j +: 8]};
        assign wbuf_wdata2[j] = fetch_is_w4 ? {{IW{1'b0}}, w4s2[j]} : {wev2[COLS * 8 + NCHK * WCW + IW * j +: IW], wev2[8 * j +: 8]};
      end else begin : g_wn
        assign wbuf_wdata[j]  = fetch_is_w4 ? w4s[j] : wev[8 * j +: 8];
        assign wbuf_wdata2[j] = fetch_is_w4 ? w4s2[j] : wev2[8 * j +: 8];
      end
    end
    if (32 * WP4 > 4 * COLS) begin : g_w4pad
      logic unused_w4pad;
      assign unused_w4pad = ^{w4v[32*WP4-1:4*COLS], w4v2[32*WP4-1:4*COLS]};
    end
    if (32 * WPW > COLS * (8 + IW) + NCHK * WCW) begin : g_wpad   // the entry's last word above its fields
      logic unused_wpad;
      assign unused_wpad = ^{wev[32*WPW-1:COLS*(8+IW)+NCHK*WCW], wev2[32*WPW-1:COLS*(8+IW)+NCHK*WCW]};
    end
  endgenerate
  // the INT4 entry's check weight (drop 0.43): the sum of its sign-extended nibbles, column 0's check weight
  logic signed [WCW-1:0] w4c, w4c2;
  always_comb begin
    w4c = '0; w4c2 = '0;
    for (int j = 0; j < COLS; j++) begin
      w4c  = w4c + WCW'(w4s[j]);
      w4c2 = w4c2 + WCW'(w4s2[j]);
    end
  end
  assign wcbuf_wdata  = fetch_is_w4 ? (NCHK*WCW)'($unsigned(w4c)) : wev[COLS * 8 +: NCHK * WCW];
  assign wcbuf_wdata2 = fetch_is_w4 ? (NCHK*WCW)'($unsigned(w4c2)) : wev2[COLS * 8 +: NCHK * WCW];
  assign abuf_we    = fx_entry && !fetch_is_w && !fetch_is_r;
  assign abuf_we2   = fx_two && !fetch_is_w && !fetch_is_r;
  assign rbuf_we    = fx_entry && fetch_is_r;
  assign rbuf_we2   = fx_two && fetch_is_r;
  assign rbuf_waddr = fetch_entry[IDXW-1:0];
  assign wbuf_we    = fx_entry && fetch_is_w;
  assign wbuf_we2   = fx_two && fetch_is_w;
  assign abuf_waddr = fetch_entry[AW-1:0];
  assign wbuf_waddr = fetch_entry[WAW-1:0];

  // partial-sum rows under assembly (drop 0.41): one neo_mem_rf leaf of NSRC words x COLS*PW bits -- the row of source s is
  // word s, column j its write group j -- instead of NSRC registers of COLS*PW bits (65,536 of the silicon NIC's 71,999 flops in
  // the synthesis of drop 0.40, and the 64:1 row mux behind them). A PSUM beat writes the columns it covers (prow_we[j] for
  // j in [col0, col0 + words), its words at their column positions) at the address of its source; the beat with the check word
  // reads its source's row through the asynchronous port A in the same cycle, where prow[rx_src] was read before, and takes
  // the columns it carries itself from the beat (a write and a read of one word in a cycle return the old word, as the
  // registers did). No reset is needed: the drain sends every PSUM row whole and in column order -- COLS + 1 words from
  // column 0, the check word last (DRAIN_PSUM always drains INT32 rows) -- and a source's beats arrive in order (XY routing;
  // the packer and the unpacker merge and split beats without reordering them), so every column the check beat reads was
  // written by an earlier beat of the same row from the same source; no row is shorter than COLS, so no read needs masking.
  // The only read of a column the row did not write follows a lost flit, a fault the stream's word count and CRC flag at
  // its CRC flit (lost_err, crc_err), with the row wrong either way: the reset registers read zeros for a column lost
  // before the source's first complete row and the previous row's column after it, the leaf its power-up content or the
  // previous row's column. The simulators initialize the array to zero, so every test sees what the registers gave.
  logic [COLS-1:0]    prow_we;                            // the PSUM beat covers column j
  logic [COLS*PW-1:0] prow_wdata;                         // the beat's words at their column positions
  logic [COLS*PW-1:0] prow_rd;                            // the row of rx_src (port A, combinational)
  logic [COLS*PW-1:0] unused_prow_rb, unused_prow_bd;     // the leaf's unused read ports (RB = 0, no hooks here)
  generate
    for (genvar j = 0; j < COLS; j++) begin : g_prow
      assign prow_we[j] = fire_psum && q_pcm[j];
      assign prow_wdata[PW * j +: PW] = bt_rot[32 * (j % BW) +: 32];     // word j - col0 (the rotation by col0)
    end
  endgenerate
  neo_mem_rf #(.W(COLS * PW), .D(NSRC), .G(COLS), .RB(0), .DV_HOOKS(0)) u_prow (
    .clk(clk),
    .we(prow_we), .waddr(rx_src), .wdata(prow_wdata),
    .ra_addr(rx_src), .ra_data(prow_rd),
    .rb_en(1'b0), .rb_addr('0), .rb_data(unused_prow_rb),
    .bd_we('0), .bd_addr('0), .bd_wdata('0), .bd_rdata(unused_prow_bd),
    .fi_en(1'b0), .fi_or(1'b0), .fi_addr('0), .fi_mask('0));

  // per-source state: write pointer, CRC accumulator, word count -- each source's registers, written from the write-back
  // register when that source's state leaves st_* (drop 0.42; before, by each beat of the source)
  generate
    for (genvar s = 0; s < NSRC; s++) begin : g_src
      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
          wr_ptr[s] <= '0; crc_acc[s] <= 16'hFFFF; rx_cnt[s] <= '0;
        end else if (wb3_v && (wb3_src == s)) begin
          wr_ptr[s] <= wb3_ptr; crc_acc[s] <= wb3_crc; rx_cnt[s] <= wb3_cnt;
        end
      end
    end
  endgenerate

  // the registered beat's source state (drop 0.42): u_* = st_* after this cycle's beat, as the g_src registers were updated
  // before; lk_* = the state of the port's beat's source, from wb3_* when it is the source going back this cycle
  assign in_src  = SRCW'(32'(rx_flit[SRCY_LO +: YW]) * NX + 32'(rx_flit[SRCX_LO +: XW]));
  assign st_load = rx_valid && rx_ready && (!st_v || (in_src != st_src));
  always_comb begin
    u_ptr = st_ptr; u_crc = st_crc; u_cnt = st_cnt;
    if (fire_wrhdr) u_ptr = rx_word[BAW-1:0];
    if (fire_wrdata) begin                                   // the whole beat, after its last row write
      u_ptr = st_ptr + BAW'(rx_nw);
      u_crc = rx_crc_next;
      u_cnt = st_cnt + 16'(rx_nw);
    end
    if (fire_rsp || fire_psum) begin                         // the whole beat, after its last entry / its columns
      u_crc = rx_crc_next;
      u_cnt = st_cnt + 16'(rx_nw);
    end
    if (fire_crc) begin
      u_crc = 16'hFFFF;
      u_cnt = '0;
    end
    if (wb3_v && (wb3_src == in_src)) begin
      lk_ptr = wb3_ptr; lk_crc = wb3_crc; lk_cnt = wb3_cnt;
    end else begin
      lk_ptr = wr_ptr[in_src]; lk_crc = crc_acc[in_src]; lk_cnt = rx_cnt[in_src];
    end
  end
  // the lane offset of st_ptr's next value as a one-hot (drop 0.42): the port beat's source's (lk_ptr), a WRHDR's
  // address, a WRDATA beat's lane turned by its word count (st_rot rotated by q_nwo), or unchanged -- and the values
  // derived from it, each an OR of the one-hot's bits where its constant has a one: no adder, no compare
  logic [BW-1:0] rot_n, rot_ld, rot_wh, rot_wd;
  logic [BW-1:0] rot_mge;
  logic [LW-1:0] rot_md1 [BW];
  logic [RW-1:0] rot_md2 [BW];
  always_comb begin
    for (int k = 0; k < BW; k++) begin
      rot_ld[k] = lk_ptr[LW-1:0] == LW'(k);
      rot_wh[k] = rx_word[LW-1:0] == LW'(k);
      rot_wd[k] = 1'b0;
      for (int j = 0; j < BW; j++) rot_wd[k] = rot_wd[k] | (st_rot[j] && q_nwo[(k + BW - j) % BW]);
    end
    rot_n = st_load ? rot_ld : fire_wrhdr ? rot_wh : fire_wrdata ? rot_wd : st_rot;
    rot_mge = '0;
    for (int l = 0; l < BW; l++) begin rot_md1[l] = '0; rot_md2[l] = '0; end
    for (int k = 0; k < BW; k++) begin                     // lane offset k:
      for (int l = 0; l < BW; l++) begin
        if (rot_n[k] && (l >= k)) rot_mge[l] = 1'b1;       //   lane l >= k
        if (rot_n[k]) rot_md1[l] = rot_md1[l] | LW'(l + BW - k);   // l - k (mod BW)
        if (rot_n[k]) rot_md2[l] = rot_md2[l] | RW'(l + BW - k);   // l + BW - k
      end
    end
  end
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      st_v <= 1'b0; st_src <= '0; st_ptr <= '0; st_crc <= 16'hFFFF; st_cnt <= '0;
      wb3_v <= 1'b0; wb3_src <= '0; wb3_ptr <= '0; wb3_crc <= 16'hFFFF; wb3_cnt <= '0;
      st_mge <= '1; st_rot <= BW'(1);
      for (int l = 0; l < BW; l++) begin st_md1[l] <= LW'(l); st_md2[l] <= RW'(l + BW); end
    end else begin
      wb3_v <= st_load && st_v;
      if (st_load) begin                                     // a beat of another source: its state in, st_* out to wb3_*
        st_v <= 1'b1; st_src <= in_src; st_ptr <= lk_ptr; st_crc <= lk_crc; st_cnt <= lk_cnt;
        wb3_src <= st_src; wb3_ptr <= u_ptr; wb3_crc <= u_crc; wb3_cnt <= u_cnt;
      end else begin                                         // the same source (or none): in place
        st_ptr <= u_ptr; st_crc <= u_crc; st_cnt <= u_cnt;
      end
      // the lane offset's derived values, with st_ptr
      st_rot  <= rot_n;
      st_mge  <= rot_mge;
      for (int l = 0; l < BW; l++) begin st_md1[l] <= rot_md1[l]; st_md2[l] <= rot_md2[l]; end
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      lost_err_sticky <= 1'b0; fetch_timeout_sticky <= 1'b0; fetch_timer <= '0; iso_err_sticky <= 1'b0;
      fetch_busy <= 1'b0; fetch_is_w <= 1'b0; fetch_is_w4 <= 1'b0; fetch_is_r <= 1'b0; fetch_src <= '0;
      for (int w = 0; w < EW; w++) stage[w] <= '0;
      efifo_valid[0] <= 1'b0; efifo_valid[1] <= 1'b0; efifo_wr <= 1'b0; efifo_rd <= 1'b0;
      efifo_idx[0] <= '0; efifo_idx[1] <= '0; efifo_chk[0] <= '0; efifo_chk[1] <= '0;
      for (int j = 0; j < COLS; j++) begin efifo_y[0][j] <= '0; efifo_y[1][j] <= '0; end
      serve_busy <= 1'b0; serve_x <= '0; serve_y <= '0; serve_len <= '0;
      crc_err_sticky <= 1'b0; rdy_seen <= 1'b0;
      wb_phase <= 1'b0;
    end else begin
      if (err_clear) begin crc_err_sticky <= 1'b0; lost_err_sticky <= 1'b0; fetch_timeout_sticky <= 1'b0; iso_err_sticky <= 1'b0; end
      if (rx_foreign) iso_err_sticky <= 1'b1;
      if (rdy_clear) rdy_seen <= 1'b0;
      // fetch watchdog: a response that does not complete in time is flagged and abandoned
      if (fetch_busy) begin
        fetch_timer <= fetch_timer + 1;
        if (fetch_timer == FTW'(FETCH_TIMEOUT - 1)) begin fetch_timeout_sticky <= 1'b1; fetch_busy <= 1'b0; end
      end else begin
        fetch_timer <= '0;
      end
      // fetch command
      if (fetch_cmd) begin                                 // (the staged count, the entry index: the block above)
        fetch_busy <= 1'b1; fetch_is_w <= (cmd_op == 3'd2);
        fetch_is_w4 <= (cmd_op == 3'd2) && cmd_int8; fetch_is_r <= (cmd_op == 3'd1) && cmd_int8;   // drop 0.43
        fetch_src  <= SRCW'(32'(cmd_y) * NX + 32'(cmd_x));
      end
      // ext FIFO pop
      if (ext_valid && ext_ready) begin
        efifo_valid[efifo_rd] <= 1'b0;
        efifo_rd <= !efifo_rd;
      end
      // second-row phase of a crossing WRDATA beat
      wb_phase <= wb_active && wb_cross && !wb_phase;
      // fetch receive: leftovers staged (the staged count, the beat position and the entry index: the block above)
      for (int w = 0; w < EW; w++)
        if (fx_swe[w]) stage[w] <= fx_two ? fx_r2[w] : fx_entry ? fx_r1[w] : fx_r0[w];
      if (fire_psum && ps_has_chk) begin                   // PSUM check word: the row is complete -- the columns this
        efifo_valid[efifo_wr] <= 1'b1;                     // beat brings are taken from the beat, the rest from the
        efifo_idx[efifo_wr]   <= rx_tag[IDXW-1:0];         // source's row in the u_prow leaf
        efifo_chk[efifo_wr]   <= bt_rot[32 * (COLS % BW) +: 32];
        for (int j = 0; j < COLS; j++)
          efifo_y[efifo_wr][j] <= q_pcm[j] ? prow_wdata[PW * j +: PW] : prow_rd[PW * j +: PW];
        efifo_wr <= !efifo_wr;
      end
      if (fire_rdreq) begin
        serve_busy <= 1'b1;
        serve_y    <= YW'(rx_data[39:36]);                 // the request carries 4-bit coordinates and a 20-bit address
        serve_x    <= XW'(rx_data[35:32]);
        serve_len  <= rx_data[31:20];
        // (the address: s_row / s_room / s_sel, loaded with the request in the transmit block -- drop 0.42)
      end
      if (fire_rdy) rdy_seen <= 1'b1;
      if (fire_crc) begin                                  // (st_*: the state of the beat's source, drop 0.42)
        if (st_crc != rx_data[15:0]) crc_err_sticky <= 1'b1;
        if (st_cnt != rx_data[31:16]) lost_err_sticky <= 1'b1;
        if (fetch_busy && rx_src == fetch_src) fetch_busy <= 1'b0;   // our fetch response is complete
      end
      if (serve_done) serve_busy <= 1'b0;
    end
  end

  assign ext_valid = efifo_valid[efifo_rd];
  assign ext_idx   = efifo_idx[efifo_rd];
  assign ext_chk   = efifo_chk[efifo_rd];
  generate
    for (genvar j = 0; j < COLS; j++) begin : g_e
      assign ext_y[j] = efifo_y[efifo_rd][j];
    end
  endgenerate
  assign cmd_busy = fetch_busy;

  // ------------------------------------------------------------------ drain FIFO (rows from the core)
  // Drop 0.40: the rows live in a neo_mem_rf leaf of DFD words -- {index, check sum, COLS partial sums}, an asynchronous read at
  // the head pointer -- the module the synthesis flow black-boxes and a memory compiler replaces (DFD = ACC_ROWS rows of
  // 1,065 bits in silicon: 545 kbit, which the synthesis of drop 0.40 found as flops); the pointers and the count stay here.
  // Drop 0.42: the head is read on the leaf's registered port B (port A tied off: a one-write one-read synchronous array),
  // at the read pointer's next value every cycle, so dfifo_head is the row at dfifo_rd from the register of the read --
  // valid for a row written at least two cycles ago (a read in the cycle of the row's write returns the old word):
  // dfifo_vis counts those rows (a push is counted in dfifo_np at once, in dfifo_vis a cycle later); the transmit engine
  // starts and continues on dfifo_vis.
  // Drop 0.42: the row carries the parity of each of its beats (bpar, formed at the push), so the parity of the flit that
  // carries a beat needs no XOR tree over the beat's words behind the beat's select
  localparam int NDB  = (COLS + BW) / BW;                  // beats of an INT32 row of COLS + 1 words (3 at 32x32, 1 at 16x8)
  localparam int DBW  = (NDB > 1) ? $clog2(NDB) : 1;
  localparam int DFWD = NDB + IDXW + (COLS + 1) * PW;      // a FIFO row: {bpar[NDB], idx, word[COLS] .. word[0]}
  logic [DFWD-1:0]      dfifo_wrow, dfifo_head;            // the row written on a push; the row at the read pointer (registered read)
  logic [DFWD-1:0]      unused_dfifo_ra, unused_dfifo_bd;  // the leaf's unused read ports (port A tied off, no hooks here)
  logic [IDXW-1:0]      dfifo_head_idx;
  logic [NDB-1:0]       dfifo_head_bpar;                   // the head row's beat parities
  localparam int DFW = $clog2(DFD);                       // pointer width from the depth (drop 0.21)
  logic [DFW-1:0]       dfifo_wr;
  logic [DFW-1:0]       dfifo_rd;
  logic [DFW-1:0]       dfifo_rd_nxt;                     // dfifo_rd of the next cycle: the address the registered port reads now
  logic [DFW:0]         dfifo_np, dfifo_nq, dfifo_npq;    // rows pushed / popped, mod 2^(DFW+1); dfifo_np of the cycle
                                                          // before (drop 0.42: two up / down counts before, dfifo_cnt and
                                                          // dfifo_vis, the push and the pop selecting in front of their
                                                          // adders; rows in the FIFO = pushed - popped, readable rows =
                                                          // pushed a cycle earlier - popped)
  logic [DFW:0]         dfifo_vis;                        // rows the registered port shows (pushed two or more cycles ago, not sent)
  assign dfifo_vis = dfifo_npq - dfifo_nq;
  logic                 dfifo_push_q;                     // a row was pushed in the last cycle (in dfifo_np, not yet in dfifo_vis)
  logic                 d_pop;                            // the head row's last beat is sent this cycle
  logic                 drain_mode_psum;
  logic [XW-1:0]        drain_x;
  logic [YW-1:0]        drain_y;
  logic [19:0]          drain_gaddr;                      // the address of the row group being sent (drop 0.43: of the drain,
                                                          // drain_addr, before groups)
  logic                 drain_tr, drain_ent, drain_pad;   // drop 0.43: DRAIN_WR's INT8 format, base[15:13]
  logic [12:0]          drain_stride;                     // base[12:0]: a group of COLS rows starts every stride words (0: none)
  localparam int CB = (COLS > 1) ? $clog2(COLS) : 1;
  logic [CB-1:0]        drain_grow;                       // the row's place in its group
  logic                 drain_hdr_pend;                   // the next row starts a group: its WRHDR first
  logic [11:0]          drain_rows;                       // rows expected in this drain message
  logic [11:0]          drain_sent;                       // rows fully sent
  logic                 drain_armed;
  logic                 drain_open;                       // message started, rows still to come
  assign drain_open = drain_armed && (drain_sent != 0);

  // ------------------------------------------------------------------ TX engine
  localparam logic [3:0] X_IDLE = 4'd0, X_REQ = 4'd1, X_SERVE_RD = 4'd2, X_SERVE_SEND = 4'd3,
                         X_SERVE_CRC = 4'd4, X_DRAIN_HDR = 4'd5, X_DRAIN_WORD = 4'd6, X_DRAIN_CRC = 4'd7,
                         X_NOTIFY = 4'd8;
  logic [3:0]  tx_state;
  logic        tx_fire;                                   // the link side takes the flit presented
  logic        ntf_pending;
  logic [XW-1:0] ntf_x;
  logic [YW-1:0] ntf_y;
  // serve: rows from the bank, beats to the link
  // Drop 0.42: the serve's position as registers -- the beat's row (s_row), the words still to send (s_rem), the lanes
  // left in the row (s_room), the shift's select as a one-hot (s_sel) and whether this beat is the last (s_last) --,
  // where serve_addr + serve_i and serve_len - serve_i were formed every cycle (the adder behind the 2,048-pin shift
  // select and the count logic: the transmit side's long path). Every beat but the first starts at lane 0 of the next
  // row (the first one ends at its row's end unless it is the last), so a beat moves them to row + 1, s_rem - s_room,
  // BW lanes, lane 0, and s_last = (s_rem - s_room <= BW)
  logic [VW-1:0]  serve_row;                              // the row read last, held while the link does not accept
  logic [VW-1:0]  serve_cur;
  logic [BW-1:0]  b_wp, sr_wp;                            // word parities of the bank's row, of serve_row (registered with it)
  logic [BAW-1:0] rq_addr;                                // the request's word address
  logic [BRAW-1:0] s_row;                                 // the row of the beat's word 0
  logic [12:0]    s_rem;                                  // words still to send
  logic [CW-1:0]  s_room;                                 // lanes from the beat's word 0 to the end of the row, 1..BW
  logic [BW-1:0]  s_sel;                                  // one-hot: bit k = (the beat's lane == k)
  logic [BW-1:0]  s_msk;                                  // the lanes from the beat's lane on (the beat's words: its parity)
  logic [CW-1:0]  s_n;                                    // words in this beat
  logic           s_last;                                 // this beat completes the serve: s_rem <= s_room (a register)
  logic           serve_last;
  logic [VW-1:0]  serve_beat;
  assign serve_cur  = b_rvalid ? b_rdata : serve_row;
  assign rq_addr    = BAW'(rx_data[19:0]);
  assign serve_last = s_last;
  assign s_n        = serve_last ? CW'(s_rem) : s_room;
  always_comb begin                                       // word i of the beat = word i + lane of the row (zero past its end)
    serve_beat = '0;
    for (int i = 0; i < BW; i++)
      for (int k = 0; k < BW - i; k++)
        if (s_sel[k]) serve_beat[32 * i +: 32] = serve_beat[32 * i +: 32] | serve_cur[32 * (i + k) +: 32];
  end
  localparam int DWW = $clog2(COLS + 1);                 // row word index width: 0..COLS (drop 0.21)
  logic [DBW-1:0] dbeat;                                  // the beat of the row being sent (drop 0.42; was its first word, dword)
  logic [DWW-1:0] dword;                                  // its first word: BW * dbeat (0..COLS = check; INT8: 0)
  logic [31:0]    dfifo_head_w [NDB * BW];                // the head row's words (INT8 rows packed at the push), zero above COLS
  logic        drain_int8;
  logic        drain_push;
  // the INT8 row pushed (drop 0.43): the requantized row, or with the transpose format the row the block buffer reads out
  logic signed [7:0] drow [COLS];
  logic signed [7:0] tr_row [COLS];
  logic              tr_out_v;                            // a block is being read out (one row per cycle)
  assign drain_push = drain_int8 ? (drain_tr ? tr_out_v : rq_valid) : rd_valid;
  generate
    for (genvar j = 0; j < COLS; j++) begin : g_drow
      assign drow[j] = drain_tr ? tr_row[j] : rq_q[j];
    end
  endgenerate
  // the transpose (drop 0.43): block k of COLS INT8 rows is written into tb by rows (k even) or by columns (k odd) and read
  // out in the other orientation, row c of the output = column c of the block, from the cycle after its last row, one row
  // per cycle -- the slot output row c reads is the one row c of block k + 1 writes, which arrives no earlier than that
  // cycle (rows come one per cycle at most), so one buffer serves a stream of blocks
  logic signed [7:0] tb [COLS][COLS];
  logic [CB-1:0]     tr_in, tr_out;                       // the row written next, the row read out
  logic              tr_or, tr_out_or;                    // the orientation of the block written, of the block read
  always_comb begin
    for (int i = 0; i < COLS; i++) tr_row[i] = tr_out_or ? tb[tr_out][i] : tb[i][tr_out];
  end
  always_ff @(posedge clk) begin                          // the block buffer (data, no reset)
    if (drain_int8 && drain_tr && rq_valid) begin
      for (int i = 0; i < COLS; i++) begin
        if (tr_or) tb[i][tr_in] <= rq_q[i];
        else       tb[tr_in][i] <= rq_q[i];
      end
    end
  end
  // the entry format's check weight: the sum of the row's bytes (WCW bits), and the words of an INT8 row by format
  logic signed [WCW-1:0] dchk;
  always_comb begin
    dchk = '0;
    for (int j = 0; j < COLS; j++) dchk = dchk + WCW'(drow[j]);
  end
  localparam int NI8  = COLS / 4;                         // words of an INT8 row
  localparam int NI8P = (WPA > NI8) ? WPA : NI8;          // padded to an activation entry
  logic [CW-1:0]  ni8;                                    // this drain's words per INT8 row
  assign ni8 = drain_ent ? CW'(WPW) : drain_pad ? CW'(NI8P) : CW'(NI8);
  // the drain beat (drop 0.31): words dword .. dword + d_n - 1 of the row at the FIFO's read pointer. Drop 0.42: a row goes
  // out in beats of BW words from its word 0, so beat word i is row word BW * dbeat + i, a select among NDB words by dbeat
  // (both modes: the INT8 rows are packed at the push), zero at and above d_n. The beat's count, whether it completes
  // the row, its first word and its word mask are constants of the beat index and the mode, selected by the two
  // registers (drop 0.42: the words left were formed and compared by adders in front of the read pointer's step)
  logic [CW-1:0]  d_n;                                    // words in this beat
  logic           d_row_done;                             // this beat completes the row
  logic [BW-1:0]  d_msk;                                  // word i is below d_n
  logic [31:0]    d_word [BW];
  logic [VW-1:0]  drain_beat;
  always_comb begin
    dword = '0; d_n = '0; d_row_done = 1'b0; d_msk = '0;
    for (int b = 0; b < NDB; b++)
      if (dbeat == DBW'(b)) begin                         // words left from beat b: COLS / 4 - b BW (INT8), COLS + 1 - b BW
        dword = DWW'(b * BW);
        if (drain_int8) begin                             // one beat (ni8 <= BW words; drop 0.43: by the format)
          d_n        = (b == 0) ? ni8 : '0;
          d_row_done = 1'b1;
          for (int i = 0; i < BW; i++) d_msk[i] = (b == 0) && (CW'(i) < ni8);
        end else begin
          d_n        = (COLS + 1 - b * BW <= 0) ? '0 : (COLS + 1 - b * BW < BW) ? CW'(COLS + 1 - b * BW) : CW'(BW);
          d_row_done = (COLS + 1 - b * BW <= BW);
          for (int i = 0; i < BW; i++) d_msk[i] = (i < COLS + 1 - b * BW);
        end
      end
    drain_beat = '0;
    for (int i = 0; i < BW; i++) begin
      d_word[i] = '0;
      for (int b = 0; b < NDB; b++)
        if (dbeat == DBW'(b)) d_word[i] = dfifo_head_w[b * BW + i];
      if (d_msk[i]) drain_beat[32 * i +: 32] = d_word[i];
    end
  end
  // the drain FIFO's leaf (drop 0.40): the row pushed = {its beats' parities, index of the row in the drain, its words}:
  // INT32 rows the COLS partial sums then the check sum; INT8 rows (drop 0.42) the requantized bytes packed four per word,
  // column 4k in the low byte of word k, the words a beat carries; beat b's parity over its words b BW .. b BW + BW - 1
  // (zero above the row's words: the beat's words below d_n); the head row is read on port B (above)
  always_comb begin
    dfifo_wrow = '0;
    for (int j = 0; j < COLS; j++) dfifo_wrow[j * PW +: PW] = rd_data[j];
    dfifo_wrow[COLS * PW +: PW] = rd_chk;
    if (drain_int8) begin                                 // (drop 0.43: drow -- rq_q, or the transposed row; the entry's check weight)
      for (int k = 0; k < COLS / 4; k++)
        dfifo_wrow[k * PW +: PW] = PW'({drow[4 * k + 3], drow[4 * k + 2], drow[4 * k + 1], drow[4 * k]});
      for (int k = COLS / 4; k <= COLS; k++)
        dfifo_wrow[k * PW +: PW] = '0;
      if (drain_ent) dfifo_wrow[(COLS / 4) * PW +: PW] = PW'($unsigned(dchk));
    end
    dfifo_wrow[(COLS + 1) * PW +: IDXW] = IDXW'(drain_sent) + IDXW'(dfifo_np - dfifo_nq);   // rows sent + rows in the FIFO: rows arrive in order 0..M-1 (the index wraps)
    for (int w = 0; w <= COLS; w++)
      dfifo_wrow[(COLS + 1) * PW + IDXW + w / BW] = dfifo_wrow[(COLS + 1) * PW + IDXW + w / BW] ^ (^dfifo_wrow[w * PW +: PW]);
  end
  assign d_pop        = (tx_state == X_DRAIN_WORD) && tx_fire && d_row_done;
  assign dfifo_rd_nxt = !d_pop ? dfifo_rd : (dfifo_rd == DFW'(DFD - 1)) ? '0 : dfifo_rd + 1;
  neo_mem_rf #(.W(DFWD), .D(DFD), .G(1), .RB(1), .DV_HOOKS(0)) u_dfifo (
    .clk(clk),
    .we(drain_push), .waddr(dfifo_wr), .wdata(dfifo_wrow),
    .ra_addr('0), .ra_data(unused_dfifo_ra),
    .rb_en(1'b1), .rb_addr(dfifo_rd_nxt), .rb_data(dfifo_head),
    .bd_we('0), .bd_addr('0), .bd_wdata('0), .bd_rdata(unused_dfifo_bd),
    .fi_en(1'b0), .fi_or(1'b0), .fi_addr('0), .fi_mask('0));
  always_comb begin
    for (int w = 0; w <= COLS; w++) dfifo_head_w[w] = dfifo_head[w * PW +: PW];
    for (int w = COLS + 1; w < NDB * BW; w++) dfifo_head_w[w] = 32'd0;
    dfifo_head_idx = dfifo_head[(COLS + 1) * PW +: IDXW];
    dfifo_head_bpar = dfifo_head[(COLS + 1) * PW + IDXW +: NDB];
  end
  logic [15:0] tx_crc;
  logic [15:0] tx_crc_next;
  logic [15:0] tx_cnt;                                    // data words sent in the current message
  logic [VW-1:0] tx_words;                                // the beat being transmitted
  logic [CW-1:0] tx_n;                                    // its word count
  logic        req_pending;
  logic [XW-1:0] req_x;
  logic [YW-1:0] req_y;
  logic [19:0]   req_addr;
  logic [11:0]   req_len;
  // the CRC one cycle behind the beats (drop 0.42): a data beat sent is registered with its count and accumulated in the
  // next cycle, so the beat's assembly (the bank row's shift, the drain row's select) and the CRC tree are two paths
  logic          txc_v;                                   // a data beat was sent in the last cycle: tx_crc takes it now
  logic [VW-1:0] txc_words;
  logic [CW-1:0] txc_n;
  logic          tx_dfire;                                // a data beat is sent this cycle
  assign tx_dfire = tx_fire && ((tx_state == X_SERVE_SEND) || (tx_state == X_DRAIN_WORD));

  crc16_beat #(.BW(BW)) u_txcrc (.crc_in(tx_crc), .words(txc_words), .n(txc_n), .crc_out(tx_crc_next));

  // flit assembly
  logic [2:0]   tx_type;
  logic [8:0]   tx_tag;
  logic [19:0]  tx_hdr;
  logic [XW-1:0] tx_dx;
  logic [YW-1:0] tx_dy;
  logic [FW-2:0] tx_body;
  always_comb begin
    tx_type = 3'd0; tx_tag = 9'd0; tx_hdr = 20'd0; tx_words = '0; tx_n = CW'(1); tx_dx = '0; tx_dy = '0; tx_valid = 1'b0;
    case (tx_state)
      X_REQ: begin
        tx_type = T_RDREQ; tx_dx = req_x; tx_dy = req_y;
        tx_hdr = {12'd0, 4'(my_y), 4'(my_x)}; tx_words = VW'({req_len, req_addr});
        tx_valid = 1'b1;
      end
      X_SERVE_SEND: begin
        tx_type = T_RDRSP; tx_dx = serve_x; tx_dy = serve_y;
        tx_hdr = {6'd0, 6'(s_n - CW'(1)), 8'd0}; tx_words = serve_beat; tx_n = s_n; tx_valid = 1'b1;
      end
      X_SERVE_CRC: begin                                     // once the last beat's CRC is in (drop 0.42)
        tx_type = T_CRC; tx_dx = serve_x; tx_dy = serve_y; tx_words = VW'({tx_cnt, tx_crc}); tx_valid = !txc_v;
      end
      X_DRAIN_HDR: begin
        tx_type = T_WRHDR; tx_dx = drain_x; tx_dy = drain_y; tx_words = VW'({12'd0, drain_gaddr}); tx_valid = 1'b1;
      end
      X_DRAIN_WORD: begin
        tx_dx = drain_x; tx_dy = drain_y; tx_words = drain_beat; tx_n = d_n; tx_valid = 1'b1;
        if (drain_mode_psum) begin
          tx_type = T_PSUM; tx_tag = 9'(dfifo_head_idx); tx_hdr = {6'd0, 6'(d_n - CW'(1)), 8'(dword)};
        end else begin
          tx_type = T_WRDATA; tx_hdr = {6'd0, 6'(d_n - CW'(1)), 8'd0};
        end
      end
      X_DRAIN_CRC: begin
        tx_type = T_CRC; tx_dx = drain_x; tx_dy = drain_y; tx_words = VW'({tx_cnt, tx_crc}); tx_valid = !txc_v;
      end
      X_NOTIFY: begin
        tx_type = T_RDY; tx_dx = ntf_x; tx_dy = ntf_y; tx_valid = 1'b1;
      end
      default: ;
    endcase
    tx_body = {tx_dy, tx_dx, my_y, my_x, 4'd0, partition, tx_type, tx_tag, tx_hdr, tx_words};
  end
  // even parity over the whole flit (drop 0.42): the header fields' XOR and the words' parity, formed per state -- a serve
  // beat carries the row's words from its lane on (the row's word parities under the lane mask s_msk), a drain beat its
  // row's beat parity from the FIFO -- where one XOR tree over the 560 bits followed the words' select
  logic [BW-1:0] sc_wp;                                   // parity of serve_cur's word j
  logic          tw_par;                                  // parity of tx_words
  always_comb begin
    for (int j = 0; j < BW; j++) begin
      b_wp[j]  = ^b_rdata[32 * j +: 32];
      sc_wp[j] = b_rvalid ? b_wp[j] : sr_wp[j];
    end
    tw_par = 1'b0;
    case (tx_state)
      X_REQ:        tw_par = ^{req_len, req_addr};
      X_SERVE_SEND: tw_par = ^(sc_wp & s_msk);
      X_SERVE_CRC, X_DRAIN_CRC: tw_par = ^{tx_cnt, tx_crc};
      X_DRAIN_HDR:  tw_par = ^drain_gaddr;
      X_DRAIN_WORD: begin
        for (int b = 0; b < NDB; b++) if (dbeat == DBW'(b)) tw_par = dfifo_head_bpar[b];
      end
      default: ;
    endcase
  end
  assign tx_flit = {^{tx_dy, tx_dx, my_y, my_x, 4'd0, partition, tx_type, tx_tag, tx_hdr} ^ tw_par, tx_body};

  assign tx_fire = tx_valid && tx_ready;
  assign serve_done = (tx_state == X_SERVE_CRC) && tx_fire;
  assign drain_busy = drain_armed;
  assign ntf_busy   = ntf_pending;

  // bank row read for serving: the first row in X_SERVE_RD, then the next row as each beat is accepted (pipelined:
  // one beat per cycle while the link accepts; the row read last is held in serve_row while it does not)
  assign b_re   = (tx_state == X_SERVE_RD) || ((tx_state == X_SERVE_SEND) && tx_fire && !serve_last);
  assign b_rrow = (tx_state == X_SERVE_RD) ? s_row : s_row + BRAW'(1);

  generate
    if (DV_HOOKS != 0) begin : g_dv
      always_ff @(negedge clk) if (dv_tx_hold) tx_state <= X_SERVE_RD;
    end else begin : g_nodv
      logic unused_dv;
      assign unused_dv = dv_tx_hold;
    end
  endgenerate

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      tx_state <= X_IDLE; serve_row <= '0; sr_wp <= '0; dbeat <= '0; tx_crc <= 16'hFFFF; tx_cnt <= '0;
      s_row <= '0; s_rem <= '0; s_room <= CW'(BW); s_sel <= BW'(1); s_msk <= '1; s_last <= 1'b1;
      txc_v <= 1'b0; txc_words <= '0; txc_n <= '0;
      req_pending <= 1'b0; req_x <= '0; req_y <= '0; req_addr <= '0; req_len <= '0;
      ntf_pending <= 1'b0; ntf_x <= '0; ntf_y <= '0;
      dfifo_wr <= '0; dfifo_rd <= '0; dfifo_np <= '0; dfifo_nq <= '0; dfifo_npq <= '0; dfifo_push_q <= 1'b0;
      drain_mode_psum <= 1'b0; drain_x <= '0; drain_y <= '0; drain_gaddr <= '0; drain_int8 <= 1'b0;
      drain_rows <= '0; drain_sent <= '0; drain_armed <= 1'b0;
      drain_tr <= 1'b0; drain_ent <= 1'b0; drain_pad <= 1'b0; drain_stride <= '0; drain_grow <= '0; drain_hdr_pend <= 1'b0;
      tr_in <= '0; tr_out <= '0; tr_or <= 1'b0; tr_out_or <= 1'b0; tr_out_v <= 1'b0;
    end else begin
      // commands
      if (cmd_valid && (cmd_op == 3'd1 || cmd_op == 3'd2) && !fetch_busy) begin
        req_pending <= 1'b1; req_x <= cmd_x; req_y <= cmd_y; req_addr <= cmd_addr; req_len <= cmd_len;
      end
      if (cmd_valid && (cmd_op == 3'd3 || cmd_op == 3'd4)) begin
        drain_mode_psum <= (cmd_op == 3'd4); drain_x <= cmd_x; drain_y <= cmd_y; drain_gaddr <= cmd_addr;
        drain_rows <= cmd_len; drain_sent <= '0; drain_armed <= 1'b1; drain_int8 <= (cmd_op == 3'd3) && cmd_int8;
        // drop 0.43: the INT8 format (an INT32 or PSUM drain has none), the groups, the transpose's blocks from the start
        drain_tr  <= (cmd_op == 3'd3) && cmd_int8 && cmd_base[15];
        drain_ent <= (cmd_op == 3'd3) && cmd_int8 && cmd_base[14];
        drain_pad <= (cmd_op == 3'd3) && cmd_int8 && cmd_base[13];
        drain_stride <= ((cmd_op == 3'd3) && cmd_int8) ? cmd_base[12:0] : '0;
        drain_grow <= '0; drain_hdr_pend <= 1'b0;
        tr_in <= '0; tr_or <= 1'b0;
      end
      // the transpose's counters (drop 0.43): a row written, a block complete -> its read-out, COLS rows
      if (drain_int8 && drain_tr && rq_valid) begin
        tr_in <= (tr_in == CB'(COLS - 1)) ? '0 : tr_in + CB'(1);
        if (tr_in == CB'(COLS - 1)) begin tr_out_v <= 1'b1; tr_out <= '0; tr_out_or <= tr_or; tr_or <= !tr_or; end
      end
      if (tr_out_v && !(drain_int8 && drain_tr && rq_valid && (tr_in == CB'(COLS - 1)))) begin
        tr_out <= tr_out + CB'(1);
        if (tr_out == CB'(COLS - 1)) tr_out_v <= 1'b0;
      end
      if (cmd_valid && cmd_op == 3'd5) begin
        ntf_pending <= 1'b1; ntf_x <= cmd_x; ntf_y <= cmd_y;
      end
      // drain FIFO push (no backpressure: sized for a full accumulator drain); the row itself goes into the leaf below
      if (drain_push) begin
        dfifo_wr <= (dfifo_wr == DFW'(DFD - 1)) ? '0 : dfifo_wr + 1;
      end
      if (drain_push) dfifo_np <= dfifo_np + 1;
      if (d_pop)      dfifo_nq <= dfifo_nq + 1;
      dfifo_npq <= dfifo_np;                              // (dfifo_vis: the registered port shows a row from the second cycle)
      dfifo_push_q <= drain_push;                         // the registered port shows a row from the second cycle after its push
      dfifo_rd <= dfifo_rd_nxt;

      if (b_rvalid) begin serve_row <= b_rdata; sr_wp <= b_wp; end   // the row read last cycle

      // the serve's position from the request as it is taken (drop 0.42; a request is taken only with no serve going on,
      // so this is the serve's start -- also for an engine held past X_IDLE, the dead-server fault of M9b)
      if (fire_rdreq) begin
        s_row     <= rq_addr[BAW-1:LW]; s_rem <= {1'b0, rx_data[31:20]};
        s_room    <= CW'(BW) - CW'(rq_addr[LW-1:0]);
        s_last    <= {1'b0, rx_data[31:20]} <= 13'(BW) - 13'(rq_addr[LW-1:0]);
        for (int k = 0; k < BW; k++) begin s_sel[k] <= rq_addr[LW-1:0] == LW'(k); s_msk[k] <= rq_addr[LW-1:0] <= LW'(k); end
      end
      // the CRC one cycle behind the beats (drop 0.42); a message starts (tx_crc <= 0xFFFF below) only with no beat pending
      txc_v <= tx_dfire;
      if (tx_dfire) begin txc_words <= tx_words; txc_n <= tx_n; end
      if (txc_v) tx_crc <= tx_crc_next;

      case (tx_state)
        X_IDLE: begin
          if (drain_open) begin                              // continue the open drain message only (a group's WRHDR first)
            if (dfifo_vis != 0) tx_state <= drain_hdr_pend ? X_DRAIN_HDR : X_DRAIN_WORD;
          end else if (req_pending) begin
            tx_state <= X_REQ;
          end else if (ntf_pending) begin
            tx_state <= X_NOTIFY;
          end else if (serve_busy) begin
            tx_crc <= 16'hFFFF; tx_cnt <= '0;                // (s_row / s_rem / s_room / s_sel / s_last: with the request)
            tx_state <= (serve_len == 0) ? X_SERVE_CRC : X_SERVE_RD;   // a zero-length request is answered by its CRC flit alone
          end else if (drain_armed && dfifo_vis != 0) begin
            tx_crc <= 16'hFFFF; tx_cnt <= '0; dbeat <= '0;
            tx_state <= drain_mode_psum ? X_DRAIN_WORD : X_DRAIN_HDR;
          end
        end
        X_REQ: begin
          if (tx_fire) begin req_pending <= 1'b0; tx_state <= X_IDLE; end
        end
        X_NOTIFY: begin
          if (tx_fire) begin ntf_pending <= 1'b0; tx_state <= X_IDLE; end
        end
        X_SERVE_RD: begin
          tx_state <= X_SERVE_SEND;                          // b_rdata valid next cycle
        end
        X_SERVE_SEND: begin
          if (tx_fire) begin                                 // (the beat's CRC: txc_* above)
            tx_cnt <= tx_cnt + 16'(s_n);
            if (serve_last) tx_state <= X_SERVE_CRC;
            else begin                                       // stay: the next row arrives next cycle, sent from lane 0
              s_row     <= s_row + BRAW'(1); s_rem <= s_rem - 13'(s_room);
              s_room    <= CW'(BW); s_sel <= BW'(1); s_msk <= '1;
              s_last    <= s_rem <= 13'(s_room) + 13'(BW);   // what is left after this beat fits in one row
            end
          end
        end
        X_SERVE_CRC: begin
          if (tx_fire) tx_state <= X_IDLE;
        end
        X_DRAIN_HDR: begin
          if (tx_fire) begin drain_hdr_pend <= 1'b0; tx_state <= X_DRAIN_WORD; end
        end
        X_DRAIN_WORD: begin
          if (tx_fire) begin                                 // (the beat's CRC: txc_* above; the read pointer: dfifo_rd_nxt)
            tx_cnt <= tx_cnt + 16'(d_n);
            if (d_row_done) begin
              dbeat <= '0;
              drain_sent <= drain_sent + 1;
              if (drain_sent + 1 == drain_rows) tx_state <= X_DRAIN_CRC;
              else begin
                // drop 0.43: with a group stride, the row after a group's last starts the next group at its address
                if ((drain_stride != '0) && (drain_grow == CB'(COLS - 1))) begin
                  drain_grow <= '0; drain_gaddr <= drain_gaddr + 20'(drain_stride); drain_hdr_pend <= 1'b1;
                  if (dfifo_vis > 1 || dfifo_push_q) tx_state <= X_DRAIN_HDR;
                  else tx_state <= X_IDLE;
                end else begin
                  drain_grow <= drain_grow + CB'(1);
                  if (!(dfifo_vis > 1 || dfifo_push_q)) tx_state <= X_IDLE;   // no next row readable next cycle: wait
                end
              end
            end else begin                                   // (the message stays open)
              dbeat <= dbeat + DBW'(1);
            end
          end
        end
        X_DRAIN_CRC: begin
          if (tx_fire) begin drain_armed <= 1'b0; tx_state <= X_IDLE; end
        end
        default: tx_state <= X_IDLE;
      endcase
    end
  end
endmodule
