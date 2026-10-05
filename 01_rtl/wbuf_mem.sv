// wbuf_mem.sv -- per-core weight buffer: one entry per tile row (COLS weights + check weight), v0.2
//   (drop 0.12): every entry is stored as LANES x (39,32) SECDED codewords; a single-bit error in any
//   lane is corrected on read (ecc_ce), a double-bit error is detected (ecc_ue). Written by the DMA,
//   read by the sequencer's shadow-load engine. Entry layout before coding: {check weight, w[COLS-1] .. w[0]}.
module wbuf_mem #(
  parameter int COLS  = 32,
  parameter int WW    = 8,
  parameter int WCW   = WW + 6,
  parameter int DEPTH = 1024,
  parameter int WAW   = $clog2(DEPTH),
  parameter int EW    = COLS * WW + WCW,            // entry width in bits
  parameter int LANES = (EW + 31) / 32
)(
  input  logic                  clk,
  input  logic                  rst_n,
  input  logic                  we,
  input  logic [WAW-1:0]        waddr,
  input  logic signed [WW-1:0]  wdata [COLS],
  input  logic signed [WCW-1:0] wcdata,
  input  logic [WAW-1:0]        raddr,
  output logic signed [WW-1:0]  rdata [COLS],
  output logic signed [WCW-1:0] rcdata,
  input  logic                  err_clear,
  output logic                  ecc_ce_sticky,
  output logic                  ecc_ue_sticky
);
  localparam int PW_ = LANES * 32;
  logic [PW_-1:0]  wflat;                            // entry packed into lanes
  logic [PW_-1:0]  rflat;
  logic [38:0]     mem [DEPTH][LANES];
  logic [38:0]     rcw  [LANES];
  logic [5:0]      wp   [LANES];
  logic            wop  [LANES];
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
    rflat = '0;
    for (int l = 0; l < LANES; l++) rflat[l*32 +: 32] = rfix[l];
  end
  always_comb begin
    for (int j = 0; j < COLS; j++) rdata[j] = rflat[j*WW +: WW];
  end
  assign rcdata = rflat[COLS*WW +: WCW];

  generate
    for (genvar l = 0; l < LANES; l++) begin : g_lane
      ecc39_enc u_enc (.d(wflat[l*32 +: 32]), .p(wp[l]), .op(wop[l]));
      ecc39_dec u_dec (.d(rcw[l][31:0]), .p(rcw[l][37:32]), .op(rcw[l][38]), .d_out(rfix[l]), .ce(ce[l]), .ue(ue[l]));
      assign rcw[l] = mem[raddr][l];
    end
  endgenerate

  always_comb begin
    any_ce = 1'b0; any_ue = 1'b0;
    for (int l = 0; l < LANES; l++) begin
      any_ce = any_ce | ce[l];
      any_ue = any_ue | ue[l];
    end
  end

  always_ff @(posedge clk) begin
    if (we) begin
      for (int l = 0; l < LANES; l++) mem[waddr][l] <= {wop[l], wp[l], wflat[l*32 +: 32]};
    end
  end
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      ecc_ce_sticky <= 1'b0; ecc_ue_sticky <= 1'b0;
    end else begin
      if (err_clear) begin ecc_ce_sticky <= 1'b0; ecc_ue_sticky <= 1'b0; end
      if (any_ce) ecc_ce_sticky <= 1'b1;        // the read is combinational on raddr: flags follow any read
      if (any_ue) ecc_ue_sticky <= 1'b1;
    end
  end
endmodule
