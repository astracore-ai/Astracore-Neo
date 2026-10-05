// act_feeder.sv -- sliding-window activation feeder (implicit im2col), v0.3.
//   Holds channel tiles of one input in a local buffer of REGIONS regions (a power of two):
//   abuf[(ct mod regions)*tile_pixels + iy*w + ix][channel]; the DMA refills a region after the
//   sequencer's ct_free for the tile that used it,
//   ROWS channels per entry. For a run at kernel position (cfg_ky, cfg_kx) over channel tile cfg_ct
//   it walks the output grid (oy, ox) and emits, one row per cycle, the ROWS channels of input pixel
//   (oy*s + ky - p, ox*s + kx - p), or zeros outside the tile (padding). Each output row is tagged
//   with its accumulator index (row_idx) and the acc_first flag sampled at start.
//   Cycle after start: swap token (swap=1, valid=0). Then one valid row per cycle.
//   Safety (R7 from the FMEDA): the address generator and run FSM are duplicated; the two copies
//   see the same inputs and a comparator flags any disagreement on address, bounds, valid or
//   row index (ctrl_err, sticky), because a consistently wrong input is invisible to ABFT.
//   ctrl_fault_inject (DV only) corrupts bit 0 of the primary address to prove the comparator.
//   In silicon abuf is an ECC SRAM macro with a one-cycle read; this is its behavioural model.
module act_feeder #(
  parameter int ROWS       = 32,
  parameter int XW         = 8,
  parameter int ABUF_DEPTH = 4096,
  parameter int AW         = $clog2(ABUF_DEPTH),
  parameter int IDXW       = 9
)(
  input  logic                 clk,
  input  logic                 rst_n,
  // buffer write port (DMA)
  input  logic                 abuf_we,
  input  logic [AW-1:0]        abuf_waddr,
  input  logic signed [XW-1:0] abuf_wdata [ROWS],
  // configuration (static during a run)
  input  logic signed [15:0]   cfg_h,          // input tile height (rows of pixels)
  input  logic signed [15:0]   cfg_w,          // input tile width
  input  logic signed [15:0]   cfg_ho,         // output rows of the whole layer
  input  logic signed [15:0]   cfg_wo,         // output columns
  input  logic signed [15:0]   cfg_oy0,        // first output row of this descriptor's M-chunk
  input  logic signed [15:0]   cfg_oy_n,       // output rows in this M-chunk
  input  logic signed [15:0]   cfg_iy0,        // first input row present in the activation region
  input  logic signed [15:0]   cfg_s,          // stride
  input  logic signed [15:0]   cfg_p,          // padding
  input  logic signed [15:0]   cfg_tile_pixels,// entries per channel tile (= h*w)
  input  logic signed [15:0]   cfg_regions_m1, // regions in the buffer minus one (regions are a power of two)
  input  logic signed [15:0]   cfg_ct,         // channel tile of this run
  input  logic signed [15:0]   cfg_ky,         // kernel position of this run
  input  logic signed [15:0]   cfg_kx,
  input  logic                 acc_first,      // 1: this run overwrites the accumulator rows
  // control
  input  logic                 start,
  output logic                 busy,
  // stream out
  output logic signed [XW-1:0] x_vec [ROWS],
  output logic                 valid,
  output logic                 swap,
  output logic [IDXW-1:0]      row_idx,
  output logic                 first,
  // safety
  input  logic                 ctrl_clear,
  output logic                 ctrl_err,
  output logic                 ctrl_err_sticky,
  input  logic                 ctrl_fault_inject
);
  logic signed [XW-1:0] abuf [ABUF_DEPTH][ROWS];

  // ---- primary address generator / run FSM ----
  logic                 running;
  logic                 tok;
  logic signed [15:0]   oy;
  logic signed [15:0]   ox;
  logic [IDXW-1:0]      m;
  logic                 first_q;
  logic signed [17:0]   iy;
  logic signed [17:0]   ix;
  logic                 inb;
  logic [AW-1:0]        addr_raw;
  logic [AW-1:0]        addr;

  assign iy       = oy * cfg_s + cfg_ky - cfg_p;
  assign ix       = ox * cfg_s + cfg_kx - cfg_p;
  assign inb      = (iy >= 0) && (iy < cfg_h) && (ix >= 0) && (ix < cfg_w);
  assign addr_raw = inb ? ((cfg_ct & cfg_regions_m1) * cfg_tile_pixels + (iy - cfg_iy0) * cfg_w + ix) : '0;
  assign addr     = addr_raw ^ {{(AW-1){1'b0}}, ctrl_fault_inject};

  assign busy    = running;
  assign swap    = tok;
  assign valid   = running && !tok;
  assign row_idx = m;
  assign first   = first_q;

  generate
    for (genvar c = 0; c < ROWS; c++) begin : g_out
      assign x_vec[c] = (valid && inb) ? abuf[addr][c] : '0;
    end
  endgenerate

  always_ff @(posedge clk) begin
    if (abuf_we) begin
      for (int c = 0; c < ROWS; c++) abuf[abuf_waddr][c] <= abuf_wdata[c];
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      running <= 1'b0;
      tok     <= 1'b0;
      oy      <= '0;
      ox      <= '0;
      m       <= '0;
      first_q <= 1'b0;
    end else begin
      tok <= 1'b0;
      if (start && !running) begin
        running <= 1'b1;
        tok     <= 1'b1;
        oy      <= cfg_oy0;
        ox      <= '0;
        m       <= '0;
        first_q <= acc_first;
      end else if (running && !tok) begin
        m <= m + 1;
        if (ox == cfg_wo - 1) begin
          ox <= '0;
          if (oy == cfg_oy0 + cfg_oy_n - 1) running <= 1'b0;
          else oy <= oy + 1;
        end else begin
          ox <= ox + 1;
        end
      end
    end
  end

  // ---- duplicate address generator / run FSM (R7) and comparator ----
  logic                 running_d;
  logic                 tok_d;
  logic signed [15:0]   oy_d;
  logic signed [15:0]   ox_d;
  logic [IDXW-1:0]      m_d;
  logic signed [17:0]   iy_d;
  logic signed [17:0]   ix_d;
  logic                 inb_d;
  logic [AW-1:0]        addr_d;
  logic                 valid_d;

  assign iy_d    = oy_d * cfg_s + cfg_ky - cfg_p;
  assign ix_d    = ox_d * cfg_s + cfg_kx - cfg_p;
  assign inb_d   = (iy_d >= 0) && (iy_d < cfg_h) && (ix_d >= 0) && (ix_d < cfg_w);
  assign addr_d  = inb_d ? ((cfg_ct & cfg_regions_m1) * cfg_tile_pixels + (iy_d - cfg_iy0) * cfg_w + ix_d) : '0;
  assign valid_d = running_d && !tok_d;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      running_d <= 1'b0;
      tok_d     <= 1'b0;
      oy_d      <= '0;
      ox_d      <= '0;
      m_d       <= '0;
    end else begin
      tok_d <= 1'b0;
      if (start && !running_d) begin
        running_d <= 1'b1;
        tok_d     <= 1'b1;
        oy_d      <= cfg_oy0;
        ox_d      <= '0;
        m_d       <= '0;
      end else if (running_d && !tok_d) begin
        m_d <= m_d + 1;
        if (ox_d == cfg_wo - 1) begin
          ox_d <= '0;
          if (oy_d == cfg_oy0 + cfg_oy_n - 1) running_d <= 1'b0;
          else oy_d <= oy_d + 1;
        end else begin
          ox_d <= ox_d + 1;
        end
      end
    end
  end

  assign ctrl_err = (valid != valid_d) || (swap != tok_d) || (row_idx != m_d) ||
                    (valid && ((addr != addr_d) || (inb != inb_d)));

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n)          ctrl_err_sticky <= 1'b0;
    else if (ctrl_clear) ctrl_err_sticky <= 1'b0;
    else if (ctrl_err)   ctrl_err_sticky <= 1'b1;
  end
endmodule
