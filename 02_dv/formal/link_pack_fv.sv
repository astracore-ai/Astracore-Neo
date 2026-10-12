// link_pack_fv.sv -- formal harness for the round trip rtl/link_packer -> rtl/link_unpacker (drop 0.41) at the silicon
//   widths: BW = 16 words per beat, WPF = 32 words per link flit, XW = YW = 3. A free beat stream enters the packer, the
//   packer's link drives the unpacker directly, a free sink takes the unpacker's beats.
//   Source assumptions (the tile interface's contract): nothing during reset; a beat is held, unchanged, until accepted;
//   a data beat (RDRSP, WRDATA, PSUM) carries 1..16 words with hdr[13:8] = words - 1; RDRSP / WRDATA carry hdr[7:0] = 0
//   as tile_nic sends them (on PSUM hdr[7:0] is the column of word 0, word i column + i); every other type is one word.
//   Scoreboard: the words are numbered in stream order on both sides (in_cnt, out_cnt); one word, number j, a free
//   constant, is captured when it is accepted -- its data and its stream fields (head, type, tag, hdr[19:14], its own
//   PSUM column, the whole hdr of a one-word type) -- and checked when the sink takes the word numbered j. j free makes
//   that every position: the beats out carry the same words, in the same order, with the same fields, none invented
//   or repeated.
//   Asserted as well: every link flit has even parity and 1..WPF words, every beat out even parity and 1..BW words;
//   accepting a one-word type (the CRC that closes every data stream) leaves nothing in flight -- every word accepted
//   before it has been presented; and with the sink ready a beat waits at most 2 cycles to be accepted.
//   Inductive invariants over the packer's pending flit and the unpacker's beat index (the words in flight, the tracked
//   word's place in pend_vec, the bits above the pending words cleared, a two-beat flit held until its second beat goes)
//   make it a full proof by k-induction. Internal names read: g_merge.pend_* of the packer (WPF >= BW), idx of the unpacker.
module link_pack_fv #(
  parameter int XW  = 3,
  parameter int YW  = 3,
  parameter int BW  = 16,
  parameter int WPF = 32,
  parameter int HW  = 2 * (XW + YW) + 8,
  parameter int DWB = 32 + 32 * BW,
  parameter int DWL = 32 + 32 * WPF,
  parameter int FWB = 1 + HW + DWB,
  parameter int FWL = 1 + HW + DWL,
  parameter int NW  = 10                                   // word numbers, modulo 2^NW
)(
  input  logic           clk,
  input  logic           src_valid,
  input  logic [FWB-1:0] src_flit,
  input  logic           snk_ready
);
  localparam logic [2:0] T_RDRSP = 3'd2, T_WRDATA = 3'd4, T_PSUM = 3'd5;
  localparam int CWL = $clog2(WPF + 1);
  logic rst_n = 1'b0;
  always_ff @(posedge clk) rst_n <= 1'b1;

  logic           src_ready, lk_valid, lk_ready, snk_valid;
  logic [FWL-1:0] lk_flit;
  logic [FWB-1:0] snk_flit;
  link_packer #(.XW(XW), .YW(YW), .BW(BW), .WPF(WPF)) u_pk (
    .clk(clk), .rst_n(rst_n), .core_valid(src_valid), .core_flit(src_flit), .core_ready(src_ready),
    .link_valid(lk_valid), .link_flit(lk_flit), .link_ready(lk_ready));
  link_unpacker #(.XW(XW), .YW(YW), .BW(BW), .WPF(WPF)) u_up (
    .clk(clk), .rst_n(rst_n), .link_valid(lk_valid), .link_flit(lk_flit), .link_ready(lk_ready),
    .core_valid(snk_valid), .core_flit(snk_flit), .core_ready(snk_ready));

  // fields: s_* source beat, k_* sink beat, l_* link flit, h_* the held source beat
  logic [HW-1:0] s_head, k_head;
  logic [2:0]    s_type, k_type, l_type;
  logic [8:0]    s_tag, k_tag;
  logic [19:0]   s_hdr, k_hdr, l_hdr;
  logic          s_data, k_data, l_data;
  logic [5:0]    s_n, k_n;                                 // words in the beat
  assign s_head = src_flit[DWB +: HW];
  assign s_type = src_flit[DWB-1 -: 3];
  assign s_tag  = src_flit[DWB-4 -: 9];
  assign s_hdr  = src_flit[DWB-13 -: 20];
  assign k_head = snk_flit[DWB +: HW];
  assign k_type = snk_flit[DWB-1 -: 3];
  assign k_tag  = snk_flit[DWB-4 -: 9];
  assign k_hdr  = snk_flit[DWB-13 -: 20];
  assign l_type = lk_flit[DWL-1 -: 3];
  assign l_hdr  = lk_flit[DWL-13 -: 20];
  assign s_data = (s_type == T_RDRSP) || (s_type == T_WRDATA) || (s_type == T_PSUM);
  assign k_data = (k_type == T_RDRSP) || (k_type == T_WRDATA) || (k_type == T_PSUM);
  assign l_data = (l_type == T_RDRSP) || (l_type == T_WRDATA) || (l_type == T_PSUM);
  assign s_n    = s_data ? s_hdr[13:8] + 6'd1 : 6'd1;
  assign k_n    = k_data ? k_hdr[13:8] + 6'd1 : 6'd1;
  logic acc_in, acc_out;
  assign acc_in  = src_valid && src_ready;
  assign acc_out = snk_valid && snk_ready;

  // ---- source protocol ----
  logic           held_v = 1'b0;
  logic [FWB-1:0] held_f;
  always_ff @(posedge clk) begin
    held_v <= rst_n && src_valid && !src_ready;
    held_f <= src_flit;
  end
  always_comb begin
    if (!rst_n) assume (!src_valid);
    if (held_v) assume (src_valid && src_flit == held_f);
    if (s_data) assume (s_hdr[13:8] <= 6'(BW - 1));
    if (s_type == T_RDRSP || s_type == T_WRDATA) assume (s_hdr[7:0] == 8'd0);
  end

  // ---- the word numbers and the tracked word ----
  logic [NW-1:0] in_cnt = '0, out_cnt = '0;
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      in_cnt <= '0; out_cnt <= '0;
    end else begin
      if (acc_in)  in_cnt  <= in_cnt + NW'(s_n);
      if (acc_out) out_cnt <= out_cnt + NW'(k_n);
    end
  end
  logic [NW-1:0] j;                                        // free constant: the tracked word's number
  always_ff @(posedge clk) j <= j;

  logic [NW-1:0] din, dout;                                // j's offset in the beat in / out
  logic          hit_in, hit_out;
  logic          cap_v = 1'b0;                             // word j accepted (captured once)
  logic          chk = 1'b0;                               // word j presented (checked once)
  assign din     = j - in_cnt;
  assign dout    = j - out_cnt;
  assign hit_in  = rst_n && acc_in && (din < NW'(s_n)) && !cap_v;
  assign hit_out = rst_n && acc_out && (dout < NW'(k_n)) && !chk;

  // the word and its fields as the source gives them / as the sink receives them
  logic [31:0]   i_word, o_word;
  logic [7:0]    i_low, o_low;                             // hdr[7:0] of the word: its PSUM column, else the beat's
  logic [5:0]    i_mid, o_mid;                             // hdr[13:8] of a one-word type (the count of a data beat is not a word field)
  assign i_word = src_flit[32*din[3:0] +: 32];
  assign o_word = snk_flit[32*dout[3:0] +: 32];
  assign i_low  = (s_type == T_PSUM) ? s_hdr[7:0] + 8'(din) : s_hdr[7:0];
  assign o_low  = (k_type == T_PSUM) ? k_hdr[7:0] + 8'(dout) : k_hdr[7:0];
  assign i_mid  = s_data ? 6'd0 : s_hdr[13:8];
  assign o_mid  = k_data ? 6'd0 : k_hdr[13:8];

  logic [31:0]   cap_word;
  logic [HW-1:0] cap_head;
  logic [2:0]    cap_type;
  logic [8:0]    cap_tag;
  logic [5:0]    cap_key, cap_mid;
  logic [7:0]    cap_low;
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      cap_v <= 1'b0; chk <= 1'b0;
    end else begin
      if (hit_in) begin
        cap_v <= 1'b1; cap_word <= i_word; cap_head <= s_head; cap_type <= s_type; cap_tag <= s_tag;
        cap_key <= s_hdr[19:14]; cap_low <= i_low; cap_mid <= i_mid;
      end
      if (hit_out) chk <= 1'b1;
    end
  end
  // the word as known in this cycle (a one-word type is accepted and presented in the same cycle)
  logic          cur_v;
  logic [31:0]   cur_word;
  logic [HW-1:0] cur_head;
  logic [2:0]    cur_type;
  logic [8:0]    cur_tag;
  logic [5:0]    cur_key, cur_mid;
  logic [7:0]    cur_low;
  assign cur_v    = cap_v || hit_in;
  assign cur_word = hit_in ? i_word : cap_word;
  assign cur_head = hit_in ? s_head : cap_head;
  assign cur_type = hit_in ? s_type : cap_type;
  assign cur_tag  = hit_in ? s_tag : cap_tag;
  assign cur_key  = hit_in ? s_hdr[19:14] : cap_key;
  assign cur_low  = hit_in ? i_low : cap_low;
  assign cur_mid  = hit_in ? i_mid : cap_mid;

  // ---- flush and progress ----
  logic          prev_flush = 1'b0;                        // a one-word type was accepted in the last cycle
  logic [1:0]    wcnt = 2'd0;                              // cycles a beat has waited with the sink ready
  always_ff @(posedge clk) begin
    prev_flush <= rst_n && acc_in && !s_data;
    if (!rst_n || !(src_valid && !src_ready && snk_ready)) wcnt <= 2'd0;
    else if (wcnt != 2'd3)                                 wcnt <= wcnt + 2'd1;
  end

  // ---- the pending flit and the beat index (inductive invariants) ----
  logic             p_valid;
  logic [CWL-1:0]   p_cnt, u_idx;
  logic [32*WPF-1:0] p_vec;
  logic [HW-1:0]    p_head;
  logic [2:0]       p_type;
  logic [8:0]       p_tag;
  logic [19:0]      p_hdr;
  assign p_valid = u_pk.g_merge.pend_valid;
  assign p_cnt   = u_pk.g_merge.pend_cnt;
  assign p_vec   = u_pk.g_merge.pend_vec;
  assign p_head  = u_pk.g_merge.pend_head;
  assign p_type  = u_pk.g_merge.pend_type;
  assign p_tag   = u_pk.g_merge.pend_tag;
  assign p_hdr   = u_pk.g_merge.pend_hdr;
  assign u_idx   = u_up.idx;
  logic [NW-1:0]    fl;                                    // words in flight
  logic [NW-1:0]    rel;                                   // word j's place after the last presented word
  logic [5:0]       pos;                                   // ... and in pend_vec
  logic             h_data, h_join;                        // the held beat is data / would join the pending flit
  logic [HW-1:0]    h_head;
  logic [2:0]       h_type;
  logic [8:0]       h_tag;
  logic [19:0]      h_hdr;
  assign fl     = in_cnt - out_cnt;
  assign rel    = j - out_cnt;
  assign pos    = 6'(rel) + 6'(16 * u_idx);
  assign h_head = held_f[DWB +: HW];
  assign h_type = held_f[DWB-1 -: 3];
  assign h_tag  = held_f[DWB-4 -: 9];
  assign h_hdr  = held_f[DWB-13 -: 20];
  assign h_data = (h_type == T_RDRSP) || (h_type == T_WRDATA) || (h_type == T_PSUM);
  assign h_join = h_data && (h_type == p_type) && (h_head == p_head) && (h_tag == p_tag) &&
                  (h_hdr[19:14] == p_hdr[19:14]) && (h_type != T_PSUM || h_hdr[7:0] == p_hdr[7:0] + 8'(p_cnt)) &&
                  (32'(p_cnt) + 32'(h_hdr[13:8]) + 1 <= WPF);

  always_comb begin
    if (rst_n) begin
      // the round trip: the word numbered j comes out as it went in
      if (hit_out) begin
        assert (cur_v);
        assert (o_word == cur_word);
        assert (k_head == cur_head && k_type == cur_type && k_tag == cur_tag);
        assert (k_hdr[19:14] == cur_key && o_low == cur_low && o_mid == cur_mid);
      end
      assert (fl <= NW'(WPF));                             // at most one flit's words in flight
      // flit and beat shape
      if (lk_valid) assert ((^lk_flit) == 1'b0);
      if (lk_valid && l_data) assert (l_hdr[13:8] <= 6'(WPF - 1));
      if (snk_valid) assert ((^snk_flit) == 1'b0);
      if (snk_valid && k_data) assert (k_hdr[13:8] <= 6'(BW - 1));
      // a CRC (any one-word type) flushes the stream ahead of it
      if (prev_flush) assert (fl == '0);
      // progress with the sink ready
      assert (wcnt <= 2'd2);
      // -- invariants --
      // the pending flit: a data stream's words, 1..WPF of them, nothing above them, the count in flight
      if (p_valid) begin
        assert (p_cnt >= CWL'(1) && p_cnt <= CWL'(WPF));
        assert (p_type == T_RDRSP || p_type == T_WRDATA || p_type == T_PSUM);
        if (p_type != T_PSUM) assert (p_hdr[7:0] == 8'd0);
        assert ((p_vec >> (32 * p_cnt)) == '0);
        assert (fl == NW'(p_cnt) - NW'(16 * u_idx));
      end else begin
        assert (p_cnt == '0);
        assert (fl == '0);
      end
      // the unpacker: beat 0 or 1; beat 1 only of a two-beat flit that keeps being offered
      assert (u_idx <= CWL'(1));
      if (u_idx == CWL'(1)) assert (p_valid && p_cnt > CWL'(BW) && (p_cnt == CWL'(WPF) || (held_v && !h_join)));
      // word j: captured and not yet presented = in flight, at its place in pend_vec
      if (!cap_v) assert (!chk && !(rel < fl));
      if (cap_v && !chk) begin
        assert (rel < fl);
        assert (p_vec[32*pos[4:0] +: 32] == cap_word);
        assert (p_head == cap_head && p_type == cap_type && p_tag == cap_tag && p_hdr[19:14] == cap_key);
        assert (cap_low == ((p_type == T_PSUM) ? p_hdr[7:0] + 8'(pos) : p_hdr[7:0]) && cap_mid == 6'd0);
      end
      // a waiting beat with the sink ready: held, and at its last cycle of waiting
      if (wcnt != 2'd0) assert (held_v);
      if (wcnt != 2'd0 && h_data) assert (u_idx == CWL'(1));
      if (wcnt == 2'd1 && !h_data) assert (!p_valid || u_idx == CWL'(1) || p_cnt <= CWL'(BW));
      if (wcnt == 2'd2) assert (!h_data && !p_valid);
    end
  end
endmodule
