// requant.sv -- per-channel requantization of INT32 accumulator rows to INT8 (drop 0.5).
//   q = sat8( round( acc * mult >> shift ) + zp ), then optional ReLU in the quantized domain
//   (max with zp). mult is an unsigned 16-bit multiplier and shift a 5-bit right shift, i.e. the
//   output scale is scale_in * mult / 2^shift, chosen by the compiler per output channel
//   (TFLite-style fixed-point requantization). Rounding is round-half-up on the shifted value.
//   Two pipeline stages: multiply, then shift/round/offset/saturate. Tables written by DMA.
module requant #(
  parameter int COLS = 32,
  parameter int PW   = 32,
  parameter int MW   = 16,
  parameter int CW   = $clog2(COLS)
)(
  input  logic                 clk,
  input  logic                 rst_n,
  // per-column table write
  input  logic                 tbl_we,
  input  logic [CW-1:0]        tbl_addr,
  input  logic [MW-1:0]        tbl_mult,
  input  logic [4:0]           tbl_shift,
  input  logic signed [7:0]    tbl_zp,
  input  logic                 relu,
  // stream
  input  logic                 in_valid,
  input  logic signed [PW-1:0] in_acc [COLS],
  output logic                 out_valid,
  output logic signed [7:0]    out_q [COLS],
  // table parity (drop 0.12): each entry stores odd parity over {mult, shift, zp}; checked on every use
  input  logic                 err_clear,
  output logic                 tbl_perr_sticky,
  input  logic                 tbl_fault_inject    // DV: corrupt bit 0 of column 0's multiplier
);
  localparam int BW = PW + MW + 1;                 // product width

  logic [MW-1:0]      mult  [COLS];
  logic [4:0]         shft  [COLS];
  logic signed [7:0]  zp    [COLS];
  logic               par   [COLS];
  logic               wrt   [COLS];                  // entry has been written (parity meaningful)
  logic [MW-1:0]      mult_p [COLS];                 // multiplier as used (with the DV fault on column 0)
  logic               perr;

  always_ff @(posedge clk) begin
    if (tbl_we) begin
      mult[tbl_addr] <= tbl_mult;
      shft[tbl_addr] <= tbl_shift;
      zp[tbl_addr]   <= tbl_zp;
      par[tbl_addr]  <= ~(^{tbl_mult, tbl_shift, tbl_zp});     // odd parity
    end
  end
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int j = 0; j < COLS; j++) wrt[j] <= 1'b0;
    end else if (tbl_we) begin
      wrt[tbl_addr] <= 1'b1;
    end
  end
  generate
    for (genvar j = 0; j < COLS; j++) begin : g_mp
      if (j == 0) begin : g_f
        assign mult_p[j] = mult[j] ^ {{(MW-1){1'b0}}, tbl_fault_inject};
      end else begin : g_n
        assign mult_p[j] = mult[j];
      end
    end
  endgenerate
  always_comb begin
    perr = 1'b0;
    if (in_valid) begin
      for (int j = 0; j < COLS; j++) begin
        if (wrt[j] && ((^{mult_p[j], shft[j], zp[j]} ^ par[j]) != 1'b1)) perr = 1'b1;
      end
    end
  end
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n)         tbl_perr_sticky <= 1'b0;
    else if (err_clear) tbl_perr_sticky <= 1'b0;
    else if (perr)      tbl_perr_sticky <= 1'b1;
  end

  // stage 1: product (PW+MW+1 bits, signed) and the column's shift/zp travelling with it
  logic                     v1;
  logic signed [PW+MW:0]    prod [COLS];
  logic [4:0]               sh1  [COLS];
  logic signed [7:0]        zp1  [COLS];

  // stage 2: shift with rounding, offset, saturate, relu
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      v1 <= 1'b0;
      out_valid <= 1'b0;
      for (int j = 0; j < COLS; j++) begin
        prod[j] <= '0; sh1[j] <= '0; zp1[j] <= '0; out_q[j] <= '0;
      end
    end else begin
      v1 <= in_valid;
      for (int j = 0; j < COLS; j++) begin
        prod[j] <= in_acc[j] * $signed({1'b0, mult_p[j]});
        sh1[j]  <= shft[j];
        zp1[j]  <= zp[j];
      end
      out_valid <= v1;
      for (int j = 0; j < COLS; j++) begin
        logic signed [PW+MW:0] bias;
        logic signed [PW+MW:0] r;
        logic signed [PW+MW:0] v;
        bias = (BW'(1) <<< sh1[j]) >>> 1;                   // 2^(shift-1), or 0 when shift == 0
        r    = (prod[j] + bias) >>> sh1[j];
        v    = r + zp1[j];
        if (relu && (v < zp1[j])) v = zp1[j];
        if (v > 127)       out_q[j] <= 8'sd127;
        else if (v < -128) out_q[j] <= -8'sd128;
        else               out_q[j] <= v[7:0];
      end
    end
  end
endmodule
