// lockstep_core_seq_fv.sv -- formal harness for the sequencer pair of neo_core (drop 0.41): two rtl/core_seq at the 16x8
//   parameters (ROWS = 16, COLS = 8, IDXW = 6, WBUF_DEPTH = 512, PE_LAT = 2), every input free and shared -- the descriptor,
//   tiles_ready, go, ext_fire, f_busy, and a reset that may be requested again at any cycle after the first. Asserted
//   every cycle: the twelve outputs neo_core's comparator (seq_err) compares are equal, and so are the others (busy,
//   ct_free, ct_free_idx). That is the comparator's "never fires without a fault" as a property of the RTL rather than of
//   a test's vectors. The register-by-register equality of the two copies is asserted as well: it is the inductive
//   invariant that closes k-induction in one step (the outputs alone do not see the counters).
module lockstep_core_seq_fv #(
  parameter int ROWS       = 16,
  parameter int COLS       = 8,
  parameter int IDXW       = 6,
  parameter int WBUF_DEPTH = 512,
  parameter int WAW        = $clog2(WBUF_DEPTH),
  parameter int PE_LAT     = 2
)(
  input  logic                  clk,
  input  logic                  rst_req,          // a later reset of both copies (the first cycle is always a reset)
  input  logic signed [15:0]    cfg_k,
  input  logic signed [15:0]    cfg_ct_n,
  input  logic signed [15:0]    cfg_ct0,
  input  logic signed [15:0]    cfg_ky0,
  input  logic signed [15:0]    cfg_kx0,
  input  logic signed [15:0]    cfg_rn,
  input  logic signed [15:0]    cfg_contrib_n,
  input  logic [IDXW-1:0]       cfg_m,
  input  logic signed [15:0]    tiles_ready,
  input  logic                  go,
  input  logic                  ext_fire,
  input  logic                  f_busy
);
  logic first = 1'b1;
  always_ff @(posedge clk) first <= 1'b0;
  logic rst_n;
  assign rst_n = !first && !rst_req;

  // the two copies' outputs: a_* primary, b_* duplicate
  logic                 a_done, a_busy, a_ct_free, a_reduce_ready, a_f_first, a_f_start, a_w_load, a_rd_en;
  logic                 b_done, b_busy, b_ct_free, b_reduce_ready, b_f_first, b_f_start, b_w_load, b_rd_en;
  logic [2:0]           a_state, b_state;
  logic signed [15:0]   a_ct_free_idx, b_ct_free_idx, a_f_ct, b_f_ct, a_f_ky, b_f_ky, a_f_kx, b_f_kx;
  logic [WAW-1:0]       a_w_raddr, b_w_raddr;
  logic [IDXW-1:0]      a_rd_idx, b_rd_idx;

  core_seq #(.ROWS(ROWS), .COLS(COLS), .IDXW(IDXW), .WBUF_DEPTH(WBUF_DEPTH), .WAW(WAW), .PE_LAT(PE_LAT)) u_a (
    .clk(clk), .rst_n(rst_n),
    .cfg_k(cfg_k), .cfg_ct_n(cfg_ct_n), .cfg_ct0(cfg_ct0), .cfg_ky0(cfg_ky0), .cfg_kx0(cfg_kx0), .cfg_rn(cfg_rn),
    .cfg_contrib_n(cfg_contrib_n), .cfg_m(cfg_m), .tiles_ready(tiles_ready), .go(go),
    .done(a_done), .busy(a_busy), .state_dbg(a_state), .ct_free(a_ct_free), .ct_free_idx(a_ct_free_idx),
    .reduce_ready(a_reduce_ready), .ext_fire(ext_fire),
    .f_ct(a_f_ct), .f_ky(a_f_ky), .f_kx(a_f_kx), .f_first(a_f_first), .f_start(a_f_start), .f_busy(f_busy),
    .w_load(a_w_load), .w_raddr(a_w_raddr), .rd_en(a_rd_en), .rd_idx(a_rd_idx));
  core_seq #(.ROWS(ROWS), .COLS(COLS), .IDXW(IDXW), .WBUF_DEPTH(WBUF_DEPTH), .WAW(WAW), .PE_LAT(PE_LAT)) u_b (
    .clk(clk), .rst_n(rst_n),
    .cfg_k(cfg_k), .cfg_ct_n(cfg_ct_n), .cfg_ct0(cfg_ct0), .cfg_ky0(cfg_ky0), .cfg_kx0(cfg_kx0), .cfg_rn(cfg_rn),
    .cfg_contrib_n(cfg_contrib_n), .cfg_m(cfg_m), .tiles_ready(tiles_ready), .go(go),
    .done(b_done), .busy(b_busy), .state_dbg(b_state), .ct_free(b_ct_free), .ct_free_idx(b_ct_free_idx),
    .reduce_ready(b_reduce_ready), .ext_fire(ext_fire),
    .f_ct(b_f_ct), .f_ky(b_f_ky), .f_kx(b_f_kx), .f_first(b_f_first), .f_start(b_f_start), .f_busy(f_busy),
    .w_load(b_w_load), .w_raddr(b_w_raddr), .rd_en(b_rd_en), .rd_idx(b_rd_idx));

  always_comb begin
    if (!first) begin
      // neo_core's seq_err list, in its order
      assert (a_f_start == b_f_start);
      assert (a_f_first == b_f_first);
      assert (a_f_ct == b_f_ct);
      assert (a_f_ky == b_f_ky);
      assert (a_f_kx == b_f_kx);
      assert (a_w_load == b_w_load);
      assert (a_w_raddr == b_w_raddr);
      assert (a_rd_en == b_rd_en);
      assert (a_rd_idx == b_rd_idx);
      assert (a_done == b_done);
      assert (a_reduce_ready == b_reduce_ready);
      assert (a_state == b_state);
      // the outputs the comparator leaves out
      assert (a_busy == b_busy);
      assert (a_ct_free == b_ct_free);
      assert (a_ct_free_idx == b_ct_free_idx);
      // the inductive invariant: every register of the two copies equal
      assert (u_a.state == u_b.state);
      assert (u_a.ct == u_b.ct && u_a.ky == u_b.ky && u_a.kx == u_b.kx);
      assert (u_a.first_q == u_b.first_q);
      assert (u_a.lcnt == u_b.lcnt && u_a.loading == u_b.loading && u_a.loaded == u_b.loaded);
      assert (u_a.tcnt == u_b.tcnt && u_a.wcnt == u_b.wcnt && u_a.dcnt == u_b.dcnt);
      assert (u_a.ltile == u_b.ltile && u_a.rcnt == u_b.rcnt && u_a.ext_cnt == u_b.ext_cnt);
      assert (u_a.red_last == u_b.red_last);                   // drop 0.41: the reduce-to-drain cycle
      assert (u_a.ct_free_q == u_b.ct_free_q && u_a.ct_free_idx_q == u_b.ct_free_idx_q);
    end
  end
endmodule
