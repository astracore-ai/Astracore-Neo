// mbist.sv -- memory built-in self-test for sram_bank (drop 0.13): March C- over WORDS words, by rows of LANES words since
//   drop 0.28 (the 512-bit port): every element reads or writes a whole row, the data background in every lane, and a
//   mismatch or a correction in any lane is the fault; fail_addr is the word address of the first failing lane.
//   Elements: up(w0); up(r0,w1); up(r1,w0); down(r0,w1); down(r1,w0); up(r0), data background
//   0x00000000 / 0xFFFFFFFF, then the same six elements with 0x55555555 / 0xAAAAAAAA to catch
//   coupling faults between adjacent columns. The engine owns the bank's ports while running
//   (bist_active), goes through the ECC encoder/decoder like normal traffic, and reports the first
//   failing address. Scheduled by the safety island inside the FTTI on banks not in use.
module mbist #(
  parameter int AW    = 12,                  // word address width (fail_addr)
  parameter int WORDS = 4096,                // words tested, a multiple of LANES
  parameter int LANES = 16,
  parameter int ROWS  = WORDS / LANES,
  parameter int RAW   = (ROWS > 1) ? $clog2(ROWS) : 1,
  parameter int DW    = 32 * LANES
)(
  input  logic          clk,
  input  logic          rst_n,
  input  logic          start,
  output logic          active,
  output logic          done,
  output logic          fail,
  output logic [AW-1:0] fail_addr,
  // bank ports owned while active (row addressed)
  output logic             we,
  output logic [RAW-1:0]   wrow,
  output logic [LANES-1:0] wmask,
  output logic [DW-1:0]    wdata,
  output logic             re,
  output logic [RAW-1:0]   rrow,
  input  logic [DW-1:0]    rdata,
  input  logic             rvalid,
  input  logic [LANES-1:0] ce_lane,          // lanes the bank corrected on this read: a stuck or weak bit, reported as a fault
  output logic             fail_ce           // the failure was found by the ECC rather than by data mismatch
);
  localparam logic [2:0] S_IDLE = 3'd0, S_W = 3'd1, S_R = 3'd2, S_CHK = 3'd3, S_DONE = 3'd4;
  logic [2:0]    state;
  logic [3:0]    elem;            // 0..5 for background 0, 6..11 for background 0x55
  localparam int IW = RAW + 1;    // idx width: 0..ROWS
  logic [IW-1:0] idx;             // position within the element (rows)
  logic [RAW-1:0] addr;
  logic [LANES-1:0] lane_bad;     // lanes whose data mismatched this read
  logic [LANES-1:0] lane_hit;     // lanes that failed by mismatch or by correction
  logic [3:0]    first_lane;
  logic [31:0]   bg, bg_inv;      // background and its complement
  logic          up;
  logic          has_read, has_write;
  logic [31:0]   exp_rd, wr_val;

  assign bg     = (elem < 4'd6) ? 32'h00000000 : 32'h55555555;
  assign bg_inv = ~bg;
  // element decode: (direction, read value, write value)
  always_comb begin
    up = 1'b1; has_read = 1'b1; has_write = 1'b1; exp_rd = bg; wr_val = bg;
    case (elem % 6)
      0: begin has_read = 1'b0; wr_val = bg; end                 // up: w0
      1: begin exp_rd = bg; wr_val = bg_inv; end                 // up: r0 w1
      2: begin exp_rd = bg_inv; wr_val = bg; end                 // up: r1 w0
      3: begin up = 1'b0; exp_rd = bg; wr_val = bg_inv; end      // down: r0 w1
      4: begin up = 1'b0; exp_rd = bg_inv; wr_val = bg; end      // down: r1 w0
      default: begin has_write = 1'b0; exp_rd = bg; end          // up: r0
    endcase
  end
  assign addr   = up ? idx[RAW-1:0] : RAW'(ROWS - 1 - 32'(idx));
  assign active = (state != S_IDLE) && (state != S_DONE);
  assign done   = (state == S_DONE);
  assign re     = (state == S_R);
  assign rrow   = addr;
  assign we     = (state == S_W);
  assign wrow   = addr;
  assign wmask  = '1;
  assign wdata  = {LANES{wr_val}};
  always_comb begin
    first_lane = 4'd0;
    for (int l = 0; l < LANES; l++) lane_bad[l] = (rdata[32*l +: 32] != exp_rd);
    lane_hit = lane_bad | ce_lane;
    for (int l = LANES - 1; l >= 0; l--) if (lane_hit[l]) first_lane = 4'(l);
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state <= S_IDLE; elem <= '0; idx <= '0; fail <= 1'b0; fail_addr <= '0; fail_ce <= 1'b0;
    end else begin
      case (state)
        S_IDLE: if (start) begin elem <= '0; idx <= '0; fail <= 1'b0; fail_ce <= 1'b0; fail_addr <= '0; state <= has_read ? S_R : S_W; end
        S_R:    state <= S_CHK;                                  // rdata valid next cycle
        S_CHK: begin
          if (rvalid && (lane_hit != '0) && !fail) begin
            fail <= 1'b1; fail_ce <= (lane_bad == '0); fail_addr <= AW'(addr) * AW'(LANES) + AW'(first_lane);
          end
          state <= has_write ? S_W : S_IDLE;                    // S_IDLE here means "advance" (see below)
          if (!has_write) begin
            if (idx == IW'(ROWS - 1)) begin
              if (elem == 4'd11) state <= S_DONE;
              else begin elem <= elem + 1; idx <= '0; state <= ((elem + 1) % 6 == 0) ? S_W : S_R; end
            end else begin idx <= idx + 1; state <= S_R; end
          end
        end
        S_W: begin
          if (idx == IW'(ROWS - 1)) begin
            if (elem == 4'd11) state <= S_DONE;
            else begin elem <= elem + 1; idx <= '0; state <= ((elem + 1) % 6 == 0) ? S_W : S_R; end
          end else begin idx <= idx + 1; state <= has_read ? S_R : S_W; end
        end
        S_DONE: if (start) begin elem <= '0; idx <= '0; fail <= 1'b0; fail_ce <= 1'b0; state <= S_W; end
        default: state <= S_IDLE;
      endcase
    end
  end
endmodule
