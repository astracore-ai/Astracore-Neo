// wbuf_mem.sv -- per-core weight buffer: one entry per tile row (COLS weights + check weight), v0.2
//   (drop 0.12): every entry is stored as LANES x (39,32) SECDED codewords; a single-bit error in any
//   lane is corrected on read (ecc_ce), a double-bit error is detected (ecc_ue). Written by the DMA,
//   read by the sequencer's shadow-load engine. Entry layout before coding: {check weight, w[COLS-1] .. w[0]}.
//   Since drop 0.32 the buffer is organised in pairs of entries -- DEPTH/2 words of two entries, the word address
//   waddr[WAW-1:1], the entry inside the word waddr[0] -- and its one write port is a word wide: we writes the entry at
//   waddr, we2 the upper entry of the same word (entry waddr + 1; the DMA asserts it only at an even waddr), each
//   through its own LANES encoders. The read is one entry per cycle as before. Drop 0.40: the array is a neo_mem_rf
//   leaf of DEPTH/2 words, each a pair of coded entries (2 x LANES x 39 bits) with one write-enable group per entry --
//   the module the synthesis flow black-boxes and a memory compiler replaces; the SECDED lanes, the pair / entry decode
//   and the flags stay here.
//   Drop 0.41: the read is the leaf's registered port B (an SRAM macro's read: address at the edge, data the cycle
//   after), enabled every cycle; port A is tied off. rdata / rcdata are the entry at the raddr of the PREVIOUS cycle:
//   rb_data -> the entry selected by raddr[0] delayed one cycle (ra0_q) -> the LANES decoders -> rdata. The ce / ue flags
//   rise one cycle after the address that read the faulty codeword. The core delays the sequencer's w_load by one cycle
//   to match (neo_core's w_load_q), so the shadow chain shifts in the cycle the data is valid.
module wbuf_mem #(
  parameter int COLS  = 32,
  parameter int WW    = 8,
  parameter int WCW   = WW + 6,
  parameter int DEPTH = 1024,
  parameter int WAW   = $clog2(DEPTH),
  parameter int EW    = COLS * WW + WCW,            // entry width in bits
  parameter int LANES = (EW + 31) / 32,
  parameter int DV_HOOKS = 0                        // 1: the testbench hook below exists (drop 0.37); 0: the silicon buffer, ports tied off
)(
  input  logic                  clk,
  input  logic                  rst_n,
  input  logic                  we,
  input  logic [WAW-1:0]        waddr,
  input  logic signed [WW-1:0]  wdata [COLS],
  input  logic signed [WCW-1:0] wcdata,
  input  logic                  we2,                      // the upper entry of the same pair (drop 0.32)
  input  logic signed [WW-1:0]  wdata2 [COLS],
  input  logic signed [WCW-1:0] wcdata2,
  input  logic [WAW-1:0]        raddr,
  output logic signed [WW-1:0]  rdata [COLS],
  output logic signed [WCW-1:0] rcdata,
  input  logic                  err_clear,
  output logic                  ecc_ce_sticky,
  output logic                  ecc_ue_sticky,
  // testbench hook (drop 0.37; DV_HOOKS = 1): lane dv_fi_lane of the entry at dv_fi_addr XOR dv_fi_mask at the falling edge
  // while dv_fi_flip -- a stored-codeword fault for the ECC flags (neosim's C9 pokes the cells directly)
  input  logic                  dv_fi_flip,
  input  logic [WAW-1:0]        dv_fi_addr,
  input  logic [3:0]            dv_fi_lane,
  input  logic [38:0]           dv_fi_mask
);
  localparam int PW_ = LANES * 32;
  localparam int EWC = LANES * 39;                   // one coded entry
  localparam int CW  = 2 * EWC;                      // one word of the leaf: the pair of coded entries
  logic [PW_-1:0]  wflat, wflat2;                    // entries packed into lanes
  logic [PW_-1:0]  rflat;
  logic [EWC-1:0]  coded, coded2;                    // the two entries after coding
  logic [CW-1:0]   pair_wdata, pair_rdata;           // the leaf's write word and the pair read (port B: raddr's pair, one cycle later)
  logic [1:0]      pair_we;                          // write-enable per entry of the pair
  logic [EWC-1:0]  rent;                             // the entry read (raddr[0], delayed with the read, selects it inside the pair)
  logic            ra0_q;                            // raddr[0] of the previous cycle: the entry of the pair now on rb_data
  logic [38:0]     rcw  [LANES];
  logic [5:0]      wp   [LANES], wp2  [LANES];
  logic            wop  [LANES], wop2 [LANES];
  logic [31:0]     rfix [LANES];
  logic            ce   [LANES];
  logic            ue   [LANES];
  logic            any_ce, any_ue;

  // pack / unpack with exactly one driver per variable (several continuous assignments to slices
  // of one variable are resolved differently by different simulators)
  always_comb begin
    wflat = '0;
    for (int j = 0; j < COLS; j++) wflat[j*WW +: WW] = wdata[j];
    wflat[COLS*WW +: WCW] = wcdata;
  end
  always_comb begin
    wflat2 = '0;
    for (int j = 0; j < COLS; j++) wflat2[j*WW +: WW] = wdata2[j];
    wflat2[COLS*WW +: WCW] = wcdata2;
  end
  always_comb begin
    rflat = '0;
    for (int l = 0; l < LANES; l++) rflat[l*32 +: 32] = rfix[l];
  end
  always_comb begin
    for (int j = 0; j < COLS; j++) rdata[j] = rflat[j*WW +: WW];
  end
  assign rcdata = rflat[COLS*WW +: WCW];

  generate
    for (genvar l = 0; l < LANES; l++) begin : g_lane
      ecc39_enc u_enc  (.d(wflat[l*32 +: 32]),  .p(wp[l]),  .op(wop[l]));
      ecc39_enc u_enc2 (.d(wflat2[l*32 +: 32]), .p(wp2[l]), .op(wop2[l]));
      ecc39_dec u_dec (.d(rcw[l][31:0]), .p(rcw[l][37:32]), .op(rcw[l][38]), .d_out(rfix[l]), .ce(ce[l]), .ue(ue[l]));
      assign coded[39*l +: 39]  = {wop[l],  wp[l],  wflat[l*32 +: 32]};
      assign coded2[39*l +: 39] = {wop2[l], wp2[l], wflat2[l*32 +: 32]};
      assign rcw[l] = rent[39*l +: 39];
    end
  endgenerate
  assign rent = ra0_q ? pair_rdata[CW-1:EWC] : pair_rdata[EWC-1:0];

  always_comb begin
    any_ce = 1'b0; any_ue = 1'b0;
    for (int l = 0; l < LANES; l++) begin
      any_ce = any_ce | ce[l];
      any_ue = any_ue | ue[l];
    end
  end

  // the leaf's write: we writes the entry waddr[0] of the pair waddr[WAW-1:1], we2 the pair's upper entry in the same
  // cycle (the upper entry takes we2's data when both address it, as the two statements did before drop 0.40)
  assign pair_we    = {we2 || (we && waddr[0]), we && !waddr[0]};
  assign pair_wdata = {we2 ? coded2 : coded, coded};
  // the fault hook (DV_HOOKS = 1): lane dv_fi_lane of entry dv_fi_addr[0] XOR dv_fi_mask, as a word-wide flip of the pair;
  // at DV_HOOKS = 0 the leaf's hook ports are constants and nothing of this exists
  logic [CW-1:0]    fi_mask_w;
  logic [WAW-2:0]   fi_pair;
  logic             fi_en;
  logic [CW-1:0]    unused_ra, unused_bd;          // the leaf's unused read ports (port A tied off, no backdoor here)
  generate
    if (DV_HOOKS != 0) begin : g_dvd
      logic [31:0] fi_shift;
      assign fi_shift  = 32'd39 * (32'(LANES) * 32'(dv_fi_addr[0]) + 32'(dv_fi_lane));
      assign fi_mask_w = CW'(dv_fi_mask) << fi_shift;
      assign fi_en     = dv_fi_flip && (32'(dv_fi_lane) < LANES);
      assign fi_pair   = dv_fi_addr[WAW-1:1];
    end else begin : g_nodvd
      assign fi_mask_w = '0; assign fi_en = 1'b0; assign fi_pair = '0;
      logic unused_dv;
      assign unused_dv = ^{dv_fi_flip, dv_fi_addr, dv_fi_lane, dv_fi_mask};
    end
  endgenerate
  neo_mem_rf #(.W(CW), .D(DEPTH/2), .G(2), .RB(1), .DV_HOOKS(DV_HOOKS)) u_mem (
    .clk(clk),
    .we(pair_we), .waddr(waddr[WAW-1:1]), .wdata(pair_wdata),
    .ra_addr('0), .ra_data(unused_ra),
    .rb_en(1'b1), .rb_addr(raddr[WAW-1:1]), .rb_data(pair_rdata),
    .bd_we('0), .bd_addr('0), .bd_wdata('0), .bd_rdata(unused_bd),
    .fi_en(fi_en), .fi_or(1'b0), .fi_addr(fi_pair), .fi_mask(fi_mask_w));
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      ra0_q <= 1'b0;
      ecc_ce_sticky <= 1'b0; ecc_ue_sticky <= 1'b0;
    end else begin
      ra0_q <= raddr[0];
      if (err_clear) begin ecc_ce_sticky <= 1'b0; ecc_ue_sticky <= 1'b0; end
      if (any_ce) ecc_ce_sticky <= 1'b1;        // the read happens every cycle: flags follow any read, one cycle after its address
      if (any_ue) ecc_ue_sticky <= 1'b1;
    end
  end
endmodule
