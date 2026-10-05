// mbist.sv -- memory built-in self-test for sram_bank (drop 0.13): March C- over WORDS words.
//   Elements: up(w0); up(r0,w1); up(r1,w0); down(r0,w1); down(r1,w0); up(r0), data background
//   0x00000000 / 0xFFFFFFFF, then the same six elements with 0x55555555 / 0xAAAAAAAA to catch
//   coupling faults between adjacent columns. The engine owns the bank's ports while running
//   (bist_active), goes through the ECC encoder/decoder like normal traffic, and reports the first
//   failing address. Scheduled by the safety island inside the FTTI on banks not in use.
module mbist #(
  parameter int AW    = 12,
  parameter int WORDS = 4096
)(
  input  logic          clk,
  input  logic          rst_n,
  input  logic          start,
  output logic          active,
  output logic          done,
  output logic          fail,
  output logic [AW-1:0] fail_addr,
  // bank ports owned while active
  output logic          we,
  output logic [AW-1:0] waddr,
  output logic [31:0]   wdata,
  output logic          re,
  output logic [AW-1:0] raddr,
  input  logic [31:0]   rdata,
  input  logic          rvalid,
  input  logic          ce_now,           // the bank corrected this read: a stuck or weak bit, reported as a fault
  output logic          fail_ce           // the failure was found by the ECC rather than by data mismatch
);
  localparam logic [2:0] S_IDLE = 3'd0, S_W = 3'd1, S_R = 3'd2, S_CHK = 3'd3, S_DONE = 3'd4;
  logic [2:0]    state;
  logic [3:0]    elem;            // 0..5 for background 0, 6..11 for background 0x55
  logic [AW:0]   idx;             // position within the element
  logic [AW-1:0] addr;
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
  assign addr   = up ? idx[AW-1:0] : (WORDS - 1 - idx[AW-1:0]);
  assign active = (state != S_IDLE) && (state != S_DONE);
  assign done   = (state == S_DONE);
  assign re     = (state == S_R);
  assign raddr  = addr;
  assign we     = (state == S_W);
  assign waddr  = addr;
  assign wdata  = wr_val;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state <= S_IDLE; elem <= '0; idx <= '0; fail <= 1'b0; fail_addr <= '0; fail_ce <= 1'b0;
    end else begin
      case (state)
        S_IDLE: if (start) begin elem <= '0; idx <= '0; fail <= 1'b0; fail_ce <= 1'b0; fail_addr <= '0; state <= has_read ? S_R : S_W; end
        S_R:    state <= S_CHK;                                  // rdata valid next cycle
        S_CHK: begin
          if (rvalid && (rdata != exp_rd || ce_now) && !fail) begin fail <= 1'b1; fail_ce <= (rdata == exp_rd); fail_addr <= addr; end
          state <= has_write ? S_W : S_IDLE;                    // S_IDLE here means "advance" (see below)
          if (!has_write) begin
            if (idx == WORDS - 1) begin
              if (elem == 4'd11) state <= S_DONE;
              else begin elem <= elem + 1; idx <= '0; state <= ((elem + 1) % 6 == 0) ? S_W : S_R; end
            end else begin idx <= idx + 1; state <= S_R; end
          end
        end
        S_W: begin
          if (idx == WORDS - 1) begin
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
