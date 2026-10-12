// noc_axi_bridge.sv -- the NoC-to-AXI4 bridge engine of a bridge node (drop 0.44, the chip top): speaks the tile
//   interface's message protocol (tile_nic.sv) on its mesh side and AXI4 on the other, in two roles, each one present by
//   parameter. The engine runs entirely in the AXI clock -- the port called clk here is neo_top's aclk -- and meets the mesh
//   through two cdc_fifo of beat flits (16-word beats, the parity bit left out: the link packer / unpacker recompute it), so
//   the bridge's data path is the only crossing it has; the link_packer / link_unpacker pair and the bridge node's router
//   are in the mesh clock outside (neo_top.sv).
//   Beats in (rx_*): {dst_y, dst_x, src_y, src_x, seq[7:0], type[2:0], tag[8:0], hdr[19:0], words[32*16-1:0]} as the NIC sees
//   them; beats out (tx_*) the same, with src = (my_x, my_y), seq = {4'd0, partition}, tag = 0. A word address on the mesh
//   side is a word address, exactly as a tile's bank is addressed by the DMA (20 bits; a fetch or a drain may start inside a
//   row), so a bridge's window is 2^20 words = 4 MB of AXI space at win_base.
//
//   Memory role (MEM = 1; AXI4 master, 40-bit addresses, single ID, INCR bursts of full-width beats, AXI_DW = 128/256/512):
//   - RDREQ (len words at addr, from any tile): AXI read bursts covering bytes [win_base + 4 addr, + 4 len), split at 4 KB
//     boundaries (at most 4096 / (AXI_DW / 8) beats per burst), one burst in flight; every R beat becomes one RDRSP beat of
//     the words of [addr, addr + len) it carries (the first from the request's offset in its beat, as the bank server's
//     first beat starts at the offset in its row), sent to the requester (the coordinates in hdr[7:0], the partition its
//     seq carried) as the R data arrives, then the CRC flit {count, CRC-16}. An R response other than OKAY in the request
//     sets ev_axi and sends the CRC inverted, so the requesting tile raises crc_err: the AXI error reaches the tile that
//     consumed the data. One request served at a time; a second RDREQ waits at the input (the NIC's flow control).
//     Throughput: one beat per aclk cycle within a burst, a 16-word row per 512 / AXI_DW cycles, plus the AR -> first R
//     latency per burst (bursts of up to 64 beats at 512 bits).
//   - WRHDR + WRDATA + CRC (a tile's drain to the bridge): per source, as the NIC keeps it -- the write pointer, the CRC and
//     the word count of each source in its own registers, so drains from several tiles interleave beat by beat; every
//     WRDATA beat is written to AXI as it arrives -- one burst of the 1..5 beats its words touch, byte strobes on the words,
//     split in two at a 4 KB boundary -- and the beat leaves the input when the burst's write response is back; the CRC
//     flit checks the source's CRC and count (ev_crc, ev_lost). That is the NIC's behaviour: the data is in memory when its
//     CRC is checked, the error is flagged, not undone (holding a message until its CRC would take a buffer per source of
//     the longest drain, and blocking a second writer at the input would stall the router port the first writer's beats
//     arrive through). A write response other than OKAY sets ev_axi. Throughput: one beat (<= 16 words) per write round
//     trip (AW and W together, then B).
//   Host role (HOST = 1; AXI4 slave, AXI_IDW-bit IDs echoed, one transaction at a time, writes before reads):
//   - the slave window is NX * NY banks, tile index n = y * NX + x in the address bits above the bank's BAW + 2 byte bits;
//     INCR bursts of full-width beats only (others: SLVERR), a tile index past the mesh: DECERR.
//   - write: WRHDR (the first written word) + one WRDATA beat per AXI beat (its strobed words: whole words, one run per beat;
//     a beat with a partial-word strobe or two runs is skipped and the response is SLVERR; a run that does not continue the
//     previous one starts with a new WRHDR, which the tile's receiver takes in the same message) + the CRC flit; BRESP when
//     the CRC flit has entered the mesh (posted: a read through the same bridge to the same tile follows the same XY path
//     and is served after the write -- the fence a host uses before starting a tile on the data).
//   - read: one RDREQ for the burst's beats (whole beats from the beat-aligned start, at most 4095 words), the RDRSP beats
//     assembled into R beats as they arrive (a 32-word stage); the last R beat waits for the CRC flit and carries its
//     verdict (OKAY, or SLVERR on a CRC, count or length mismatch); no complete response within HOST_TIMEOUT cycles: the
//     remaining beats are SLVERR and ev_timeout is set (late beats of that response are then dropped at the input).
//   - the requests carry seq = {4'd0, partition} (a tile drops flits of another partition); responses to tiles echo the
//     requester's.
//   Both roles share the mesh output one message at a time (arbitrated round-robin at message boundaries) and the input:
//   RDREQ and RDY / PSUM flits for a role that is absent are consumed and dropped (the requester times out as at a dead
//   tile); every data stream's CRC and count are checked per source whichever role consumes it.
//   Error events (one cycle, to top_ctrl's sticky registers): ev_crc, ev_lost (a stream's CRC flit disagrees), ev_axi (an
//   AXI response other than OKAY on the memory role), ev_timeout (a host read timed out), ev_host (a host transaction
//   answered SLVERR or DECERR).
module noc_axi_bridge #(
  parameter int XW = 4, YW = 3,              // mesh coordinate widths (XW = $clog2(NX + 1): the bridge column is x = NX)
  parameter int NX = 8, NY = 8,              // tile columns and rows
  parameter int BAW = 19,                    // the tiles' bank word-address width (the host window)
  parameter int AXI_DW = 512,                // 128, 256 or 512
  parameter int AXI_IDW = 4,                 // the host role's ID width
  parameter int MEM = 1,                     // 1: the memory role (AXI4 master)
  parameter int HOST = 1,                    // 1: the host role (AXI4 slave)
  parameter int HOST_TIMEOUT = 1048575,      // aclk cycles a host read waits for its response
  parameter int BW  = 16,                    // words per beat (a bank row)
  parameter int DWB = 32 + 32 * BW,          // beat payload
  parameter int FB  = 2 * (XW + YW) + 8 + DWB,         // beat flit without its parity bit
  parameter int NT  = NX * NY,
  parameter int NTW = (NT > 1) ? $clog2(NT) : 1,
  parameter int HAW = NTW + BAW + 2          // host window byte-address width
)(
  input  logic                clk,           // the AXI clock (aclk)
  input  logic                rst_n,         // the AXI reset (aresetn)
  input  logic [XW-1:0]       my_x,          // the bridge node's coordinates (straps: x = NX, y = its row)
  input  logic [YW-1:0]       my_y,
  // mesh side, through the cdc_fifo pair
  input  logic                rx_valid,
  input  logic [FB-1:0]       rx_flit,
  output logic                rx_ready,
  output logic                tx_valid,
  output logic [FB-1:0]       tx_flit,
  input  logic                tx_ready,
  // control plane (top_ctrl, the same clock)
  input  logic [39:6]         win_base,      // the memory role's window base (64-byte aligned)
  input  logic [3:0]          partition,     // the host role's requests carry it
  output logic                ev_crc,
  output logic                ev_lost,
  output logic                ev_axi,
  output logic                ev_timeout,
  output logic                ev_host,
  // memory role: AXI4 master (AxID 0, AxLOCK 0, AxCACHE / AxPROT / AxQOS tied by the integrator)
  output logic [39:0]         m_awaddr,
  output logic [7:0]          m_awlen,
  output logic [2:0]          m_awsize,
  output logic [1:0]          m_awburst,
  output logic                m_awvalid,
  input  logic                m_awready,
  output logic [AXI_DW-1:0]   m_wdata,
  output logic [AXI_DW/8-1:0] m_wstrb,
  output logic                m_wlast,
  output logic                m_wvalid,
  input  logic                m_wready,
  input  logic [1:0]          m_bresp,
  input  logic                m_bvalid,
  output logic                m_bready,
  output logic [39:0]         m_araddr,
  output logic [7:0]          m_arlen,
  output logic [2:0]          m_arsize,
  output logic [1:0]          m_arburst,
  output logic                m_arvalid,
  input  logic                m_arready,
  input  logic [AXI_DW-1:0]   m_rdata,
  input  logic [1:0]          m_rresp,
  input  logic                m_rlast,
  input  logic                m_rvalid,
  output logic                m_rready,
  // host role: AXI4 slave
  input  logic [AXI_IDW-1:0]  s_awid,
  input  logic [HAW-1:0]      s_awaddr,
  input  logic [7:0]          s_awlen,
  input  logic [2:0]          s_awsize,
  input  logic [1:0]          s_awburst,
  input  logic                s_awvalid,
  output logic                s_awready,
  input  logic [AXI_DW-1:0]   s_wdata,
  input  logic [AXI_DW/8-1:0] s_wstrb,
  input  logic                s_wlast,
  input  logic                s_wvalid,
  output logic                s_wready,
  output logic [AXI_IDW-1:0]  s_bid,
  output logic [1:0]          s_bresp,
  output logic                s_bvalid,
  input  logic                s_bready,
  input  logic [AXI_IDW-1:0]  s_arid,
  input  logic [HAW-1:0]      s_araddr,
  input  logic [7:0]          s_arlen,
  input  logic [2:0]          s_arsize,
  input  logic [1:0]          s_arburst,
  input  logic                s_arvalid,
  output logic                s_arready,
  output logic [AXI_IDW-1:0]  s_rid,
  output logic [AXI_DW-1:0]   s_rdata,
  output logic [1:0]          s_rresp,
  output logic                s_rlast,
  output logic                s_rvalid,
  input  logic                s_rready
);
  localparam logic [2:0] T_RDREQ = 3'd1, T_RDRSP = 3'd2, T_WRHDR = 3'd3, T_WRDATA = 3'd4, T_PSUM = 3'd5, T_CRC = 3'd6;
  localparam int WPB  = AXI_DW / 32;         // words per AXI beat: 4, 8 or 16
  localparam int LWP  = $clog2(WPB);
  localparam int LBB  = LWP + 2;             // log2 of the bytes per AXI beat
  localparam int PB   = 4096 / (AXI_DW / 8); // AXI beats per 4 KB page
  localparam int VW   = 32 * BW;             // the words of a beat
  localparam int CW   = $clog2(BW + 1);      // beat word count 0..BW
  localparam int NSRC = (NX + 1) * NY;       // every node of the mesh with the bridge column, indexed y * (NX + 1) + x
  localparam int SRCW = (NSRC > 1) ? $clog2(NSRC) : 1;
  localparam int SMW  = 2 * BW;              // a beat's words placed at their AXI lanes: up to two rows
  localparam int FW1  = LWP + 1;             // a word index in an AXI beat, and one more (none)
  localparam logic [2:0] AXSIZE = 3'(LBB);
  localparam logic [1:0] R_OKAY = 2'b00, R_SLVERR = 2'b10, R_DECERR = 2'b11;

  // ------------------------------------------------------------------ RX: the beat at the input
  logic [2:0]      rx_type;
  logic [19:0]     rx_hdr;
  logic [VW-1:0]   rx_words;
  logic [31:0]     rx_word0;
  logic [7:0]      rx_seq;
  logic [XW-1:0]   rx_src_x;
  logic [YW-1:0]   rx_src_y;
  logic [SRCW-1:0] rx_src;
  logic            rx_is_data;
  logic [CW-1:0]   rx_nw;                    // words in the beat
  logic            rx_fire;
  assign rx_type    = rx_flit[DWB-1 -: 3];
  assign rx_hdr     = rx_flit[DWB-13 -: 20];
  assign rx_words   = rx_flit[VW-1:0];
  assign rx_word0   = rx_words[31:0];
  assign rx_seq     = rx_flit[DWB +: 8];
  assign rx_src_x   = rx_flit[DWB + 8 +: XW];
  assign rx_src_y   = rx_flit[DWB + 8 + XW +: YW];
  assign rx_src     = SRCW'(32'(rx_src_y) * (NX + 1) + 32'(rx_src_x));
  assign rx_is_data = (rx_type == T_RDRSP) || (rx_type == T_WRDATA) || (rx_type == T_PSUM);
  assign rx_nw      = rx_is_data ? CW'(rx_hdr[13:8]) + CW'(1) : CW'(1);
  logic unused_rx_bits;                      // the destination (this node), the tag, seq[7:4], hdr[19:14]
  assign unused_rx_bits = ^{rx_flit[FB-1 -: XW + YW], rx_flit[DWB-4 -: 9], rx_seq[7:4], rx_hdr[19:14]};

  // per-source receive state (each source in its own block, as tile_nic keeps it): the write pointer of a drain into the
  // memory window, the CRC and the word count of the stream in progress
  logic [19:0] wr_ptr  [NSRC];
  logic [15:0] crc_acc [NSRC];
  logic [15:0] rx_cnt  [NSRC];
  logic [15:0] rx_crc_next;
  logic        crc_bad, cnt_bad;
  crc16_beat #(.BW(BW)) u_rxcrc (.crc_in(crc_acc[rx_src]), .words(rx_words), .n(rx_nw), .crc_out(rx_crc_next));
  assign crc_bad = (crc_acc[rx_src] != rx_word0[15:0]);
  assign cnt_bad = (rx_cnt[rx_src] != rx_word0[31:16]);
  generate
    for (genvar s = 0; s < NSRC; s++) begin : g_src
      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
          crc_acc[s] <= 16'hFFFF; rx_cnt[s] <= '0;
        end else if (rx_fire && (rx_src == SRCW'(s))) begin
          case (rx_type)
            T_WRDATA, T_RDRSP, T_PSUM: begin            // a data beat: its words into the stream's CRC and count
              crc_acc[s] <= rx_crc_next;
              rx_cnt[s]  <= rx_cnt[s] + 16'(rx_nw);
            end
            T_CRC: begin
              crc_acc[s] <= 16'hFFFF;
              rx_cnt[s]  <= '0;
            end
            default: ;
          endcase
        end
      end
      if (MEM != 0) begin : g_wp                        // the drain's write pointer into the window
        always_ff @(posedge clk or negedge rst_n) begin
          if (!rst_n) wr_ptr[s] <= '0;
          else if (rx_fire && (rx_src == SRCW'(s))) begin
            if (rx_type == T_WRHDR) wr_ptr[s] <= rx_word0[19:0];
            else if (rx_type == T_WRDATA) wr_ptr[s] <= wr_ptr[s] + 20'(rx_nw);   // the whole beat, after its write
          end
        end
      end else begin : g_nowp
        assign wr_ptr[s] = '0;
      end
    end
  endgenerate

  // ------------------------------------------------------------------ TX: one message at a time, round-robin at its end
  logic        mem_want, host_want;          // a role needs the output for its message (held until its last flit)
  logic        own_mem, own_host;
  logic        tx_rr;                        // 1: the last grant went to the memory role
  logic [15:0] tx_crc, tx_crc_next, tx_cnt;  // the CRC and word count of the message being sent
  logic        tx_fire;
  // the roles' flits
  logic           mt_valid, ht_valid;
  logic [XW-1:0]  mt_dx, ht_dx;
  logic [YW-1:0]  mt_dy, ht_dy;
  logic [3:0]     mt_part;
  logic [2:0]     mt_type, ht_type;
  logic [19:0]    mt_hdr, ht_hdr;
  logic [VW-1:0]  mt_words, ht_words;
  logic [CW-1:0]  mt_n, ht_n;
  logic           t_data;
  logic [XW-1:0]  t_dx;
  logic [YW-1:0]  t_dy;
  logic [3:0]     t_part;
  logic [2:0]     t_type;
  logic [19:0]    t_hdr;
  logic [VW-1:0]  t_words;
  logic [CW-1:0]  t_n;
  always_comb begin
    tx_valid = 1'b0; t_dx = '0; t_dy = '0; t_part = '0; t_type = '0; t_hdr = '0; t_words = '0; t_n = '0;
    if (own_mem) begin
      tx_valid = mt_valid; t_dx = mt_dx; t_dy = mt_dy; t_part = mt_part; t_type = mt_type; t_hdr = mt_hdr; t_words = mt_words; t_n = mt_n;
    end else if (own_host) begin
      tx_valid = ht_valid; t_dx = ht_dx; t_dy = ht_dy; t_part = partition; t_type = ht_type; t_hdr = ht_hdr; t_words = ht_words; t_n = ht_n;
    end
  end
  assign t_data  = (t_type == T_RDRSP) || (t_type == T_WRDATA);
  assign tx_flit = {t_dy, t_dx, my_y, my_x, 4'd0, t_part, t_type, 9'd0, t_hdr, t_words};
  assign tx_fire = tx_valid && tx_ready;
  crc16_beat #(.BW(BW)) u_txcrc (.crc_in(tx_crc), .words(t_words), .n(t_n), .crc_out(tx_crc_next));
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      own_mem <= 1'b0; own_host <= 1'b0; tx_rr <= 1'b0; tx_crc <= 16'hFFFF; tx_cnt <= '0;
    end else begin
      if (own_mem && !mem_want) own_mem <= 1'b0;
      else if (own_host && !host_want) own_host <= 1'b0;
      else if (!own_mem && !own_host) begin   // a grant starts a message: its CRC and count from the initial value
        if (mem_want && (!host_want || !tx_rr)) begin own_mem <= 1'b1; tx_rr <= 1'b1; tx_crc <= 16'hFFFF; tx_cnt <= '0; end
        else if (host_want) begin own_host <= 1'b1; tx_rr <= 1'b0; tx_crc <= 16'hFFFF; tx_cnt <= '0; end
      end
      if (tx_fire && t_data) begin tx_crc <= tx_crc_next; tx_cnt <= tx_cnt + 16'(t_n); end
    end
  end

  // ------------------------------------------------------------------ RX handshake
  logic rdreq_ok;                            // memory role: an RDREQ is taken now (the server is free)
  logic wrdata_ok;                           // memory role: the WRDATA beat at the input is written (its burst's B is back)
  logic host_rsp;                            // host role: the RDRSP beat at the input belongs to the read in progress
  logic host_rsp_ok;                         // ... and the stage takes it now
  always_comb begin
    case (rx_type)
      T_RDREQ:  rx_ready = rdreq_ok;
      T_RDRSP:  rx_ready = !host_rsp || host_rsp_ok;
      T_WRDATA: rx_ready = wrdata_ok;
      default:  rx_ready = 1'b1;
    endcase
  end
  assign rx_fire = rx_valid && rx_ready;

  // error events: a stream's CRC flit against the source's CRC and count; the roles' events below
  logic m_ev_axi, h_ev_timeout, h_ev_host;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      ev_crc <= 1'b0; ev_lost <= 1'b0; ev_axi <= 1'b0; ev_timeout <= 1'b0; ev_host <= 1'b0;
    end else begin
      ev_crc     <= rx_fire && (rx_type == T_CRC) && crc_bad;
      ev_lost    <= rx_fire && (rx_type == T_CRC) && cnt_bad;
      ev_axi     <= m_ev_axi;
      ev_timeout <= h_ev_timeout;
      ev_host    <= h_ev_host;
    end
  end

  // ------------------------------------------------------------------ memory role (AXI4 master)
  generate
    if (MEM != 0) begin : g_mem
      // ---- serve: RDREQ -> AXI read bursts -> RDRSP beats + CRC ----
      localparam logic [1:0] SV_IDLE = 2'd0, SV_AR = 2'd1, SV_R = 2'd2, SV_CRC = 2'd3;
      logic [1:0]     sv_state;
      logic           sv_busy;
      logic [XW-1:0]  sv_x;
      logic [YW-1:0]  sv_y;
      logic [3:0]     sv_part;
      logic [39:0]    sv_baddr;              // the next AXI beat to request (beat-aligned)
      logic [12:0]    sv_beats;              // AXI beats still to request
      logic [12:0]    sv_words;              // words still to send
      logic [LWP-1:0] sv_off;                // the first word's lane in its beat
      logic           sv_first;              // the next R beat is the request's first
      logic           sv_err;                // an R response of this request was not OKAY
      logic [39:0]    rq_a;                  // the RDREQ at the input: its byte address
      logic [12:0]    rq_beats;              // ... and the AXI beats it covers
      logic [12:0]    sv_pleft, sv_nb;       // beats to the end of the 4 KB page; beats of the next burst
      logic [LWP-1:0] r_lo;                  // the R beat's first word of the request
      logic [CW-1:0]  r_avail, r_n;          // words from r_lo to the beat's end; words of the request it carries
      logic           r_fire;
      assign rq_a     = {win_base, 6'd0} + {18'd0, rx_word0[19:0], 2'd0};
      assign rq_beats = (13'(rq_a[LBB-1:2]) + 13'(rx_word0[31:20]) + 13'(WPB - 1)) >> LWP;
      assign sv_pleft = 13'(PB) - 13'(sv_baddr[11:LBB]);
      assign sv_nb    = (sv_beats < sv_pleft) ? sv_beats : sv_pleft;
      assign r_lo     = sv_first ? sv_off : '0;
      assign r_avail  = CW'(WPB) - CW'(r_lo);
      assign r_n      = (13'(r_avail) < sv_words) ? r_avail : CW'(sv_words);
      assign r_fire   = m_rvalid && m_rready;
      assign rdreq_ok = !sv_busy;
      assign mem_want = sv_busy;
      assign m_arvalid = (sv_state == SV_AR);
      assign m_araddr  = sv_baddr;
      assign m_arlen   = 8'(sv_nb - 13'd1);
      assign m_arsize  = AXSIZE;
      assign m_arburst = 2'b01;
      assign m_rready  = own_mem && (sv_state == SV_R) && tx_ready;
      // the flit: an RDRSP beat per R beat, then the CRC flit (inverted after an AXI error)
      assign mt_valid = own_mem && (((sv_state == SV_R) && m_rvalid) || (sv_state == SV_CRC));
      assign mt_dx    = sv_x;
      assign mt_dy    = sv_y;
      assign mt_part  = sv_part;
      assign mt_type  = (sv_state == SV_CRC) ? T_CRC : T_RDRSP;
      assign mt_hdr   = (sv_state == SV_CRC) ? 20'd0 : {6'd0, 6'(r_n - CW'(1)), 8'd0};
      assign mt_words = (sv_state == SV_CRC) ? VW'({tx_cnt, tx_crc ^ {16{sv_err}}}) : VW'(m_rdata) >> (32 * r_lo);
      assign mt_n     = (sv_state == SV_CRC) ? CW'(1) : r_n;
      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
          sv_state <= SV_IDLE; sv_busy <= 1'b0; sv_x <= '0; sv_y <= '0; sv_part <= '0; sv_baddr <= '0; sv_beats <= '0;
          sv_words <= '0; sv_off <= '0; sv_first <= 1'b0; sv_err <= 1'b0;
        end else begin
          if (rx_fire && (rx_type == T_RDREQ)) begin         // latched; served once the output is ours
            sv_busy  <= 1'b1;
            sv_x     <= XW'(rx_hdr[3:0]);                       // the requester's coordinates and partition
            sv_y     <= YW'(rx_hdr[7:4]);
            sv_part  <= rx_seq[3:0];
            sv_baddr <= {rq_a[39:LBB], LBB'(0)};
            sv_off   <= rq_a[LBB-1:2];
            sv_beats <= rq_beats;
            sv_words <= 13'(rx_word0[31:20]);
            sv_first <= 1'b1;
            sv_err   <= 1'b0;
          end
          case (sv_state)
            SV_IDLE: if (sv_busy && own_mem) sv_state <= (sv_words == 13'd0) ? SV_CRC : SV_AR;   // a zero-length request: the CRC flit alone
            SV_AR: if (m_arready) begin
              sv_baddr <= sv_baddr + (40'(sv_nb) << LBB);
              sv_beats <= sv_beats - sv_nb;
              sv_state <= SV_R;
            end
            SV_R: if (r_fire) begin
              sv_words <= sv_words - 13'(r_n);
              sv_first <= 1'b0;
              if (m_rresp != R_OKAY) sv_err <= 1'b1;
              if (m_rlast) sv_state <= (sv_beats == 13'd0) ? SV_CRC : SV_AR;
            end
            SV_CRC: if (tx_ready) begin sv_state <= SV_IDLE; sv_busy <= 1'b0; end
            default: sv_state <= SV_IDLE;
          endcase
        end
      end

      // ---- drain into the window: each WRDATA beat one AXI write burst (two at a 4 KB boundary), B back, then consumed ----
      localparam logic [1:0] MW_IDLE = 2'd0, MW_XFER = 2'd1, MW_RESP = 2'd2;
      logic [1:0]       mw_state;
      logic             mw_second;           // the beat's second burst (after a 4 KB boundary)
      logic             mw_aw_done;
      logic [2:0]       mw_j;                // W beats sent for this WRDATA beat (both bursts)
      logic [2:0]       mw_w;                // W beats sent in this burst
      logic [39:0]      wa, wa_al;           // the beat's byte address, beat-aligned
      logic [LWP-1:0]   w_off;
      logic [2:0]       w_k, w_b1, w_b2, w_kb;   // AXI beats: all, first burst, second burst, this burst
      logic [12:0]      w_pleft;
      logic [2*VW-1:0]  w_vec;               // the beat's words at their lanes from w_off
      logic [2*BW-1:0]  w_sm;                // ... their word strobes
      logic [WPB-1:0]   w_ws;                // word strobes of W beat mw_j
      logic             w_aw_fire, w_w_fire, w_last_w;
      assign wa       = {win_base, 6'd0} + {18'd0, wr_ptr[rx_src], 2'd0};
      assign wa_al    = {wa[39:LBB], LBB'(0)};
      assign w_off    = wa[LBB-1:2];
      assign w_k      = 3'((7'(w_off) + 7'(rx_nw) + 7'(WPB - 1)) >> LWP);
      assign w_pleft  = 13'(PB) - 13'(wa_al[11:LBB]);
      assign w_b1     = (13'(w_k) < w_pleft) ? w_k : 3'(w_pleft);
      assign w_b2     = w_k - w_b1;
      assign w_kb     = mw_second ? w_b2 : w_b1;
      assign w_vec    = {VW'(0), rx_words} << (32 * w_off);
      assign w_sm     = ((SMW'(1) << rx_nw) - SMW'(1)) << w_off;
      assign w_ws     = w_sm[WPB * mw_j +: WPB];
      assign m_awvalid = (mw_state == MW_XFER) && !mw_aw_done;
      assign m_awaddr  = mw_second ? wa_al + (40'(w_b1) << LBB) : wa_al;
      assign m_awlen   = 8'(w_kb) - 8'd1;
      assign m_awsize  = AXSIZE;
      assign m_awburst = 2'b01;
      assign m_wvalid  = (mw_state == MW_XFER) && (mw_w < w_kb);
      assign m_wdata   = w_vec[AXI_DW * mw_j +: AXI_DW];
      for (genvar b = 0; b < WPB; b++) begin : g_strb
        assign m_wstrb[4 * b +: 4] = {4{w_ws[b]}};
      end
      assign m_wlast   = (mw_w == w_kb - 3'd1);
      assign m_bready  = (mw_state == MW_RESP);
      assign w_aw_fire = m_awvalid && m_awready;
      assign w_w_fire  = m_wvalid && m_wready;
      assign w_last_w  = w_w_fire && m_wlast;
      assign wrdata_ok = (mw_state == MW_RESP) && m_bvalid && (mw_second || (w_b2 == 3'd0));
      assign m_ev_axi  = (r_fire && (m_rresp != R_OKAY)) || (m_bvalid && m_bready && (m_bresp != R_OKAY));
      logic unused_mem_bits;                 // byte addresses of words: bits 1:0 are zero
      assign unused_mem_bits = ^{rq_a[1:0], wa[1:0]};
      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
          mw_state <= MW_IDLE; mw_second <= 1'b0; mw_aw_done <= 1'b0; mw_j <= '0; mw_w <= '0;
        end else begin
          case (mw_state)
            MW_IDLE: if (rx_valid && (rx_type == T_WRDATA)) begin
              mw_state <= MW_XFER; mw_second <= 1'b0; mw_aw_done <= 1'b0; mw_j <= '0; mw_w <= '0;
            end
            MW_XFER: begin
              if (w_aw_fire) mw_aw_done <= 1'b1;
              if (w_w_fire) begin mw_j <= mw_j + 3'd1; mw_w <= mw_w + 3'd1; end
              if ((mw_aw_done || w_aw_fire) && (w_last_w || (mw_w == w_kb))) mw_state <= MW_RESP;
            end
            MW_RESP: if (m_bvalid) begin
              if (!mw_second && (w_b2 != 3'd0)) begin
                mw_second <= 1'b1; mw_aw_done <= 1'b0; mw_w <= '0; mw_state <= MW_XFER;
              end else begin
                mw_state <= MW_IDLE;
              end
            end
            default: mw_state <= MW_IDLE;
          endcase
        end
      end
    end else begin : g_nomem
      assign rdreq_ok = 1'b1;                // no memory role: an RDREQ is consumed and dropped (the requester times out)
      assign wrdata_ok = 1'b1;
      assign mem_want = 1'b0;
      assign m_ev_axi = 1'b0;
      assign mt_valid = 1'b0; assign mt_dx = '0; assign mt_dy = '0; assign mt_part = '0; assign mt_type = '0;
      assign mt_hdr = '0; assign mt_words = '0; assign mt_n = '0;
      assign m_awaddr = '0; assign m_awlen = '0; assign m_awsize = '0; assign m_awburst = '0; assign m_awvalid = 1'b0;
      assign m_wdata = '0; assign m_wstrb = '0; assign m_wlast = 1'b0; assign m_wvalid = 1'b0; assign m_bready = 1'b0;
      assign m_araddr = '0; assign m_arlen = '0; assign m_arsize = '0; assign m_arburst = '0; assign m_arvalid = 1'b0;
      assign m_rready = 1'b0;
      logic unused_mem;
      assign unused_mem = ^{m_awready, m_wready, m_bresp, m_bvalid, m_arready, m_rdata, m_rresp, m_rlast, m_rvalid, win_base,
                            rx_hdr[7:0], rx_seq[3:0], wr_ptr[0]};
    end
  endgenerate

  // ------------------------------------------------------------------ host role (AXI4 slave)
  generate
    if (HOST != 0) begin : g_host
      localparam logic [2:0] H_IDLE = 3'd0, H_W = 3'd1, H_WCRC = 3'd2, H_B = 3'd3, H_WSKIP = 3'd4, H_RREQ = 3'd5, H_R = 3'd6;
      localparam int SGW = 2 * BW;           // words of the read stage
      logic [2:0]          h_state;
      logic [AXI_IDW-1:0]  h_id;
      logic [XW-1:0]       h_x;
      logic [YW-1:0]       h_y;
      logic [SRCW-1:0]     h_src;            // the target tile's source index (its responses)
      logic [BAW-1:0]      h_bword;          // the bank word address of the current AXI beat's lane 0
      logic [8:0]          h_beats;          // AXI beats left in the burst
      logic [1:0]          h_resp;           // the write's response; the read's error response
      logic                h_hdr;            // a WRHDR has been sent in this message
      logic                h_hdr_now;        // ... for the current W beat (its WRDATA goes out next)
      logic [BAW-1:0]      h_next;           // the word address a WRDATA continues at
      logic [12:0]         h_nwreq;          // the read's words
      logic [12:0]         h_got;            // words received for it
      logic                h_wait, h_final, h_ok;
      localparam int HTW = $clog2(HOST_TIMEOUT + 1);
      logic [HTW-1:0]      h_timer;
      logic [32*SGW-1:0]   h_stage;          // the read's words, the oldest at word 0
      logic [5:0]          h_cnt;            // words in the stage
      // address decode of the transaction at the AXI inputs (AW has priority)
      logic                aw_take, ar_take;
      logic [HAW-1:0]      d_addr;
      logic [NTW-1:0]      d_node;
      logic                d_inmesh, d_sup;
      logic [8:0]          d_beats;
      logic [12:0]         d_nw;
      assign aw_take  = (h_state == H_IDLE) && s_awvalid;
      assign ar_take  = (h_state == H_IDLE) && !s_awvalid && s_arvalid;
      assign s_awready = (h_state == H_IDLE);
      assign s_arready = (h_state == H_IDLE) && !s_awvalid;
      assign d_addr   = s_awvalid ? s_awaddr : s_araddr;
      assign d_node   = d_addr[HAW-1 -: NTW];
      assign d_inmesh = (32'(d_node) < NT);
      assign d_sup    = s_awvalid ? ((s_awburst == 2'b01) && (s_awsize == AXSIZE)) : ((s_arburst == 2'b01) && (s_arsize == AXSIZE));
      logic [7:0] d_len;
      assign d_len    = s_awvalid ? s_awlen : s_arlen;
      assign d_beats  = {1'b0, d_len} + 9'd1;
      assign d_nw     = 13'(d_beats) << LWP;
      // ---- write beats: the strobed run of the W beat at the input ----
      logic [WPB-1:0] ws, wp;               // whole-word strobes; partial-word strobes
      logic           w_any, w_bad, w_contig, w_need_hdr;
      logic [FW1-1:0] w_first;               // the run's first word (WPB when none)
      logic [CW-1:0]  w_n;                   // its words
      logic [BAW-1:0] w_addr;                // its bank word address
      always_comb begin
        w_first = FW1'(WPB);
        w_n = '0;
        for (int b = WPB - 1; b >= 0; b--) begin
          ws[b] = &s_wstrb[4 * b +: 4];
          wp[b] = (|s_wstrb[4 * b +: 4]) && !ws[b];
          if (ws[b]) w_first = FW1'(b);
        end
        for (int b = 0; b < WPB; b++) w_n = w_n + CW'(ws[b]);
      end
      assign w_any      = |ws;
      assign w_contig   = (32'(ws) >> w_first) == ((32'd1 << w_n) - 32'd1);
      assign w_bad      = (|wp) || (w_any && !w_contig);
      assign w_addr     = h_bword + BAW'(w_first);
      assign w_need_hdr = !h_hdr || (w_addr != h_next);
      logic w_flit;                          // the W beat at the input has words to send
      assign w_flit     = w_any && !w_bad;
      // the WRDATA / WRHDR / CRC / RDREQ flits
      always_comb begin
        ht_valid = 1'b0; ht_type = T_WRDATA; ht_hdr = '0; ht_words = '0; ht_n = CW'(1);
        case (h_state)
          H_W: if (s_wvalid && w_flit && own_host) begin
            ht_valid = 1'b1;
            if (w_need_hdr && !h_hdr_now) begin ht_type = T_WRHDR; ht_words = VW'(32'(w_addr)); end
            else begin ht_type = T_WRDATA; ht_hdr = {6'd0, 6'(w_n - CW'(1)), 8'd0}; ht_words = VW'(s_wdata) >> (32 * w_first); ht_n = w_n; end
          end
          H_WCRC: if (own_host && h_hdr) begin ht_valid = 1'b1; ht_type = T_CRC; ht_words = VW'({tx_cnt, tx_crc}); end
          H_RREQ: if (own_host) begin
            ht_valid = 1'b1; ht_type = T_RDREQ; ht_hdr = {12'd0, 4'(my_y), 4'(my_x)};
            ht_words = VW'({12'(h_nwreq), 20'(h_bword)});
          end
          default: ;
        endcase
      end
      assign ht_dx = h_x;
      assign ht_dy = h_y;
      assign host_want = (h_state == H_W) || (h_state == H_WCRC) || (h_state == H_RREQ);
      // a W beat is consumed when it has nothing to send, or when its WRDATA leaves
      assign s_wready = ((h_state == H_W) && own_host && (!w_flit || (tx_ready && !(w_need_hdr && !h_hdr_now)))) ||
                        (h_state == H_WSKIP);
      logic w_fire;
      assign w_fire   = s_wvalid && s_wready;
      assign s_bvalid = (h_state == H_B);
      assign s_bid    = h_id;
      assign s_bresp  = h_resp;
      // ---- read: the stage of RDRSP words, R beats out ----
      logic           r_fire;
      logic [5:0]     cnt1;                  // words left in the stage after this cycle's R beat
      logic [32*SGW-1:0] stage1, rx_in;
      logic [VW-1:0]  rx_masked;
      assign s_rvalid  = (h_state == H_R) && (h_final || ((h_beats > 9'd1) && (h_cnt >= 6'(WPB))));
      assign s_rdata   = h_stage[AXI_DW-1:0];
      assign s_rresp   = (h_final && !h_ok) ? h_resp : R_OKAY;
      assign s_rlast   = (h_beats == 9'd1);
      assign s_rid     = h_id;
      assign r_fire    = s_rvalid && s_rready;
      assign cnt1      = !r_fire ? h_cnt : ((h_cnt >= 6'(WPB)) ? h_cnt - 6'(WPB) : 6'd0);
      assign stage1    = r_fire ? (h_stage >> (32 * WPB)) : h_stage;
      always_comb begin
        for (int i = 0; i < BW; i++) rx_masked[32 * i +: 32] = (CW'(i) < rx_nw) ? rx_words[32 * i +: 32] : 32'd0;
      end
      assign rx_in       = {VW'(0), rx_masked} << (32 * cnt1);
      assign host_rsp    = h_wait && (rx_type == T_RDRSP) && (rx_src == h_src);
      assign host_rsp_ok = (cnt1 < 6'(WPB));
      logic rsp_fire, crc_fire;
      assign rsp_fire = rx_fire && host_rsp;
      assign crc_fire = rx_fire && h_wait && (rx_type == T_CRC) && (rx_src == h_src);
      assign h_ev_timeout = h_wait && (h_timer == HTW'(HOST_TIMEOUT - 1)) && !crc_fire;
      assign h_ev_host = ((aw_take || ar_take) && !(d_inmesh && d_sup && (!ar_take || d_nw <= 13'd4095))) ||
                         ((h_state == H_W) && w_fire && w_bad);
      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
          h_state <= H_IDLE; h_id <= '0; h_x <= '0; h_y <= '0; h_src <= '0; h_bword <= '0; h_beats <= '0; h_resp <= R_OKAY;
          h_hdr <= 1'b0; h_hdr_now <= 1'b0; h_next <= '0; h_nwreq <= '0; h_got <= '0; h_wait <= 1'b0; h_final <= 1'b0;
          h_ok <= 1'b0; h_timer <= '0; h_stage <= '0; h_cnt <= '0;
        end else begin
          case (h_state)
            H_IDLE: if (aw_take || ar_take) begin
              h_id    <= aw_take ? s_awid : s_arid;
              h_x     <= XW'(32'(d_node) % NX);
              h_y     <= YW'(32'(d_node) / NX);
              h_src   <= SRCW'((32'(d_node) / NX) * (NX + 1) + 32'(d_node) % NX);
              h_bword <= {d_addr[BAW+1:2+LWP], LWP'(0)};
              h_beats <= d_beats;
              h_hdr   <= 1'b0; h_hdr_now <= 1'b0;
              h_nwreq <= d_nw;
              h_got   <= '0; h_cnt <= '0; h_final <= 1'b0; h_ok <= 1'b0; h_wait <= 1'b0; h_timer <= '0;
              h_resp  <= !d_inmesh ? R_DECERR : (!d_sup ? R_SLVERR : R_OKAY);
              if (aw_take) h_state <= (d_inmesh && d_sup) ? H_W : H_WSKIP;
              else if (d_inmesh && d_sup && (d_nw <= 13'd4095)) h_state <= H_RREQ;
              else begin                       // a read that cannot be sent: its beats with the error
                h_state <= H_R; h_final <= 1'b1; h_ok <= 1'b0; if (d_inmesh && d_sup) h_resp <= R_SLVERR;
              end
            end
            H_W: begin
              if (own_host && s_wvalid && w_flit && w_need_hdr && !h_hdr_now && tx_ready) begin
                h_hdr_now <= 1'b1; h_hdr <= 1'b1; h_next <= w_addr;
              end
              if (w_fire) begin
                h_hdr_now <= 1'b0;
                if (w_flit) h_next <= w_addr + BAW'(w_n);
                if (w_bad) h_resp <= R_SLVERR;
                h_bword <= h_bword + BAW'(WPB);
                h_beats <= h_beats - 9'd1;
                if (h_beats == 9'd1) h_state <= H_WCRC;
              end
            end
            H_WCRC: if (own_host && (!h_hdr || tx_ready)) h_state <= H_B;
            H_WSKIP: if (w_fire && (h_beats == 9'd1)) h_state <= H_B; else if (w_fire) h_beats <= h_beats - 9'd1;
            H_B: if (s_bready) h_state <= H_IDLE;
            H_RREQ: if (own_host && tx_ready) begin h_state <= H_R; h_wait <= 1'b1; h_timer <= '0; end
            H_R: begin
              if (r_fire) begin
                h_beats <= h_beats - 9'd1;
                if (h_beats == 9'd1) h_state <= H_IDLE;
              end
              if (h_wait) h_timer <= h_timer + HTW'(1);
              if (crc_fire) begin                // the response is complete: the verdict for the last beat
                h_wait <= 1'b0; h_final <= 1'b1;
                h_ok   <= !crc_bad && !cnt_bad && (h_got == h_nwreq);
                h_resp <= R_SLVERR;
              end else if (h_ev_timeout) begin
                h_wait <= 1'b0; h_final <= 1'b1; h_ok <= 1'b0; h_resp <= R_SLVERR;
              end
            end
            default: h_state <= H_IDLE;
          endcase
          // the stage: an R beat out and an RDRSP beat in, in the same cycle when there is room; empty for a new transaction
          if (aw_take || ar_take) begin
            h_stage <= '0; h_cnt <= '0;
          end else begin
            h_stage <= rsp_fire ? (stage1 | rx_in) : stage1;
            h_cnt   <= rsp_fire ? cnt1 + 6'(rx_nw) : cnt1;
          end
          if (rsp_fire) h_got <= h_got + 13'(rx_nw);
        end
      end
      logic unused_host;                     // WLAST (the burst length counts the beats), the address bits below a beat
      assign unused_host = ^{s_wlast, d_addr[LWP+1:0]};
    end else begin : g_nohost
      assign host_want = 1'b0; assign host_rsp = 1'b0; assign host_rsp_ok = 1'b1;
      assign h_ev_timeout = 1'b0; assign h_ev_host = 1'b0;
      assign ht_valid = 1'b0; assign ht_dx = '0; assign ht_dy = '0; assign ht_type = '0; assign ht_hdr = '0; assign ht_words = '0;
      assign ht_n = '0;
      assign s_awready = 1'b0; assign s_wready = 1'b0; assign s_bid = '0; assign s_bresp = '0; assign s_bvalid = 1'b0;
      assign s_arready = 1'b0; assign s_rid = '0; assign s_rdata = '0; assign s_rresp = '0; assign s_rlast = 1'b0;
      assign s_rvalid = 1'b0;
      logic unused_host;
      assign unused_host = ^{s_awid, s_awaddr, s_awlen, s_awsize, s_awburst, s_awvalid, s_wdata, s_wstrb, s_wlast, s_wvalid, s_bready,
                             s_arid, s_araddr, s_arlen, s_arsize, s_arburst, s_arvalid, s_rready, partition};
    end
  endgenerate
endmodule
