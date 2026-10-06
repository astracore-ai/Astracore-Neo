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
  localparam int VWB = 32 * BW;
  localparam int VWL = 32 * WPF;
  logic [HW-1:0]  in_head;
  logic [2:0]     in_type;
  logic [8:0]     in_tag;
  logic [19:0]    in_hdr;
  logic [VWB-1:0] in_words;
  logic           in_data;
  logic [CWB-1:0] in_n;                                     // words in the beat
  assign in_head  = core_flit[DWB +: HW];
  assign in_type  = core_flit[DWB-1 -: 3];
  assign in_tag   = core_flit[DWB-4 -: 9];
  assign in_hdr   = core_flit[DWB-13 -: 20];
  assign in_words = core_flit[VWB-1:0];
  assign in_data  = (in_type == T_RDRSP) || (in_type == T_WRDATA) || (in_type == T_PSUM);
  assign in_n     = in_data ? CWB'(in_hdr[13:8]) + CWB'(1) : CWB'(1);

  generate
    if (WPF >= BW) begin : g_merge
      logic           pend_valid;
      logic [HW-1:0]  pend_head;
      logic [2:0]     pend_type;
      logic [8:0]     pend_tag;
      logic [19:0]    pend_hdr;                             // hdr of word 0 (PSUM: its column)
      logic [VWL-1:0] pend_vec;                             // words held, word i at [32*i +: 32]
      logic [CWL-1:0] pend_cnt;                             // words held, 1..WPF while pend_valid
      logic [VWB-1:0] in_vec;                               // the beat's words with the unused positions cleared
      logic           full, fits, same, mergeable, emit_pend;
      always_comb begin
        in_vec = '0;
        for (int w = 0; w < BW; w++)
          if (w < in_n) in_vec[32*w +: 32] = in_words[32*w +: 32];
      end
      assign full      = pend_valid && (pend_cnt == WPF);
      assign fits      = (pend_cnt + in_n) <= WPF;
      assign same      = (in_type == pend_type) && (in_head == pend_head) && (in_tag == pend_tag) &&
                         (in_hdr[19:14] == pend_hdr[19:14]) &&
                         (in_type != T_PSUM || in_hdr[7:0] == pend_hdr[7:0] + 8'(pend_cnt));
      assign mergeable = pend_valid && core_valid && in_data && same && fits;
      // the pending flit goes out when it is full, or when the next beat cannot join it
      assign emit_pend = pend_valid && (full || (core_valid && !mergeable));
      logic [FWL-2:0] pend_body, pass_body;
      always_comb begin
        pend_body = {pend_head, pend_type, pend_tag, pend_hdr[19:14], 6'(pend_cnt - 1), pend_hdr[7:0], pend_vec};
        pass_body = {in_head, in_type, in_tag, in_hdr, VWL'(in_words[31:0])};
      end
      always_comb begin
        link_valid = 1'b0; link_flit = '0; core_ready = 1'b0;
        if (emit_pend) begin
          link_valid = 1'b1; link_flit = {^pend_body, pend_body};
          core_ready = core_valid && in_data && link_ready;   // a data beat that could not join starts the next flit as this one leaves
        end else if (core_valid && !in_data && !pend_valid) begin
          link_valid = 1'b1; link_flit = {^pass_body, pass_body};   // single-word types pass through
          core_ready = link_ready;
        end else if (core_valid && in_data) begin
          core_ready = 1'b1;                                // joins or starts the pending flit
        end
      end
      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
          pend_valid <= 1'b0; pend_cnt <= '0; pend_head <= '0; pend_type <= '0; pend_tag <= '0; pend_hdr <= '0; pend_vec <= '0;
        end else begin
          if (emit_pend && link_ready) begin
            pend_valid <= 1'b0; pend_cnt <= '0;
          end
          if (core_valid && in_data && core_ready) begin
            if (mergeable) begin
              pend_vec <= pend_vec | (VWL'(in_vec) << (32 * pend_cnt));
              pend_cnt <= pend_cnt + in_n;
            end else begin                                  // the pending flit is empty, or leaves this cycle
              pend_valid <= 1'b1; pend_cnt <= CWL'(in_n);
              pend_head <= in_head; pend_type <= in_type; pend_tag <= in_tag; pend_hdr <= in_hdr;
              pend_vec  <= VWL'(in_vec);
            end
          end
        end
      end
    end else begin : g_split
      logic [CWB-1:0] idx;                                  // chunk of WPF words being sent
      logic [CWB-1:0] base, left, cn;
      logic           last;
      logic [VWB-1:0] sh;
      logic [VWL-1:0] cvec;
      logic [19:0]    hdr_out;
      logic [FWL-2:0] body;
      assign base    = idx * WPF;
      assign left    = in_n - base;                         // words from this chunk on
      assign last    = (left <= WPF);
      assign cn      = last ? left : CWB'(WPF);
      assign sh      = in_words >> (32 * base);
      always_comb begin
        cvec = '0;
        for (int w = 0; w < WPF; w++)
          if (w < cn) cvec[32*w +: 32] = sh[32*w +: 32];
      end
      assign hdr_out = in_data ? {in_hdr[19:14], 6'(cn - 1), (in_type == T_PSUM) ? in_hdr[7:0] + 8'(base) : in_hdr[7:0]} : in_hdr;
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
  assign base    = idx * BW;
  assign left    = n_words - base;                          // words from this beat on
  assign last    = (left <= BW);
  assign bn      = last ? left : CWL'(BW);
  assign sh      = l_words >> (32 * base);
  assign bvec    = VWB'(sh);                                // positions above bn are not read by the interface
  assign hdr_out = l_data ? {l_hdr[19:14], 6'(bn - 1), (l_type == T_PSUM) ? l_hdr[7:0] + 8'(base) : l_hdr[7:0]} : l_hdr;
  assign body    = {l_head, l_type, l_tag, hdr_out, bvec};
  assign core_flit  = {^body, body};
  assign core_valid = link_valid;
  assign link_ready = core_ready && last;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) idx <= '0;
    else if (link_valid && core_ready) idx <= last ? '0 : idx + 1;
  end
endmodule
