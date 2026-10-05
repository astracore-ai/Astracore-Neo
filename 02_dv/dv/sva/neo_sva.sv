// neo_sva.sv -- SystemVerilog assertions for the Neo RTL protocols (drop 0.11).
//   For Xcelium (simulation) and JasperGold (formal). Bound into the design by bind_all.sv;
//   never included in the neosim file lists (neosim does not parse SVA).
//   Each property states a rule the architecture documents already rely on.

// ---- streaming core: token/double-buffer rule, exact latency, ABFT invariant -----------------
module neo_mac_core_sva #(parameter int ROWS = 32, COLS = 32, PE_LAT = 2) (
  input logic clk, rst_n, w_load, swap_in, valid_in, valid_out, abft_err, fault_inject);
  localparam int LAT = PE_LAT * ROWS + COLS;
  logic fault_seen;
  always_ff @(posedge clk or negedge rst_n)
    if (!rst_n) fault_seen <= 1'b0; else if (fault_inject) fault_seen <= 1'b1;
  // no shadow load may start within LAT cycles of a swap token (it would be adopted half-shifted)
  p_no_load_after_token: assert property (@(posedge clk) disable iff (!rst_n)
    swap_in |=> (!w_load)[*LAT]);
  // every valid row produces exactly one valid output LAT cycles later
  p_latency: assert property (@(posedge clk) disable iff (!rst_n)
    valid_in |-> ##LAT valid_out);
  p_no_spurious_valid: assert property (@(posedge clk) disable iff (!rst_n)
    valid_out |-> $past(valid_in, LAT));
  // with no fault injected the check column always agrees with the data columns
  p_abft_silent_without_fault: assert property (@(posedge clk) disable iff (!rst_n)
    (valid_out && !fault_seen) |-> !abft_err);
endmodule

// ---- sequencer: legal state machine, run accounting, reduce gating --------------------------
module core_seq_sva (input logic clk, rst_n, go, done, busy, reduce_ready, f_start, rd_en, w_load,
                     input logic [2:0] state_dbg);
  localparam logic [2:0] S_IDLE = 3'd0, S_LOAD0 = 3'd1, S_START = 3'd2, S_RUN = 3'd3,
                         S_WAIT = 3'd4, S_DRAIN = 3'd5, S_DONE = 3'd6, S_REDUCE = 3'd7;
  p_idle_to_load:   assert property (@(posedge clk) disable iff (!rst_n) (state_dbg == S_IDLE && go) |=> state_dbg == S_LOAD0);
  p_done_one_cycle: assert property (@(posedge clk) disable iff (!rst_n) done |=> !done);
  p_done_is_state:  assert property (@(posedge clk) disable iff (!rst_n) done |-> state_dbg == S_DONE);
  p_drain_only_in_drain: assert property (@(posedge clk) disable iff (!rst_n) rd_en |-> state_dbg == S_DRAIN);
  p_reduce_ready_state:  assert property (@(posedge clk) disable iff (!rst_n) reduce_ready |-> state_dbg == S_REDUCE);
  p_start_in_start:      assert property (@(posedge clk) disable iff (!rst_n) f_start |-> state_dbg == S_START);
  p_busy_consistent:     assert property (@(posedge clk) disable iff (!rst_n) busy == (state_dbg != S_IDLE));
  p_legal_from_wait: assert property (@(posedge clk) disable iff (!rst_n)
    state_dbg == S_WAIT |=> state_dbg inside {S_WAIT, S_DRAIN, S_REDUCE});
  p_legal_from_drain: assert property (@(posedge clk) disable iff (!rst_n)
    state_dbg == S_DRAIN |=> state_dbg inside {S_DRAIN, S_DONE});
endmodule

// ---- accumulator: local write has priority over the reduce port ------------------------------
module acc_bank_sva (input logic clk, rst_n, wr_valid, ext_valid, ext_ready, rd_en, rd_valid);
  p_ext_blocked_by_local: assert property (@(posedge clk) disable iff (!rst_n) wr_valid |-> !ext_ready);
  p_ext_accept_only_idle: assert property (@(posedge clk) disable iff (!rst_n) (ext_valid && ext_ready) |-> !wr_valid);
  p_read_latency:         assert property (@(posedge clk) disable iff (!rst_n) rd_en |=> rd_valid);
endmodule

// ---- router: ready/valid discipline on every port, never emits a bad-parity flit --------------
module noc_router_sva #(parameter int FW = 81) (
  input logic clk, rst_n,
  input logic in_valid [5], in_ready [5], out_valid [5], out_ready [5],
  input logic [FW-1:0] in_flit [5], out_flit [5]);
  genvar p;
  generate for (p = 0; p < 5; p++) begin : g_p
    p_in_hold:  assert property (@(posedge clk) disable iff (!rst_n)
      (in_valid[p] && !in_ready[p]) |=> (in_valid[p] && $stable(in_flit[p])));
    p_out_hold: assert property (@(posedge clk) disable iff (!rst_n)
      (out_valid[p] && !out_ready[p]) |=> (out_valid[p] && $stable(out_flit[p])));
    p_out_parity: assert property (@(posedge clk) disable iff (!rst_n)
      out_valid[p] |-> (^out_flit[p] == 1'b0));
  end endgenerate
endmodule

// ---- tile interface: one message at a time, requests held while serving, fetch completion ----
module tile_nic_sva (input logic clk, rst_n, rx_valid, rx_ready, tx_valid, tx_ready, cmd_busy, drain_busy,
                     input logic [2:0] rx_type, input logic serve_busy, drain_open);
  localparam logic [2:0] T_RDREQ = 3'd1;
  p_req_held_while_serving: assert property (@(posedge clk) disable iff (!rst_n)
    (rx_valid && rx_type == T_RDREQ && serve_busy) |-> !rx_ready);
  p_tx_hold: assert property (@(posedge clk) disable iff (!rst_n)
    (tx_valid && !tx_ready) |=> tx_valid);
  // an open drain message is never interleaved with a served read (receiver CRC accumulates per source)
  p_drain_exclusive: assert property (@(posedge clk) disable iff (!rst_n)
    drain_open |-> !(tx_valid && !drain_busy));
endmodule

// ---- DMA program engine ----------------------------------------------------------------------
module tile_dma_sva (input logic clk, rst_n, prog_start, prog_done, prog_busy, cmd_valid, go,
                     input logic [3:0] state);
  p_done_quiet:   assert property (@(posedge clk) disable iff (!rst_n) prog_done |-> (!cmd_valid && !go));
  p_busy_or_done: assert property (@(posedge clk) disable iff (!rst_n) prog_busy |-> !prog_done);
  p_go_pulse:     assert property (@(posedge clk) disable iff (!rst_n) go |=> !go);
endmodule

// ---- requantization: fixed two-cycle latency, valid never dropped ----------------------------
module requant_sva (input logic clk, rst_n, in_valid, out_valid);
  p_rq_latency: assert property (@(posedge clk) disable iff (!rst_n) in_valid |-> ##2 out_valid);
  p_rq_no_spurious: assert property (@(posedge clk) disable iff (!rst_n) out_valid |-> $past(in_valid, 2));
endmodule
