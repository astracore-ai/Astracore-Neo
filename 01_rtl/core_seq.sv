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
//   port (an owner drains the result, a contributor drains its partial sums for the owner).
//   Weight tiles live in a buffer outside this module (wbuf_mem, read at w_raddr) so that a
//   duplicate sequencer (R10, lockstep with comparator) shares one copy. `done` pulses for one
//   cycle when the drain has finished.
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

  logic [WAW-1:0]      laddr;               // weight-buffer row of the load: tile ltile, row ROWS-1-lcnt (in the buffer's address width)
  assign laddr = WAW'(32'(ltile) * ROWS + (ROWS - 1 - 32'(lcnt)));
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
  assign tile_ok   = !((ky == 0) && (kx == 0)) || (tiles_ready > ct - cfg_ct0);
  assign f_start   = (state == S_START) && tile_ok;
  assign rd_en     = (state == S_DRAIN);
  assign rd_idx    = dcnt;
  assign done      = (state == S_DONE);
  assign busy      = (state != S_IDLE);
  assign state_dbg = state;
  assign reduce_ready = (state == S_REDUCE);

  // next-run enumeration (kx fastest, then ky, then ct), bounded by this core's share
  always_comb begin
    nct = ct; nky = ky; nkx = kx; has_next = (rcnt < cfg_rn);
    if (kx != cfg_k - 1) begin
      nkx = kx + 1;
    end else if (ky != cfg_k - 1) begin
      nkx = '0; nky = ky + 1;
    end else if (ct != cfg_ct_n - 1) begin
      nkx = '0; nky = '0; nct = ct + 1;
    end else begin
      has_next = 1'b0;
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state <= S_IDLE; ct <= '0; ky <= '0; kx <= '0; first_q <= 1'b0;
      lcnt <= '0; loading <= 1'b0; loaded <= 1'b0; tcnt <= '0; wcnt <= '0; dcnt <= '0; ltile <= '0;
      ct_free_q <= 1'b0; ct_free_idx_q <= '0; rcnt <= '0; ext_cnt <= '0;
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
            ct <= cfg_ct0; ky <= cfg_ky0; kx <= cfg_kx0; first_q <= 1'b1;
            ltile <= '0; lcnt <= '0; loading <= 1'b1; loaded <= 1'b0;
            rcnt <= 16'd1; ext_cnt <= '0;
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
          if (wcnt == 16'(LAT + 3)) begin
            dcnt  <= '0;
            state <= (cfg_contrib_n > 0) ? S_REDUCE : S_DRAIN;
          end
        end
        S_REDUCE: begin
          if (ext_fire) ext_cnt <= ext_cnt + 1;
          if (ext_cnt + (ext_fire ? 1 : 0) >= cfg_contrib_n * cfg_m) begin
            dcnt  <= '0;
            state <= S_DRAIN;
          end
        end
        S_DRAIN: begin
          if (dcnt == cfg_m - 1) state <= S_DONE;
          else dcnt <= dcnt + 1;
        end
        S_DONE: state <= S_IDLE;
        default: state <= S_IDLE;
      endcase
    end
  end
endmodule
