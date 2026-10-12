/* verilator lint_off DECLFILENAME */        // two modules, the reference and the pair, in one file by design
// link_packer_ref.sv -- the drop-0.40 link_packer (rtl/link_pack.sv as it was before drop 0.41), kept as the golden model
//   of the flit stream: the link side combinational from core_flit (the merge, the 1,077-bit output mux and its parity in
//   the beat's cycle). Never to change. link_packer_pair puts it and rtl/link_pack.sv's link_packer on the same beats
//   (sim/run_nic.py I13): with link_ready held high the registered packer must show the reference's link flit one cycle
//   later, bit for bit, and the same core_ready in every cycle.
module link_packer_ref #(
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
  logic unused_core_parity;                                // the flit's parity is the router's business (lint)
  assign unused_core_parity = core_flit[FWB-1];

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


// the reference and the design on the same beats; link_ready per side (n_* the design, r_* the reference)
module link_packer_pair #(
  parameter int XW = 2, YW = 2, BW = 16, WPF = 32,
  parameter int DWB = 32 + 32 * BW,
  parameter int DWL = 32 + 32 * WPF,
  parameter int FWB = 1 + 2 * (XW + YW) + 8 + DWB,
  parameter int FWL = 1 + 2 * (XW + YW) + 8 + DWL
)(
  input  logic           clk,
  input  logic           rst_n,
  input  logic           core_valid,
  input  logic [FWB-1:0] core_flit,
  input  logic           n_link_ready,
  input  logic           r_link_ready,
  output logic           n_core_ready,
  output logic           n_link_valid,
  output logic [FWL-1:0] n_link_flit,
  output logic           r_core_ready,
  output logic           r_link_valid,
  output logic [FWL-1:0] r_link_flit
);
  link_packer #(.XW(XW), .YW(YW), .BW(BW), .WPF(WPF)) u_new (
    .clk(clk), .rst_n(rst_n), .core_valid(core_valid), .core_flit(core_flit), .core_ready(n_core_ready),
    .link_valid(n_link_valid), .link_flit(n_link_flit), .link_ready(n_link_ready));
  link_packer_ref #(.XW(XW), .YW(YW), .BW(BW), .WPF(WPF)) u_ref (
    .clk(clk), .rst_n(rst_n), .core_valid(core_valid), .core_flit(core_flit), .core_ready(r_core_ready),
    .link_valid(r_link_valid), .link_flit(r_link_flit), .link_ready(r_link_ready));
endmodule
