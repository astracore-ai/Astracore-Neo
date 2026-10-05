// esm.sv -- error-signaling module (drop 0.12): the safety island's view of one tile's faults.
//   Latches every sticky flag into a cause register, drives err_pin for any unmasked cause, and
//   runs a windowed watchdog: software must kick it at least once per wd_window cycles while
//   enabled, else a watchdog cause is raised. Everything is cleared together by err_clear.
module esm #(parameter int NFLAGS = 16) (
  input  logic              clk,
  input  logic              rst_n,
  input  logic [NFLAGS-1:0] flags,        // sticky flags from the tile, bit i = cause i
  input  logic [NFLAGS:0]   mask,         // bit NFLAGS masks the watchdog
  input  logic              err_clear,
  input  logic              wd_enable,
  input  logic [23:0]       wd_window,
  input  logic              wd_kick,
  output logic [NFLAGS:0]   cause,        // latched causes, bit NFLAGS = watchdog
  output logic              err_pin
);
  logic [23:0] wd_cnt;
  logic        wd_err;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      cause <= '0; wd_cnt <= '0; wd_err <= 1'b0;
    end else begin
      if (err_clear) begin cause <= '0; wd_err <= 1'b0; wd_cnt <= '0; end
      else begin
        cause[NFLAGS-1:0] <= cause[NFLAGS-1:0] | flags;
        if (wd_enable) begin
          if (wd_kick) wd_cnt <= '0;
          else if (wd_cnt == wd_window) begin wd_err <= 1'b1; end
          else wd_cnt <= wd_cnt + 1;
        end else begin
          wd_cnt <= '0;
        end
        if (wd_err) cause[NFLAGS] <= 1'b1;
      end
    end
  end
  assign err_pin = |(cause & ~mask);
endmodule
