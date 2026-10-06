// tile_dma.sv -- per-tile DMA program engine (drop 0.9).
//   Runs a small descriptor program written by the host, driving the tile interface's command
//   port and the core's go, so a layer executes without the host in the loop:
//     word[63:60] op, [59:56] x, [55:52] y, [51:32] addr, [31:20] len, [19:4] base, [3:0] arg
//     op 1 FETCH_A   pull `len` words from bank (x,y) at addr into activation entries from `base`;
//                    waits for completion, then tiles_ready++ (one channel tile landed)
//     op 2 FETCH_W   same into weight entries; waits for completion
//     op 3 DRAIN_WR  route the core's drain to bank (x,y) at addr, `len` rows; arg bit 0 = INT8 rows
//     op 4 DRAIN_PSUM route the core's drain to the owner core at (x,y), `len` rows
//     op 5 GO        pulse the core's go (the program continues, so refills overlap the run)
//     op 6 WAIT_FREE wait until the core has released `arg`+1 channel-tile regions (ct_free pulses)
//     op 7 WAIT_DONE wait for the core's done pulse
//     op 8 END       stop; prog_done
//     op 9 NOTIFY    send an RDY flit to tile (x,y): "my reduce port is open" (owner, after WAIT_REDUCE)
//     op 10 WAIT_RDY  wait for an RDY flit (contributor, before DRAIN_PSUM), so partial sums never
//                    block an owner that is still fetching
//     op 11 WAIT_REDUCE wait until the core is in its reduce state (owner)
//   The sequencer starts the first run of channel tile ct only when tiles_ready > ct, which is what
//   makes a 2-region activation buffer safe: FETCH_A ct0, FETCH_A ct1, GO, WAIT_FREE 0, FETCH_A ct2
//   into region 0, ... with the core never reading a region being refilled.
module tile_dma #(
  parameter int XW = 2,
  parameter int YW = 2,
  parameter int PDEPTH = 32,                 // program memory entries (drop 0.22: 32, was 16)
  parameter int PAW = $clog2(PDEPTH)
)(
  input  logic          clk,
  input  logic          rst_n,
  // program memory outside this module (prog_mem, SECDED), read at pc_out
  output logic [PAW-1:0] pc_out,
  input  logic [63:0]   ins_in,
  input  logic          prog_start,
  output logic          fsm_err,        // state register outside the legal set
  output logic          prog_done,
  output logic          prog_busy,
  // tile interface command port
  output logic          cmd_valid,
  output logic [2:0]    cmd_op,
  output logic [XW-1:0] cmd_x,
  output logic [YW-1:0] cmd_y,
  output logic [19:0]   cmd_addr,
  output logic [11:0]   cmd_len,
  output logic [15:0]   cmd_base,
  output logic          cmd_int8,
  input  logic          cmd_busy,
  // core
  output logic          go,
  input  logic          done,
  input  logic          ct_free,
  input  logic          reduce_ready,
  input  logic          rdy_seen,
  output logic          rdy_clear,
  input  logic          ntf_busy,
  output logic signed [15:0] tiles_ready
);
  localparam logic [3:0] S_IDLE = 4'd0, S_FETCH = 4'd1, S_ISSUE = 4'd2, S_WAITCMD = 4'd3,
                         S_WAITFREE = 4'd4, S_WAITDONE = 4'd5, S_END = 4'd6, S_WAITRDY = 4'd7, S_WAITRED = 4'd8,
                         S_WAITNTF = 4'd9;
  logic [3:0]  state;
  logic [PAW-1:0] pc;
  logic [63:0] ins;
  logic [3:0]  op;
  logic signed [15:0] tiles_freed;
  logic        done_seen;
  logic signed [15:0] ready_q;

  assign pc_out  = pc;
  assign fsm_err = (state > S_WAITNTF);

  assign op       = ins[63:60];
  assign cmd_x    = ins[59:56];
  assign cmd_y    = ins[55:52];
  assign cmd_addr = ins[51:32];
  assign cmd_len  = ins[31:20];
  assign cmd_base = ins[19:4];
  assign cmd_int8 = ins[0];
  assign cmd_op   = (op == 4'd9) ? 3'd5 : op[2:0];
  assign cmd_valid = (state == S_ISSUE) && (((op >= 4'd1) && (op <= 4'd4)) || (op == 4'd9));
  assign rdy_clear = (state == S_IDLE) && prog_start;
  assign go        = (state == S_ISSUE) && (op == 4'd5);
  assign prog_done = (state == S_END);
  assign prog_busy = (state != S_IDLE) && (state != S_END);
  assign tiles_ready = ready_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state <= S_IDLE; pc <= '0; ins <= '0; tiles_freed <= '0; done_seen <= 1'b0; ready_q <= '0;
    end else begin
      if (ct_free) tiles_freed <= tiles_freed + 1;
      if (done)    done_seen   <= 1'b1;
      case (state)
        S_IDLE: begin
          if (prog_start) begin
            pc <= '0; tiles_freed <= '0; done_seen <= 1'b0; ready_q <= '0;
            state <= S_FETCH;
          end
        end
        S_FETCH: begin
          ins <= ins_in;
          pc  <= pc + 1;
          state <= S_ISSUE;
        end
        S_ISSUE: begin
          case (op)
            4'd1, 4'd2: state <= S_WAITCMD;                 // fetch: wait for the response
            4'd3, 4'd4, 4'd5: state <= S_FETCH;              // drain routing and go: fire and continue
            4'd9: state <= S_WAITNTF;                        // notify: wait until the flit has left
            4'd6: state <= S_WAITFREE;
            4'd7: state <= S_WAITDONE;
            4'd10: state <= S_WAITRDY;
            4'd11: state <= S_WAITRED;
            default: state <= S_END;
          endcase
        end
        S_WAITCMD: begin
          if (!cmd_busy) begin
            if (op == 4'd1) ready_q <= ready_q + 1;
            state <= S_FETCH;
          end
        end
        S_WAITFREE: begin
          if (tiles_freed > $signed({12'd0, ins[3:0]})) state <= S_FETCH;
        end
        S_WAITDONE: begin
          if (done_seen || done) state <= S_FETCH;
        end
        S_WAITRDY: begin
          if (rdy_seen) state <= S_FETCH;
        end
        S_WAITNTF: begin
          if (!ntf_busy) state <= S_FETCH;
        end
        S_WAITRED: begin
          if (reduce_ready) state <= S_FETCH;
        end
        S_END: begin
          if (prog_start) begin
            pc <= '0; tiles_freed <= '0; done_seen <= 1'b0; ready_q <= '0;
            state <= S_FETCH;
          end
        end
        default: state <= S_IDLE;
      endcase
    end
  end
endmodule
