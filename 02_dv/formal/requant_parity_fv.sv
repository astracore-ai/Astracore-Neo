// requant_parity_fv.sv -- formal harness for the table parity of rtl/requant (drop 0.41) at COLS = 4 (PW = 32, MW = 16),
//   every input free, tbl_fault_inject included (the DV hook that flips bit 0 of column 0's multiplier as used). The
//   harness tracks the entries written since reset from tbl_we / tbl_addr and models the sticky: it rises the cycle
//   after the fault meets a written entry 0 under in_valid, holds, and falls on err_clear (which has priority).
//   Asserted every cycle after reset:
//     no false alarm  tbl_perr_sticky only when the model says so -- with tbl_fault_inject = 0 (or the fault never on a
//                     written entry 0 during in_valid) the sticky never rises, whatever the tables hold or the
//                     unwritten entries contain
//     detection       the model set implies tbl_perr_sticky -- 1 in the cycle after the fault, written entry 0 and
//                     in_valid coincide (the brief's "within two cycles", met in one)
//   Helper invariants (the written flags, odd parity stored with every written entry) close k-induction in one step.
module requant_parity_fv #(
  parameter int COLS = 4,
  parameter int PW   = 32,
  parameter int MW   = 16,
  parameter int CW   = $clog2(COLS)
)(
  input  logic                 clk,
  input  logic                 tbl_we,
  input  logic [CW-1:0]        tbl_addr,
  input  logic [MW-1:0]        tbl_mult,
  input  logic [4:0]           tbl_shift,
  input  logic signed [7:0]    tbl_zp,
  input  logic                 relu,
  input  logic                 in_valid,
  input  logic [COLS*PW-1:0]   in_acc_v,
  input  logic                 err_clear,
  input  logic                 fault
);
  logic rst_n = 1'b0;
  always_ff @(posedge clk) rst_n <= 1'b1;

  logic signed [PW-1:0] in_acc [COLS];
  generate
    for (genvar j = 0; j < COLS; j++) begin : g_in
      assign in_acc[j] = in_acc_v[PW*j +: PW];
    end
  endgenerate
  logic                out_valid, perr;
  logic signed [7:0]   out_q [COLS];
  requant #(.COLS(COLS), .PW(PW), .MW(MW)) u_dut (
    .clk(clk), .rst_n(rst_n), .tbl_we(tbl_we), .tbl_addr(tbl_addr), .tbl_mult(tbl_mult), .tbl_shift(tbl_shift),
    .tbl_zp(tbl_zp), .relu(relu), .in_valid(in_valid), .in_acc(in_acc), .out_valid(out_valid), .out_q(out_q),
    .err_clear(err_clear), .tbl_perr_sticky(perr), .tbl_fault_inject(fault));   // out_valid / out_q: lockstep_requant's

  logic [COLS-1:0] written = '0;
  logic            exp_perr = 1'b0;
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      written  <= '0;
      exp_perr <= 1'b0;
    end else begin
      if (tbl_we) written[tbl_addr] <= 1'b1;
      if (err_clear)                              exp_perr <= 1'b0;
      else if (fault && written[0] && in_valid)   exp_perr <= 1'b1;
    end
  end

  always_comb begin
    if (rst_n) begin
      assert (!perr || exp_perr);                          // no false alarm
      assert (!exp_perr || perr);                          // detection, the cycle after
      for (int j = 0; j < COLS; j++) begin                 // helpers
        assert (u_dut.wrt[j] == written[j]);
        if (written[j]) assert ((^{u_dut.mult[j], u_dut.shft[j], u_dut.zp[j], u_dut.par[j]}) == 1'b1);
      end
    end
  end
endmodule
