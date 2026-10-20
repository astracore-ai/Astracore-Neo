// lockstep_requant_fv.sv -- formal harness for the duplicated requantizer of neo_core (drop 0.41): two rtl/requant at
//   COLS = 4, PW = 32 (MW = 4 from lockstep_requant.sby: the multiplier pair at MW = 16 is beyond the solvers in the
//   induction step, README.md), every input free and shared (table writes, relu, the stream, err_clear),
//   tbl_fault_inject = 0 on both. The tables are not reset (as in silicon), so the one assumption is the use the core
//   makes of them: in_valid only once all four entries have been written, tracked here from tbl_we / tbl_addr. Asserted
//   every cycle: out_valid equal, out_q equal while out_valid (neo_core's rq_err compares exactly that), and the parity
//   stickies equal. Helper invariants (the written flags, the written entries, the stage-1 registers while v1 and the
//   stage-2 registers while v2 -- the three-stage requant of drop 0.41) close k-induction in one step.
module lockstep_requant_fv #(
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
  input  logic [COLS*PW-1:0]   in_acc_v,          // column j at [PW*j +: PW]
  input  logic                 err_clear
);
  logic rst_n = 1'b0;
  always_ff @(posedge clk) rst_n <= 1'b1;

  logic signed [PW-1:0] in_acc [COLS];
  generate
    for (genvar j = 0; j < COLS; j++) begin : g_in
      assign in_acc[j] = in_acc_v[PW*j +: PW];
    end
  endgenerate

  logic                a_valid, b_valid, a_perr, b_perr;
  logic                a_cg, b_cg;                  // drop 0.43: the copies' clock enables (the gate's formal model: ungated)
  logic signed [7:0]   a_q [COLS];
  logic signed [7:0]   b_q [COLS];
  requant #(.COLS(COLS), .PW(PW), .MW(MW)) u_a (
    .clk(clk), .rst_n(rst_n), .test_en(1'b0), .tbl_we(tbl_we), .tbl_addr(tbl_addr), .tbl_mult(tbl_mult), .tbl_shift(tbl_shift),
    .tbl_zp(tbl_zp), .relu(relu), .in_valid(in_valid), .in_acc(in_acc), .out_valid(a_valid), .out_q(a_q),
    .err_clear(err_clear), .tbl_perr_sticky(a_perr), .tbl_fault_inject(1'b0), .cg_en(a_cg));
  requant #(.COLS(COLS), .PW(PW), .MW(MW)) u_b (
    .clk(clk), .rst_n(rst_n), .test_en(1'b0), .tbl_we(tbl_we), .tbl_addr(tbl_addr), .tbl_mult(tbl_mult), .tbl_shift(tbl_shift),
    .tbl_zp(tbl_zp), .relu(relu), .in_valid(in_valid), .in_acc(in_acc), .out_valid(b_valid), .out_q(b_q),
    .err_clear(err_clear), .tbl_perr_sticky(b_perr), .tbl_fault_inject(1'b0), .cg_en(b_cg));

  // the entries written since reset (a write takes effect at the clock edge)
  logic [COLS-1:0] written = '0;
  always_ff @(posedge clk) begin
    if (!rst_n)      written <= '0;
    else if (tbl_we) written[tbl_addr] <= 1'b1;
  end

  always_comb begin
    assume (!in_valid || (&written));               // the stream starts once the table is loaded
    if (rst_n) begin
      assert (a_valid == b_valid);
      for (int j = 0; j < COLS; j++) if (a_valid) assert (a_q[j] == b_q[j]);
      assert (a_perr == b_perr);
      // helper invariants
      for (int j = 0; j < COLS; j++) begin
        assert (u_a.wrt[j] == written[j] && u_b.wrt[j] == written[j]);
        if (written[j]) assert (u_a.mult[j] == u_b.mult[j] && u_a.shft[j] == u_b.shft[j] && u_a.zp[j] == u_b.zp[j] &&
                                u_a.par[j] == u_b.par[j]);
        if (u_a.v1) assert (u_a.ra1[j] == u_b.ra1[j] && u_a.rb1[j] == u_b.rb1[j] && u_a.n1[j] == u_b.n1[j] &&
                            u_a.sh1[j] == u_b.sh1[j] && u_a.zp1[j] == u_b.zp1[j]);
        if (u_a.v2) assert (u_a.sum2[j] == u_b.sum2[j] && u_a.n2[j] == u_b.n2[j] &&
                            u_a.sh2[j] == u_b.sh2[j] && u_a.zp2[j] == u_b.zp2[j]);
      end
      assert (u_a.v1 == u_b.v1);
      assert (u_a.v2 == u_b.v2);
      assert (a_cg == b_cg);                        // drop 0.43: the two gates open in the same cycles
    end
  end
endmodule
