// core_seq.sv -- per-core sequencer, v0.2 (drop 0.6: K-split). Executes one layer descriptor:
//   the core's share of the runs r = (ct, ky, kx), cfg_rn runs starting at (cfg_ct0, cfg_ky0,
//   cfg_kx0) in channel-tile-major order (R8: a group's K-tiles may be split across cores; each
//   core's weight tiles are stored at LOCAL indices 0..cfg_rn-1 in run order). For each run:
//     shadow-load weight tile r from the local weight buffer (tile r = (ct*k + ky)*k + kx,
//     row ROWS-1-t at cycle t), start the feeder, and shadow-load tile r+1 while run r
//     streams (no earlier than PE_LAT*ROWS+COLS cycles after the run's token);
//   after the last run, wait for the pipeline to drain into the accumulator; if this core owns
//   the group (cfg_contrib_n > 0) accept cfg_contrib_n * cfg_m partial-sum rows on the reduce
//   port first (reduce_ready); then read the accumulator rows 0..cfg_m-1 out through the drain
//   port (an owner drains the result, a contributor drains its partial sums for the owner). cfg_m = 0 means ACC_ROWS
//   rows (drop 0.44, review #13: the drain's end compared at IDXW bits, the reduce count at 32 -- the one meaning the
//   vector unit already gave it; a contributor group with cfg_m = 0 sends cfg_contrib_n * ACC_ROWS rows).
//   Weight tiles live in a buffer outside this module (wbuf_mem, read at w_raddr) so that a
//   duplicate sequencer (R10, lockstep with comparator) shares one copy. `done` pulses for one
//   cycle when the drain has finished.
//   Drop 0.41 -- the cycle counts with registered buffer reads. T = the cycle f_start is asserted; S_RUN from T+1 with
//   tcnt = 0; M = rows of the run; LAT = PE_LAT*ROWS + COLS (a row entering the array at cycle c leaves at c + LAT).
//   The pipeline (drop 0.40 in brackets): the feeder's token at T+2 [T+1], row m at T+3+m [T+2+m], f_busy low first at
//   B = T+M+3 [T+M+2]; PE(i, j) adopts its shadow weights at the edge ending T+2+PE_LAT*i+j, the last PE at
//   T+2+LAT-PE_LAT; a shadow load begun at cycle L shifts at the edges ending L+1 .. L+ROWS [L .. L+ROWS-1] (neo_core's
//   w_load_q, beside the weight buffer's registered read); row m is written at the end of T+4+m+LAT [T+2+m+LAT].
//     shadow load       tcnt == LAT+1, unchanged    first shift at the edge ending T+LAT+4, after the last adoption: the
//                                                   minimum is LAT-PE_LAT; token and strobe both moved one cycle, so the
//                                                   3-cycle margin and the constant stand
//     run to run        !f_busy && loaded && tcnt > 2, unchanged: the next S_START at max(B, T+LAT+ROWS+3) + 1, one
//                                                   cycle later where f_busy binds -- the last run (loaded is set at its
//                                                   start) and runs with M > LAT+ROWS; tcnt > 2 only guards the start
//                                                   handshake (f_busy rises at tcnt = 0 and now falls at tcnt = M+2)
//     drain wait        wcnt == LAT+4 [LAT+3]       the last row is written at the end of B+LAT [B+LAT-1]; the drain reads
//                                                   row 0 at the end of B+2+wcnt: minimum LAT-1 [LAT-2]; +1 for the
//                                                   accumulator's second stage, drop 0.40's margin of 5 cycles kept
//     reduce to drain   one cycle more (red_last)   the last partial-sum row is accepted at c and written at the end of
//                                                   c+1: the drain starts at c+2 [c+1]; reduce_ready drops at c+1
//   Drop 0.43 -- the first-run test without the subtraction: tile_ok compared tiles_ready with ct - cfg_ct0, a 16-bit
//   subtraction and a 16-bit compare in front of f_start (an output to the feeder and the lockstep comparator: 1,110 ps,
//   the unit's one endpoint over 1,250 ps); ct_rel = ct - cfg_ct0 is now a register kept beside ct (0 at go, +1 when the
//   enumeration steps to the next channel tile), so f_start is the compare alone. Cycle for cycle the same sequencer.
module core_seq #(
  parameter int ROWS       = 32,
  parameter int COLS       = 32,
  parameter int IDXW       = 9,
  parameter int WBUF_DEPTH = 1024,
  parameter int WAW        = $clog2(WBUF_DEPTH),
  parameter int PE_LAT     = 2
)(
  input  logic                  clk,
  input  logic                  rst_n,
  // layer descriptor
  input  logic signed [15:0]    cfg_k,        // kernel size
  input  logic signed [15:0]    cfg_ct_n,     // number of channel tiles (for ct_free and run order)
  input  logic signed [15:0]    cfg_ct0,      // first run of this core's share
  input  logic signed [15:0]    cfg_ky0,
  input  logic signed [15:0]    cfg_kx0,
  input  logic signed [15:0]    cfg_rn,       // number of runs in this core's share
  input  logic signed [15:0]    cfg_contrib_n,// partial-sum contributors to wait for (owner), else 0
  input  logic [IDXW-1:0]       cfg_m,        // output rows to drain (ho*wo)
  input  logic signed [15:0]    tiles_ready,  // channel tiles landed in the activation buffer (from the DMA)
  input  logic                  go,
  output logic                  done,
  output logic                  busy,
  output logic [2:0]            state_dbg,
  output logic                  ct_free,      // pulse: the activation region of ct_free_idx is no longer read
  output logic signed [15:0]    ct_free_idx,
  output logic                  reduce_ready, // owner: accepting partial sums on the reduce port
  input  logic                  ext_fire,     // one partial-sum row accepted this cycle
  // feeder
  output logic signed [15:0]    f_ct,
  output logic signed [15:0]    f_ky,
  output logic signed [15:0]    f_kx,
  output logic                  f_first,
  output logic                  f_start,
  input  logic                  f_busy,
  // shadow chain: the weight buffer (outside, so that a duplicate sequencer shares it) is read at w_raddr
  output logic                  w_load,
  output logic [WAW-1:0]        w_raddr,
  // accumulator drain
  output logic                  rd_en,
  output logic [IDXW-1:0]       rd_idx
);
  localparam int LAT = PE_LAT * ROWS + COLS;   // core latency: token-to-array clearance and drain
  localparam logic [2:0] S_IDLE = 3'd0, S_LOAD0 = 3'd1, S_START = 3'd2, S_RUN = 3'd3,
                         S_WAIT = 3'd4, S_DRAIN = 3'd5, S_DONE = 3'd6, S_REDUCE = 3'd7;

  logic [2:0]          state;
  logic signed [15:0]  ct, ky, kx;          // current run
  logic signed [15:0]  nct, nky, nkx;       // next run
  logic                has_next;
  logic                nct_inc;             // the next run is in the next channel tile (nct = ct + 1)
  logic signed [15:0]  ct_rel;              // drop 0.43: ct - cfg_ct0, the channel tile relative to the share's first
  logic                first_q;
  logic signed [15:0]  lcnt;                // shadow-load row counter
  logic                loading;
  logic                loaded;
  logic signed [15:0]  tcnt;                // cycles since start
  logic signed [15:0]  wcnt;
  logic [IDXW-1:0]     dcnt;
  logic signed [15:0]  ltile;               // tile being loaded (local index)
  logic signed [15:0]  rcnt;                // runs started so far
  logic signed [31:0]  ext_cnt;             // partial-sum rows accepted
  logic                red_last;            // drop 0.41: the count is reached; one cycle for the last row's accumulate, then the drain

  logic [WAW-1:0]      laddr;               // weight-buffer row of the load: tile ltile, row ROWS-1-lcnt (in the buffer's address width)
  assign laddr = WAW'(32'(ltile) * ROWS + (ROWS - 1 - 32'(lcnt)));
  // drop 0.44 (review #13): cfg_m = 0 means ACC_ROWS = 2^IDXW rows -- the drain's last index at IDXW bits (cm1 wraps to all
  // ones, as vec_unit's) and the reduce count at 32 bits (m_rows); the 32-bit `cfg_m - 1` of before never ended the drain
  logic [IDXW-1:0]     cm1;                 // cfg_m - 1 at IDXW bits: the last row index of the drain
  logic signed [31:0]  m_rows;              // the rows a contributor sends: cfg_m, or ACC_ROWS when cfg_m = 0
  assign cm1    = cfg_m - IDXW'(1);
  assign m_rows = (cfg_m == '0) ? (32'sd1 <<< IDXW) : 32'(cfg_m);
  logic                ct_free_q;
  logic signed [15:0]  ct_free_idx_q;
  assign ct_free     = ct_free_q;
  assign ct_free_idx = ct_free_idx_q;

  assign w_raddr = laddr;
  assign w_load  = loading;

  assign f_ct      = ct;
  assign f_ky      = ky;
  assign f_kx      = kx;
  assign f_first   = first_q;
  // the first run of a channel tile waits for that tile to have landed: the DMA counts the tiles
  // this core fetched, so the comparison is relative to the core's first tile (K-split shares)
  logic tile_ok;
  assign tile_ok   = !((ky == 0) && (kx == 0)) || (tiles_ready > ct_rel);
  assign f_start   = (state == S_START) && tile_ok;
  assign rd_en     = (state == S_DRAIN);
  assign rd_idx    = dcnt;
  assign done      = (state == S_DONE);
  assign busy      = (state != S_IDLE);
  assign state_dbg = state;
  assign reduce_ready = (state == S_REDUCE) && !red_last;

  // next-run enumeration (kx fastest, then ky, then ct), bounded by this core's share
  always_comb begin
    nct = ct; nky = ky; nkx = kx; has_next = (rcnt < cfg_rn); nct_inc = 1'b0;
    if (kx != cfg_k - 1) begin
      nkx = kx + 1;
    end else if (ky != cfg_k - 1) begin
      nkx = '0; nky = ky + 1;
    end else if (ct != cfg_ct_n - 1) begin
      nkx = '0; nky = '0; nct = ct + 1; nct_inc = 1'b1;
    end else begin
      has_next = 1'b0;
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state <= S_IDLE; ct <= '0; ky <= '0; kx <= '0; first_q <= 1'b0; ct_rel <= '0;
      lcnt <= '0; loading <= 1'b0; loaded <= 1'b0; tcnt <= '0; wcnt <= '0; dcnt <= '0; ltile <= '0;
      ct_free_q <= 1'b0; ct_free_idx_q <= '0; rcnt <= '0; ext_cnt <= '0; red_last <= 1'b0;
    end else begin
      ct_free_q <= 1'b0;
      // shadow-load engine: ROWS cycles once started
      if (loading) begin
        if (lcnt == 16'(ROWS - 1)) begin
          loading <= 1'b0;
          loaded  <= 1'b1;
        end else begin
          lcnt <= lcnt + 1;
        end
      end
      case (state)
        S_IDLE: begin
          if (go) begin
            ct <= cfg_ct0; ky <= cfg_ky0; kx <= cfg_kx0; first_q <= 1'b1; ct_rel <= '0;
            ltile <= '0; lcnt <= '0; loading <= 1'b1; loaded <= 1'b0;
            rcnt <= 16'd1; ext_cnt <= '0; red_last <= 1'b0;
            state <= S_LOAD0;
          end
        end
        S_LOAD0: begin
          if (loaded) state <= S_START;
        end
        S_START: begin
          if (tile_ok) begin
            tcnt   <= '0;
            loaded <= !has_next;            // nothing to load if this is the last run
            state  <= S_RUN;
          end
        end
        S_RUN: begin
          tcnt <= tcnt + 1;
          if (has_next && !loaded && !loading && tcnt == 16'(LAT + 1)) begin
            ltile   <= rcnt;                  // local tile index of the next run
            lcnt    <= '0;
            loading <= 1'b1;
          end
          if (!f_busy && loaded && tcnt > 2) begin
            if (nct != ct || !has_next) begin
              ct_free_q     <= 1'b1;            // last pass over this channel tile has streamed
              ct_free_idx_q <= ct;
            end
            if (has_next) begin
              ct <= nct; ky <= nky; kx <= nkx; first_q <= 1'b0;
              if (nct_inc) ct_rel <= ct_rel + 1;
              rcnt  <= rcnt + 1;
              state <= S_START;
            end else begin
              wcnt  <= '0;
              state <= S_WAIT;
            end
          end
        end
        S_WAIT: begin
          wcnt <= wcnt + 1;
          if (wcnt == 16'(LAT + 4)) begin       // the last row is written (two-cycle accumulate); see the table above
            dcnt  <= '0;
            state <= (cfg_contrib_n > 0) ? S_REDUCE : S_DRAIN;
          end
        end
        S_REDUCE: begin
          if (ext_fire) ext_cnt <= ext_cnt + 1;
          if (red_last) begin                  // the last partial-sum row is in the accumulator's stage 1: written this cycle
            red_last <= 1'b0;
            dcnt     <= '0;
            state    <= S_DRAIN;
          end else if (ext_cnt + (ext_fire ? 1 : 0) >= 32'(cfg_contrib_n) * m_rows) begin   // (m_rows: cfg_m = 0 is ACC_ROWS, drop 0.44)
            red_last <= 1'b1;                  // the last row accepted this cycle (stage 0); reduce_ready drops
          end
        end
        S_DRAIN: begin
          if (dcnt == cm1) state <= S_DONE;        // at IDXW bits (drop 0.44, review #13): cfg_m = 0 drains ACC_ROWS rows
          else dcnt <= dcnt + 1;
        end
        S_DONE: state <= S_IDLE;
        default: state <= S_IDLE;
      endcase
    end
  end
endmodule
