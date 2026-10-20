// noc_axi_bridge.sv -- the NoC-to-AXI4 bridge engine of a bridge node (drop 0.42, the chip top): speaks the tile
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
//   - RDREQ (len words at addr, from any tile): taken off the input into a request queue of QD = 4 entries (drop 0.43,
//     stream B2; B1 held a second RDREQ at the input, and every beat behind it -- a drain's WRDATA, a host read's RDRSP --
//     with it). The AR engine forms each entry's bursts, bytes [win_base + 4 addr, + 4 len) split at 4 KB boundaries (at most
//     4096 / (AXI_DW / 8) beats per burst), and issues them while fewer than QD bursts are outstanding: up to QD bursts of
//     up to QD requests in flight. One AXI ID: the slave returns the bursts in order and the responses leave in queue order
//     (a slave that reorders needs an ID per burst and a reorder buffer of up to QD bursts in front of the RDRSP path -- not
//     built; AXI requires same-ID responses in order, so the single ID is correct with every slave, only less concurrent
//     with one that could reorder). Every R beat becomes one RDRSP beat of the words of [addr, addr + len) it carries (the
//     first from the request's offset in its beat, as the bank server's first beat starts at the offset in its row), sent
//     to the requester (the coordinates in hdr[7:0], the partition its seq carried) as the R data arrives, then the CRC
//     flit {count, CRC-16}. An R response other than OKAY in the request sets ev_axi and sends the CRC inverted, so the
//     requesting tile raises crc_err: the AXI error reaches the tile that consumed the data. An entry's AR waits until every
//     write burst holding data taken before the RDREQ has its B: a read never overtakes an earlier write to the window (the
//     order B1's blocking input gave for free). Throughput: one beat per aclk cycle within a burst, the AR -> first R
//     latency hidden behind the other bursts in flight (B1 paid it per burst).
//   - WRHDR + WRDATA + CRC (a tile's drain to the bridge): per source, as the NIC keeps it -- the write pointer, the CRC and
//     the word count of each source in its own registers, so drains from several tiles interleave beat by beat. Drop 0.43:
//     the writes are posted and combined -- each WRDATA beat leaves the input for a register (with its source's write
//     pointer), a second register takes it with its AXI address formed, and from there it is placed one AXI beat per cycle
//     into the open burst (registers compared, a beat per AXI beat flowing): the continuation of the burst's last beat (a
//     drain going on inside a beat) merges into it, the next beat in the same 4 KB page from the same source appends, any
//     other closes the burst and opens a new one; a burst also closes at its source's CRC flit, when an RDREQ is taken and
//     after 15 idle cycles. Closed bursts queue for AW (QD of them), their beats in a 4 KB write buffer (a neo_mem_rf of
//     4096 / (AXI_DW / 8) beats with word strobes), at most QD awaiting B; nothing at the input waits for B (B1 wrote each
//     beat as one burst of its 1..5 AXI beats and held it at the input until the burst's B). The CRC flit checks the
//     source's CRC and count (ev_crc, ev_lost): the error is flagged, not undone, as before (holding a message until its CRC
//     would take a buffer per source of the longest drain). A write response other than OKAY sets ev_axi. wr_busy: a beat
//     buffered, a burst open, queued or awaiting B (top_ctrl: the command queue's completion, the bridge's STATUS).
//     Throughput: an AXI beat per cycle into the buffer and one W beat per cycle out, in bursts of up to a 4 KB page.
//   - Telemetry (drop 0.43): ev_rd_wait (a read burst outstanding -- its AR taken, its last R beat not -- and no R beat
//     this cycle) and ev_wr_wait (a write burst outstanding -- its AW taken, its B not -- and no W beat this cycle): the
//     AXI side's wait cycles, counted by top_ctrl.
//   Host role (HOST = 1; AXI4 slave, AXI_IDW-bit IDs echoed, one transaction at a time, writes before reads):
//   - the slave window is NX * NY banks, tile index n = y * NX + x in the address bits above the bank's BAW + 2 byte bits;
//     INCR bursts of full-width beats only (others: SLVERR), a tile index past the mesh: DECERR.
//   - write: WRHDR (the first written word) + one WRDATA beat per AXI beat (its strobed words: whole words, one run per beat;
//     a beat with a partial-word strobe or two runs is skipped and the response is SLVERR; a run that does not continue the
//     previous one starts with a new WRHDR, which the tile's receiver takes in the same message) + the CRC flit; BRESP when
//     the CRC flit has entered the mesh (posted: a read through the same bridge to the same tile follows the same XY path
//     and is served after the write -- the fence a host uses before starting a tile on the data). Each W beat is taken
//     into a register with its strobes decoded on the way in and sent from there (one beat per cycle).
//   - read: one RDREQ for the burst's beats (whole beats from the beat-aligned start, at most 4095 words), the RDRSP beats
//     assembled into R beats as they arrive (a 32-word stage, which takes a beat while it has room for a whole one); the
//     last R beat waits for the CRC flit and carries its verdict (OKAY, or SLVERR on a CRC, count or length mismatch); no
//     complete response within HOST_TIMEOUT cycles: the remaining beats are SLVERR and ev_timeout is set (late beats of
//     that response are then dropped at the input).
//   - the requests carry seq = {4'd0, partition} (a tile drops flits of another partition); responses to tiles echo the
//     requester's.
//   Both roles share the mesh output one message at a time (arbitrated round-robin at message boundaries) and the input:
//   RDREQ and RDY / PSUM flits for a role that is absent are consumed and dropped (the requester times out as at a dead
//   tile); every data stream's CRC and count are checked per source whichever role consumes it.
//   Error events (one cycle, to top_ctrl's sticky registers): ev_crc, ev_lost (a stream's CRC flit disagrees), ev_axi (an
//   AXI response other than OKAY on the memory role), ev_timeout (a host read timed out), ev_host (a host transaction
//   answered SLVERR or DECERR).
//   Timing structure: drop 0.44 (A4) -- the beat is registered at the port with its source's receive state beside it
//   (the RX stage below, the NIC's drop-0.42 form), so rx_ready is the stage's room alone and every action on a beat
//   reads registers; the receive CRC is crc16_beat on the registered beat and the registered state (the two-halves
//   split of drop 0.42 is gone with the select it served); the read request's fields (taken from the queue at IS_IDLE), its address and
//   burst sizes (IS_ADDR, IS_ARP), the R beat's lane and word count are registers, the write path works from its second
//   beat register (its address formed between the two) into registers and the buffer, so AR, AW and W come from
//   registers or the buffer's read port; a queue's full test compares its pointers (no subtractor before rx_ready); the
//   host's W beats are registered with their decode and shifted on the way in. Paths left from an input to an output:
//   R data to the flit toward the mesh (the memory role forwards R as it arrives), the mesh FIFO's ready to RREADY /
//   WREADY.
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
  output logic                ev_rd_wait,    // drop 0.43: telemetry -- an AXI read waited this cycle (above)
  output logic                ev_wr_wait,    // ... an AXI write waited this cycle
  output logic                wr_busy,       // drop 0.43: the memory role holds writes not yet answered by B
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
  localparam logic [2:0] AXSIZE = 3'(LBB);
  localparam logic [1:0] R_OKAY = 2'b00, R_SLVERR = 2'b10, R_DECERR = 2'b11;
  // the memory window's byte address of word a: win_base + 4 a, as the 16-bit add of win_base[21:6] and a[19:4] with the
  // bits above taken from win_base or win_base + 1 by its carry (both formed beside the add)
  function automatic logic [39:0] win_addr(input logic [39:6] wb, input logic [19:0] a);
    logic [16:0] lo;
    lo = 17'(wb[21:6]) + 17'(a[19:4]);
    return {lo[16] ? wb[39:22] + 18'd1 : wb[39:22], lo[15:0], a[3:0], 2'b00};
  endfunction

  // ------------------------------------------------------------------ RX stage (drop 0.44, A4: the NIC's drop-0.42 form)
  // The beat from the mesh FIFO is registered at the port (q_*) before anything acts on it, with its word count formed on
  // the way (q_nw, and a copy stored inverted for the CRC -- two flops of one input would be one flop to the synthesis)
  // and, registered beside it, the receive state of its source: the write pointer, the CRC accumulator and the word
  // count (st_*), read from the source's registers through an AND-OR select of the source's one-hot in the cycle the
  // stage takes the beat, then updated in place by every beat of that source; a beat of another source loads its own
  // state while the outgoing one goes back to its registers through the write-back register wb3_* (a beat whose source
  // is in wb3_* takes the state from there). Every action on a beat -- the queue's take, the write path's register A,
  // the host read's stage, the CRC and the count -- then reads registers only. Before (drop 0.42 / 0.43), the beat at
  // the port drove the per-source select and the receive CRC in one cycle: rx_flit's count field through the CRC's
  // 17-way matrix select into crc_acc, 1,359 ps register to register and 1,339 endpoints at 1,500 ps (run 211). The
  // port's rx_ready is the stage's room (st_free): it no longer depends on the beat's type; every flit is one aclk cycle
  // later, the receive rate unchanged (a beat per cycle while the stage consumes one per cycle).
  logic            q_valid;
  logic [FB-1:0]   q_flit;
  logic [CW-1:0]   q_nw;
  logic [CW-1:0]   q_nwc_n;                  // ~q_nw, the CRC's copy
  logic            q_ready;                  // the registered beat is consumed this cycle (the handshake below)
  logic            st_free;                  // the stage takes a beat this cycle
  logic            i_take;
  logic [2:0]      in_type;                  // the port beat's type and word count
  logic            in_data;
  logic [CW-1:0]   in_nw;
  logic [XW-1:0]   in_cx;                    // the port beat's source: its coordinates and their one-hot over the sources
  logic [YW-1:0]   in_cy;
  logic [NSRC-1:0] in_hot;
  assign in_type = rx_flit[DWB-1 -: 3];
  assign in_data = (in_type == T_RDRSP) || (in_type == T_WRDATA) || (in_type == T_PSUM);
  assign in_nw   = in_data ? CW'(rx_flit[DWB-13-6 -: 6]) + CW'(1) : CW'(1);
  assign in_cx   = rx_flit[DWB + 8 +: XW];
  assign in_cy   = rx_flit[DWB + 8 + XW +: YW];
  assign st_free = !q_valid || q_ready;
  assign rx_ready = st_free;
  assign i_take  = rx_valid && st_free;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      q_valid <= 1'b0; q_flit <= '0; q_nw <= '0; q_nwc_n <= '1;
    end else if (st_free) begin
      q_valid <= rx_valid;
      if (rx_valid) begin q_flit <= rx_flit; q_nw <= in_nw; q_nwc_n <= ~in_nw; end
    end
  end
  // the registered beat's fields (rx_*: what the roles act on)
  logic [2:0]      rx_type;
  logic [19:0]     rx_hdr;
  logic [VW-1:0]   rx_words;
  logic [31:0]     rx_word0;
  logic [7:0]      rx_seq;
  logic [XW-1:0]   rx_src_x;
  logic [YW-1:0]   rx_src_y;
  logic [CW-1:0]   rx_nw;                    // words in the beat
  logic [CW-1:0]   rx_nwc;                   // ... the CRC's copy
  logic            rx_fire;                  // the registered beat is consumed
  assign rx_type    = q_flit[DWB-1 -: 3];
  assign rx_hdr     = q_flit[DWB-13 -: 20];
  assign rx_words   = q_flit[VW-1:0];
  assign rx_word0   = rx_words[31:0];
  assign rx_seq     = q_flit[DWB +: 8];
  assign rx_src_x   = q_flit[DWB + 8 +: XW];
  assign rx_src_y   = q_flit[DWB + 8 + XW +: YW];
  assign rx_nw      = q_nw;
  assign rx_nwc     = ~q_nwc_n;
  assign rx_fire    = q_valid && q_ready;
  logic unused_rx_bits;                      // the destination (this node), the tag, seq[7:4], hdr[19:14]
  assign unused_rx_bits = ^{q_flit[FB-1 -: XW + YW], q_flit[DWB-4 -: 9], rx_seq[7:4], rx_hdr[19:14], rx_flit[FB-1 -: XW + YW],
                            rx_flit[DWB-4 -: 9], rx_flit[DWB +: 8], rx_flit[DWB-13 -: 8], rx_flit[DWB-13-6-8 -: 8]};

  // per-source receive state (each source in its own block, as tile_nic keeps it): the write pointer of a drain into the
  // memory window, the CRC and the word count of the stream in progress, written from the write-back register when the
  // source's state leaves st_*; read into st_* through the port beat's one-hot (source s = y * (NX + 1) + x)
  logic [19:0]     wr_ptr  [NSRC];
  logic [15:0]     crc_acc [NSRC];
  logic [15:0]     rx_cnt  [NSRC];
  logic            st_v;                     // st_* holds a source's state (after the first beat)
  logic [XW-1:0]   st_cx;
  logic [YW-1:0]   st_cy;
  logic [NSRC-1:0] st_hot;
  logic [19:0]     st_ptr;
  logic [15:0]     st_crc, st_cnt;
  logic            wb3_v;                    // wb3_* goes back to the registers of source wb3_hot this cycle
  logic [NSRC-1:0] wb3_hot;
  logic [19:0]     wb3_ptr;
  logic [15:0]     wb3_crc, wb3_cnt;
  logic            st_load;                  // the stage takes a beat of another source than st_*'s
  logic            wb3_hit;                  // ... whose state is the one going back this cycle
  logic [19:0]     lk_ptr, u_ptr;            // the port beat's source's state; st_* after this cycle's beat
  logic [15:0]     lk_crc, lk_cnt, u_crc, u_cnt;
  logic [15:0]     rx_crc_next;
  logic            crc_bad, cnt_bad;
  crc16_beat #(.BW(BW)) u_rxcrc (.crc_in(st_crc), .words(rx_words), .n(rx_nwc), .crc_out(rx_crc_next));
  assign crc_bad = (st_crc != rx_word0[15:0]);
  assign cnt_bad = (st_cnt != rx_word0[31:16]);
  assign st_load = i_take && (!st_v || (in_cx != st_cx) || (in_cy != st_cy));
  always_comb begin
    for (int y = 0; y < NY; y++)
      for (int x = 0; x <= NX; x++)
        in_hot[y * (NX + 1) + x] = (in_cy == YW'(y)) && (in_cx == XW'(x));
    wb3_hit = wb3_v && ((wb3_hot & in_hot) != '0);
    lk_ptr = '0; lk_crc = '0; lk_cnt = '0;
    for (int s = 0; s < NSRC; s++) begin
      lk_ptr = lk_ptr | ({20{in_hot[s]}} & wr_ptr[s]);
      lk_crc = lk_crc | ({16{in_hot[s]}} & crc_acc[s]);
      lk_cnt = lk_cnt | ({16{in_hot[s]}} & rx_cnt[s]);
    end
    if (wb3_hit) begin lk_ptr = wb3_ptr; lk_crc = wb3_crc; lk_cnt = wb3_cnt; end
    u_ptr = st_ptr; u_crc = st_crc; u_cnt = st_cnt;
    if (rx_fire) begin
      case (rx_type)
        T_WRHDR:  u_ptr = rx_word0[19:0];
        T_WRDATA: begin                      // a data beat: its words into the stream's CRC and count; the whole beat written
          u_ptr = st_ptr + 20'(rx_hdr[13:8]) + 20'd1;
          u_crc = rx_crc_next; u_cnt = st_cnt + 16'(rx_hdr[13:8]) + 16'd1;
        end
        T_RDRSP, T_PSUM: begin u_crc = rx_crc_next; u_cnt = st_cnt + 16'(rx_hdr[13:8]) + 16'd1; end
        T_CRC:    begin u_crc = 16'hFFFF; u_cnt = '0; end
        default: ;
      endcase
    end
  end
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      st_v <= 1'b0; st_cx <= '0; st_cy <= '0; st_hot <= '0; st_ptr <= '0; st_crc <= 16'hFFFF; st_cnt <= '0;
      wb3_v <= 1'b0; wb3_hot <= '0; wb3_ptr <= '0; wb3_crc <= 16'hFFFF; wb3_cnt <= '0;
    end else begin
      wb3_v <= st_load && st_v;
      if (st_load) begin                       // a beat of another source: its state in, st_* out to wb3_*
        st_v <= 1'b1; st_cx <= in_cx; st_cy <= in_cy; st_hot <= in_hot; st_ptr <= lk_ptr; st_crc <= lk_crc; st_cnt <= lk_cnt;
        wb3_hot <= st_hot; wb3_ptr <= u_ptr; wb3_crc <= u_crc; wb3_cnt <= u_cnt;
      end else begin                           // the same source (or none): in place
        st_ptr <= u_ptr; st_crc <= u_crc; st_cnt <= u_cnt;
      end
    end
  end
  generate
    for (genvar s = 0; s < NSRC; s++) begin : g_src
      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
          crc_acc[s] <= 16'hFFFF; rx_cnt[s] <= '0;
        end else if (wb3_v && wb3_hot[s]) begin
          crc_acc[s] <= wb3_crc; rx_cnt[s] <= wb3_cnt;
        end
      end
      if (MEM != 0) begin : g_wp                        // the drain's write pointer into the window
        always_ff @(posedge clk or negedge rst_n) begin
          if (!rst_n) wr_ptr[s] <= '0;
          else if (wb3_v && wb3_hot[s]) wr_ptr[s] <= wb3_ptr;
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
  logic [CW-1:0]  mt_nd, ht_nd;              // drop 0.44: each role's data beat's count from its registers alone (tx_cnt's adder)
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
  // the CRC one cycle behind the beats (drop 0.44, the NIC's drop-0.42 form): a data beat sent is registered with its
  // count (txc_*) and accumulated in the next cycle, so the beat's assembly -- the R data's lane rotation, the host W
  // beat's shift -- and the CRC tree are two paths; the message's CRC flit waits for the last beat's update (txc_v low).
  // Before, m_rdata through the rotation and the matrix select into tx_crc was 1,392 ps from the pin at 1,250 ps, and
  // the host W beat's count register into tx_crc 1,435 ps register to register (the receive stage above fixed).
  logic          txc_v;
  logic [VW-1:0] txc_words;
  logic [CW-1:0] txc_n;
  crc16_beat #(.BW(BW)) u_txcrc (.crc_in(tx_crc), .words(txc_words), .n(txc_n), .crc_out(tx_crc_next));
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      own_mem <= 1'b0; own_host <= 1'b0; tx_rr <= 1'b0; tx_crc <= 16'hFFFF; tx_cnt <= '0;
      txc_v <= 1'b0; txc_words <= '0; txc_n <= '0;
    end else begin
      if (own_mem && !mem_want) own_mem <= 1'b0;
      else if (own_host && !host_want) own_host <= 1'b0;
      else if (!own_mem && !own_host) begin   // a grant starts a message: its CRC and count from the initial value
        if (mem_want && (!host_want || !tx_rr)) begin own_mem <= 1'b1; tx_rr <= 1'b1; tx_crc <= 16'hFFFF; tx_cnt <= '0; end
        else if (host_want) begin own_host <= 1'b1; tx_rr <= 1'b0; tx_crc <= 16'hFFFF; tx_cnt <= '0; end
      end
      txc_v <= tx_fire && t_data;
      // the count's adder takes the role's data count from registers (the memory role's r_n, the host's wq_n); the late
      // decision of what the flit is (a WRHDR before this beat, or the beat itself) only enables it: the host's 19-bit
      // address compare was in front of the 16-bit add, 1,302 ps to tx_cnt[14] (drop 0.44)
      if (tx_fire && t_data) begin txc_words <= t_words; txc_n <= t_n; tx_cnt <= tx_cnt + 16'(own_mem ? mt_nd : ht_nd); end
      if (txc_v) tx_crc <= tx_crc_next;
    end
  end

  // ------------------------------------------------------------------ RX handshake (the registered beat, drop 0.44)
  logic rdreq_ok;                            // memory role: an RDREQ is taken now (the server is free)
  logic wrdata_ok;                           // memory role: the WRDATA beat in the stage is written (register A is free)
  logic host_rsp;                            // host role: the RDRSP beat in the stage belongs to the read in progress
  logic host_rsp_ok;                         // ... and the read's stage takes it now
  always_comb begin
    case (rx_type)
      T_RDREQ:  q_ready = rdreq_ok;
      T_RDRSP:  q_ready = !host_rsp || host_rsp_ok;
      T_WRDATA: q_ready = wrdata_ok;
      default:  q_ready = 1'b1;
    endcase
  end

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
      // ======== read: the request queue, AR issued ahead, R beats forwarded in order ========
      // An RDREQ is taken off the input into the queue (QD entries) whenever the queue has room and no WRDATA beat is in
      // the write path's two beat registers (so that every write that arrived before it is in a burst it can be ordered
      // after), never blocking the input behind an AXI read. Three pointers walk the queue: q_wp (taken), q_ip (the entry
      // the AR engine works on) and q_rp (the entry whose response is being sent; it leaves at its CRC flit). The AR
      // engine takes the entry's request into registers (IS_IDLE), forms its address (win_addr, IS_ADDR), then issues its
      // bursts (4 KB pages, sized a cycle before AR in IS_ARP) while fewer than QD bursts are outstanding -- so up to QD
      // bursts of up to QD requests are in flight; one AXI ID, so the slave returns them in order and the responses leave
      // in queue order. An entry's AR waits until every write burst that held data received before it has its B (its tag
      // against w_done): a read never overtakes a write to the window that arrived first -- the order B1's blocking input
      // gave for free.
      localparam int QD  = 4;                // the request queue per direction
      localparam int QPW = 2;                // $clog2(QD)
      localparam int PGW = 12 - LBB;         // an AXI beat's index in its 4 KB page
      localparam int SQW = 8;                // write-burst sequence numbers (the read-after-write order)
      localparam int MAW = 40 - LBB;         // a beat-aligned AXI address
      localparam int PBW = $clog2(PB);       // the write buffer's address
      logic [XW-1:0]  q_x     [QD];
      logic [YW-1:0]  q_y     [QD];
      logic [3:0]     q_part  [QD];
      logic [19:0]    q_addr  [QD];
      logic [12:0]    q_words [QD];
      logic [SQW-1:0] q_tag   [QD];
      logic [QPW:0]   q_wp, q_ip, q_rp;      // one bit above the index: full / empty
      logic [QPW-1:0] qi_w, qi_i, qi_r;
      assign qi_w = q_wp[QPW-1:0];
      assign qi_i = q_ip[QPW-1:0];
      assign qi_r = q_rp[QPW-1:0];
      logic [SQW-1:0] w_closed, w_done;      // write bursts closed (queued for AW) / completed (B)
      logic           ob_open, wa_valid, wb_valid;   // the write path's open burst; its two beat registers (below)
      logic           rq_take;
      // (a queue is full when its pointers differ in the bit above the index alone: no subtractor in front of rx_ready)
      assign rdreq_ok = !((q_wp[QPW-1:0] == q_rp[QPW-1:0]) && (q_wp[QPW] != q_rp[QPW])) && !wa_valid && !wb_valid;
      assign rq_take  = rx_fire && (rx_type == T_RDREQ);
      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
          q_wp <= '0;
          for (int e = 0; e < QD; e++) begin
            q_x[e] <= '0; q_y[e] <= '0; q_part[e] <= '0; q_addr[e] <= '0; q_words[e] <= '0; q_tag[e] <= '0;
          end
        end else if (rq_take) begin                    // the requester's coordinates and partition, the request, its tag
          q_wp          <= q_wp + (QPW + 1)'(1);
          q_x[qi_w]     <= XW'(rx_hdr[3:0]);
          q_y[qi_w]     <= YW'(rx_hdr[7:4]);
          q_part[qi_w]  <= rx_seq[3:0];
          q_addr[qi_w]  <= rx_word0[19:0];
          q_words[qi_w] <= 13'(rx_word0[31:20]);
          q_tag[qi_w]   <= w_closed + SQW'(ob_open);  // the open burst is closed because of this request (below)
        end
      end
      // ---- the AR engine ----
      localparam logic [1:0] IS_IDLE = 2'd0, IS_ADDR = 2'd1, IS_ARP = 2'd2, IS_AR = 2'd3;
      logic [1:0]     is_state;
      logic [39:12]   is_page;               // the next burst's 4 KB page ...
      logic [PGW-1:0] is_boff;               // ... and its first beat in the page
      logic [12:0]    is_beats;              // AXI beats of the entry still to request
      logic [PGW:0]   is_nb;                 // beats of the next burst (1 .. PB)
      logic [PGW:0]   is_pleft;              // beats to the end of the page
      logic [19:0]    ie_addr;               // the entry's request, taken from the queue at IS_IDLE (registers: the queue's
      logic [12:0]    ie_words;              // read mux and the address add in different cycles)
      logic [SQW-1:0] ie_tag;
      logic [39:0]    is_a;                  // the entry's byte address
      logic [12:0]    is_rqb;                // ... and the AXI beats it covers
      logic [QPW:0]   ar_out;                // bursts whose AR was taken and whose last R beat was not (<= QD)
      logic           ar_fire, r_fire;
      assign is_a      = win_addr(win_base, ie_addr);
      assign is_rqb    = (13'(ie_addr[LWP-1:0]) + ie_words + 13'(WPB - 1)) >> LWP;   // win_base is 64-byte aligned
      assign is_pleft  = (PGW + 1)'(PB) - (PGW + 1)'(is_boff);
      logic [SQW-1:0] raw_d;                 // w_done - tag: below half the range when the writes before it are done
      assign raw_d     = w_done - ie_tag;
      assign ar_fire   = m_arvalid && m_arready;
      assign m_arvalid = (is_state == IS_AR);
      assign m_araddr  = {is_page, is_boff, LBB'(0)};
      assign m_arlen   = 8'(is_nb - (PGW + 1)'(1));
      assign m_arsize  = AXSIZE;
      assign m_arburst = 2'b01;
      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
          is_state <= IS_IDLE; is_page <= '0; is_boff <= '0; is_beats <= '0; is_nb <= '0; q_ip <= '0;
          ie_addr <= '0; ie_words <= '0; ie_tag <= '0;
        end else begin
          case (is_state)
            IS_IDLE: if (q_ip != q_wp) begin
              is_state <= IS_ADDR;
              ie_addr <= q_addr[qi_i]; ie_words <= q_words[qi_i]; ie_tag <= q_tag[qi_i];
            end
            IS_ADDR: if (!raw_d[SQW-1]) begin         // the writes it follows are done
              is_page  <= is_a[39:12];
              is_boff  <= is_a[11:LBB];
              is_beats <= is_rqb;
              if (ie_words == 13'd0) begin q_ip <= q_ip + (QPW + 1)'(1); is_state <= IS_IDLE; end   // the CRC flit alone
              else is_state <= IS_ARP;
            end
            IS_ARP: if (ar_out != (QPW + 1)'(QD)) begin
              is_nb    <= (is_beats < 13'(is_pleft)) ? (PGW + 1)'(is_beats) : is_pleft;
              is_state <= IS_AR;
            end
            IS_AR: if (m_arready) begin
              is_beats <= is_beats - 13'(is_nb);
              if (is_nb == is_pleft) begin is_page <= is_page + 28'd1; is_boff <= '0; end    // the burst ends the page
              else is_boff <= is_boff + PGW'(is_nb);
              if (is_beats == 13'(is_nb)) begin q_ip <= q_ip + (QPW + 1)'(1); is_state <= IS_IDLE; end
              else is_state <= IS_ARP;
            end
            default: is_state <= IS_IDLE;
          endcase
        end
      end
      // ---- the response side: the entry at q_rp, its R beats as RDRSP beats, then its CRC flit (inverted after an error) ----
      logic           rs_active;             // the entry at q_rp is the response in progress
      logic [LWP-1:0] r_lo;                  // the next R beat's first word of the request (its lane): a register
      logic [CW-1:0]  r_n;                   // the words of the request it carries: a register
      logic [12:0]    r_words;               // words still to send
      logic           r_err;                 // an R response of this request was not OKAY
      logic [12:0]    r_rem;                 // words left after the R beat
      logic [CW-1:0]  r_n_first;             // the first R beat's words of the entry at q_rp
      logic           rs_crc;                // its data is sent: the CRC flit is next
      logic           crc_fire;
      assign r_rem     = r_words - 13'(r_n);
      assign r_n_first = ((13'(WPB) - 13'(q_addr[qi_r][LWP-1:0])) < q_words[qi_r]) ? CW'(WPB) - CW'(q_addr[qi_r][LWP-1:0])
                                                                                       : CW'(q_words[qi_r]);
      assign rs_crc    = rs_active && (r_words == 13'd0);
      assign r_fire    = m_rvalid && m_rready;
      assign m_rready  = own_mem && rs_active && !rs_crc && tx_ready;
      assign crc_fire  = own_mem && rs_crc && tx_ready && !txc_v;          // (the last beat's CRC in: drop 0.44)
      // the output is asked for when there is a flit to send and held to the CRC flit (released at the message's end)
      assign mem_want  = rs_active && (own_mem || m_rvalid || rs_crc);
      assign mt_valid  = own_mem && rs_active && (rs_crc ? !txc_v : m_rvalid);
      assign mt_dx     = q_x[qi_r];
      assign mt_dy     = q_y[qi_r];
      assign mt_part   = q_part[qi_r];
      assign mt_type   = rs_crc ? T_CRC : T_RDRSP;
      assign mt_nd     = r_n;
      assign mt_hdr    = rs_crc ? 20'd0 : {6'd0, 6'(r_n - CW'(1)), 8'd0};
      assign mt_words  = rs_crc ? VW'({tx_cnt, tx_crc ^ {16{r_err}}}) : VW'(m_rdata) >> (32 * r_lo);
      assign mt_n      = rs_crc ? CW'(1) : r_n;
      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
          rs_active <= 1'b0; r_lo <= '0; r_n <= '0; r_words <= '0; r_err <= 1'b0; q_rp <= '0; ar_out <= '0;
        end else begin
          // the next entry's response (its first beat's lane and words) once the output is released: a message per grant, so
          // the CRC and count of the next one start from their initial values
          if (!rs_active && !own_mem && (q_rp != q_wp)) begin
            rs_active <= 1'b1;
            r_lo      <= q_addr[qi_r][LWP-1:0];
            r_n       <= r_n_first;
            r_words   <= q_words[qi_r];
            r_err     <= 1'b0;
          end
          if (r_fire) begin
            r_words <= r_rem;
            r_lo    <= '0;                           // the beats after the first start at lane 0
            r_n     <= (r_rem < 13'(WPB)) ? CW'(r_rem) : CW'(WPB);
            if (m_rresp != R_OKAY) r_err <= 1'b1;
          end
          if (crc_fire) begin rs_active <= 1'b0; q_rp <= q_rp + (QPW + 1)'(1); end
          ar_out <= ar_out + (QPW + 1)'(ar_fire) - (QPW + 1)'(r_fire && m_rlast);
        end
      end
      assign ev_rd_wait = (ar_out != '0) && !r_fire;

      // ======== write: posted, combined into bursts of up to 4 KB ========
      // A WRDATA beat is taken off the input into register A (wa_*) with its source's write pointer; A hands it to register
      // B (wb_*) with its first AXI beat's address formed on the way (win_addr from A's pointer, registered); from B its AXI
      // beats (1 .. 5 by alignment) are placed one per cycle, B's address counting up: a beat at the address of the open
      // burst's last beat (the same source: a drain continuing inside a beat) merges into the staging beat; the next
      // address in the same 4 KB page (the same source) appends -- the staging beat goes to the write buffer and the new
      // one takes its place; anything else closes the open burst (the staging beat to the buffer, {address, length} to the
      // AW queue) and opens a new one. A moves into B as B places its last beat, so a beat per AXI beat flows. The
      // decisions compare registers only (B's address against the burst's last and next addresses). A burst also closes
      // when its source's CRC flit arrives (the message's end), when an RDREQ is taken (so the read can be ordered after
      // it), and after 15 idle cycles. The AW engine takes the queue's head: AW from registers, its W beats from the
      // buffer (a neo_mem_rf of PB beats: 4 KB of data with word strobes), at most QD bursts awaiting B; B is counted
      // (w_done) and checked (ev_axi), nothing waits for it at the input. A drain is in memory some cycles after its CRC
      // flit was taken, not when (B1 held each beat at the input until its B): wr_busy tells top_ctrl while anything is
      // buffered or outstanding.
      localparam int IDLE_CLOSE = 15;
      // register A
      logic [VW-1:0]      wa_words;
      logic [CW-1:0]      wa_nw;
      logic [19:0]        wa_ptr;
      logic [XW+YW-1:0]   wa_src;
      logic               wb_take, wa_move;
      logic [39:0]        wa_addr;           // A's first word's byte address (formed between A and B)
      logic [2*BW-1:0]    wa_therm;          // A's word count as a mask from word 0
      assign wa_addr = win_addr(win_base, wa_ptr);
      always_comb for (int i = 0; i < 2 * BW; i++) wa_therm[i] = (6'(i) < 6'(wa_nw));
      // register B
      logic [VW-1:0]      wb_words;
      logic [LWP-1:0]     wb_off;            // its first word's lane
      logic [2*BW-1:0]    wb_sm;             // its word strobes at their lanes
      logic [2:0]         wb_k;              // its AXI beats
      logic [2:0]         wb_j;              // the next one
      logic [MAW-1:0]     wb_a;              // ... and its address
      logic [XW+YW-1:0]   wb_src;
      logic               wb_step, wb_last;
      logic [2*VW-1:0]    w_vec;             // B's words at their lanes
      logic [AXI_DW-1:0]  d_j, d_jm;         // AXI beat wb_j's data, masked to its strobes
      logic [WPB-1:0]     s_j;               // ... and its word strobes
      assign w_vec   = {VW'(0), wb_words} << (32 * wb_off);
      assign d_j     = w_vec[AXI_DW * wb_j +: AXI_DW];
      assign s_j     = wb_sm[WPB * wb_j +: WPB];
      for (genvar b = 0; b < WPB; b++) begin : g_dm
        assign d_jm[32 * b +: 32] = s_j[b] ? d_j[32 * b +: 32] : 32'd0;
      end
      // the open burst and its staging beat
      logic [MAW-1:0]     ob_start, ob_tail, ob_next;   // its first and its last AXI beat, the one after the last
      logic [8:0]         ob_len;            // its beats (1 .. PB)
      logic [XW+YW-1:0]   ob_src;
      logic [AXI_DW-1:0]  st_data;           // the staging beat (the burst's last, still open to a merge)
      logic [WPB-1:0]     st_strb;
      logic               close_pend;        // a close asked for (CRC flit, RDREQ, idle)
      logic [3:0]         ob_idle;
      logic               j_same, j_merge, j_append, room, do_close;
      assign j_same   = ob_open && (ob_src == wb_src);
      assign j_merge  = j_same && (wb_a == ob_tail);
      assign j_append = j_same && (wb_a == ob_next) && (wb_a[PGW-1:0] != '0);
      // the write buffer and the AW queue
      logic [PBW:0]       f_wp, f_rp;        // the buffer's pointers (one bit above the address)
      logic [MAW-1:0]     aq_addr [QD];
      logic [8:0]         aq_len  [QD];
      logic [QPW:0]       aq_wp, aq_rp;
      logic               f_we;
      logic [AXI_DW+WPB-1:0] f_wdata, f_rdata;
      assign room     = !((f_wp[PBW-1:0] == f_rp[PBW-1:0]) && (f_wp[PBW] != f_rp[PBW])) &&       // the buffer, the AW
                        !((aq_wp[QPW-1:0] == aq_rp[QPW-1:0]) && (aq_wp[QPW] != aq_rp[QPW]));     // queue: not full
      assign wb_step  = wb_valid && room;           // a beat in B goes first; a pending close waits for both registers
      assign wb_last  = (wb_j == wb_k - 3'd1);
      assign wa_move  = wa_valid && (!wb_valid || (wb_step && wb_last));
      assign do_close = close_pend && !wa_valid && !wb_valid && room;
      assign wb_take  = rx_fire && (rx_type == T_WRDATA);
      assign wrdata_ok = !wa_valid || wa_move;
      // the buffer takes the staging beat on an append, on a close and when a new burst replaces an open one
      assign f_we     = (wb_step && !j_merge && ob_open) || do_close;
      assign f_wdata  = {st_strb, st_data};
      logic [AXI_DW+WPB-1:0] unused_wbuf_rb, unused_wbuf_bd;   // the leaf's unused read ports (RB = 0, no hooks)
      logic [PBW-1:0]        unused_wbuf_bq;                   // its port-B address latch output (D2's rb_addr_q: 0 at RBQ = 0)
      neo_mem_rf #(.W(AXI_DW + WPB), .D(PB), .RB(0), .DV_HOOKS(0)) u_wbuf (
        .clk(clk), .we(f_we), .waddr(f_wp[PBW-1:0]), .wdata(f_wdata), .ra_addr(f_rp[PBW-1:0]), .ra_data(f_rdata),
        .rb_en(1'b0), .rb_addr('0), .rb_data(unused_wbuf_rb), .rb_addr_q(unused_wbuf_bq),
        .bd_we(1'b0), .bd_addr('0), .bd_wdata('0), .bd_rdata(unused_wbuf_bd), .fi_en(1'b0), .fi_or(1'b0), .fi_addr('0), .fi_mask('0));
      logic aq_push;                         // a burst closes: its {address, length} to the AW queue
      assign aq_push  = (wb_step && !j_merge && !j_append && ob_open) || do_close;
      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
          wa_valid <= 1'b0; wa_words <= '0; wa_nw <= '0; wa_ptr <= '0; wa_src <= '0;
          wb_valid <= 1'b0; wb_words <= '0; wb_off <= '0; wb_sm <= '0; wb_k <= '0; wb_j <= '0; wb_a <= '0; wb_src <= '0;
          ob_open <= 1'b0; ob_start <= '0; ob_tail <= '0; ob_next <= '0; ob_len <= '0; ob_src <= '0; st_data <= '0;
          st_strb <= '0; close_pend <= 1'b0; ob_idle <= '0; f_wp <= '0; aq_wp <= '0; w_closed <= '0;
          for (int e = 0; e < QD; e++) begin aq_addr[e] <= '0; aq_len[e] <= '0; end
        end else begin
          // register A: from the input; it empties into B
          if (wb_take) begin
            wa_valid <= 1'b1; wa_words <= rx_words; wa_nw <= rx_nw; wa_ptr <= st_ptr; wa_src <= {rx_src_y, rx_src_x};
          end else if (wa_move) begin
            wa_valid <= 1'b0;
          end
          // register B: a beat placed per step; refilled from A as its last beat goes
          if (wb_step) begin wb_j <= wb_j + 3'd1; wb_a <= wb_a + MAW'(1); end
          if (wb_step && wb_last) wb_valid <= 1'b0;
          if (wa_move) begin
            wb_valid <= 1'b1; wb_words <= wa_words; wb_off <= wa_ptr[LWP-1:0]; wb_sm <= wa_therm << wa_ptr[LWP-1:0];
            wb_k <= 3'((7'(wa_ptr[LWP-1:0]) + 7'(wa_nw) + 7'(WPB - 1)) >> LWP);      // win_base is 64-byte aligned
            wb_j <= 3'd0; wb_a <= wa_addr[39:LBB]; wb_src <= wa_src;
          end
          // an AXI beat placed: merged, appended, or the start of a new burst
          if (wb_step) begin
            ob_idle <= '0;
            if (j_merge) begin
              st_data <= st_data | d_jm; st_strb <= st_strb | s_j;
            end else if (j_append) begin
              st_data <= d_jm; st_strb <= s_j; ob_tail <= wb_a; ob_next <= wb_a + MAW'(1); ob_len <= ob_len + 9'd1;
            end else begin
              st_data <= d_jm; st_strb <= s_j; ob_open <= 1'b1; ob_start <= wb_a; ob_tail <= wb_a; ob_next <= wb_a + MAW'(1);
              ob_len <= 9'd1; ob_src <= wb_src;
            end
          end else if (ob_open && !wa_valid && !wb_valid && !close_pend) begin
            ob_idle <= ob_idle + 4'd1;
          end
          if (f_we) f_wp <= f_wp + (PBW + 1)'(1);
          if (aq_push) begin
            aq_addr[aq_wp[QPW-1:0]] <= ob_start; aq_len[aq_wp[QPW-1:0]] <= ob_len;
            aq_wp <= aq_wp + (QPW + 1)'(1); w_closed <= w_closed + SQW'(1);
          end
          // closing without a new burst
          if (ob_open && ((rx_fire && (rx_type == T_CRC) && ({rx_src_y, rx_src_x} == ob_src)) || rq_take ||
                          (ob_idle == 4'(IDLE_CLOSE)))) close_pend <= 1'b1;
          if (do_close) begin close_pend <= 1'b0; ob_open <= 1'b0; ob_idle <= '0; end
        end
      end
      // ---- the AW engine: the AW queue's head, its W beats from the buffer, B counted ----
      logic           aw_sent;               // the head burst's AW was taken
      logic [8:0]     w_cnt;                 // ... W beats sent
      logic [QPW:0]   b_out;                 // bursts whose AW was taken and whose B was not (<= QD)
      logic           aq_ne, w_fire, aw_fire, b_fire, w_end;
      logic [QPW-1:0] aqi;
      assign aqi       = aq_rp[QPW-1:0];
      assign aq_ne     = (aq_rp != aq_wp);
      assign m_awvalid = aq_ne && !aw_sent && (b_out != (QPW + 1)'(QD));
      assign m_awaddr  = {aq_addr[aqi], LBB'(0)};
      assign m_awlen   = 8'(aq_len[aqi] - 9'd1);
      assign m_awsize  = AXSIZE;
      assign m_awburst = 2'b01;
      assign m_wvalid  = aq_ne && (w_cnt != aq_len[aqi]);
      assign m_wdata   = f_rdata[AXI_DW-1:0];
      for (genvar b = 0; b < WPB; b++) begin : g_strb
        assign m_wstrb[4 * b +: 4] = {4{f_rdata[AXI_DW + b]}};
      end
      assign m_wlast   = (w_cnt == aq_len[aqi] - 9'd1);
      assign m_bready  = 1'b1;
      assign aw_fire   = m_awvalid && m_awready;
      assign w_fire    = m_wvalid && m_wready;
      assign b_fire    = m_bvalid;
      assign w_end     = (aw_sent || aw_fire) && ((w_fire && m_wlast) || (w_cnt == aq_len[aqi]));   // the head burst is out
      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
          aw_sent <= 1'b0; w_cnt <= '0; b_out <= '0; aq_rp <= '0; f_rp <= '0; w_done <= '0;
        end else begin
          if (aw_fire) aw_sent <= 1'b1;
          if (w_fire) begin w_cnt <= w_cnt + 9'd1; f_rp <= f_rp + (PBW + 1)'(1); end
          if (w_end) begin aw_sent <= 1'b0; w_cnt <= '0; aq_rp <= aq_rp + (QPW + 1)'(1); end
          b_out  <= b_out + (QPW + 1)'(aw_fire) - (QPW + 1)'(b_fire);
          if (b_fire) w_done <= w_done + SQW'(1);
        end
      end
      assign m_ev_axi   = (r_fire && (m_rresp != R_OKAY)) || (b_fire && (m_bresp != R_OKAY));
      assign ev_wr_wait = (b_out != '0) && !w_fire;
      assign wr_busy    = wa_valid || wb_valid || ob_open || aq_ne || (b_out != '0);
      logic unused_mem_bits;                 // the byte addresses below a beat: the lanes come from the word addresses
      assign unused_mem_bits = ^{is_a[LBB-1:0], wa_addr[LBB-1:0], unused_wbuf_rb, unused_wbuf_bd, unused_wbuf_bq};
    end else begin : g_nomem
      assign rdreq_ok = 1'b1;                // no memory role: an RDREQ is consumed and dropped (the requester times out)
      assign wrdata_ok = 1'b1;
      assign mem_want = 1'b0;
      assign m_ev_axi = 1'b0;
      assign ev_rd_wait = 1'b0; assign ev_wr_wait = 1'b0; assign wr_busy = 1'b0;
      assign mt_valid = 1'b0; assign mt_dx = '0; assign mt_dy = '0; assign mt_part = '0; assign mt_type = '0;
      assign mt_hdr = '0; assign mt_words = '0; assign mt_n = '0; assign mt_nd = '0;
      assign m_awaddr = '0; assign m_awlen = '0; assign m_awsize = '0; assign m_awburst = '0; assign m_awvalid = 1'b0;
      assign m_wdata = '0; assign m_wstrb = '0; assign m_wlast = 1'b0; assign m_wvalid = 1'b0; assign m_bready = 1'b0;
      assign m_araddr = '0; assign m_arlen = '0; assign m_arsize = '0; assign m_arburst = '0; assign m_arvalid = 1'b0;
      assign m_rready = 1'b0;
      logic unused_mem;
      assign unused_mem = ^{m_awready, m_wready, m_bresp, m_bvalid, m_arready, m_rdata, m_rresp, m_rlast, m_rvalid, win_base,
                            rx_hdr[7:0], rx_seq[3:0], st_ptr};
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
      logic [BAW-1:0]      h_bword;          // lane 0's bank word address: the read's first beat; the next W beat to take
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
      // ---- write beats: each W beat is taken into the register wq with its strobes decoded on the way in, and its flits
      // are sent from wq (so the W inputs reach only the decode and wq: the flits, the CRC and the handshakes start at
      // registers) ----
      logic [WPB-1:0]    ws, wp, w_st, w_en; // whole-word strobes, partial-word strobes, run starts, run ends
      logic [LWP-1:0]    w_first, w_lastw;   // the run's first and last word (one-hot encodes: exact for one run)
      logic              w_any, w_multi;     // a run; more than one run
      always_comb begin
        for (int b = 0; b < WPB; b++) begin
          ws[b] = &s_wstrb[4 * b +: 4];
          wp[b] = (|s_wstrb[4 * b +: 4]) && !ws[b];
        end
        w_st = ws & ~(ws << 1);
        w_en = ws & ~(ws >> 1);
        w_first = '0; w_lastw = '0; w_any = 1'b0; w_multi = 1'b0;
        for (int b = 0; b < WPB; b++) begin
          if (w_st[b]) w_first = w_first | LWP'(b);
          if (w_en[b]) w_lastw = w_lastw | LWP'(b);
          w_multi = w_multi || (w_any && w_st[b]);
          w_any   = w_any || w_st[b];
        end
      end
      logic              wq_valid;           // wq holds a W beat of the burst
      logic [AXI_DW-1:0] wq_data;            // its run's words from word 0 (shifted down on the way in)
      logic [CW-1:0]     wq_n;               // its run's words (0: no whole word strobed)
      logic              wq_bad;             // a partial-word strobe or two runs: not written, the burst answers SLVERR
      logic [8:0]        h_wcap;             // W beats of the burst still to take into wq
      logic              wq_flit, w_need_hdr, wq_take, w_fire;
      logic [BAW-1:0]    wq_addr;            // its run's bank word address
      assign wq_flit    = (wq_n != '0) && !wq_bad;
      assign w_need_hdr = !h_hdr || (wq_addr != h_next);
      // the beat in wq is done with when it has nothing to send, or when its WRDATA leaves; wq takes the next beat then
      assign wq_take    = (h_state == H_W) && wq_valid && own_host && (!wq_flit || (tx_ready && !(w_need_hdr && !h_hdr_now)));
      assign s_wready   = ((h_state == H_W) && (h_wcap != 9'd0) && (!wq_valid || wq_take)) || (h_state == H_WSKIP);
      assign w_fire     = s_wvalid && s_wready;
      // the WRDATA / WRHDR / CRC / RDREQ flits
      always_comb begin
        ht_valid = 1'b0; ht_type = T_WRDATA; ht_hdr = '0; ht_words = '0; ht_n = CW'(1);
        case (h_state)
          H_W: if (wq_valid && wq_flit && own_host) begin
            ht_valid = 1'b1;
            if (w_need_hdr && !h_hdr_now) begin ht_type = T_WRHDR; ht_words = VW'(32'(wq_addr)); end
            else begin ht_type = T_WRDATA; ht_hdr = {6'd0, 6'(wq_n - CW'(1)), 8'd0}; ht_words = VW'(wq_data); ht_n = wq_n; end
          end
          H_WCRC: if (own_host && h_hdr && !txc_v) begin ht_valid = 1'b1; ht_type = T_CRC; ht_words = VW'({tx_cnt, tx_crc}); end
          H_RREQ: if (own_host) begin
            ht_valid = 1'b1; ht_type = T_RDREQ; ht_hdr = {12'd0, 4'(my_y), 4'(my_x)};
            ht_words = VW'({12'(h_nwreq), 20'(h_bword)});
          end
          default: ;
        endcase
      end
      assign ht_dx = h_x;
      assign ht_dy = h_y;
      assign ht_nd = wq_n;
      assign host_want = (h_state == H_W) || (h_state == H_WCRC) || (h_state == H_RREQ);
      assign s_bvalid = (h_state == H_B);
      assign s_bid    = h_id;
      assign s_bresp  = h_resp;
      // ---- read: the stage of RDRSP words, R beats out ----
      // An RDRSP beat is taken while the stage has room for a whole beat whatever the R side does in the cycle (h_cnt <=
      // SGW - BW: registers only, so rx_ready does not wait on RREADY); it lands above the h_cnt words held, and an R beat
      // leaving in the same cycle shifts the merged stage down by its WPB words. A read starts at a whole AXI beat, so at
      // 512 bits the responses come as whole rows and the stage passes one beat per cycle.
      logic           r_fire;
      logic [32*SGW-1:0] merged, rx_in;
      logic [5:0]     cnt_m;                 // words after this cycle's RDRSP beat
      logic [VW-1:0]  rx_masked;
      logic           rsp_src;               // the beat at the input comes from the read's tile
      logic           rsp_fire, crc_fire;    // an RDRSP / the CRC flit of the read's response taken
      assign s_rvalid  = (h_state == H_R) && (h_final || ((h_beats > 9'd1) && (h_cnt >= 6'(WPB))));
      assign s_rdata   = h_stage[AXI_DW-1:0];
      assign s_rresp   = (h_final && !h_ok) ? h_resp : R_OKAY;
      assign s_rlast   = (h_beats == 9'd1);
      assign s_rid     = h_id;
      assign r_fire    = s_rvalid && s_rready;
      always_comb begin
        for (int i = 0; i < BW; i++) rx_masked[32 * i +: 32] = (CW'(i) < rx_nw) ? rx_words[32 * i +: 32] : 32'd0;
      end
      assign rx_in       = {VW'(0), rx_masked} << (32 * h_cnt[4:0]);
      assign merged      = rsp_fire ? (h_stage | rx_in) : h_stage;
      assign cnt_m       = rsp_fire ? h_cnt + 6'(rx_nw) : h_cnt;
      assign rsp_src     = (rx_src_x == h_x) && (rx_src_y == h_y);
      assign host_rsp    = h_wait && (rx_type == T_RDRSP) && rsp_src;
      assign host_rsp_ok = (h_cnt <= 6'(SGW - BW));
      assign rsp_fire = rx_fire && host_rsp;
      assign crc_fire = rx_fire && h_wait && (rx_type == T_CRC) && rsp_src;
      assign h_ev_timeout = h_wait && (h_timer == HTW'(HOST_TIMEOUT - 1)) && !crc_fire;
      assign h_ev_host = ((aw_take || ar_take) && !(d_inmesh && d_sup && (!ar_take || d_nw <= 13'd4095))) || (wq_take && wq_bad);
      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
          h_state <= H_IDLE; h_id <= '0; h_x <= '0; h_y <= '0; h_bword <= '0; h_beats <= '0; h_resp <= R_OKAY;
          h_hdr <= 1'b0; h_hdr_now <= 1'b0; h_next <= '0; h_nwreq <= '0; h_got <= '0; h_wait <= 1'b0; h_final <= 1'b0;
          h_ok <= 1'b0; h_timer <= '0; h_stage <= '0; h_cnt <= '0;
          wq_valid <= 1'b0; wq_data <= '0; wq_addr <= '0; wq_n <= '0; wq_bad <= 1'b0; h_wcap <= '0;
        end else begin
          case (h_state)
            H_IDLE: if (aw_take || ar_take) begin
              h_id    <= aw_take ? s_awid : s_arid;
              h_x     <= XW'(32'(d_node) % NX);
              h_y     <= YW'(32'(d_node) / NX);
              h_bword <= {d_addr[BAW+1:2+LWP], LWP'(0)};
              h_beats <= d_beats;
              h_hdr   <= 1'b0; h_hdr_now <= 1'b0;
              h_nwreq <= d_nw;
              h_got   <= '0; h_final <= 1'b0; h_ok <= 1'b0; h_wait <= 1'b0; h_timer <= '0;
              h_resp  <= !d_inmesh ? R_DECERR : (!d_sup ? R_SLVERR : R_OKAY);
              if (aw_take) h_state <= (d_inmesh && d_sup) ? H_W : H_WSKIP;
              else if (d_inmesh && d_sup && (d_nw <= 13'd4095)) h_state <= H_RREQ;
              else begin                       // a read that cannot be sent: its beats with the error
                h_state <= H_R; h_final <= 1'b1; h_ok <= 1'b0; if (d_inmesh && d_sup) h_resp <= R_SLVERR;
              end
            end
            H_W: begin
              if (own_host && wq_valid && wq_flit && w_need_hdr && !h_hdr_now && tx_ready) begin
                h_hdr_now <= 1'b1; h_hdr <= 1'b1; h_next <= wq_addr;
              end
              if (wq_take) begin
                h_hdr_now <= 1'b0;
                if (wq_flit) h_next <= wq_addr + BAW'(wq_n);
                if (wq_bad) h_resp <= R_SLVERR;
                h_beats <= h_beats - 9'd1;
                if (h_beats == 9'd1) h_state <= H_WCRC;
              end
            end
            H_WCRC: if (own_host && (!h_hdr || (tx_ready && !txc_v))) h_state <= H_B;
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
          // wq: the burst's W beats one at a time (h_wcap of them), each decoded on the way in
          if (aw_take || ar_take) begin
            wq_valid <= 1'b0; h_wcap <= d_beats;
          end else if ((h_state == H_W) && w_fire) begin
            wq_valid <= 1'b1; wq_data <= s_wdata >> (32 * w_first); wq_bad <= (|wp) || w_multi;
            wq_addr  <= {h_bword[BAW-1:LWP], w_first};       // lane 0 of the beat is WPB-aligned
            h_bword  <= {h_bword[BAW-1:LWP] + (BAW - LWP)'(1), LWP'(0)};
            wq_n     <= w_any ? CW'(w_lastw) - CW'(w_first) + CW'(1) : '0;
            h_wcap   <= h_wcap - 9'd1;
          end else if (wq_take) begin
            wq_valid <= 1'b0;
          end
          // the stage: an RDRSP beat in, an R beat out, both in a cycle when they meet; empty for a new transaction
          if (aw_take || ar_take) begin
            h_stage <= '0; h_cnt <= '0;
          end else begin
            h_stage <= r_fire ? (merged >> (32 * WPB)) : merged;
            h_cnt   <= !r_fire ? cnt_m : ((cnt_m >= 6'(WPB)) ? cnt_m - 6'(WPB) : 6'd0);
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
      assign ht_n = '0; assign ht_nd = '0;
      assign s_awready = 1'b0; assign s_wready = 1'b0; assign s_bid = '0; assign s_bresp = '0; assign s_bvalid = 1'b0;
      assign s_arready = 1'b0; assign s_rid = '0; assign s_rdata = '0; assign s_rresp = '0; assign s_rlast = 1'b0;
      assign s_rvalid = 1'b0;
      logic unused_host;
      assign unused_host = ^{s_awid, s_awaddr, s_awlen, s_awsize, s_awburst, s_awvalid, s_wdata, s_wstrb, s_wlast, s_wvalid, s_bready,
                             s_arid, s_araddr, s_arlen, s_arsize, s_arburst, s_arvalid, s_rready, partition};
    end
  endgenerate
endmodule
