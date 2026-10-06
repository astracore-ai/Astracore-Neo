// tile_nic.sv -- tile network interface (drop 0.7; widths for the silicon configuration in drop 0.21): the core's DMA
//   and the bank's server.
//   Flit payload (DW = 64): [63:61] type, [60:52] tag, [51:0] data. Message types:
//     RDREQ  data = {req_y[3:0], req_x[3:0], len[11:0] words, addr[19:0]}   one flit, parity-protected
//     RDRSP  data[31:0] = word, in order; the requester knows where they go  (+ CRC flit)
//     WRHDR  data[19:0] = bank address, then WRDATA data[31:0] words         (+ CRC flit)
//     PSUM   tag = accumulator row, data[39:32] = column (COLS = check), data[31:0] = value (+ CRC)
//     CRC    data[15:0] = CRC-16 over the message's data words, data[31:16] = number of data words
//            sent (drop 0.12): a receiver that counted a different number flags a lost or duplicated flit
//     RDY    one flit, owner -> contributor: "my reduce port is open" (flow control for PSUM, so a
//            partial-sum stream can never block the owner's own fetch responses behind it)
//   Drain modes (drop 0.13): INT32 rows (COLS words + check word) for partial results and debug, or
//   INT8 rows from the requantization stage packed 4 channels per word (COLS/4 words per row, no check
//   word): with COLS == ROWS one INT8 row is exactly one activation entry of the next layer, so the
//   written region is the next layer's input with no reformatting.
//   RX: per-source write pointers, PSUM row assembly and CRC accumulators (sources interleave at a
//   destination; each (source, destination) stream is in order on the XY mesh). TX: one message at
//   a time (RDREQ, serve a read, or drain), so a receiver never sees two of our messages interleaved.
//   Commands from the host or sequencer: FETCH_A / FETCH_W pull `len` words from a remote bank into
//   the activation / weight buffer at entry `base`; DRAIN_WR / DRAIN_PSUM route the core's drained
//   rows to a remote bank address / to the owner core's reduce port. crc_err_sticky is the
//   end-to-end protection the FMEDA counts on (row 12): it covers everything between the words
//   read from the source bank and the words written into the destination buffer, including NIC
//   logic the link parity cannot see.
module tile_nic #(
  parameter int XW    = 2,
  parameter int YW    = 2,
  parameter int NX    = 2,
  parameter int NSRC  = 4,                  // NX * NY nodes
  parameter int DW    = 64,
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
  // router local port
  input  logic                  rx_valid,
  input  logic [FW-1:0]         rx_flit,
  output logic                  rx_ready,
  output logic                  tx_valid,
  output logic [FW-1:0]         tx_flit,
  input  logic                  tx_ready,
  // local bank
  output logic                  b_we,
  output logic [BAW-1:0]        b_waddr,
  output logic [31:0]           b_wdata,
  output logic                  b_re,
  output logic [BAW-1:0]        b_raddr,
  input  logic [31:0]           b_rdata,
  input  logic                  b_rvalid,
  // core buffers
  output logic                  abuf_we,
  output logic [AW-1:0]         abuf_waddr,
  output logic signed [7:0]     abuf_wdata [ROWS],
  output logic                  wbuf_we,
  output logic [WAW-1:0]        wbuf_waddr,
  output logic signed [7:0]     wbuf_wdata [COLS],
  output logic signed [WCW-1:0] wcbuf_wdata,
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
  localparam int SEQ_LO = DW;                            // seq field above the payload
  localparam int SRCX_LO = DW + 8;
  localparam int SRCY_LO = DW + 8 + XW;
  localparam int DSTX_LO = DW + 8 + XW + YW;
  localparam int DSTY_LO = DW + 8 + 2 * XW + YW;

  // ------------------------------------------------------------------ RX decode
  logic [2:0]      rx_type;
  logic [8:0]      rx_tag;
  logic [51:0]     rx_data;
  logic [XW-1:0]   rx_src_x;
  logic [YW-1:0]   rx_src_y;
  logic [7:0]      rx_src;                                // source node index
  assign rx_type  = rx_flit[63:61];
  assign rx_tag   = rx_flit[60:52];
  assign rx_data  = rx_flit[51:0];
  assign rx_src_x = rx_flit[SRCX_LO +: XW];
  assign rx_src_y = rx_flit[SRCY_LO +: YW];
  assign rx_src   = rx_src_y * NX + rx_src_x;

  // per-source receive state
  logic [BAW-1:0]       wr_ptr  [NSRC];
  logic [15:0]          crc_acc [NSRC];
  logic [15:0]          rx_cnt  [NSRC];                 // data words received in the current message
  localparam int FTW = $clog2(FETCH_TIMEOUT + 1);          // timer width follows the parameter (drop 0.24: was a fixed 16 bits)
  logic [FTW-1:0]       fetch_timer;
  logic signed [PW-1:0] prow    [NSRC][COLS];            // partial-sum row under assembly

  // outstanding fetch
  logic                 fetch_busy;
  logic                 fetch_is_w;
  logic [7:0]           fetch_src;                        // node serving our fetch
  logic [15:0]          fetch_base;
  logic [11:0]          fetch_cnt;                        // words received
  localparam int EW = (WPA > WPW) ? WPA : WPW;             // words of the largest entry (4 at 16x8, 9 at 32x32; drop 0.21)
  logic [31:0]          stage [EW];                       // words of the entry under assembly

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

  logic [7:0] rx_col;                                     // PSUM column (COLS = the check value); 8 bits for COLS up to 255 (drop 0.21)
  assign rx_col = rx_data[39:32];
  logic rx_is_psum_last;
  assign rx_is_psum_last = (rx_type == T_PSUM) && (rx_col == COLS);
  logic rx_foreign;
  assign rx_foreign = rx_valid && (rx_flit[SEQ_LO +: 4] != partition);
  assign rx_ready = rx_foreign || (!((rx_type == T_RDREQ) && serve_busy) && !(rx_is_psum_last && efifo_full));

  logic rx_fire;
  assign rx_fire = rx_valid && rx_ready && !rx_foreign;

  // CRC update for the received word
  logic [15:0] rx_crc_next;
  crc16_word u_rxcrc (.crc_in(crc_acc[rx_src]), .word(rx_data[31:0]), .crc_out(rx_crc_next));

  // which word of the entry is this
  logic [11:0] fetch_wpe;                                  // words per entry for the current fetch
  assign fetch_wpe = fetch_is_w ? WPW : WPA;
  logic        fetch_last_word;
  logic [11:0] fetch_word_in_entry;
  logic [15:0] fetch_entry;
  assign fetch_word_in_entry = fetch_cnt % fetch_wpe;
  assign fetch_entry = fetch_base + fetch_cnt / fetch_wpe;
  assign fetch_last_word = (fetch_word_in_entry == fetch_wpe - 1);

  // entry assembly: the last word arrives on the wire, earlier words sit in stage[]
  logic [31:0] ent [EW];
  always_comb begin
    for (int w = 0; w < EW; w++) ent[w] = (w == fetch_word_in_entry) ? rx_data[31:0] : stage[w];
  end
  generate
    for (genvar c = 0; c < ROWS; c++) begin : g_a
      assign abuf_wdata[c] = ent[c / 4][8 * (c % 4) +: 8];
    end
    for (genvar j = 0; j < COLS; j++) begin : g_w
      assign wbuf_wdata[j] = ent[j / 4][8 * (j % 4) +: 8];
    end
  endgenerate
  assign wcbuf_wdata = ent[(COLS * 8) / 32][WCW-1:0];
  assign abuf_we    = rx_fire && (rx_type == T_RDRSP) && fetch_busy && !fetch_is_w && fetch_last_word;
  assign wbuf_we    = rx_fire && (rx_type == T_RDRSP) && fetch_busy && fetch_is_w && fetch_last_word;
  assign abuf_waddr = fetch_entry[AW-1:0];
  assign wbuf_waddr = fetch_entry[WAW-1:0];

  // bank writes from WRDATA
  assign b_we    = rx_fire && (rx_type == T_WRDATA);
  assign b_waddr = wr_ptr[rx_src];
  assign b_wdata = rx_data[31:0];

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int s = 0; s < NSRC; s++) begin
        wr_ptr[s] <= '0; crc_acc[s] <= 16'hFFFF; rx_cnt[s] <= '0;
        for (int j = 0; j < COLS; j++) prow[s][j] <= '0;
      end
      lost_err_sticky <= 1'b0; fetch_timeout_sticky <= 1'b0; fetch_timer <= '0; iso_err_sticky <= 1'b0;
      fetch_busy <= 1'b0; fetch_is_w <= 1'b0; fetch_base <= '0; fetch_cnt <= '0; fetch_src <= '0;
      for (int w = 0; w < EW; w++) stage[w] <= '0;
      efifo_valid[0] <= 1'b0; efifo_valid[1] <= 1'b0; efifo_wr <= 1'b0; efifo_rd <= 1'b0;
      efifo_idx[0] <= '0; efifo_idx[1] <= '0; efifo_chk[0] <= '0; efifo_chk[1] <= '0;
      for (int j = 0; j < COLS; j++) begin efifo_y[0][j] <= '0; efifo_y[1][j] <= '0; end
      serve_busy <= 1'b0; serve_x <= '0; serve_y <= '0; serve_addr <= '0; serve_len <= '0;
      crc_err_sticky <= 1'b0; rdy_seen <= 1'b0;
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
        fetch_busy <= 1'b1; fetch_is_w <= (cmd_op == 3'd2); fetch_base <= cmd_base; fetch_cnt <= '0;
        fetch_src  <= cmd_y * NX + cmd_x;
      end
      // ext FIFO pop
      if (ext_valid && ext_ready) begin
        efifo_valid[efifo_rd] <= 1'b0;
        efifo_rd <= !efifo_rd;
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
          T_RDRSP: begin
            if (fetch_busy) begin
              stage[fetch_word_in_entry] <= rx_data[31:0];
              fetch_cnt <= fetch_cnt + 1;
            end
            crc_acc[rx_src] <= rx_crc_next;
            rx_cnt[rx_src]  <= rx_cnt[rx_src] + 1;
          end
          T_WRHDR: begin
            wr_ptr[rx_src] <= rx_data[BAW-1:0];
          end
          T_WRDATA: begin
            wr_ptr[rx_src]  <= wr_ptr[rx_src] + 1;
            crc_acc[rx_src] <= rx_crc_next;
            rx_cnt[rx_src]  <= rx_cnt[rx_src] + 1;
          end
          T_PSUM: begin
            if (rx_col < COLS) begin
              prow[rx_src][rx_col] <= rx_data[31:0];
            end else begin
              efifo_valid[efifo_wr] <= 1'b1;
              efifo_idx[efifo_wr]   <= rx_tag[IDXW-1:0];
              efifo_chk[efifo_wr]   <= rx_data[31:0];
              for (int j = 0; j < COLS; j++) efifo_y[efifo_wr][j] <= prow[rx_src][j];
              efifo_wr <= !efifo_wr;
            end
            crc_acc[rx_src] <= rx_crc_next;
            rx_cnt[rx_src]  <= rx_cnt[rx_src] + 1;
          end
          T_RDY: rdy_seen <= 1'b1;
          T_CRC: begin
            if (crc_acc[rx_src] != rx_data[15:0]) crc_err_sticky <= 1'b1;
            if (rx_cnt[rx_src] != rx_data[31:16]) lost_err_sticky <= 1'b1;
            crc_acc[rx_src] <= 16'hFFFF;
            rx_cnt[rx_src]  <= '0;
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
  logic [11:0] serve_i;
  logic [31:0] serve_word;
  logic [31:0] serve_cur;
  assign serve_cur = b_rvalid ? b_rdata : serve_word;
  localparam int DWW = $clog2(COLS + 1);                 // row word index width: 0..COLS (drop 0.21)
  logic [DWW-1:0] dword;                                  // word within the drain row (0..COLS = check; INT8: 0..COLS/4-1)
  logic        drain_int8;
  logic [DWW-1:0] last_word;
  logic        drain_push;
  logic [31:0] int8_word;
  assign last_word  = drain_int8 ? DWW'(COLS / 4 - 1) : DWW'(COLS);
  assign drain_push = drain_int8 ? rq_valid : rd_valid;
  logic [DWW-1:0] dw8;                                    // word index kept inside the row for the INT8 pack
  assign dw8        = dword % DWW'(COLS / 4);
  assign int8_word  = {dfifo_y[dfifo_rd][dw8 * 4 + 3][7:0], dfifo_y[dfifo_rd][dw8 * 4 + 2][7:0],
                       dfifo_y[dfifo_rd][dw8 * 4 + 1][7:0], dfifo_y[dfifo_rd][dw8 * 4][7:0]};
  logic [15:0] tx_crc;
  logic [15:0] tx_crc_next;
  logic [15:0] tx_cnt;                                    // data words sent in the current message
  logic [31:0] tx_word;
  logic        req_pending;
  logic [XW-1:0] req_x;
  logic [YW-1:0] req_y;
  logic [19:0]   req_addr;
  logic [11:0]   req_len;

  crc16_word u_txcrc (.crc_in(tx_crc), .word(tx_word), .crc_out(tx_crc_next));

  // the word being transmitted this cycle (for CRC accumulation)
  logic signed [PW-1:0] drow_word;
  assign drow_word = drain_int8 ? int8_word : ((dword < COLS) ? dfifo_y[dfifo_rd][dword] : dfifo_chk[dfifo_rd]);
  always_comb begin
    tx_word = 32'd0;
    if (tx_state == X_SERVE_SEND) tx_word = serve_cur;
    else if (tx_state == X_DRAIN_WORD) tx_word = drow_word;
  end

  // flit assembly
  logic [2:0]   tx_type;
  logic [8:0]   tx_tag;
  logic [51:0]  tx_data;
  logic [XW-1:0] tx_dx;
  logic [YW-1:0] tx_dy;
  logic [FW-2:0] tx_body;
  always_comb begin
    tx_type = 3'd0; tx_tag = 9'd0; tx_data = 52'd0; tx_dx = '0; tx_dy = '0; tx_valid = 1'b0;
    case (tx_state)
      X_REQ: begin
        tx_type = T_RDREQ; tx_dx = req_x; tx_dy = req_y;
        tx_data = {12'd0, 4'(MY_Y), 4'(MY_X), req_len, req_addr};
        tx_valid = 1'b1;
      end
      X_SERVE_SEND: begin
        tx_type = T_RDRSP; tx_dx = serve_x; tx_dy = serve_y; tx_data = {20'd0, serve_cur}; tx_valid = 1'b1;
      end
      X_SERVE_CRC: begin
        tx_type = T_CRC; tx_dx = serve_x; tx_dy = serve_y; tx_data = {20'd0, tx_cnt, tx_crc}; tx_valid = 1'b1;
      end
      X_DRAIN_HDR: begin
        tx_type = T_WRHDR; tx_dx = drain_x; tx_dy = drain_y; tx_data = {32'd0, drain_addr}; tx_valid = 1'b1;
      end
      X_DRAIN_WORD: begin
        tx_dx = drain_x; tx_dy = drain_y; tx_valid = 1'b1;
        if (drain_mode_psum) begin
          tx_type = T_PSUM; tx_tag = 9'(dfifo_idx[dfifo_rd]); tx_data = {12'd0, 8'(dword), drow_word};
        end else begin
          tx_type = T_WRDATA; tx_data = {20'd0, drow_word};
        end
      end
      X_DRAIN_CRC: begin
        tx_type = T_CRC; tx_dx = drain_x; tx_dy = drain_y; tx_data = {20'd0, tx_cnt, tx_crc}; tx_valid = 1'b1;
      end
      X_NOTIFY: begin
        tx_type = T_RDY; tx_dx = ntf_x; tx_dy = ntf_y; tx_valid = 1'b1;
      end
      default: ;
    endcase
    tx_body = {tx_dy, tx_dx, YW'(MY_Y), XW'(MY_X), 4'd0, partition, tx_type, tx_tag, tx_data};
  end
  assign tx_flit = {^tx_body, tx_body};                   // even parity over the whole flit

  logic tx_fire;
  assign tx_fire = tx_valid && tx_ready;
  assign serve_done = (tx_state == X_SERVE_CRC) && tx_fire;
  assign drain_busy = drain_armed;
  assign ntf_busy   = ntf_pending;

  // bank read for serving: the first read in X_SERVE_RD, then one read per sent word (pipelined,
  // one word per cycle while the link accepts)
  assign b_re    = (tx_state == X_SERVE_RD) ||
                   ((tx_state == X_SERVE_SEND) && tx_fire && (serve_i + 1 < serve_len));
  assign b_raddr = serve_addr + ((tx_state == X_SERVE_RD) ? serve_i : serve_i + 1);

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      tx_state <= X_IDLE; serve_i <= '0; serve_word <= '0; dword <= '0; tx_crc <= 16'hFFFF; tx_cnt <= '0;
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
      if (drain_push && !(tx_state == X_DRAIN_WORD && tx_fire && dword == last_word))      dfifo_cnt <= dfifo_cnt + 1;
      else if (!drain_push && (tx_state == X_DRAIN_WORD && tx_fire && dword == last_word)) dfifo_cnt <= dfifo_cnt - 1;

      case (tx_state)
        X_IDLE: begin
          if (drain_open) begin
            if (dfifo_cnt != 0) tx_state <= X_DRAIN_WORD;   // continue the open drain message only
          end else if (req_pending) begin
            tx_state <= X_REQ;
          end else if (ntf_pending) begin
            tx_state <= X_NOTIFY;
          end else if (serve_busy) begin
            serve_i <= '0; tx_crc <= 16'hFFFF; tx_cnt <= '0; tx_state <= X_SERVE_RD;
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
          if (b_rvalid) serve_word <= b_rdata;               // the word read last cycle
          if (tx_fire) begin
            tx_crc  <= tx_crc_next;
            tx_cnt  <= tx_cnt + 1;
            serve_i <= serve_i + 1;
            if (serve_i == serve_len - 1) tx_state <= X_SERVE_CRC;   // else stay: next word arrives next cycle
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
            tx_cnt <= tx_cnt + 1;
            if (dword == last_word) begin
              dword <= '0;
              dfifo_rd <= (dfifo_rd == DFD - 1) ? '0 : dfifo_rd + 1;
              drain_sent <= drain_sent + 1;
              if (drain_sent + 1 == drain_rows) tx_state <= X_DRAIN_CRC;
              else if (dfifo_cnt == 1) tx_state <= X_IDLE;   // wait for more rows (message stays open)
            end else begin
              dword <= dword + 1;
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
