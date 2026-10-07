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
//   (source, destination) stream is in order on the XY mesh), each source's state in its own register block. TX: one
//   message at a time (RDREQ, serve a read, or drain), so a receiver never sees two of our messages interleaved.
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
  parameter int MY_X  = 0,
  parameter int MY_Y  = 0,
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
  parameter int WPA   = ROWS * 8 / 32,      // words per activation entry
  parameter int WPW   = (COLS * 8 + WCW + 31) / 32,   // words per weight entry
  parameter int DFD   = 64,                 // drain FIFO rows
  parameter int FETCH_TIMEOUT = 1048575     // cycles a fetch may wait for its response. Silicon rule (drop 0.24): every tile has at
                                            // most one fetch outstanding and a fetch is at most 4,095 words, so the longest wait at one
                                            // bank is (NX*NY - 1) queued maximal fetches plus its own: FETCH_TIMEOUT >= NX*NY*(4,095 + H),
                                            // H the per-request overhead; 2^20 - 1 covers 64 tiles with H up to 12,000 cycles.
                                            // The simulation builds pass 8,192 / 65,535 so the timeout tests stay short.
)(
  input  logic                  clk,
  input  logic                  rst_n,
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
  output logic                  wbuf_we,
  output logic [WAW-1:0]        wbuf_waddr,
  output logic signed [7:0]     wbuf_wdata [COLS],
  output logic signed [WCW-1:0] wcbuf_wdata,
  output logic                  wbuf_we2,
  output logic signed [7:0]     wbuf_wdata2 [COLS],
  output logic signed [WCW-1:0] wcbuf_wdata2,
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
  input  logic                  cmd_int8,     // with DRAIN_WR: drain the requantized INT8 rows instead of INT32
  input  logic [XW-1:0]         cmd_x,
  input  logic [YW-1:0]         cmd_y,
  input  logic [19:0]           cmd_addr,
  input  logic [11:0]           cmd_len,      // words to fetch, or rows to drain
  input  logic [15:0]           cmd_base,     // local entry index for fetches
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
  output logic                  fetch_timeout_sticky // a fetch response did not complete within FETCH_TIMEOUT cycles
);
  localparam logic [2:0] T_RDREQ = 3'd1, T_RDRSP = 3'd2, T_WRHDR = 3'd3, T_WRDATA = 3'd4,
                         T_PSUM = 3'd5, T_CRC = 3'd6, T_RDY = 3'd7;
  localparam int VW = 32 * BW;                            // the words field of a beat
  localparam int CW = $clog2(BW + 1);                      // beat word count 0..BW
  localparam int MWP = BW + 1;                             // (1 << n) - 1 lane masks for n = 0..BW need one bit more than BW
  localparam int SEQ_LO = DW;                              // seq field above the payload
  localparam int SRCX_LO = DW + 8;
  localparam int SRCY_LO = DW + 8 + XW;

  // ------------------------------------------------------------------ RX decode
  logic [2:0]      rx_type;
  logic [8:0]      rx_tag;
  logic [19:0]     rx_hdr;
  logic [VW-1:0]   rx_words;
  logic            rx_is_data;
  logic [CW-1:0]   rx_nw;                                 // words in the beat
  logic [31:0]     rx_word;                               // word 0: the word of the single-word types
  logic [51:0]     rx_data;                               // the one-word view {hdr, word}: the fields of RDREQ, WRHDR, CRC
  logic [XW-1:0]   rx_src_x;
  logic [YW-1:0]   rx_src_y;
  logic [7:0]      rx_src;                                // source node index
  assign rx_type  = rx_flit[DW-1 -: 3];
  assign rx_tag   = rx_flit[DW-4 -: 9];
  assign rx_hdr   = rx_flit[DW-13 -: 20];
  assign rx_words = rx_flit[VW-1:0];
  assign rx_is_data = (rx_type == T_RDRSP) || (rx_type == T_WRDATA) || (rx_type == T_PSUM);
  assign rx_nw    = rx_is_data ? CW'(rx_hdr[13:8]) + CW'(1) : CW'(1);
  assign rx_word  = rx_words[31:0];
  assign rx_data  = {rx_hdr, rx_word};
  assign rx_src_x = rx_flit[SRCX_LO +: XW];
  assign rx_src_y = rx_flit[SRCY_LO +: YW];
  assign rx_src   = rx_src_y * NX + rx_src_x;

  // per-source receive state (each source's registers in its own block, g_src below)
  logic [BAW-1:0]       wr_ptr  [NSRC];
  logic [15:0]          crc_acc [NSRC];
  logic [15:0]          rx_cnt  [NSRC];                 // data words received in the current message
  logic [COLS*PW-1:0]   prow    [NSRC];                 // partial-sum row under assembly, column j at [PW*j +: PW]
  localparam int FTW = $clog2(FETCH_TIMEOUT + 1);          // timer width follows the parameter (drop 0.24: was a fixed 16 bits)
  logic [FTW-1:0]       fetch_timer;

  // outstanding fetch
  logic                 fetch_busy;
  logic                 fetch_is_w;
  logic [7:0]           fetch_src;                        // node serving our fetch
  logic [15:0]          fetch_base;
  logic [15:0]          fetch_ent;                        // entries written by this fetch
  localparam int EW = (WPA > WPW) ? WPA : WPW;             // words of the largest entry (4 at 16x8, 9 at 32x32; drop 0.21)
  localparam int SW = $clog2(EW + 1);                      // staged word count 0..EW
  logic [31:0]          stage [EW];                       // words of the entry under assembly, from earlier beats
  logic [SW-1:0]        stage_cnt;
  logic [CW-1:0]        rx_woff;                          // words of the current RDRSP beat already taken into entries

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
  logic [BAW-1:0]       serve_addr;
  logic [11:0]          serve_len;

  // PSUM beat (drop 0.31): columns ps_col0 .. ps_col0 + words - 1 of row rx_tag; column COLS is the check value and
  // completes the row (8-bit columns for COLS up to 255, drop 0.21)
  logic [7:0]  ps_col0;
  logic [8:0]  ps_end;                                    // one past the beat's last column
  logic        ps_has_chk;                                // the beat carries the check word: the row goes to the reduce port
  logic [LW-1:0] ps_chk_idx;                              // its position in the beat
  assign ps_col0    = rx_hdr[7:0];
  assign ps_end     = {1'b0, ps_col0} + {4'd0, rx_nw};
  assign ps_has_chk = (rx_type == T_PSUM) && ({1'b0, ps_col0} <= 9'(COLS)) && (ps_end > 9'(COLS));
  assign ps_chk_idx = LW'(9'(COLS) - {1'b0, ps_col0});
  logic rx_foreign;
  assign rx_foreign = rx_valid && (rx_flit[SEQ_LO +: 4] != partition);

  // ------------------------------------------------------------------ WRDATA writeback: beat -> bank rows
  logic                 wb_active;                        // a WRDATA beat is at the input
  logic                 wb_phase;                         // 1: writing the second row of a beat that crosses a row boundary
  logic                 wb_cross;
  logic [BAW-1:0]       wb_ptr;                           // word address of the beat's word 0
  logic [LW-1:0]        wb_off;                           // its lane
  logic [BRAW-1:0]      wb_row;
  logic [CW-1:0]        wb_room;                          // lanes from wb_off to the end of the row, 1..BW
  logic [CW-1:0]        wb_n1, wb_n2;                     // words into the first row, into the next row
  logic [MWP-1:0]       wb_one1, wb_one2;
  logic [BW-1:0]        wb_mask1, wb_mask2;
  logic [VW-1:0]        wb_rot;                           // the beat rotated so word i sits in lane (wb_off + i) mod BW
  assign wb_active = rx_valid && !rx_foreign && (rx_type == T_WRDATA);
  assign wb_ptr    = wr_ptr[rx_src];
  assign wb_off    = wb_ptr[LW-1:0];
  assign wb_row    = wb_ptr[BAW-1:LW];
  assign wb_room   = CW'(BW) - CW'(wb_off);
  assign wb_cross  = (rx_nw > wb_room);
  assign wb_n1     = wb_cross ? wb_room : rx_nw;
  assign wb_n2     = rx_nw - wb_n1;
  assign wb_one1   = MWP'(1) << wb_n1;
  assign wb_one2   = MWP'(1) << wb_n2;
  assign wb_mask1  = BW'(wb_one1 - MWP'(1)) << wb_off;
  assign wb_mask2  = BW'(wb_one2 - MWP'(1));
  assign wb_rot    = (rx_words << (32 * wb_off)) | (rx_words >> (VW - 32 * wb_off));
  assign b_we      = wb_active;
  assign b_wrow    = wb_phase ? wb_row + BRAW'(1) : wb_row;
  assign b_wmask   = wb_phase ? wb_mask2 : wb_mask1;
  assign b_wdata   = wb_rot;

  // ------------------------------------------------------------------ fetch receive at entry rate (drop 0.30; two entries per cycle, drop 0.32)
  // Each cycle up to two entries (WPE = WPA or WPW words each) are assembled from the words staged from earlier beats
  // followed by the current beat's words from rx_woff on, and written to the buffer's word-wide port: a pair when the
  // entry address is even and the words of both are there, otherwise one entry (the odd trailing entry, or the first
  // entry of a fetch at an odd base). The words taken from the beat (fx_k) advance rx_woff; when what is left of the
  // beat still holds an entry the beat stays (fx_more), otherwise the leftover words are staged and the beat is consumed.
  // A response that arrives with no fetch outstanding (abandoned by the watchdog) is consumed without storing anything.
  logic          fx_act;                                  // an RDRSP beat is at the input
  logic [SW-1:0] fetch_wpe;                               // words per entry for the current fetch
  logic [CW:0]   fx_avail;                                // staged + unconsumed beat words
  logic          fx_entry;                                // at least one entry is written this cycle
  logic          fx_two;                                  // two entries are written this cycle
  logic [CW:0]   fx_take;                                 // words the entries need in all: fetch_wpe or 2 * fetch_wpe
  logic [CW-1:0] fx_k;                                    // beat words the entries take
  logic [CW:0]   fx_rem;                                  // beat words left after them
  logic          fx_more;                                 // ... enough for another entry: stay on the beat
  logic          fx_stash;                                // ... fewer: they go to stage, the beat is consumed
  logic [SW-1:0] fx_dst;                                  // first stage slot written by the stash
  logic [CW-1:0] fx_src;                                  // first beat word it takes
  logic          fx_done;                                 // the beat is consumed this cycle
  logic [15:0]   fetch_entry;
  assign fx_act    = rx_valid && !rx_foreign && (rx_type == T_RDRSP);
  assign fetch_wpe = fetch_is_w ? SW'(WPW) : SW'(WPA);
  assign fetch_entry = fetch_base + fetch_ent;
  assign fx_avail  = {1'b0, stage_cnt} + {1'b0, rx_nw} - {1'b0, rx_woff};
  assign fx_entry  = fx_act && fetch_busy && (fx_avail >= {1'b0, fetch_wpe});
  assign fx_two    = fx_entry && !fetch_entry[0] && (fx_avail >= {1'b0, fetch_wpe} + {1'b0, fetch_wpe});
  assign fx_take   = fx_two ? ({1'b0, fetch_wpe} + {1'b0, fetch_wpe}) : {1'b0, fetch_wpe};
  assign fx_k      = CW'(fx_take - {1'b0, stage_cnt});
  assign fx_rem    = {1'b0, rx_nw} - {1'b0, rx_woff} - (fx_entry ? {1'b0, fx_k} : '0);
  assign fx_more   = fx_entry && (fx_rem >= {1'b0, fetch_wpe});
  assign fx_stash  = fx_act && fetch_busy && !fx_more;
  assign fx_dst    = fx_entry ? '0 : stage_cnt;
  assign fx_src    = fx_entry ? rx_woff + fx_k : rx_woff;
  assign fx_done   = !fetch_busy || !fx_more;

  // entry assembly: the first entry from the staged words then the beat's words from rx_woff; the second entry from the
  // beat's words that follow (the staged words are fewer than one entry, so it never reaches into stage)
  logic [31:0]   ent  [EW];
  logic [31:0]   ent2 [EW];
  logic [LW-1:0] ent_bi  [EW];                            // beat word index for entry word w (meaningful for w >= stage_cnt)
  logic [LW-1:0] ent2_bi [EW];
  always_comb begin
    for (int w = 0; w < EW; w++) begin
      ent_bi[w]  = LW'(rx_woff + w - stage_cnt);
      ent[w]     = (w < stage_cnt) ? stage[w] : rx_words[32 * ent_bi[w] +: 32];
      ent2_bi[w] = LW'(rx_woff + fetch_wpe + w - stage_cnt);
      ent2[w]    = rx_words[32 * ent2_bi[w] +: 32];
    end
  end

  // ------------------------------------------------------------------ RX handshake
  // RDRSP beats are consumed at entry rate (fx_done); a PSUM beat whole, held only while its check word finds the reduce
  // FIFO full; the other types, and a WRDATA beat once its last row write is on the port, whole (rx_fire).
  logic rx_fire;
  always_comb begin
    if (rx_foreign) rx_ready = 1'b1;                       // dropped
    else begin
      case (rx_type)
        T_RDREQ:  rx_ready = !serve_busy;
        T_RDRSP:  rx_ready = fx_done;
        T_PSUM:   rx_ready = !(ps_has_chk && efifo_full);
        T_WRDATA: rx_ready = !wb_cross || wb_phase;
        default:  rx_ready = 1'b1;
      endcase
    end
  end
  assign rx_fire  = rx_valid && rx_ready && !rx_foreign;

  // CRC update for what is received this cycle: the whole data beat (its first rx_nw words), otherwise the one word
  logic [15:0]   rx_crc_next;
  crc16_beat #(.BW(BW)) u_rxcrc (.crc_in(crc_acc[rx_src]), .words(rx_words), .n(rx_nw), .crc_out(rx_crc_next));
  generate
    for (genvar c = 0; c < ROWS; c++) begin : g_a
      assign abuf_wdata[c]  = ent[c / 4][8 * (c % 4) +: 8];
      assign abuf_wdata2[c] = ent2[c / 4][8 * (c % 4) +: 8];
    end
    for (genvar j = 0; j < COLS; j++) begin : g_w
      assign wbuf_wdata[j]  = ent[j / 4][8 * (j % 4) +: 8];
      assign wbuf_wdata2[j] = ent2[j / 4][8 * (j % 4) +: 8];
    end
  endgenerate
  assign wcbuf_wdata  = ent[(COLS * 8) / 32][WCW-1:0];
  assign wcbuf_wdata2 = ent2[(COLS * 8) / 32][WCW-1:0];
  assign abuf_we    = fx_entry && !fetch_is_w;
  assign abuf_we2   = fx_two && !fetch_is_w;
  assign wbuf_we    = fx_entry && fetch_is_w;
  assign wbuf_we2   = fx_two && fetch_is_w;
  assign abuf_waddr = fetch_entry[AW-1:0];
  assign wbuf_waddr = fetch_entry[WAW-1:0];

  // per-source state: write pointer, CRC accumulator, word count, partial-sum row
  generate
    for (genvar s = 0; s < NSRC; s++) begin : g_src
      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
          wr_ptr[s] <= '0; crc_acc[s] <= 16'hFFFF; rx_cnt[s] <= '0; prow[s] <= '0;
        end else if (rx_src == s) begin
          if (rx_fire) begin
            case (rx_type)
              T_WRHDR:  wr_ptr[s] <= rx_word[BAW-1:0];
              T_WRDATA: begin                                // the whole beat, after its last row write
                wr_ptr[s]  <= wr_ptr[s] + BAW'(rx_nw);
                crc_acc[s] <= rx_crc_next;
                rx_cnt[s]  <= rx_cnt[s] + 16'(rx_nw);
              end
              T_RDRSP: begin                                 // the whole beat, after its last entry
                crc_acc[s] <= rx_crc_next;
                rx_cnt[s]  <= rx_cnt[s] + 16'(rx_nw);
              end
              T_PSUM: begin                                  // the whole beat: its columns into the row under assembly
                crc_acc[s] <= rx_crc_next;
                rx_cnt[s]  <= rx_cnt[s] + 16'(rx_nw);
                for (int j = 0; j < COLS; j++)
                  if ((j >= ps_col0) && (j < ps_end)) prow[s][PW * j +: PW] <= rx_words[32 * LW'(j - ps_col0) +: 32];
              end
              T_CRC: begin
                crc_acc[s] <= 16'hFFFF;
                rx_cnt[s]  <= '0;
              end
              default: ;
            endcase
          end
        end
      end
    end
  endgenerate

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      lost_err_sticky <= 1'b0; fetch_timeout_sticky <= 1'b0; fetch_timer <= '0; iso_err_sticky <= 1'b0;
      fetch_busy <= 1'b0; fetch_is_w <= 1'b0; fetch_base <= '0; fetch_ent <= '0; fetch_src <= '0;
      for (int w = 0; w < EW; w++) stage[w] <= '0;
      stage_cnt <= '0; rx_woff <= '0;
      efifo_valid[0] <= 1'b0; efifo_valid[1] <= 1'b0; efifo_wr <= 1'b0; efifo_rd <= 1'b0;
      efifo_idx[0] <= '0; efifo_idx[1] <= '0; efifo_chk[0] <= '0; efifo_chk[1] <= '0;
      for (int j = 0; j < COLS; j++) begin efifo_y[0][j] <= '0; efifo_y[1][j] <= '0; end
      serve_busy <= 1'b0; serve_x <= '0; serve_y <= '0; serve_addr <= '0; serve_len <= '0;
      crc_err_sticky <= 1'b0; rdy_seen <= 1'b0;
      wb_phase <= 1'b0;
    end else begin
      if (err_clear) begin crc_err_sticky <= 1'b0; lost_err_sticky <= 1'b0; fetch_timeout_sticky <= 1'b0; iso_err_sticky <= 1'b0; end
      if (rx_foreign) iso_err_sticky <= 1'b1;
      if (rdy_clear) rdy_seen <= 1'b0;
      // fetch watchdog: a response that does not complete in time is flagged and abandoned
      if (fetch_busy) begin
        fetch_timer <= fetch_timer + 1;
        if (fetch_timer == FETCH_TIMEOUT - 1) begin fetch_timeout_sticky <= 1'b1; fetch_busy <= 1'b0; end
      end else begin
        fetch_timer <= '0;
      end
      // fetch command
      if (cmd_valid && (cmd_op == 3'd1 || cmd_op == 3'd2) && !fetch_busy) begin
        fetch_busy <= 1'b1; fetch_is_w <= (cmd_op == 3'd2); fetch_base <= cmd_base; fetch_ent <= '0; stage_cnt <= '0;
        fetch_src  <= cmd_y * NX + cmd_x;
      end
      // ext FIFO pop
      if (ext_valid && ext_ready) begin
        efifo_valid[efifo_rd] <= 1'b0;
        efifo_rd <= !efifo_rd;
      end
      // second-row phase of a crossing WRDATA beat
      wb_phase <= wb_active && wb_cross && !wb_phase;
      // fetch receive: entries out, leftovers staged (the beat position resets if the beat vanishes under us -- a dropped
      // flit, M9a -- so the next beat starts at word 0 again)
      if (!rx_valid) rx_woff <= '0;
      else if (fx_act) rx_woff <= fx_more ? rx_woff + fx_k : '0;
      if (fx_entry) fetch_ent <= fetch_ent + (fx_two ? 16'd2 : 16'd1);
      if (fx_more) stage_cnt <= '0;
      else if (fx_stash) begin
        stage_cnt <= fx_dst + SW'(fx_rem);
        for (int w = 0; w < EW; w++)
          if (w >= fx_dst && w < fx_dst + fx_rem) stage[w] <= rx_words[32 * LW'(fx_src + w - fx_dst) +: 32];
      end
      if (rx_fire && ps_has_chk) begin                     // PSUM check word: the row is complete -- the columns this
        efifo_valid[efifo_wr] <= 1'b1;                     // beat brings are taken from the beat, the rest from prow
        efifo_idx[efifo_wr]   <= rx_tag[IDXW-1:0];
        efifo_chk[efifo_wr]   <= rx_words[32 * ps_chk_idx +: 32];
        for (int j = 0; j < COLS; j++)
          efifo_y[efifo_wr][j] <= ((j >= ps_col0) && (j < ps_end)) ? rx_words[32 * LW'(j - ps_col0) +: 32] : prow[rx_src][PW * j +: PW];
        efifo_wr <= !efifo_wr;
      end
      if (rx_fire) begin
        case (rx_type)
          T_RDREQ: begin
            serve_busy <= 1'b1;
            serve_y    <= rx_data[39:36];
            serve_x    <= rx_data[35:32];
            serve_len  <= rx_data[31:20];
            serve_addr <= rx_data[19:0];
          end
          T_RDY: rdy_seen <= 1'b1;
          T_CRC: begin
            if (crc_acc[rx_src] != rx_data[15:0]) crc_err_sticky <= 1'b1;
            if (rx_cnt[rx_src] != rx_data[31:16]) lost_err_sticky <= 1'b1;
            if (fetch_busy && rx_src == fetch_src) fetch_busy <= 1'b0;   // our fetch response is complete
          end
          default: ;
        endcase
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
  logic [IDXW-1:0]      dfifo_idx [DFD];
  logic signed [PW-1:0] dfifo_y   [DFD][COLS];
  logic signed [PW-1:0] dfifo_chk [DFD];
  localparam int DFW = $clog2(DFD);                       // pointer width from the depth (drop 0.21)
  logic [DFW-1:0]       dfifo_wr;
  logic [DFW-1:0]       dfifo_rd;
  logic [DFW:0]         dfifo_cnt;
  logic                 drain_mode_psum;
  logic [XW-1:0]        drain_x;
  logic [YW-1:0]        drain_y;
  logic [19:0]          drain_addr;
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
  logic        ntf_pending;
  logic [XW-1:0] ntf_x;
  logic [YW-1:0] ntf_y;
  // serve: rows from the bank, beats to the link
  logic [11:0]    serve_i;                                // words sent
  logic [VW-1:0]  serve_row;                              // the row read last, held while the link does not accept
  logic [VW-1:0]  serve_cur;
  logic [BAW-1:0] serve_pos;                              // word address of the beat's word 0
  logic [LW-1:0]  s_off;                                  // its lane
  logic [BRAW-1:0] s_row;
  logic [12:0]    s_rem;                                  // words still to send
  logic [CW-1:0]  s_room;                                 // lanes from s_off to the end of the row
  logic [CW-1:0]  s_n;                                    // words in this beat
  logic           serve_last;                             // this beat completes the serve
  logic [VW-1:0]  serve_beat;
  assign serve_cur  = b_rvalid ? b_rdata : serve_row;
  assign serve_pos  = serve_addr + BAW'(serve_i);
  assign s_off      = serve_pos[LW-1:0];
  assign s_row      = serve_pos[BAW-1:LW];
  assign s_rem      = {1'b0, serve_len} - {1'b0, serve_i};
  assign s_room     = CW'(BW) - CW'(s_off);
  assign s_n        = (s_rem < {8'd0, s_room}) ? CW'(s_rem) : s_room;
  assign serve_last = (s_rem == {8'd0, s_n});
  assign serve_beat = serve_cur >> (32 * s_off);
  localparam int DWW = $clog2(COLS + 1);                 // row word index width: 0..COLS (drop 0.21)
  logic [DWW-1:0] dword;                                  // first word of the beat within the drain row (0..COLS = check; INT8: 0..COLS/4-1)
  logic        drain_int8;
  logic [DWW-1:0] last_word;
  logic        drain_push;
  assign last_word  = drain_int8 ? DWW'(COLS / 4 - 1) : DWW'(COLS);
  assign drain_push = drain_int8 ? rq_valid : rd_valid;
  // the drain beat (drop 0.31): words dword .. dword + d_n - 1 of the row at the FIFO's read pointer
  logic [DWW:0]   d_left;                                 // words of the row from dword on
  logic [CW-1:0]  d_n;                                    // words in this beat
  logic           d_row_done;                             // this beat completes the row
  logic [DWW-1:0] d_wi [BW];                              // row word index of beat word i
  logic [31:0]    d_word [BW];
  logic [VW-1:0]  drain_beat;
  assign d_left     = {1'b0, last_word} - {1'b0, dword} + 1'b1;
  assign d_n        = (d_left < BW) ? CW'(d_left) : CW'(BW);
  assign d_row_done = (d_left <= BW);
  always_comb begin
    drain_beat = '0;
    for (int i = 0; i < BW; i++) begin
      d_wi[i]   = dword + DWW'(i);
      d_word[i] = '0;
      if (i < d_n) begin                                  // inside the row (the indices below stay in range)
        if (drain_int8)
          d_word[i] = {dfifo_y[dfifo_rd][d_wi[i] * 4 + 3][7:0], dfifo_y[dfifo_rd][d_wi[i] * 4 + 2][7:0],
                       dfifo_y[dfifo_rd][d_wi[i] * 4 + 1][7:0], dfifo_y[dfifo_rd][d_wi[i] * 4][7:0]};
        else
          d_word[i] = (d_wi[i] < COLS) ? dfifo_y[dfifo_rd][d_wi[i]] : dfifo_chk[dfifo_rd];
        drain_beat[32 * i +: 32] = d_word[i];
      end
    end
  end
  logic [15:0] tx_crc;
  logic [15:0] tx_crc_next;
  logic [15:0] tx_cnt;                                    // data words sent in the current message
  logic [VW-1:0] tx_words;                                // the beat being transmitted (for CRC accumulation)
  logic [CW-1:0] tx_n;                                    // its word count
  logic        req_pending;
  logic [XW-1:0] req_x;
  logic [YW-1:0] req_y;
  logic [19:0]   req_addr;
  logic [11:0]   req_len;

  crc16_beat #(.BW(BW)) u_txcrc (.crc_in(tx_crc), .words(tx_words), .n(tx_n), .crc_out(tx_crc_next));

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
        tx_hdr = {12'd0, 4'(MY_Y), 4'(MY_X)}; tx_words = VW'({req_len, req_addr});
        tx_valid = 1'b1;
      end
      X_SERVE_SEND: begin
        tx_type = T_RDRSP; tx_dx = serve_x; tx_dy = serve_y;
        tx_hdr = {6'd0, 6'(s_n - CW'(1)), 8'd0}; tx_words = serve_beat; tx_n = s_n; tx_valid = 1'b1;
      end
      X_SERVE_CRC: begin
        tx_type = T_CRC; tx_dx = serve_x; tx_dy = serve_y; tx_words = VW'({tx_cnt, tx_crc}); tx_valid = 1'b1;
      end
      X_DRAIN_HDR: begin
        tx_type = T_WRHDR; tx_dx = drain_x; tx_dy = drain_y; tx_words = VW'({12'd0, drain_addr}); tx_valid = 1'b1;
      end
      X_DRAIN_WORD: begin
        tx_dx = drain_x; tx_dy = drain_y; tx_words = drain_beat; tx_n = d_n; tx_valid = 1'b1;
        if (drain_mode_psum) begin
          tx_type = T_PSUM; tx_tag = 9'(dfifo_idx[dfifo_rd]); tx_hdr = {6'd0, 6'(d_n - CW'(1)), 8'(dword)};
        end else begin
          tx_type = T_WRDATA; tx_hdr = {6'd0, 6'(d_n - CW'(1)), 8'd0};
        end
      end
      X_DRAIN_CRC: begin
        tx_type = T_CRC; tx_dx = drain_x; tx_dy = drain_y; tx_words = VW'({tx_cnt, tx_crc}); tx_valid = 1'b1;
      end
      X_NOTIFY: begin
        tx_type = T_RDY; tx_dx = ntf_x; tx_dy = ntf_y; tx_valid = 1'b1;
      end
      default: ;
    endcase
    tx_body = {tx_dy, tx_dx, YW'(MY_Y), XW'(MY_X), 4'd0, partition, tx_type, tx_tag, tx_hdr, tx_words};
  end
  assign tx_flit = {^tx_body, tx_body};                   // even parity over the whole flit

  logic tx_fire;
  assign tx_fire = tx_valid && tx_ready;
  assign serve_done = (tx_state == X_SERVE_CRC) && tx_fire;
  assign drain_busy = drain_armed;
  assign ntf_busy   = ntf_pending;

  // bank row read for serving: the first row in X_SERVE_RD, then the next row as each beat is accepted (pipelined:
  // one beat per cycle while the link accepts; the row read last is held in serve_row while it does not)
  assign b_re   = (tx_state == X_SERVE_RD) || ((tx_state == X_SERVE_SEND) && tx_fire && !serve_last);
  assign b_rrow = (tx_state == X_SERVE_RD) ? s_row : s_row + BRAW'(1);

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      tx_state <= X_IDLE; serve_i <= '0; serve_row <= '0; dword <= '0; tx_crc <= 16'hFFFF; tx_cnt <= '0;
      req_pending <= 1'b0; req_x <= '0; req_y <= '0; req_addr <= '0; req_len <= '0;
      ntf_pending <= 1'b0; ntf_x <= '0; ntf_y <= '0;
      dfifo_wr <= '0; dfifo_rd <= '0; dfifo_cnt <= '0;
      drain_mode_psum <= 1'b0; drain_x <= '0; drain_y <= '0; drain_addr <= '0; drain_int8 <= 1'b0;
      drain_rows <= '0; drain_sent <= '0; drain_armed <= 1'b0;
    end else begin
      // commands
      if (cmd_valid && (cmd_op == 3'd1 || cmd_op == 3'd2) && !fetch_busy) begin
        req_pending <= 1'b1; req_x <= cmd_x; req_y <= cmd_y; req_addr <= cmd_addr; req_len <= cmd_len;
      end
      if (cmd_valid && (cmd_op == 3'd3 || cmd_op == 3'd4)) begin
        drain_mode_psum <= (cmd_op == 3'd4); drain_x <= cmd_x; drain_y <= cmd_y; drain_addr <= cmd_addr;
        drain_rows <= cmd_len; drain_sent <= '0; drain_armed <= 1'b1; drain_int8 <= (cmd_op == 3'd3) && cmd_int8;
      end
      if (cmd_valid && cmd_op == 3'd5) begin
        ntf_pending <= 1'b1; ntf_x <= cmd_x; ntf_y <= cmd_y;
      end
      // drain FIFO push (no backpressure: sized for a full accumulator drain)
      if (drain_push) begin
        dfifo_idx[dfifo_wr] <= drain_sent + dfifo_cnt;    // rows arrive in order 0..M-1
        for (int j = 0; j < COLS; j++) dfifo_y[dfifo_wr][j] <= drain_int8 ? PW'(rq_q[j]) : rd_data[j];
        dfifo_chk[dfifo_wr] <= rd_chk;
        dfifo_wr <= (dfifo_wr == DFD - 1) ? '0 : dfifo_wr + 1;
      end
      if (drain_push && !(tx_state == X_DRAIN_WORD && tx_fire && d_row_done))      dfifo_cnt <= dfifo_cnt + 1;
      else if (!drain_push && (tx_state == X_DRAIN_WORD && tx_fire && d_row_done)) dfifo_cnt <= dfifo_cnt - 1;

      if (b_rvalid) serve_row <= b_rdata;                 // the row read last cycle

      case (tx_state)
        X_IDLE: begin
          if (drain_open) begin
            if (dfifo_cnt != 0) tx_state <= X_DRAIN_WORD;   // continue the open drain message only
          end else if (req_pending) begin
            tx_state <= X_REQ;
          end else if (ntf_pending) begin
            tx_state <= X_NOTIFY;
          end else if (serve_busy) begin
            serve_i <= '0; tx_crc <= 16'hFFFF; tx_cnt <= '0;
            tx_state <= (serve_len == 0) ? X_SERVE_CRC : X_SERVE_RD;   // a zero-length request is answered by its CRC flit alone
          end else if (drain_armed && dfifo_cnt != 0) begin
            tx_crc <= 16'hFFFF; tx_cnt <= '0; dword <= '0;
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
          if (tx_fire) begin
            tx_crc  <= tx_crc_next;
            tx_cnt  <= tx_cnt + 16'(s_n);
            serve_i <= serve_i + 12'(s_n);
            if (serve_last) tx_state <= X_SERVE_CRC;        // else stay: the next row arrives next cycle
          end
        end
        X_SERVE_CRC: begin
          if (tx_fire) tx_state <= X_IDLE;
        end
        X_DRAIN_HDR: begin
          if (tx_fire) tx_state <= X_DRAIN_WORD;
        end
        X_DRAIN_WORD: begin
          if (tx_fire) begin
            tx_crc <= tx_crc_next;
            tx_cnt <= tx_cnt + 16'(d_n);
            if (d_row_done) begin
              dword <= '0;
              dfifo_rd <= (dfifo_rd == DFD - 1) ? '0 : dfifo_rd + 1;
              drain_sent <= drain_sent + 1;
              if (drain_sent + 1 == drain_rows) tx_state <= X_DRAIN_CRC;
              else if (dfifo_cnt == 1) tx_state <= X_IDLE;   // wait for more rows (message stays open)
            end else begin
              dword <= dword + DWW'(d_n);
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
