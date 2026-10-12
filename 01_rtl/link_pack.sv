/* verilator lint_off DECLFILENAME */        // two modules, link_packer and link_unpacker, in one file by design
// link_pack.sv -- wide links (drop 0.25): WPF words per flit on the router side. Since drop 0.29 the tile interface moves
//   beats of up to BW = 16 words (one bank row) on its side, so the two modules here re-block between the two widths:
//   link_packer takes beats and emits link flits of up to WPF words, link_unpacker takes link flits and presents beats.
//   Both payloads have the same shape, {type[2:0], tag[8:0], hdr[19:0], words[32*W-1:0]} with word i at [32*i +: 32];
//   hdr[13:8] = number of words carried - 1 on the data types (RDRSP, WRDATA, PSUM; 1..W words), the other types (RDREQ,
//   WRHDR, CRC, RDY) carry their one word in word 0 with hdr[13:8] = 0; on PSUM hdr[7:0] is the column of word 0 and word
//   i carries column hdr[7:0] + i. The bits above the payload (seq/partition, src, dst) are copied unchanged; even parity is
//   recomputed over the flit each side sees.
//   Packer, WPF >= BW (32 on the silicon links): consecutive data beats of the same stream (type, head, tag, hdr[19:14],
//   PSUM columns consecutive) are merged into one flit while they fit; the pending flit goes out when it is full or when
//   the next beat cannot join it, and a beat that cannot join starts the next flit in the same cycle the pending one
//   leaves, so a 32-word flit leaves every second cycle at 16 words per beat. A data stream always ends with a CRC flit,
//   which flushes the last partial flit; a beat is never split across two flits, so flits of 17..31 words occur after a
//   partial first beat (a serve that starts inside a row) -- the receiver counts words, not flits.
//   Drop 0.42: the link side registered (it was combinational from core_flit: same / fits -> emit_pend -> the 1,077-bit
//   output mux and its parity, 1.04 ns input to output on the ASAP7 SS proxy). link_valid / link_flit are registers,
//   loaded whenever the stage is empty or draining (oready = !link_valid || link_ready); the pending logic sees oready
//   where it saw link_ready. A data beat is taken when nothing is pending or the stage can take the pending flit
//   (!pend_valid || oready: a beat that would merge waits while the stage holds an unaccepted flit), a single-word beat
//   when nothing is pending and oready. The stage's next flit is the pending one when a flit is pending, else the passing
//   single-word one (selected by the pend_valid register). So that the merge decision (the beat's fields compared with
//   the pending flit's) steers no data, the pending flit's words live in one of two buffers: a taken beat is written
//   both where it goes if it merges and, at word 0 of the other buffer, where it goes if it starts the next flit; the
//   decision picks the buffer and the counts (g_merge below). The parity of the pending flit is kept as it is built: the
//   fixed fields' parity when the flit starts, the word parities of each beat (one per word, 32-bit reductions on the
//   beat) registered as it joins and folded into a running bit when the next beat joins, so the emitted parity is a
//   reduction of 24 registered bits instead of 1,077. Throughput is unchanged (a 32-word flit every second cycle at 16
//   words per beat, back-to-back flits one per cycle while link_ready is high); latency +1 cycle.
//   Packer, WPF < BW (the 16x8 and 8x8-core builds, WPF = 1): a beat is sent as ceil(n / WPF) flits of up to WPF words, so
//   the link sees the same single-word flits, in the same order, as before drop 0.29.
//   Unpacker: a flit of n words is presented as ceil(n / BW) beats of up to BW words (two per 32-word flit; one, as it is,
//   when WPF <= BW); the interface holds a beat until it has consumed it.
module link_packer #(
  parameter int XW = 2, YW = 2, BW = 16, WPF = 1,
  parameter int DWB = 32 + 32 * BW,                        // beat payload (interface side)
  parameter int DWL = 32 + 32 * WPF,                       // link payload (router side)
  parameter int FWB = 1 + 2 * (XW + YW) + 8 + DWB,
  parameter int FWL = 1 + 2 * (XW + YW) + 8 + DWL,
  parameter int HW  = 2 * (XW + YW) + 8                    // head: seq/partition, src, dst
)(
  input  logic           clk,
  input  logic           rst_n,
  input  logic           core_valid,
  input  logic [FWB-1:0] core_flit,
  output logic           core_ready,
  output logic           link_valid,
  output logic [FWL-1:0] link_flit,
  input  logic           link_ready
);
  localparam logic [2:0] T_RDRSP = 3'd2, T_WRDATA = 3'd4, T_PSUM = 3'd5;
  localparam int CWB = $clog2(BW + 1);                     // beat word count 0..BW
  localparam int CWL = $clog2(WPF + 1);                    // flit word count 0..WPF
  localparam int CWR = CWL + 1;                            // the room left in a flit, WPF - count (drop 0.42)
  localparam int BWL = $clog2(BW);                         // the low bits of a beat's count field (drop 0.42)
  localparam int VWB = 32 * BW;
  localparam int VWL = 32 * WPF;
  logic [HW-1:0]  in_head;
  logic [2:0]     in_type;
  logic [8:0]     in_tag;
  logic [19:0]    in_hdr;
  logic [VWB-1:0] in_words;
  logic           in_data;
  assign in_head  = core_flit[DWB +: HW];
  assign in_type  = core_flit[DWB-1 -: 3];
  assign in_tag   = core_flit[DWB-4 -: 9];
  assign in_hdr   = core_flit[DWB-13 -: 20];
  assign in_words = core_flit[VWB-1:0];
  assign in_data  = (in_type == T_RDRSP) || (in_type == T_WRDATA) || (in_type == T_PSUM);
  logic unused_core_parity;                                // the flit's parity is the router's business (lint)
  assign unused_core_parity = core_flit[FWB-1];

  generate
    if (WPF >= BW) begin : g_merge
      // The pending flit lives in one of two word buffers (cur). A data beat that is taken is written, unconditionally,
      // into the pending buffer at word pend_cnt (where it goes if it merges) AND into the other buffer at word 0 (where
      // it goes if it starts the next flit); the merge decision then only picks the buffer (cur flips on a start) and the
      // counts. The write that turns out wrong lands in a buffer that is free (the other one) or whose flit leaves in this
      // very cycle (the pending one, read from its registers into the output stage), at words the flit does not hold; the
      // emitted flit shows words below pend_cnt only (zeros above), so stale words never reach the link. The fields that
      // a merging beat repeats (head, type, tag, hdr[19:14]) are loaded on every taken beat.
      logic           pend_valid;
      logic           cur;                                  // the buffer holding the pending flit
      logic [VWL-1:0] pbuf0, pbuf1;                         // the two flit buffers, word i at [32*i +: 32]
      logic [CWL-1:0] pend_cnt;                             // words held, 1..WPF while pend_valid (the merge position)
      logic [WPF:0]   pend_thr;                             // pend_cnt as a thermometer: bit s = (s < pend_cnt)
      logic [5:0]     pend_cm1;                             // pend_cnt - 1: the flit's hdr[13:8] (the counts' arithmetic)
      logic [CWR-1:0] pend_room;                            // WPF - pend_cnt
      logic [HW-1:0]  pend_head;
      logic [2:0]     pend_type;
      logic [8:0]     pend_tag;
      logic [5:0]     pend_hhi;                             // hdr[19:14]
      logic [7:0]     pend_col0;                            // hdr[7:0] of word 0 (PSUM: its column)
      logic [7:0]     pend_ncol;                            // PSUM: the column the next beat continues at
      logic           pend_par0;                            // parity of the fixed fields and of the words before the last beat
      logic [BW-1:0]  last_wp;                              // the word parities of the last beat that joined
      logic [BW-1:0]  in_wp;                                // the beat's word parities (zero at the unused positions)
      logic [VWL-1:0] mshift;                               // the beat's words at the merge position
      logic [WPF-1:0] mwe;                                  // the words of the pending buffer the merge candidate writes
      logic [WPF:0]   in_thr;                               // a data beat's word count as a thermometer: bit s = (s < in_n)
      logic           full, fits, same, mergeable, emit_pend, pass, take, oready, room_big;
      assign room_big  = !pend_thr[WPF-BW];                 // pend_cnt <= WPF - BW: any beat still fits
      always_comb begin
        for (int w = 0; w < BW; w++)                        // word w is in the beat: w < in_n
          in_wp[w] = (in_data ? (in_hdr[13:8] >= 6'(w)) : (w == 0)) && (^in_words[32*w +: 32]);
        for (int s = 0; s <= WPF; s++)
          in_thr[s] = (in_hdr[13:8] >= 6'(s));
        for (int s = 0; s < BW; s++)                        // words pend_cnt .. pend_cnt + BW - 1
          mwe[s] = !pend_thr[s];
        for (int s = BW; s < WPF; s++)
          mwe[s] = !pend_thr[s] && pend_thr[s - BW];
      end
      assign mshift    = VWL'(in_words) << (32 * pend_cnt);
      assign oready    = !link_valid || link_ready;         // the output stage is empty or drains this cycle
      assign full      = pend_valid && pend_thr[WPF-1];
      // in_n <= WPF - pend_cnt for a data beat (in_n = hdr[13:8] + 1 <= BW): any beat fits while BW words are left, else
      // the low bits of the count field against the low bits of the room
      assign fits      = (in_hdr[13:8] < 6'(BW)) && (room_big || (in_hdr[8 +: BWL] < pend_room[BWL-1:0]));
      assign same      = (in_type == pend_type) && (in_head == pend_head) && (in_tag == pend_tag) &&
                         (in_hdr[19:14] == pend_hhi) && (in_type != T_PSUM || in_hdr[7:0] == pend_ncol);
      assign mergeable = pend_valid && core_valid && in_data && same && fits;
      // the pending flit goes out when it is full, or when the next beat cannot join it
      assign emit_pend = pend_valid && (full || (core_valid && !mergeable));
      assign pass      = core_valid && !in_data && !pend_valid;      // single-word types pass through
      // a data beat joins or starts the pending flit when the stage could take a flit that leaves; a single-word beat
      // passes when nothing is pending and the stage takes it
      assign core_ready = core_valid && (in_data ? (!pend_valid || oready) : (!pend_valid && oready));
      assign take      = core_ready && in_data;
      logic [VWL-1:0] pend_words;                           // the pending flit's words, zero at and above pend_cnt
      always_comb begin
        for (int s = 0; s < WPF; s++)
          pend_words[32*s +: 32] = pend_thr[s] ? (cur ? pbuf1[32*s +: 32] : pbuf0[32*s +: 32]) : 32'd0;
      end
      logic [FWL-2:0] pend_body, pass_body;
      logic           pend_par, pass_par;
      always_comb begin
        pend_body = {pend_head, pend_type, pend_tag, pend_hhi, pend_cm1, pend_col0, pend_words};
        pass_body = {in_head, in_type, in_tag, in_hdr, VWL'(in_words[31:0])};
      end
      assign pend_par = pend_par0 ^ (^pend_cm1) ^ (^last_wp);                    // = ^pend_body
      assign pass_par = ^{in_head, in_type, in_tag, in_hdr, in_words[31:0]};     // = ^pass_body
      // the buffers (no reset: a word is shown only below pend_cnt, i.e. after the beat that brought it has written it)
      always_ff @(posedge clk) begin
        if (take) begin
          for (int s = 0; s < WPF; s++) begin                // the merge candidate, into the pending buffer
            if (mwe[s]) begin
              if (cur) pbuf1[32*s +: 32] <= mshift[32*s +: 32];
              else     pbuf0[32*s +: 32] <= mshift[32*s +: 32];
            end
          end
          for (int s = 0; s < BW; s++) begin                 // the start candidate, into the other buffer
            if (cur) pbuf0[32*s +: 32] <= in_words[32*s +: 32];
            else     pbuf1[32*s +: 32] <= in_words[32*s +: 32];
          end
        end
      end
      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
          pend_valid <= 1'b0; cur <= 1'b0; pend_cnt <= '0; pend_thr <= '0; pend_cm1 <= '0; pend_room <= '0;
          pend_head <= '0; pend_type <= '0; pend_tag <= '0; pend_hhi <= '0; pend_col0 <= '0; pend_ncol <= '0;
          pend_par0 <= 1'b0; last_wp <= '0;
          link_valid <= 1'b0; link_flit <= '0;
        end else begin
          if (oready) begin                                 // the output stage: loaded when empty or draining
            link_valid <= emit_pend || pass;
            link_flit  <= pend_valid ? {pend_par, pend_body} : {pass_par, pass_body};
          end
          if (emit_pend && oready) begin
            pend_valid <= 1'b0; pend_cnt <= '0; pend_thr <= '0;
          end
          if (take) begin
            pend_valid <= 1'b1;
            last_wp    <= in_wp;
            pend_head  <= in_head; pend_type <= in_type; pend_tag <= in_tag; pend_hhi <= in_hdr[19:14];   // = pend's on a merge
            // the counts of a data beat as one adder each, in_n = hdr[13:8] + 1 folded in as a carry or a complement
            pend_ncol  <= in_hdr[7:0] + 8'(in_hdr[13:8]) + 8'd1;
            if (mergeable) begin
              pend_cnt  <= CWL'(pend_cm1) + CWL'(in_hdr[13:8]) + CWL'(2);       // pend_cnt + in_n
              pend_thr  <= ((pend_thr << 1) << in_hdr[13:8]) | in_thr;          // (s < pend_cnt + in_n)
              pend_cm1  <= pend_cm1 + in_hdr[13:8] + 6'd1;
              pend_room <= pend_room + ~CWR'(in_hdr[13:8]);                     // room - in_n
              pend_par0 <= pend_par0 ^ (^last_wp);
            end else begin                                  // the pending flit is empty, or leaves this cycle
              cur       <= !cur;
              pend_cnt  <= CWL'(in_hdr[13:8]) + CWL'(1);
              pend_thr  <= in_thr;
              pend_cm1  <= in_hdr[13:8];
              pend_room <= CWR'(WPF - 1) - CWR'(in_hdr[13:8]);                 // WPF - in_n
              pend_col0 <= in_hdr[7:0];
              pend_par0 <= ^{in_head, in_type, in_tag, in_hdr[19:14], in_hdr[7:0]};
            end
          end
        end
      end
    end else begin : g_split
      logic [CWB-1:0] in_n;                                 // words in the beat
      logic [CWB-1:0] idx;                                  // chunk of WPF words being sent
      logic [CWB-1:0] base, left, cn;
      assign in_n    = in_data ? CWB'(in_hdr[13:8]) + CWB'(1) : CWB'(1);
      logic           last;
      logic [VWB-1:0] sh;
      logic [VWL-1:0] cvec;
      logic [19:0]    hdr_out;
      logic [FWL-2:0] body;
      assign base    = CWB'(32'(idx) * WPF);
      assign left    = in_n - base;                         // words from this chunk on
      assign last    = (32'(left) <= WPF);
      assign cn      = last ? left : CWB'(WPF);
      assign sh      = in_words >> (32 * base);
      always_comb begin
        cvec = '0;
        for (int w = 0; w < WPF; w++)
          if (w < cn) cvec[32*w +: 32] = sh[32*w +: 32];
      end
      assign hdr_out = in_data ? {in_hdr[19:14], 6'(cn) - 6'd1, (in_type == T_PSUM) ? in_hdr[7:0] + 8'(base) : in_hdr[7:0]} : in_hdr;
      assign body    = {in_head, in_type, in_tag, hdr_out, cvec};
      assign link_flit  = {^body, body};
      assign link_valid = core_valid;
      assign core_ready = link_ready && last;
      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) idx <= '0;
        else if (core_valid && link_ready) idx <= last ? '0 : idx + 1;
      end
    end
  endgenerate
endmodule


module link_unpacker #(
  parameter int XW = 2, YW = 2, BW = 16, WPF = 1,
  parameter int DWB = 32 + 32 * BW,
  parameter int DWL = 32 + 32 * WPF,
  parameter int FWB = 1 + 2 * (XW + YW) + 8 + DWB,
  parameter int FWL = 1 + 2 * (XW + YW) + 8 + DWL,
  parameter int HW  = 2 * (XW + YW) + 8
)(
  input  logic           clk,
  input  logic           rst_n,
  input  logic           link_valid,
  input  logic [FWL-1:0] link_flit,
  output logic           link_ready,
  output logic           core_valid,
  output logic [FWB-1:0] core_flit,
  input  logic           core_ready
);
  localparam logic [2:0] T_RDRSP = 3'd2, T_WRDATA = 3'd4, T_PSUM = 3'd5;
  localparam int CWL = $clog2(WPF + 1);
  localparam int VWB = 32 * BW;
  localparam int VWL = 32 * WPF;
  logic [HW-1:0]  l_head;
  logic [2:0]     l_type;
  logic [8:0]     l_tag;
  logic [19:0]    l_hdr;
  logic           l_data;
  logic [CWL-1:0] n_words;                                  // words in this flit
  logic [VWL-1:0] l_words;
  logic [CWL-1:0] idx;                                      // beat of BW words presented now
  logic [CWL-1:0] base, left, bn;
  logic           last;
  logic [VWL-1:0] sh;
  logic [VWB-1:0] bvec;
  logic [19:0]    hdr_out;
  logic [FWB-2:0] body;
  assign l_head  = link_flit[DWL +: HW];
  assign l_type  = link_flit[DWL-1 -: 3];
  assign l_tag   = link_flit[DWL-4 -: 9];
  assign l_hdr   = link_flit[DWL-13 -: 20];
  assign l_data  = (l_type == T_RDRSP) || (l_type == T_WRDATA) || (l_type == T_PSUM);
  assign n_words = l_data ? CWL'(l_hdr[13:8]) + CWL'(1) : CWL'(1);
  assign l_words = link_flit[VWL-1:0];
  assign base    = CWL'(32'(idx) * BW);
  assign left    = n_words - base;                          // words from this beat on
  assign last    = (32'(left) <= BW);
  assign bn      = last ? left : CWL'(BW);
  assign sh      = l_words >> (32 * base);
  assign bvec    = VWB'(sh);                                // positions above bn are not read by the interface
  assign hdr_out = l_data ? {l_hdr[19:14], 6'(bn) - 6'd1, (l_type == T_PSUM) ? l_hdr[7:0] + 8'(base) : l_hdr[7:0]} : l_hdr;
  logic unused_link_parity;                                 // the flit's parity is the router's business (lint)
  assign unused_link_parity = link_flit[FWL-1];
  assign body    = {l_head, l_type, l_tag, hdr_out, bvec};
  assign core_flit  = {^body, body};
  assign core_valid = link_valid;
  assign link_ready = core_ready && last;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) idx <= '0;
    else if (link_valid && core_ready) idx <= last ? '0 : idx + 1;
  end
endmodule
