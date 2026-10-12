// link_pack_fv.sv -- formal harness for the round trip rtl/link_packer -> rtl/link_unpacker (drop 0.41, the packer with the
//   registered link side) at the silicon widths: BW = 16 words per beat, WPF = 32 words per link flit, XW = YW = 3. A free
//   beat stream enters the packer, the packer's output register drives the unpacker directly, a free sink takes the
//   unpacker's beats.
//   Source assumptions (the tile interface's contract): nothing during reset; a beat is held, unchanged, until accepted;
//   a data beat (RDRSP, WRDATA, PSUM) carries 1..16 words with hdr[13:8] = words - 1; RDRSP / WRDATA carry hdr[7:0] = 0
//   as tile_nic sends them (on PSUM hdr[7:0] is the column of word 0, word i column + i); every other type is one word.
//   Scoreboard: the words are numbered in stream order on both sides (in_cnt, out_cnt); one word, number j, a free
//   constant, is captured when it is accepted -- its data and its stream fields (head, type, tag, hdr[19:14], its own
//   PSUM column, the whole hdr of a one-word type) -- and checked when the sink takes the word numbered j. j free makes
//   that every position: the beats out carry the same words, in the same order, with the same fields, none invented
//   or repeated.
//   Asserted as well: every link flit has even parity (stated as the XOR of its field and word parities, which is the
//   XOR of all its bits) and 1..WPF words, every beat out even parity and 1..BW words; accepting a one-word type (the
//   CRC that closes every data stream) leaves only that word in flight -- every word accepted before it has been
//   presented (before the link side was registered: none at all); and with the sink ready a beat waits at most 2 cycles
//   to be accepted -- the bound of the combinational link side still holds, because while the output register holds a
//   flit at most one beat is pending: a one-word beat waits for the register's first of two beats and the pending
//   flit's move into the register, or, with the register empty, for that move and the moved flit's first beat.
//   Inductive invariants over the output register (the words it still holds, a two-beat flit at beat 1 only, at most
//   one beat pending behind it), the pending flit (count, thermometer, count - 1, room, the next PSUM column), the
//   running parity (pend_par0 ^ ^last_wp against the fixed fields and a ghost of the prefix parities of the pending
//   buffer's words below the count -- the words at and above the count are stale and never read), the tracked word's
//   place (in the output register at (j - out_cnt) + 16 idx, else in the pending buffer behind the register's words)
//   and the remaining wait of a held beat make it a full proof by k-induction. Internal names read: the packer's
//   g_merge.pend_valid / cur / pbuf0 / pbuf1 / pend_cnt / pend_thr / pend_cm1 / pend_room / pend_head / pend_type /
//   pend_tag / pend_hhi / pend_col0 / pend_ncol / pend_par0 / last_wp / take / mergeable / in_wp (WPF >= BW), the
//   unpacker's idx.
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
  localparam int CWR = CWL + 1;
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

  // fields: s_* source beat, k_* sink beat, l_* link flit (the packer's output register), h_* the held source beat
  logic [HW-1:0] s_head, k_head, l_head;
  logic [2:0]    s_type, k_type, l_type;
  logic [8:0]    s_tag, k_tag, l_tag;
  logic [19:0]   s_hdr, k_hdr, l_hdr;
  logic          s_data, k_data, l_data;
  logic [5:0]    s_n, k_n;                                 // words in the beat
  logic [6:0]    l_n;                                      // words in the link flit
  assign s_head = src_flit[DWB +: HW];
  assign s_type = src_flit[DWB-1 -: 3];
  assign s_tag  = src_flit[DWB-4 -: 9];
  assign s_hdr  = src_flit[DWB-13 -: 20];
  assign k_head = snk_flit[DWB +: HW];
  assign k_type = snk_flit[DWB-1 -: 3];
  assign k_tag  = snk_flit[DWB-4 -: 9];
  assign k_hdr  = snk_flit[DWB-13 -: 20];
  assign l_head = lk_flit[DWL +: HW];
  assign l_type = lk_flit[DWL-1 -: 3];
  assign l_tag  = lk_flit[DWL-4 -: 9];
  assign l_hdr  = lk_flit[DWL-13 -: 20];
  assign s_data = (s_type == T_RDRSP) || (s_type == T_WRDATA) || (s_type == T_PSUM);
  assign k_data = (k_type == T_RDRSP) || (k_type == T_WRDATA) || (k_type == T_PSUM);
  assign l_data = (l_type == T_RDRSP) || (l_type == T_WRDATA) || (l_type == T_PSUM);
  assign s_n    = s_data ? s_hdr[13:8] + 6'd1 : 6'd1;
  assign k_n    = k_data ? k_hdr[13:8] + 6'd1 : 6'd1;
  assign l_n    = l_data ? 7'(l_hdr[13:8]) + 7'd1 : 7'd1;
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
  // the word as known in this cycle (kept general: with the registered link no word is accepted and presented in one cycle)
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
  logic [2:0]    wcnt = 3'd0;                              // cycles a beat has waited with the sink ready
  always_ff @(posedge clk) begin
    prev_flush <= rst_n && acc_in && !s_data;
    if (!rst_n || !(src_valid && !src_ready && snk_ready)) wcnt <= 3'd0;
    else if (wcnt != 3'd7)                                 wcnt <= wcnt + 3'd1;
  end

  // ---- the packer's state, the unpacker's beat index (inductive invariants) ----
  logic              p_valid, p_cur, p_par0;
  logic [32*WPF-1:0] p_buf;                                // the buffer holding the pending flit
  logic [CWL-1:0]    p_cnt, u_idx;
  logic [WPF:0]      p_thr;
  logic [5:0]        p_cm1, p_hhi;
  logic [CWR-1:0]    p_room;
  logic [HW-1:0]     p_head;
  logic [2:0]        p_type;
  logic [8:0]        p_tag;
  logic [7:0]        p_col0, p_ncol;
  logic [BW-1:0]     p_lwp;
  assign p_valid = u_pk.g_merge.pend_valid;
  assign p_cur   = u_pk.g_merge.cur;
  assign p_buf   = p_cur ? u_pk.g_merge.pbuf1 : u_pk.g_merge.pbuf0;
  assign p_cnt   = u_pk.g_merge.pend_cnt;
  assign p_thr   = u_pk.g_merge.pend_thr;
  assign p_cm1   = u_pk.g_merge.pend_cm1;
  assign p_room  = u_pk.g_merge.pend_room;
  assign p_head  = u_pk.g_merge.pend_head;
  assign p_type  = u_pk.g_merge.pend_type;
  assign p_tag   = u_pk.g_merge.pend_tag;
  assign p_hhi   = u_pk.g_merge.pend_hhi;
  assign p_col0  = u_pk.g_merge.pend_col0;
  assign p_ncol  = u_pk.g_merge.pend_ncol;
  assign p_par0  = u_pk.g_merge.pend_par0;
  assign p_lwp   = u_pk.g_merge.last_wp;
  assign u_idx   = u_up.idx;

  // the parities by group: the output register's words (a chain in word order) and fields, the pending flit's fields
  logic          p_fpar, l_wpar, l_fpar;
  always_comb begin
    l_wpar = 1'b0;
    for (int s = 0; s < WPF; s++) l_wpar = l_wpar ^ (^lk_flit[32*s +: 32]);
  end
  assign p_fpar = ^{p_head, p_type, p_tag, p_hhi, p_col0};
  assign l_fpar = ^{l_head, l_type, l_tag, l_hdr[19:14], l_hdr[7:0]};
  // ghost: the prefix parities of the pending flit's words, g_pre[s] = the XOR of the parities of words 0..s-1 (constant
  // from pend_cnt on), rebuilt on every taken beat from the beat's word parities -- kept as state, so that each slot's
  // invariant is local (one word's parity) and the running parity's is one bit, where the XOR of a masked, shifted buffer
  // was beyond the solver in the induction step
  logic          g_take, g_merge;
  logic [BW-1:0] g_iwp;
  logic [WPF:0]  g_pre, g_new;
  assign g_take  = u_pk.g_merge.take;
  assign g_merge = u_pk.g_merge.mergeable;
  assign g_iwp   = u_pk.g_merge.in_wp;
  always_comb begin
    g_new[0] = 1'b0;
    for (int s = 1; s <= WPF; s++) begin
      int k;                                               // slot s-1 within the beat taken now
      k = s - 1 - (g_merge ? int'(p_cnt) : 0);
      if (g_merge && CWL'(s) <= p_cnt) g_new[s] = g_pre[s];
      else                             g_new[s] = g_new[s-1] ^ ((k >= 0 && k < BW) ? g_iwp[k] : 1'b0);
    end
  end
  always_ff @(posedge clk) begin
    if (!rst_n)      g_pre <= '0;
    else if (g_take) g_pre <= g_new;
  end

  // the words in flight: the output register's still to be presented, then the pending flit's
  logic [NW-1:0] fl, rel, l_left;
  logic [5:0]    pos_l, pos_p;                             // word j's place in the output register / the pending buffer
  logic          l_two;                                    // the output register holds a flit of two beats
  assign fl     = in_cnt - out_cnt;
  assign rel    = j - out_cnt;
  assign l_left = lk_valid ? NW'(l_n) - NW'(16 * u_idx) : '0;
  assign pos_l  = 6'(rel) + 6'(16 * u_idx);
  assign pos_p  = 6'(rel - l_left);
  assign l_two  = lk_valid && l_data && (l_hdr[13:8] >= 6'(BW));

  // the remaining wait of a held beat with the sink ready from now on: a data beat waits while a flit is pending and the
  // output register cannot drain (its first of two beats); a one-word beat waits for the register to drain, then, behind
  // a pending flit, for the pending flit's move into the register and that flit's first beat when it has two
  logic [2:0]    h_type;
  logic          h_data;
  logic [2:0]    r_data, r_one;
  assign h_type = held_f[DWB-1 -: 3];
  assign h_data = (h_type == T_RDRSP) || (h_type == T_WRDATA) || (h_type == T_PSUM);
  assign r_data = (p_valid && l_two && u_idx == '0) ? 3'd1 : 3'd0;
  assign r_one  = ((l_two && u_idx == '0) ? 3'd1 : 3'd0) + (p_valid ? 3'd1 : 3'd0) +
                  ((p_valid && p_cnt > CWL'(BW)) ? 3'd1 : 3'd0);

  always_comb begin
    if (rst_n) begin
      // the round trip: the word numbered j comes out as it went in
      if (hit_out) begin
        assert (cur_v);
        assert (o_word == cur_word);
        assert (k_head == cur_head && k_type == cur_type && k_tag == cur_tag);
        assert (k_hdr[19:14] == cur_key && o_low == cur_low && o_mid == cur_mid);
      end
      assert (fl <= NW'(WPF + BW));                        // a flit in the output register, a beat pending behind it
      // flit and beat shape: even parity over the whole flit (field and word parities), 1..WPF / 1..BW words
      if (lk_valid) assert (lk_flit[FWL-1] == (l_fpar ^ (^l_hdr[13:8]) ^ l_wpar));
      if (lk_valid && l_data) assert (l_hdr[13:8] <= 6'(WPF - 1));
      if (snk_valid) assert ((^snk_flit) == 1'b0);
      if (snk_valid && k_data) assert (k_hdr[13:8] <= 6'(BW - 1));
      // a CRC (any one-word type) flushes the stream ahead of it: only that word is left in flight
      if (prev_flush) assert (fl == NW'(1));
      // progress with the sink ready
      assert (wcnt <= 3'd2);
      // -- invariants --
      // the pending flit: a data stream's words, 1..WPF of them, the counts consistent, the running parity
      if (p_valid) begin
        assert (p_cnt >= CWL'(1) && p_cnt <= CWL'(WPF));
        assert (p_cm1 == 6'(p_cnt - CWL'(1)) && p_room == CWR'(WPF) - CWR'(p_cnt));
        assert (p_type == T_RDRSP || p_type == T_WRDATA || p_type == T_PSUM);
        if (p_type != T_PSUM) assert (p_col0 == 8'd0);
        if (p_type == T_PSUM) assert (p_ncol == p_col0 + 8'(p_cnt));
        // the running parity: the fixed fields' and the words' below the count, through the ghost prefix parities
        assert (g_pre[0] == 1'b0);
        for (int s = 0; s < WPF; s++) assert (g_pre[s+1] == (g_pre[s] ^ (p_thr[s] & (^p_buf[32*s +: 32]))));
        assert ((p_par0 ^ (^p_lwp)) == (p_fpar ^ g_pre[WPF]));
      end else begin
        assert (p_cnt == '0);
      end
      for (int s = 0; s <= WPF; s++) assert (p_thr[s] == (CWL'(s) < p_cnt));
      // the output register: data flits with their fields; while it holds a flit, at most one beat is pending
      if (lk_valid && l_data && l_type != T_PSUM) assert (l_hdr[7:0] == 8'd0);
      if (lk_valid) assert (!p_valid || p_cnt <= CWL'(BW));
      // the unpacker: beat 0 or 1, beat 1 only of a two-beat flit
      assert (u_idx <= CWL'(1));
      if (u_idx == CWL'(1)) assert (l_two);
      // the count in flight
      assert (fl == l_left + (p_valid ? NW'(p_cnt) : '0));
      // word j: captured and not yet presented = in flight -- in the output register, else in the pending buffer
      if (!cap_v) assert (!chk && !(rel < fl));
      if (cap_v && !chk) begin
        assert (rel < fl);
        if (rel < l_left) begin
          assert (lk_flit[32*pos_l[4:0] +: 32] == cap_word);
          assert (l_head == cap_head && l_type == cap_type && l_tag == cap_tag && l_hdr[19:14] == cap_key);
          assert (cap_low == ((l_type == T_PSUM) ? l_hdr[7:0] + 8'(pos_l) : l_hdr[7:0]));
          assert (l_data ? (cap_mid == 6'd0) : (l_hdr[13:8] == cap_mid && pos_l == 6'd0));
        end else begin
          assert (p_buf[32*pos_p[4:0] +: 32] == cap_word);
          assert (p_head == cap_head && p_type == cap_type && p_tag == cap_tag && p_hhi == cap_key);
          assert (cap_low == ((p_type == T_PSUM) ? p_col0 + 8'(pos_p) : p_col0) && cap_mid == 6'd0);
        end
      end
      // a waiting beat with the sink ready: held, and its waited cycles plus the remaining wait within the bound
      if (wcnt != 3'd0) assert (held_v);
      if (held_v && h_data)  assert (4'(wcnt) + 4'(r_data) <= 4'd1);
      if (held_v && !h_data) assert (4'(wcnt) + 4'(r_one) <= 4'd2);
    end
  end
endmodule
