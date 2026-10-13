// acc_bank.sv -- per-core output accumulator with ABFT at drain, v0.4 (drop 0.41: two-cycle read-modify-write).
//   ACC_ROWS x COLS x PW-bit partial sums plus one check-column accumulator per row.
//   Write: when wr_valid, acc[wr_idx] <= (wr_first ? 0 : acc[wr_idx]) + wr_y; the check sum
//   accumulates alongside, so sum_j acc[r][j] == acc_chk[r] holds across every K-tile and any
//   single-point fault in the array OR in this memory breaks it.
//   Reduce (R8): partial sums from other cores arrive on ext_*; acc[ext_idx] += ext_y and the
//   check column += ext_chk, so the drain-time ABFT also covers the reduction transfer. The local
//   write has priority (ext_ready = !wr_valid); the sequencer gates acceptance to its reduce state.
//   Drain: rd_en reads row rd_idx one cycle later with rd_valid; abft_err flags a mismatch.
//   Drop 0.40: the rows live in a neo_mem_rf leaf of ACC_ROWS words x (COLS + 1) x PW bits (the check column is the top
//   word); the leaf is what the synthesis flow black-boxes and a memory compiler replaces; the adders and the ABFT check
//   stay here.
//   Drop 0.41: the leaf is used as a one-write-one-read synchronous SRAM -- every read is its registered port B, port A is
//   tied off -- and the accumulate is a two-cycle read-modify-write:
//     stage 0 (the cycle wr_valid / ext_valid arrives): the operation is registered (v1, idx1, first1 and the COLS + 1
//       addends as one RW-bit vector add1) and port B reads row acc_idx;
//     stage 1: old = fwd1 ? new_q : rb_data -- fwd1, registered in stage 0 as v1 && (idx1 == acc_idx), says that the
//       previous operation (in stage 1 then) wrote this row at the edge where this one's read was taken, so the
//       registered read holds the row before that write and the forward supplies it (the compare is in stage 0, off the
//       accumulate path). new = (first1 ? 0 : old) + add1 per column (the COLS + 1 adders), written to row idx1 and kept
//       in new_q for the next forward. One forwarding stage is enough: an operation two cycles back completed its write
//       before this one's read. Back-to-back operations on one row -- two partial-sum rows of the same index, or a local
//       write followed by a reduce write -- are therefore exact at one operation per cycle, as before.
//   The drain shares port B: rb_addr = rd_en ? rd_idx : acc_idx, rb_en = rd_en || acc_we, rd_valid = rd_en one cycle
//   later. The two never overlap by protocol: the sequencer drains after its wait state (local rows) or after the reduce
//   state, and accepts partial sums only in the reduce state. A drain read in the cycle after an accumulate's stage 0
//   would read that row before the accumulate's write (the write lands at the end of stage 1); the sequencer's waits
//   cover it (core_seq's header: the wait state ends after the last local row is written, and the drain follows the
//   reduce state two cycles after the last partial-sum row is accepted, not one).
//   Drop 0.43 (D2): the stage-0 control (v1, first1, idx1, fwd1) and the leaf's port-B address latch (neo_mem_rf RBQ = 1:
//   rb_addr_q, the row row_rd shows) are compared with copies kept complemented (v1_n .. fwd1_n, rbq_n: each loads the
//   inverse of what its primary loads, the copy's fwd1_n from the copies' own v1_n / idx1_n, so synthesis keeps two
//   registers where one D input would merge them). A fault in either side of a pair raises ctrl_err (to neo_core's R10
//   list, seq_err): the control in every cycle, the latch in the cycles its row is used (stage 1 of an accumulate or a
//   drained row). Every one of these, corrupted, writes or drains a whole consistent row -- a sum added to the wrong row,
//   overwritten instead of accumulated, forwarded from the wrong operation, another row drained -- whose check word
//   matches its columns, so the ABFT check at drain does not see it. The data registers (add1, new_q) need no copy: a
//   fault there changes one column of a row and not its check word, which the drain check sees.
module acc_bank #(
  parameter int COLS     = 32,
  parameter int PW       = 32,
  parameter int ACC_ROWS = 512,
  parameter int IDXW     = $clog2(ACC_ROWS)
)(
  input  logic                 clk,
  input  logic                 rst_n,
  input  logic                 wr_valid,
  input  logic [IDXW-1:0]      wr_idx,
  input  logic                 wr_first,
  input  logic signed [PW-1:0] wr_y   [COLS],
  input  logic signed [PW-1:0] wr_chk,
  input  logic                 ext_valid,
  input  logic [IDXW-1:0]      ext_idx,
  input  logic signed [PW-1:0] ext_y   [COLS],
  input  logic signed [PW-1:0] ext_chk,
  output logic                 ext_ready,
  input  logic                 rd_en,
  input  logic [IDXW-1:0]      rd_idx,
  output logic signed [PW-1:0] rd_data [COLS],
  output logic signed [PW-1:0] rd_chk,
  output logic                 rd_valid,
  input  logic                 abft_clear,
  output logic                 abft_err,
  output logic                 abft_err_valid,   // drop 0.41: abft_err is the verdict of the row rd_valid showed two cycles before
  output logic                 abft_err_sticky,
  output logic                 ctrl_err          // drop 0.43: the stage-0 control or the port-B latch differs from its copy
);
  localparam int RW = (COLS + 1) * PW;              // a row: COLS partial sums and the check sum
  logic [RW-1:0]        row_rd;                     // port B: the row read last cycle (the accumulate's operand or the drained row)
  logic [RW-1:0]        add_in;                     // stage 0: this cycle's addends, packed (wr_y / wr_chk or ext_y / ext_chk)
  logic [IDXW-1:0]      acc_idx;
  logic                 acc_we;                     // stage 0: an operation arrives
  logic                 v1, first1;                 // stage 1: the operation registered in stage 0
  logic [IDXW-1:0]      idx1;
  logic [RW-1:0]        add1;
  logic                 fwd1;                       //   it reads the row the operation before it wrote (that row is in new_q)
  logic [RW-1:0]        new_q;                      // the row the previous operation wrote
  logic [RW-1:0]        row_old;                    // stage 1: the row before this operation
  logic [RW-1:0]        row_new;                    // what the write stores
  logic [RW-1:0]        unused_ra, unused_bd_rd;    // the leaf's unused read ports (port A tied off, no backdoor here)
  logic                 unused_bd;
  logic [IDXW-1:0]      rb_addr;                    // port B's address: the drain's row or the arriving operation's
  logic [IDXW-1:0]      rb_addr_q;                  // drop 0.43: the leaf's latched port-B address (the row on row_rd)
  logic                 v1_n, first1_n, fwd1_n;     // drop 0.43: the complemented copies of the stage-0 control and the latch
  logic [IDXW-1:0]      idx1_n, rbq_n;
  logic [IDXW-1:0]      idx1_c, rbq_c;              // the two multi-bit copies inverted back, at their own width (compared below)

  assign ext_ready = !wr_valid;
  assign acc_idx   = wr_valid ? wr_idx : ext_idx;   // the local write has priority
  assign acc_we    = wr_valid || ext_valid;

  // stage 0: the addends of a local write (wr_y, wr_chk) or of a partial-sum row (ext_y, ext_chk), packed; one driver
  always_comb begin
    for (int j = 0; j < COLS; j++) add_in[j*PW +: PW] = wr_valid ? wr_y[j] : ext_y[j];
    add_in[COLS*PW +: PW] = wr_valid ? wr_chk : ext_chk;
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      v1 <= 1'b0; first1 <= 1'b0; idx1 <= '0; fwd1 <= 1'b0;
    end else begin
      v1     <= acc_we;
      first1 <= wr_valid && wr_first;
      idx1   <= acc_idx;
      fwd1   <= v1 && (idx1 == acc_idx);          // the operation in stage 1 writes row idx1 at the edge the arriving one reads
    end
  end
  // drop 0.43: the copies, complemented (the header); reset to the inverse of the primaries' reset values
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      v1_n <= 1'b1; first1_n <= 1'b1; idx1_n <= '1; fwd1_n <= 1'b1; rbq_n <= '1;
    end else begin
      v1_n     <= !acc_we;
      first1_n <= !(wr_valid && wr_first);
      idx1_n   <= ~acc_idx;
      fwd1_n   <= !(!v1_n && (idx1_c == acc_idx));
      if (rd_en || acc_we) rbq_n <= ~rb_addr;
    end
  end
  assign idx1_c   = ~idx1_n;
  assign rbq_c    = ~rbq_n;
  assign ctrl_err = (v1 == v1_n) || (first1 == first1_n) || (idx1 != idx1_c) || (fwd1 == fwd1_n) ||
                    ((v1 || rd_valid) && (rb_addr_q != rbq_c));
  // the data registers of the pipeline (2 x RW bits) carry no reset: add1 is read only under v1, new_q only under fwd1
  always_ff @(posedge clk) begin
    add1  <= add_in;
    new_q <= row_new;
  end

  // stage 1: the forward, then the accumulate (a first write adds to zero)
  assign row_old = fwd1 ? new_q : row_rd;
  always_comb begin
    for (int j = 0; j <= COLS; j++) begin
      logic signed [PW-1:0] old_j;
      old_j = first1 ? PW'(0) : row_old[j*PW +: PW];
      row_new[j*PW +: PW] = old_j + add1[j*PW +: PW];
    end
  end

  assign rb_addr = rd_en ? rd_idx : acc_idx;
  neo_mem_rf #(.W(RW), .D(ACC_ROWS), .G(1), .RB(1), .RBQ(1), .DV_HOOKS(0)) u_mem (
    .clk(clk),
    .we(v1), .waddr(idx1), .wdata(row_new),
    .ra_addr('0), .ra_data(unused_ra),
    .rb_en(rd_en || acc_we), .rb_addr(rb_addr), .rb_data(row_rd), .rb_addr_q(rb_addr_q),
    .bd_we('0), .bd_addr('0), .bd_wdata('0), .bd_rdata(unused_bd_rd),
    .fi_en(1'b0), .fi_or(1'b0), .fi_addr('0), .fi_mask('0));
  assign unused_bd = ^unused_bd_rd;

  always_comb begin
    for (int j = 0; j < COLS; j++) rd_data[j] = row_rd[j*PW +: PW];
    rd_chk = row_rd[COLS*PW +: PW];
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) rd_valid <= 1'b0;
    else        rd_valid <= rd_en;
  end

  abft_checker #(.COLS(COLS), .PW(PW)) u_abft (
    .clk(clk), .rst_n(rst_n), .valid(rd_valid), .y(rd_data), .y_chk(rd_chk),
    .abft_clear(abft_clear), .err(abft_err), .err_valid(abft_err_valid), .err_sticky(abft_err_sticky));
endmodule
