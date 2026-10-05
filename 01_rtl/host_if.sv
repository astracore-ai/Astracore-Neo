// host_if.sv -- tile host register interface (drop 0.11): what a CPU (safety island R52+, or a
//   PCIe host through the bridge) sees of one tile. Simple synchronous register bus; an AXI-Lite
//   or APB wrapper is a thin shim on top. Word addresses:
//     0x00 CTRL      w: bit0 prog_start (pulse), bit1 err_clear (pulse)
//     0x01 STATUS    r: bit0 prog_done, bit1 drain_busy, bit2 core done (latched since last start),
//                       bit8 parity, bit9 crc, bit10 array_abft, bit11 acc_abft, bit12 ctrl, bit13 seq,
//                       bit14 rq, bit15 ecc_ue, bit16 ecc_ce
//     0x02 PROG_ADDR w: program entry index (0..15)
//     0x03 PROG_LO   w: low 32 bits of the entry
//     0x04 PROG_HI   w: high 32 bits; writing it commits the 64-bit entry at PROG_ADDR
//     0x10..0x23     w: descriptor registers in the order of neo_tile's cfg_* ports (20 x 16-bit)
//     0x30 RQ_TBL    w: {zp[7:0], shift[4:0], 3'b0, mult[15:0]} -> table row at RQ_ADDR (0x31)
//     0x32 RQ_RELU   w: bit0
//     0x05 ERR_MASK  w: bit i masks cause i from err_pin (bit 16 = watchdog)
//     0x06 ERR_CAUSE r: latched causes (bit 16 = watchdog)
//     0x07 WD_CTRL   w: bit0 enable, [31:8] window in cycles
//     0x08 WD_KICK   w: any write kicks the watchdog
//     0x09 SELFTEST  w: bit0 asserts the core's fault-injection hook (checker self-test on a dummy run)
//     0x0A MBIST     w: any write starts the bank MBIST (March C-); r: bit0 active, bit1 done, bit2 fail,
//                       bit3 fail found by ECC correction, [31:16] failing address
//     0x0B PARTITION w/r: [3:0] this tile's spatial partition; flits from another partition are dropped and flagged
//   The descriptor registers are kept twice and compared every cycle (cfg_err): a corrupted descriptor is a
//   control-path fault the datapath checks cannot see. Causes: bits 0-17 (see neo_tile), bit 18 = watchdog.
//   Timing: writes take effect one cycle after the bus cycle (CTRL pulses are one cycle wide); a read
//   issued in the cycle after a CTRL clear still returns the pre-clear state; after CTRL.start the DMA
//   engine leaves its done state two cycles later (poll STATUS.prog_done low, then high); the same holds
//   for MBIST.done after a write to 0x0A.
module host_if #(
  parameter int IDXW = 6
)(
  input  logic        clk,
  input  logic        rst_n,
  // register bus
  input  logic        h_we,
  input  logic        h_re,
  input  logic [7:0]  h_addr,
  input  logic [31:0] h_wdata,
  output logic [31:0] h_rdata,
  // to the tile
  output logic        prog_we,
  output logic [3:0]  prog_waddr,
  output logic [63:0] prog_wdata,
  output logic        prog_start,
  output logic        err_clear,
  output logic signed [15:0] cfg [20],
  output logic [IDXW-1:0] cfg_m,
  output logic        rq_tbl_we,
  output logic [4:0]  rq_tbl_addr,
  output logic [15:0] rq_tbl_mult,
  output logic [4:0]  rq_tbl_shift,
  output logic signed [7:0] rq_tbl_zp,
  output logic        rq_relu,
  // error signaling and self-test
  output logic [18:0] err_mask,
  input  logic [18:0] err_cause,
  output logic [3:0]  partition,
  output logic        cfg_err,            // descriptor register pair disagrees
  output logic        wd_enable,
  output logic [23:0] wd_window,
  output logic        wd_kick,
  output logic        selftest_inject,
  output logic        bist_start,
  input  logic        bist_active,
  input  logic        bist_done,
  input  logic        bist_fail,
  input  logic        bist_fail_ce,
  input  logic [15:0] bist_fail_addr,
  // from the tile
  input  logic        prog_done,
  input  logic        drain_busy,
  input  logic        core_done,
  input  logic [8:0]  flags            // parity, crc, array, acc, ctrl, seq, rq, ecc_ue, ecc_ce
);
  logic [31:0] prog_lo;
  logic        done_latched;
  logic signed [15:0] cfg_dup [20];
  logic [IDXW-1:0]    cfg_m_dup;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      prog_we <= 1'b0; prog_waddr <= '0; prog_wdata <= '0; prog_start <= 1'b0; err_clear <= 1'b0;
      prog_lo <= '0; done_latched <= 1'b0; cfg_m <= '0; rq_tbl_we <= 1'b0; rq_tbl_addr <= '0;
      rq_tbl_mult <= '0; rq_tbl_shift <= '0; rq_tbl_zp <= '0; rq_relu <= 1'b0; h_rdata <= '0;
      err_mask <= '0; wd_enable <= 1'b0; wd_window <= '0; wd_kick <= 1'b0; selftest_inject <= 1'b0; bist_start <= 1'b0;
      partition <= '0; cfg_m_dup <= '0;
      for (int i = 0; i < 20; i++) begin cfg[i] <= '0; cfg_dup[i] <= '0; end
    end else begin
      prog_we <= 1'b0; prog_start <= 1'b0; err_clear <= 1'b0; rq_tbl_we <= 1'b0; wd_kick <= 1'b0; bist_start <= 1'b0;
      if (core_done) done_latched <= 1'b1;
      if (h_we) begin
        case (h_addr)
          8'h00: begin prog_start <= h_wdata[0]; err_clear <= h_wdata[1]; if (h_wdata[0]) done_latched <= 1'b0; end
          8'h02: prog_waddr <= h_wdata[3:0];
          8'h03: prog_lo <= h_wdata;
          8'h04: begin prog_wdata <= {h_wdata, prog_lo}; prog_we <= 1'b1; end
          8'h05: err_mask <= h_wdata[18:0];
          8'h0B: partition <= h_wdata[3:0];
          8'h07: begin wd_enable <= h_wdata[0]; wd_window <= h_wdata[31:8]; end
          8'h08: wd_kick <= 1'b1;
          8'h09: selftest_inject <= h_wdata[0];
          8'h0A: bist_start <= 1'b1;
          8'h30: begin rq_tbl_mult <= h_wdata[15:0]; rq_tbl_shift <= h_wdata[20:16]; rq_tbl_zp <= h_wdata[31:24]; rq_tbl_we <= 1'b1; end
          8'h31: rq_tbl_addr <= h_wdata[4:0];
          8'h32: rq_relu <= h_wdata[0];
          default: begin
            if (h_addr >= 8'h10 && h_addr < 8'h24) begin cfg[h_addr - 8'h10] <= h_wdata[15:0]; cfg_dup[h_addr - 8'h10] <= h_wdata[15:0]; end
            if (h_addr == 8'h24) begin cfg_m <= h_wdata[IDXW-1:0]; cfg_m_dup <= h_wdata[IDXW-1:0]; end
          end
        endcase
      end
      if (h_re) begin
        case (h_addr)
          8'h01: h_rdata <= {15'd0, flags[8], flags[7:0], 5'd0, done_latched, drain_busy, prog_done};
          8'h02: h_rdata <= {28'd0, prog_waddr};
          8'h05: h_rdata <= {13'd0, err_mask};
          8'h06: h_rdata <= {13'd0, err_cause};
          8'h0B: h_rdata <= {28'd0, partition};
          8'h0A: h_rdata <= {bist_fail_addr, 12'd0, bist_fail_ce, bist_fail, bist_done, bist_active};
          8'h03: h_rdata <= prog_lo;
          8'h24: h_rdata <= {{(32-IDXW){1'b0}}, cfg_m};
          default: h_rdata <= (h_addr >= 8'h10 && h_addr < 8'h24) ? {16'd0, cfg[h_addr - 8'h10]} : 32'd0;
        endcase
      end
    end
  end
  always_comb begin
    cfg_err = (cfg_m != cfg_m_dup);
    for (int i = 0; i < 20; i++) if (cfg[i] != cfg_dup[i]) cfg_err = 1'b1;
  end
endmodule
