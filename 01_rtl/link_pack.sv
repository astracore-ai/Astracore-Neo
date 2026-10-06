// link_pack.sv -- wide links (drop 0.25): WPF words per flit between the tile interface and its router.
//   The tile interface (tile_nic) keeps its one-word-per-cycle protocol: link_packer merges consecutive data flits
//   (RDRSP, WRDATA, PSUM) that carry the same destination, header and tag into one wide flit of up to WPF words, and
//   link_unpacker presents a wide flit's words to the interface one per cycle. Every other flit type travels alone.
//   Narrow payload (DW = 64, as before): {type[2:0], tag[8:0], hdr[19:0], word[31:0]}.
//   Wide payload   (DWL = 32 + 32*WPF): {type[2:0], tag[8:0], hdr[19:0], words[WPF*32-1:0]}, word i at [32*i +: 32];
//   hdr[13:8] = number of words carried - 1 on data flits (0 on every other type, whose payload is word 0); on PSUM
//   flits hdr[7:0] is the column of word 0 and word i carries column hdr[7:0] + i, so a row is at most two flits.
//   The bits above the payload (seq/partition, src, dst) are copied unchanged; even parity is recomputed over the
//   flit the router sees. A data stream always ends with a CRC flit, which flushes the last partial wide flit, so no
//   idle flush is needed. With WPF = 1 both modules are wires: every configuration simulated before this drop is
//   bit-identical. 1024-bit links are WPF = 32 (DWL = 1056; FWL = 1077 on the 8x8 mesh with XW = YW = 3).
module link_packer #(
  parameter int XW = 2, YW = 2, DW = 64, WPF = 1,
  parameter int DWL = 32 + 32 * WPF,
  parameter int FW  = 1 + 2 * (XW + YW) + 8 + DW,
  parameter int FWL = 1 + 2 * (XW + YW) + 8 + DWL,
  parameter int HW  = 2 * (XW + YW) + 8                  // head: seq/partition, src, dst
)(
  input  logic           clk,
  input  logic           rst_n,
  input  logic           core_valid,
  input  logic [FW-1:0]  core_flit,
  output logic           core_ready,
  output logic           link_valid,
  output logic [FWL-1:0] link_flit,
  input  logic           link_ready
);
  localparam logic [2:0] T_RDRSP = 3'd2, T_WRDATA = 3'd4, T_PSUM = 3'd5;
  localparam int CW = (WPF > 1) ? $clog2(WPF + 1) : 1;
  localparam int VW = WPF * 32;                            // the words field
  logic [HW-1:0]  in_head;
  logic [2:0]     in_type;
  logic [8:0]     in_tag;
  logic [19:0]    in_hdr;
  logic [31:0]    in_word;
  logic           in_data;
  assign in_head = core_flit[DW +: HW];
  assign in_type = core_flit[DW-1 -: 3];
  assign in_tag  = core_flit[DW-4 -: 9];
  assign in_hdr  = core_flit[51:32];
  assign in_word = core_flit[31:0];
  assign in_data = (in_type == T_RDRSP) || (in_type == T_WRDATA) || (in_type == T_PSUM);

  generate
    if (WPF == 1) begin : g_wire
      assign core_ready = link_ready;
      assign link_valid = core_valid;
      assign link_flit  = core_flit;
    end else begin : g_pack
      logic           pend_valid;
      logic [HW-1:0]  pend_head;
      logic [2:0]     pend_type;
      logic [8:0]     pend_tag;
      logic [19:0]    pend_hdr;                              // hdr of word 0 (PSUM: its column)
      logic [31:0]    pend_words [WPF];
      logic [CW-1:0]  pend_cnt;                              // words held, 1..WPF while pend_valid
      logic           full, mergeable, emit_pend;
      assign full      = pend_valid && (pend_cnt == WPF);
      assign mergeable = pend_valid && !full && core_valid && in_data && (in_type == pend_type) && (in_head == pend_head) &&
                         (in_tag == pend_tag) && (in_type != T_PSUM || in_hdr[7:0] == pend_hdr[7:0] + 8'(pend_cnt)) &&
                         (in_hdr[19:14] == pend_hdr[19:14]);
      // the pending flit goes out when it is full, or when the next core flit cannot join it
      assign emit_pend = pend_valid && (full || (core_valid && !mergeable));
      logic [FWL-2:0] pend_body, pass_body;
      logic [VW-1:0] pend_vec, pass_vec;
      always_comb begin
        pend_vec = '0;
        for (int w = 0; w < WPF; w++)
          if (w < pend_cnt) pend_vec = pend_vec | (VW'(pend_words[w]) << (32 * w));
        pass_vec = VW'(in_word);
        pend_body = {pend_head, pend_type, pend_tag, pend_hdr[19:14], 6'(pend_cnt - 1), pend_hdr[7:0], pend_vec};
        pass_body = {in_head, in_type, in_tag, in_hdr, pass_vec};
      end
      always_comb begin
        link_valid = 1'b0; link_flit = '0; core_ready = 1'b0;
        if (emit_pend) begin
          link_valid = 1'b1; link_flit = {^pend_body, pend_body};
          core_ready = 1'b0;                                // the core flit that could not join waits one cycle
        end else if (core_valid && !in_data && !pend_valid) begin
          link_valid = 1'b1; link_flit = {^pass_body, pass_body};   // single-word types pass through
          core_ready = link_ready;
        end else if (core_valid && in_data) begin
          core_ready = 1'b1;                                // joins or starts the pending flit
        end
      end
      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
          pend_valid <= 1'b0; pend_cnt <= '0; pend_head <= '0; pend_type <= '0; pend_tag <= '0; pend_hdr <= '0;
          for (int w = 0; w < WPF; w++) pend_words[w] <= '0;
        end else begin
          if (emit_pend && link_ready) begin
            pend_valid <= 1'b0; pend_cnt <= '0;
          end
          if (core_valid && in_data && core_ready) begin
            if (mergeable) begin
              pend_words[pend_cnt] <= in_word;
              pend_cnt <= pend_cnt + 1;
            end else begin                                  // pend_valid is 0 here
              pend_valid <= 1'b1; pend_cnt <= CW'(1);
              pend_head <= in_head; pend_type <= in_type; pend_tag <= in_tag; pend_hdr <= in_hdr;
              pend_words[0] <= in_word;
            end
          end
        end
      end
    end
  endgenerate
endmodule


module link_unpacker #(
  parameter int XW = 2, YW = 2, DW = 64, WPF = 1,
  parameter int DWL = 32 + 32 * WPF,
  parameter int FW  = 1 + 2 * (XW + YW) + 8 + DW,
  parameter int FWL = 1 + 2 * (XW + YW) + 8 + DWL,
  parameter int HW  = 2 * (XW + YW) + 8
)(
  input  logic           clk,
  input  logic           rst_n,
  input  logic           link_valid,
  input  logic [FWL-1:0] link_flit,
  output logic           link_ready,
  output logic           core_valid,
  output logic [FW-1:0]  core_flit,
  input  logic           core_ready
);
  localparam logic [2:0] T_RDRSP = 3'd2, T_WRDATA = 3'd4, T_PSUM = 3'd5;
  localparam int CW = (WPF > 1) ? $clog2(WPF + 1) : 1;
  localparam int VW = WPF * 32;
  generate
    if (WPF == 1) begin : g_wire
      assign link_ready = core_ready;
      assign core_valid = link_valid;
      assign core_flit  = link_flit;
    end else begin : g_unpack
      logic [HW-1:0] l_head;
      logic [2:0]    l_type;
      logic [8:0]    l_tag;
      logic [19:0]   l_hdr;
      logic          l_data;
      logic [CW-1:0] n_words, idx;                           // words in this flit, word presented now
      logic [31:0]   word;
      logic [VW-1:0] words, shifted;
      logic [7:0]    col;
      logic [FW-2:0] body;
      assign l_head  = link_flit[DWL +: HW];
      assign l_type  = link_flit[DWL-1 -: 3];
      assign l_tag   = link_flit[DWL-4 -: 9];
      assign l_hdr   = link_flit[DWL-13 -: 20];
      assign l_data  = (l_type == T_RDRSP) || (l_type == T_WRDATA) || (l_type == T_PSUM);
      assign n_words = l_data ? CW'(l_hdr[13:8]) + CW'(1) : CW'(1);
      assign words   = link_flit[VW-1:0];
      assign shifted = words >> (32 * idx);
      assign word    = shifted[31:0];
      assign col     = l_hdr[7:0] + 8'(idx);
      assign body    = {l_head, l_type, l_tag, l_hdr[19:14], 6'd0, (l_type == T_PSUM) ? col : l_hdr[7:0], word};
      assign core_flit  = {^body, body};
      assign core_valid = link_valid;
      assign link_ready = core_ready && (idx == n_words - 1);
      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) idx <= '0;
        else if (link_valid && core_ready) idx <= (idx == n_words - 1) ? '0 : idx + 1;
      end
    end
  endgenerate
endmodule
