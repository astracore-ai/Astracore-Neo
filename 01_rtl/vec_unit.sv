/* verilator lint_off DECLFILENAME */        // four modules -- vec_ctrl, vec_mul, vec_interp, vec_unit -- in one file by design
/* verilator coverage_off */                 // the whole file: no line / toggle points in Verilator's coverage builds -- the
                                             // tile32 coverage build (run 196: 15.6 GB of the runner's 16 GB) runs out of
                                             // memory with the pipelined unit's; V1 checks it bit for bit, the functional
                                             // bins sample its ops (a comment to every other tool)
// vec_unit.sv -- the vector unit on the core's drain path (drop 0.43, stream C2): a row-wide micro-op engine between the
//   accumulator and the requantizer that executes a per-layer program of up to 16 micro-ops on every output row.
//
//   Placement and modes. neo_core feeds the requantizer from the accumulator drain (rd_data, INT32 rows) while the program is
//   empty (slot 0 = END: `active` low) -- the bypass, the dense path of drop 0.42 unchanged to the cycle. With a program loaded
//   the sequencer's drain still runs (neo_core holds rd_valid low: its rows reach no drain), and after its `done` this unit reads the
//   accumulator rows 0 .. cfg_m - 1 again through the core's read port, one row at a time, runs the program on each row and
//   hands the rows its EMIT ops produce to the requantizer; neo_core's `done` is then this unit's. A row is held in the row
//   register X (COLS lanes of INT32) while the ops execute, so an op may reduce the row and a later one apply the result to
//   the same row -- the two passes of a softmax or a layer norm over the row's COLS lanes.
//
//   State: X[COLS] (the row, INT32), four scalar registers S0..S3 (INT32), and the row statistics RS0 / RS1 -- two INT32
//   words per accumulator row in a leaf (ACC_ROWS x 64 bits) that persists across descriptors, so a reduction spanning
//   several output tiles of one layer (a softmax over keys in eight tiles, a layer norm over channels in four) is the
//   per-tile lane reduction combined into the row's word: the compiler runs the tiles of a row block on one core, one
//   round to accumulate, a round to apply. RS0 / RS1 of the row are read with the row (RSLD), written by RSST.
//   The residual rows (RBUF: ACC_ROWS entries of ROWS INT8 bytes, a leaf filled by the DMA's FETCH_A with arg 1 -- pairs
//   at even entries, as the activation buffer) are read with the row (lanes 0 .. COLS - 1 of the entry).
//
//   Ops (op word: [3:0] op, [5:4] d, [7:6] a, [9:8] b, [14:10] sh, [16:15] k, [17] t, [19:18] mode, [20] ka, [21] kb;
//   [30:22] reserved), saturating to INT32 unless noted, rounding = round half up ((v + 2^(sh-1)) >>> sh, none at sh = 0).
//   S[a] / S[b] below is the scalar register a / b, or the constant K[a] / K[b] when ka / kb is set (a program reaches its
//   per-layer constants without loading them). Cycles in brackets (RED's: the lane tree's stages and the result's,
//   log2 COLS / 2 + 2 -- 4 at 32 lanes, 3 at 8):
//     0  END              the row's program ends (slot 0 END = no program: the bypass)                                [1]
//     1  EMIT             X goes to the requantizer (one output row)                                                 [1]
//     2  ADD_ROW sh       X[j] += RBUF[m][j] << sh  (the INT8 residual, shifted up to the accumulator's scale)        [3]
//     3  MUL_SCALE sh     X[j] = round(X[j] * SC[j] >> sh) + BI[j]  (per column: INT16 scale, INT32 offset)           [4]
//     4  CLAMP a b        X[j] = min(max(X[j], S[a]), S[b])                                                          [3]
//     5  LUT sh           X[j] = LUTV(sat16(X[j] >>> sh))  (256 entries {D16, V16}: V[i] + round(D[i] * frac / 256),
//                         i = the top byte of the INT16 offset by 128, frac = its low byte)                          [3]
//     6  RED d mode       S[d] = max (mode 0) / sum (mode 1) over the COLS lanes                                     [RED]
//     7  BSUB a           X[j] -= S[a]                                                                               [3]
//     8  BMUL a sh        X[j] = round(X[j] * sat16(S[a]) >> sh)                                                     [4]
//     9  SQR sh           X[j] = round(X[j] * sat16(X[j]) >> sh)                                                     [4]
//    10  SLD d mode t k a S[d] = RS[t] (mode 0), K[k] (1), S[a] (2), 0 (3)                                           [1]
//    11  RSST a t mode    RS[t] = S[a] (mode 0, 3), max(RS[t], S[a]) (1), RS[t] + S[a] (2)                           [3]
//    12  SALU d a b mode  S[d] = S[a] + S[b] (0), S[a] - S[b] (1), max (2), min (3)                                  [3]
//    13  SMUL d a b sh    S[d] = round(S[a] * sat16(S[b]) >> sh)                                                     [4]
//    14  SLUT d a sh      S[d] = LUTS(sat16(S[a] >>> sh))  (the scalar table, the same format as LUTV)               [3]
//    15  RELOAD           X = the row as read (the accumulator's port B holds it while the row's ops run): the second
//                         pass over a row whose first pass reduced a function of it (an in-row layer norm squares X)  [1]
//   A row costs 2 cycles (the row's load and its END) plus its ops' cycles; the first row one more (its read). The
//   program runs on cfg_m rows (cfg_m = 0: ACC_ROWS rows, as the sequencer counts).
//
//   The pipeline (timing, ASAP7 SS: one adder or comparator and its muxes a cycle -- run 196 timed the single-stage
//   lanes at 3.2 ns, the 16:1 op select fixed then). An op runs alone: X and the scalars are written only in its last
//   cycle, so the stage registers below are loaded every cycle and an op's later stages see what its earlier ones
//   computed from the same operands:
//   - the multiplies (MUL_SCALE, BMUL, SQR per lane, SMUL on the scalars): the operands registered in the first cycle
//     (the lane's INT16 operand -- SC[j], sat16(X[j]) or sat16(S[a]) --, the scalar's S[a] and sat16(S[b])), then vec_mul,
//     requant's three stages: the partial products and 2^(sh-1) + BI * 2^sh in carry-save form, two rows registered (no
//     carry-propagate adder); their sum; the shift and the saturation (BI * 2^sh moved above the shift as requant moves
//     its zero point: (v + BI 2^sh) >>> sh = (v >>> sh) + BI);
//   - LUT / SLUT: the shift, the saturation and the entry's index (the leaf's address) in the first cycle; vec_interp's
//     carry-save rows of D * frac + 128 + V * 256 from the entry read in the second; their sum, shifted by 8, in the third;
//   - ADD_ROW: the residual shifted by sh registered in the first cycle, added in the second (the sum registered),
//     saturated into X in the third; BSUB: the difference registered in the second, saturated in the third;
//   - CLAMP: X < S[a], X > S[b] and S[a] > S[b] registered in the second cycle (all three on X: min(max(X, a), b) is b
//     when a > b, else a, b or X by the two), the select into X in the third;
//   - RSST / SALU: S[a] / S[b] (and the row's RS[t]) registered in the first cycle, the add, compare and saturation in
//     the second (the result registered), the write in the third;
//   - RED: the lane tree -- its first level pairwise on X, then radix-4 stages (the max of four by six comparisons and a
//     one-hot select, the sum of four by two 3:2 compressors and an adder), each registered, then the saturation and the
//     mode's select into S[d];
//   - the lanes' control -- the op, sh (three copies: the multiplier's offset row, its final shift, the other shifters),
//     the op cycle and the load / exec strobes, from vec_ctrl's next-cycle values and the program at the next pc -- is a
//     copy per lane, and S[a] / S[b] a copy per pair of lanes, so no control or broadcast
//     operand drives all COLS lanes (run 196: sh's buffer chain into the 32 lanes' shifters, 0.7 ns of the 3.2 ns). A copy's
//     D is formed through its own output (q ^ (q ^ d) = d), so the synthesis keeps the copies apart instead of merging
//     identical flops (noc_router's rd_a); S[a] / S[b] are copied the cycle the op starts, so BSUB and CLAMP read them
//     from their second cycle. X itself is held four times the same way: the multiplier's partial products read their
//     own copy, the requantizers another (out_row: in neo_core the requantizer's carry-save stage follows it in the same
//     cycle), ADD_ROW's and BSUB's adders a third (run 209: X's fanout put 230 ps in front of ADD_ROW's 35-bit sum), the
//     comparators, the tree, the LUT's shifter and X's hold the row itself.
//
//   Tables, written through the host bus (host_if's VEC_ADDR / VEC_DATA; vh_addr[11:8] the table, [7:0] the entry):
//   0 PROG (16 op words), 1 LUTV (256 x {D[31:16], V[15:0]}), 2 LUTS (the same), 3 SC (COLS x INT16), 4 BI (COLS x INT32),
//   5 K (4 x INT32). Every entry stores odd parity, formed at the write and checked on use (the op word when its slot
//   executes, a LUT entry when an op reads it, SC / BI by MUL_SCALE, K by SLD): perr_sticky (R10-style, the requant
//   tables' mechanism). LUTV is a leaf per lane (COLS copies written together, each read by its lane, the host's write
//   registered with its parity on the way to the leaves), LUTS one leaf; the
//   program, SC, BI and K are flops, reset to zero with their parity. The control -- state, program counter, op cycle,
//   row index and the op decode -- is two vec_ctrl copies with a comparator (R7-style): ctrl_err_sticky. Each copy's op
//   word is registered from the program at the next cycle's pc (vec_ctrl's pc_nx) with its parity bit, so the program's
//   16:1 select is not on the datapath's control paths. The lanes' control copies and the datapath are covered by the
//   drain CRC and the next layer's checks (the brief's FMEDA split).
module vec_ctrl #(
  parameter int IDXW    = 9,
  parameter int RED_CYC = 4                // RED's cycles (the lane tree's stages and the result's)
)(
  input  logic             clk,
  input  logic             rst_n,
  input  logic             go,
  input  logic             active,
  input  logic             seq_done,
  input  logic [IDXW-1:0]  cfg_m,
  input  logic [30:0]      op_word,      // the op word at this copy's pc (the unit reads the program for each copy)
  input  logic             fault_inject, // DV: flips bit 0 of the row index this copy presents
  output logic [3:0]       pc,
  output logic [3:0]       pc_nx,        // the next cycle's pc, op cycle and strobes: the unit registers the op word and the
  output logic [2:0]       cyc_nx,       // lanes' control copies a cycle ahead
  output logic             exec_nx,
  output logic             ld_nx,
  output logic [IDXW+35:0] ctl,          // everything the comparator compares: state, pc, cycle, row, decode, strobes
  output logic             exec,         // an op executes this cycle
  output logic [2:0]       cyc,          // its cycle: 0 .. the op's cycles - 1
  output logic             last,         // its last cycle
  output logic             ld,           // the row read last cycle is loaded this cycle
  output logic             rd_issue,     // read row rd_idx (accumulator, RS, RBUF) this cycle
  output logic [IDXW-1:0]  rd_idx,
  output logic [IDXW-1:0]  m,            // the row in execution
  output logic             done,
  output logic             busy
);
  localparam logic [2:0] S_IDLE = 3'd0, S_WAIT = 3'd1, S_RD = 3'd2, S_LD = 3'd3, S_EX = 3'd4, S_DONE = 3'd5;
  logic [2:0]      state, state_nx;
  logic [IDXW-1:0] mq, mq_nx, cm1, mn;
  logic [3:0]      op;
  logic [2:0]      cq, cq_nx, ncm1;                      // ncm1: the op's cycles - 1
  logic            row_end, last_row;

  assign op = op_word[3:0];
  always_comb begin
    case (op)
      4'd2, 4'd4, 4'd5, 4'd7, 4'd11, 4'd12, 4'd14:
                                 ncm1 = 3'd2;            // ADD_ROW, CLAMP, LUT, BSUB, RSST, SALU, SLUT
      4'd3, 4'd8, 4'd9, 4'd13:   ncm1 = 3'd3;            // MUL_SCALE, BMUL, SQR, SMUL
      4'd6:                      ncm1 = 3'(RED_CYC - 1);
      default:                   ncm1 = 3'd0;            // END, EMIT, SLD, RELOAD
    endcase
  end
  assign last     = (cq == ncm1);
  assign exec     = (state == S_EX);
  assign row_end  = exec && ((op == 4'd0) || (last && (pc == 4'd15)));
  assign cm1      = cfg_m - IDXW'(1);
  assign mn       = mq + IDXW'(1);
  assign last_row = (mq == cm1);
  assign m        = mq ^ {{(IDXW-1){1'b0}}, fault_inject};
  assign rd_issue = (state == S_RD) || (row_end && !last_row);
  assign rd_idx   = ((state == S_RD) ? mq : mn) ^ {{(IDXW-1){1'b0}}, fault_inject};
  assign ld       = (state == S_LD);
  assign cyc      = cq;
  assign done     = (state == S_DONE);
  assign busy     = (state != S_IDLE);
  assign ctl      = {state, pc, cq, m, op_word[21:0], rd_issue, done, ld, exec};
  assign cyc_nx   = cq_nx;
  assign exec_nx  = (state_nx == S_EX);
  assign ld_nx    = (state_nx == S_LD);
  always_comb begin
    state_nx = state; pc_nx = pc; cq_nx = cq; mq_nx = mq;
    case (state)
      S_IDLE: if (go && active) state_nx = S_WAIT;
      S_WAIT: if (seq_done) begin state_nx = S_RD; mq_nx = '0; end
      S_RD:   state_nx = S_LD;
      S_LD:   begin state_nx = S_EX; pc_nx = '0; cq_nx = '0; end
      S_EX: begin
        if (row_end) begin
          pc_nx = '0; cq_nx = '0;
          if (last_row) state_nx = S_DONE;
          else begin mq_nx = mn; state_nx = S_LD; end
        end else if (!last) begin
          cq_nx = cq + 3'd1;
        end else begin
          cq_nx = '0; pc_nx = pc + 4'd1;
        end
      end
      S_DONE:  state_nx = S_IDLE;
      default: state_nx = S_IDLE;
    endcase
  end
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state <= S_IDLE; pc <= '0; cq <= '0; mq <= '0;
    end else begin
      state <= state_nx; pc <= pc_nx; cq <= cq_nx; mq <= mq_nx;
    end
  end
  logic unused_ctl;
  assign unused_ctl = ^op_word[30:22];
endmodule

// vec_mul: round(x * mb >> sh) + bias, saturated to INT32, in three stages (requant.sv's structure): (1) the 16 partial
// products of x (INT32) and mb (INT16), the correction of mb's sign bit and c = 2^(sh-1) + bias * 2^sh reduced to two
// rows by 3:2 compressors (64 bits, modulo 2^64; the true sum is below 2^63 in magnitude), registered; (2) their sum; (3)
// v = s >>> sh (= the rounded quotient plus bias: bias sits above the shift), saturated when s's bits from 31 + sh up are
// not all its sign. res is valid two cycles after x / mb / bias / sh are presented (held for those cycles).
// Partial products, as requant's: row k < 15 is x's low 31 bits where mb[k] is set and ~(x[31] & mb[k]) above them,
// shifted by k; row 15 the same of ~x (-x = ~x + 1: mb's bit 15 weighs -2^15) and row 16 its + 1 (mb[15] at bit 15); the
// sign constant, the sum over k of -2^(31+k), rides in row 0 (its bits 32 / 31 and the ones from bit 47 up). c (behind its
// shifter) joins the compressors a level later than the products. sh1 / sh3: the shift amount for stage 1 (c) and stage 3,
// equal; two inputs so a caller can drive the two shifters from separate copies.
module vec_mul (
  input  logic               clk,
  input  logic signed [31:0] x,
  input  logic signed [15:0] mb,
  input  logic signed [31:0] bias,
  input  logic [4:0]         sh1,
  input  logic [4:0]         sh3,
  output logic signed [31:0] res
);
  localparam int SW   = 64;
  localparam int NP0  = 17;                              // the rows before c: 16 partial products and the sign's + 1
  localparam int NIN  = NP0 + 1;
  localparam int NC   = NIN - 2;                         // compressors: each takes one row away, two remain
  localparam int QI   = 9;                               // c joins after the first QI compressors (its shifter in front)
  localparam int NROW = NIN + 2 * NC;
  logic [SW-1:0]   row [NROW];
  logic [31:0]     xn;                                   // ~x
  logic [SW-1:0]   ra, rb, s;
  logic [SW-32:0]  ss;                                   // s's bits 31 .. 63: 1 where one differs from the sign
  logic            fits;
  assign xn = ~x;
  always_comb begin
    for (int i = 0; i < NROW; i++) row[i] = '0;
    row[0] = SW'({(x[31] & mb[0]) ^ 1'b1, x[31] & mb[0], x[30:0] & {31{mb[0]}}}) | ({SW{1'b1}} << 47);
    for (int k = 1; k < 15; k++)
      row[k] = SW'({(x[31] & mb[k]) ^ 1'b1, x[30:0] & {31{mb[k]}}}) << k;
    row[15] = SW'({(xn[31] & mb[15]) ^ 1'b1, xn[30:0] & {31{mb[15]}}}) << 15;
    row[16] = SW'(mb[15]) << 15;
    row[NP0 + 2 * QI] = ({{(SW-32){bias[31]}}, bias} << sh1) | ((SW'(1) << sh1) >> 1);
    for (int i = 0; i < NC; i++) begin
      row[NP0 + 2 * i + ((i >= QI) ? 1 : 0)]     = row[3 * i] ^ row[3 * i + 1] ^ row[3 * i + 2];
      row[NP0 + 2 * i + ((i >= QI) ? 1 : 0) + 1] = ((row[3 * i] & row[3 * i + 1]) | (row[3 * i] & row[3 * i + 2]) |
                                                    (row[3 * i + 1] & row[3 * i + 2])) << 1;
    end
  end
  always_ff @(posedge clk) begin                         // data only: the unit's control says when res is used
    ra <= row[NROW - 2];
    rb <= row[NROW - 1];
    s  <= ra + rb;
  end
  assign ss   = s[SW-1:31] ^ {(SW-31){s[SW-1]}};
  assign fits = (ss & ({(SW-31){1'b1}} << sh3)) == '0;
  assign res  = fits ? 32'(s >> sh3) : (s[SW-1] ? 32'sh80000000 : 32'sh7FFFFFFF);
endmodule

// vec_interp: a LUT entry e = {D[31:16], V[15:0]} at fraction frac -> V + round(D * frac / 256) = floor((D frac + 128 +
// 256 V) / 256), in two stages: (1) D's partial products by frac's bits (requant's sign handling, D signed, frac unsigned)
// and the row 128 + 256 V reduced to two rows by 3:2 compressors (26 bits) and registered -- straight from the leaf's
// output, so the multiply-add is not behind the leaf's access time in one cycle; (2) their sum, shifted by 8. y is valid
// a cycle after e / frac are presented (held for that cycle).
module vec_interp (
  input  logic               clk,
  input  logic [31:0]        e,
  input  logic [7:0]         frac,
  output logic signed [17:0] y
);
  localparam int SW   = 26;
  localparam int NP0  = 8;                               // frac's partial products; the row of V joins first (QI = 0)
  localparam int NC   = NP0 - 1;
  localparam int NROW = NP0 + 1 + 2 * NC;
  logic [SW-1:0]   row [NROW];
  logic [SW-1:0]   ra, rb, s;
  always_comb begin
    for (int i = 0; i < NROW; i++) row[i] = '0;
    row[0] = SW'({(e[31] & frac[0]) ^ 1'b1, e[31] & frac[0], e[30:16] & {15{frac[0]}}}) | ({SW{1'b1}} << 23);
    for (int k = 1; k < NP0; k++)
      row[k] = SW'({(e[31] & frac[k]) ^ 1'b1, e[30:16] & {15{frac[k]}}}) << k;
    row[NP0] = {{(SW-24){e[15]}}, e[15:0], 8'd128};
    for (int i = 0; i < NC; i++) begin
      row[NP0 + 2 * i + 1] = row[3 * i] ^ row[3 * i + 1] ^ row[3 * i + 2];
      row[NP0 + 2 * i + 2] = ((row[3 * i] & row[3 * i + 1]) | (row[3 * i] & row[3 * i + 2]) |
                              (row[3 * i + 1] & row[3 * i + 2])) << 1;
    end
  end
  always_ff @(posedge clk) begin                         // data only
    ra <= row[NROW - 2];
    rb <= row[NROW - 1];
  end
  assign s = ra + rb;
  assign y = s[SW-1:8];
  logic unused_s;                                        // the fraction's bits below the result
  assign unused_s = ^s[7:0];
endmodule

module vec_unit #(
  parameter int COLS     = 32,
  parameter int ROWS     = 32,                     // bytes of an RBUF entry (an activation entry); lanes 0 .. COLS - 1 used
  parameter int PW       = 32,
  parameter int ACC_ROWS = 512,
  parameter int IDXW     = $clog2(ACC_ROWS)
)(
  input  logic                  clk,
  input  logic                  rst_n,
  // tables and program, from the host bus
  input  logic                  vh_we,
  input  logic [11:0]           vh_addr,
  input  logic [31:0]           vh_wdata,
  // the core
  input  logic                  go,
  input  logic                  seq_done,       // the sequencer's drain has finished
  input  logic [IDXW-1:0]       cfg_m,
  output logic                  acc_rd_en,      // the unit's own read of the accumulator (after the sequencer's drain)
  output logic [IDXW-1:0]       acc_rd_idx,
  input  logic signed [PW-1:0]  acc_rd_data [COLS],
  // residual rows from the DMA (FETCH_A, arg 1)
  input  logic                  rbuf_we,
  input  logic                  rbuf_we2,
  input  logic [IDXW-1:0]       rbuf_waddr,
  input  logic signed [7:0]     rbuf_wdata  [ROWS],
  input  logic signed [7:0]     rbuf_wdata2 [ROWS],
  output logic                  active,         // a program is loaded
  output logic                  busy,
  output logic                  done,
  output logic                  rd_q,           // the accumulator's output row this cycle is the unit's read
  output logic                  out_valid,
  output logic signed [PW-1:0]  out_row [COLS],
  // safety
  input  logic                  err_clear,
  output logic                  ctrl_err_sticky,
  output logic                  perr_sticky,
  input  logic                  ctrl_fault_inject,   // DV: the primary control's row index bit 0 flipped
  input  logic                  tbl_fault_inject     // DV: bit 30 (reserved) of slot 0's op word flipped as used
);
  localparam int LV  = (COLS > 1) ? $clog2(COLS) : 1;    // levels of the lane tree
  localparam int NP  = 1 << LV;                         // lanes padded to a power of two
  localparam int NST = 1 + LV / 2;                      // the tree's stages: radix 2 on X, then radix 4 (2 last if needed)
  localparam int RCY = NST + 1;                         // RED's cycles: the stages, then the result into S[d]
  localparam int TW  = PW + LV;                         // the sum tree's width
  localparam int RBW = ROWS * 8;                        // an RBUF entry
  localparam int NS  = (COLS + 1) / 2;                  // S[a] / S[b] copies: one per pair of lanes
  localparam logic [3:0] OP_END = 4'd0, OP_EMIT = 4'd1, OP_ADDR = 4'd2, OP_MULS = 4'd3, OP_CLAMP = 4'd4, OP_LUT = 4'd5,
                         OP_RED = 4'd6, OP_BSUB = 4'd7, OP_BMUL = 4'd8, OP_SQR = 4'd9, OP_SLD = 4'd10, OP_RSST = 4'd11,
                         OP_SALU = 4'd12, OP_SMUL = 4'd13, OP_SLUT = 4'd14, OP_RLD = 4'd15;

  // ------------------------------------------------------------------------------------------------------------ tables
  logic [30:0]          prog  [16];
  logic                 ppar  [16];
  logic [31:0]          kreg  [4];
  logic                 kpar  [4];
  logic signed [15:0]   sc    [COLS];
  logic                 scpar [COLS];
  logic signed [31:0]   bi    [COLS];
  logic                 bipar [COLS];
  logic [3:0]           vh_tbl;
  logic [7:0]           vh_ent;
  logic                 vh_par;                          // odd parity of the written word (31 bits for an op word)
  assign vh_tbl = vh_addr[11:8];
  assign vh_ent = vh_addr[7:0];
  assign vh_par = ~(^((vh_tbl == 4'd0) ? {1'b0, vh_wdata[30:0]} : vh_wdata));
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int i = 0; i < 16; i++) begin prog[i] <= '0; ppar[i] <= 1'b1; end
      for (int i = 0; i < 4; i++) begin kreg[i] <= '0; kpar[i] <= 1'b1; end
      for (int j = 0; j < COLS; j++) begin sc[j] <= '0; scpar[j] <= 1'b1; bi[j] <= '0; bipar[j] <= 1'b1; end
    end else if (vh_we) begin
      for (int i = 0; i < 16; i++)
        if ((vh_tbl == 4'd0) && (vh_ent == 8'(i))) begin prog[i] <= vh_wdata[30:0]; ppar[i] <= vh_par; end
      for (int j = 0; j < COLS; j++) begin
        if ((vh_tbl == 4'd3) && (vh_ent == 8'(j))) begin sc[j] <= vh_wdata[15:0]; scpar[j] <= ~(^vh_wdata[15:0]); end
        if ((vh_tbl == 4'd4) && (vh_ent == 8'(j))) begin bi[j] <= vh_wdata; bipar[j] <= vh_par; end
      end
      for (int i = 0; i < 4; i++)
        if ((vh_tbl == 4'd5) && (vh_ent == 8'(i))) begin kreg[i] <= vh_wdata; kpar[i] <= vh_par; end
    end
  end
  assign active = (prog[0][3:0] != OP_END);

  // ----------------------------------------------------------------------------------------------- control, twice (R7)
  logic [3:0]           pc_p, pc_d, pcn_p, pcn_d;
  logic [30:0]          ow_nx;                           // the primary's op word of the next cycle (the program at pcn_p)
  logic [30:0]          ow_p, ow_d;                      // each copy's op word: registered from the next pc (timing: the
  logic                 owpar_p;                         // program's 16:1 select is off the datapath's control paths)
  logic [IDXW+35:0]     ctl_p, ctl_d;
  logic                 exec, ld, last, rd_issue, done_d, busy_d;
  logic [2:0]           cyc, cyc_nx;
  logic                 exec_nx, ld_nx;
  logic [IDXW-1:0]      rd_idx, m;
  logic                 unused_d_exec, unused_d_last, unused_d_ld, unused_d_rd, unused_d_exn, unused_d_ldn;
  logic [2:0]           unused_d_cyc, unused_d_cyn;
  logic [IDXW-1:0]      unused_d_idx, unused_d_m;
  assign ow_nx = prog[pcn_p];
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin ow_p <= '0; ow_d <= '0; owpar_p <= 1'b1; end
    else begin
      ow_p    <= ow_nx ^ {tbl_fault_inject && (pcn_p == 4'd0), 30'd0};
      ow_d    <= prog[pcn_d];
      owpar_p <= ppar[pcn_p];
    end
  end
  logic unused_pc;
  assign unused_pc = ^{pc_p, pc_d};
  vec_ctrl #(.IDXW(IDXW), .RED_CYC(RCY)) u_ctl (
    .clk(clk), .rst_n(rst_n), .go(go), .active(active), .seq_done(seq_done), .cfg_m(cfg_m), .op_word(ow_p),
    .fault_inject(ctrl_fault_inject), .pc(pc_p), .pc_nx(pcn_p), .cyc_nx(cyc_nx), .exec_nx(exec_nx), .ld_nx(ld_nx),
    .ctl(ctl_p), .exec(exec), .cyc(cyc), .last(last), .ld(ld), .rd_issue(rd_issue), .rd_idx(rd_idx), .m(m), .done(done),
    .busy(busy));
  vec_ctrl #(.IDXW(IDXW), .RED_CYC(RCY)) u_ctl_dup (
    .clk(clk), .rst_n(rst_n), .go(go), .active(active), .seq_done(seq_done), .cfg_m(cfg_m), .op_word(ow_d),
    .fault_inject(1'b0), .pc(pc_d), .pc_nx(pcn_d), .cyc_nx(unused_d_cyn), .exec_nx(unused_d_exn), .ld_nx(unused_d_ldn),
    .ctl(ctl_d), .exec(unused_d_exec), .cyc(unused_d_cyc), .last(unused_d_last), .ld(unused_d_ld), .rd_issue(unused_d_rd),
    .rd_idx(unused_d_idx), .m(unused_d_m), .done(done_d), .busy(busy_d));
  logic ctrl_err;
  assign ctrl_err = (ctl_p != ctl_d);
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n)         ctrl_err_sticky <= 1'b0;
    else if (err_clear) ctrl_err_sticky <= 1'b0;
    else if (ctrl_err)  ctrl_err_sticky <= 1'b1;
  end
  assign acc_rd_en  = rd_issue;
  assign acc_rd_idx = rd_idx;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) rd_q <= 1'b0;
    else        rd_q <= rd_issue;
  end

  // the decode (the primary's op word): the scalar side, the strobes, the leaves' reads and the parity checks
  logic [3:0]  op;
  logic [1:0]  fd, fa, fb, fk, fm;
  logic [4:0]  fsh;
  logic        ft, fka, fkb;
  assign op  = ow_p[3:0];
  assign fd  = ow_p[5:4];
  assign fa  = ow_p[7:6];
  assign fb  = ow_p[9:8];
  assign fsh = ow_p[14:10];
  assign fk  = ow_p[16:15];
  assign ft  = ow_p[17];
  assign fm  = ow_p[19:18];
  assign fka = ow_p[20];
  assign fkb = ow_p[21];
  logic        ex0, ex1;                                 // an op's first cycle, its second
  assign ex0 = exec && (cyc == 3'd0);
  assign ex1 = exec && (cyc == 3'd1);

  // ---------------------------------------------------------------------------------------------------- leaves
  // row statistics: word m = {RS1, RS0} of accumulator row m, read with the row, RSST writes one half (its last cycle)
  logic [63:0]          rs_rd;
  logic signed [31:0]   rsh   [2];                       // the row's RS0 / RS1 (loaded with the row, updated by RSST)
  logic signed [31:0]   rsst_v;
  logic signed [31:0]   rsst_q;                          // RSST's result, registered (written in its third cycle)
  logic [1:0]           rs_we;
  logic [63:0]          unused_rs_ra, unused_rs_bd;
  assign rs_we = {exec && last && (op == OP_RSST) && ft, exec && last && (op == OP_RSST) && !ft};
  neo_mem_rf #(.W(64), .D(ACC_ROWS), .G(2), .RB(1), .DV_HOOKS(0)) u_rs (
    .clk(clk), .we(rs_we), .waddr(m), .wdata({rsst_q, rsst_q}),
    .ra_addr('0), .ra_data(unused_rs_ra), .rb_en(rd_issue), .rb_addr(rd_idx), .rb_data(rs_rd),
    .bd_we('0), .bd_addr('0), .bd_wdata('0), .bd_rdata(unused_rs_bd),
    .fi_en(1'b0), .fi_or(1'b0), .fi_addr('0), .fi_mask('0));

  // the residual buffer: ACC_ROWS / 2 words of an entry pair, written as the activation buffer is (we: entry rbuf_waddr,
  // we2: the pair's upper entry at an even rbuf_waddr), read on port B with the row
  logic [RBW-1:0]       rb_w1, rb_w2;
  logic [2*RBW-1:0]     rb_pair_w, rb_pair_r;
  logic [1:0]           rb_pair_we;
  logic                 rb_sel_q;                        // the entry of the pair read last cycle
  logic [RBW-1:0]       rbe;                             // the row's entry (loaded with the row)
  logic signed [7:0]    rbb   [COLS];
  logic [2*RBW-1:0]     unused_rb_ra, unused_rb_bd;
  generate                                              // continuous assignments per element: Verilator 5.020 ran an
    for (genvar c = 0; c < ROWS; c++) begin : g_rbw      // always_comb packing a port's unpacked array as settle-only logic
      assign rb_w1[8*c +: 8] = rbuf_wdata[c];
      assign rb_w2[8*c +: 8] = rbuf_wdata2[c];
    end
  endgenerate
  assign rb_pair_we = {rbuf_we2 || (rbuf_we && rbuf_waddr[0]), rbuf_we && !rbuf_waddr[0]};
  assign rb_pair_w  = {rbuf_we2 ? rb_w2 : rb_w1, rb_w1};
  neo_mem_rf #(.W(2 * RBW), .D(ACC_ROWS / 2), .G(2), .RB(1), .DV_HOOKS(0)) u_rbuf (
    .clk(clk), .we(rb_pair_we), .waddr(rbuf_waddr[IDXW-1:1]), .wdata(rb_pair_w),
    .ra_addr('0), .ra_data(unused_rb_ra), .rb_en(rd_issue), .rb_addr(rd_idx[IDXW-1:1]), .rb_data(rb_pair_r),
    .bd_we('0), .bd_addr('0), .bd_wdata('0), .bd_rdata(unused_rb_bd),
    .fi_en(1'b0), .fi_or(1'b0), .fi_addr('0), .fi_mask('0));
  generate
    for (genvar j = 0; j < COLS; j++) begin : g_rbb
      assign rbb[j] = rbe[8*j +: 8];
    end
    if (ROWS > COLS) begin : g_rbu
      logic unused_rbe;
      assign unused_rbe = ^rbe[RBW-1:8*COLS];
    end
  endgenerate

  // the LUTs: LUTV one leaf per lane (written together), LUTS one leaf; {parity, D, V} per entry
  logic [7:0]           lidx  [COLS];                    // LUT op, first cycle: each lane's entry
  logic [7:0]           lfrac [COLS];
  logic [7:0]           lfq   [COLS];                    // the fraction, registered (held: X does not change in the op)
  logic [32:0]          lrd   [COLS];                    // second cycle: the entries read
  logic                 luts_we;                         // the leaves' write: the host's, registered with its parity --
  logic                 lut_we [COLS];                   // the data's fanout into the COLS + 1 leaves is not behind the
  logic [7:0]           lut_ent;                         // bus and the parity's XOR tree in one cycle (run 201: 1,475 ps);
  logic [32:0]          lut_wd;                          // the write enable a copy per lane (a leaf pin's load each)
  logic                 lut_re [COLS];                   // a lane's leaf read: LUT's first cycle, from the lane's copies
  logic [7:0]           sidx, sfrac, sfq;
  logic [32:0]          srd;
  logic [32:0]          unused_lra [COLS];
  logic [32:0]          unused_lbd [COLS];
  logic [32:0]          unused_sra, unused_sbd;
  always_ff @(posedge clk or negedge rst_n) begin       // (the flop tables take a write at once: a program loaded and
    if (!rst_n) begin                                    // started at once still runs; a LUT is read only in a run)
      for (int j = 0; j < COLS; j++) lut_we[j] <= 1'b0;
      luts_we <= 1'b0; lut_ent <= '0; lut_wd <= '0;
    end else begin
      for (int j = 0; j < COLS; j++) lut_we[j] <= lut_we[j] ^ (lut_we[j] ^ (vh_we && (vh_tbl == 4'd1)));
      luts_we <= vh_we && (vh_tbl == 4'd2);
      lut_ent <= vh_ent;
      lut_wd  <= {vh_par, vh_wdata};
    end
  end
  generate
    for (genvar j = 0; j < COLS; j++) begin : g_lut
      neo_mem_rf #(.W(33), .D(256), .G(1), .RB(1), .DV_HOOKS(0)) u_lutv (
        .clk(clk), .we(lut_we[j]), .waddr(lut_ent), .wdata(lut_wd),
        .ra_addr('0), .ra_data(unused_lra[j]), .rb_en(lut_re[j]), .rb_addr(lidx[j]), .rb_data(lrd[j]),
        .bd_we('0), .bd_addr('0), .bd_wdata('0), .bd_rdata(unused_lbd[j]),
        .fi_en(1'b0), .fi_or(1'b0), .fi_addr('0), .fi_mask('0));
    end
  endgenerate
  neo_mem_rf #(.W(33), .D(256), .G(1), .RB(1), .DV_HOOKS(0)) u_luts (
    .clk(clk), .we(luts_we), .waddr(lut_ent), .wdata(lut_wd),
    .ra_addr('0), .ra_data(unused_sra), .rb_en(ex0 && (op == OP_SLUT)), .rb_addr(sidx), .rb_data(srd),
    .bd_we('0), .bd_addr('0), .bd_wdata('0), .bd_rdata(unused_sbd),
    .fi_en(1'b0), .fi_or(1'b0), .fi_addr('0), .fi_mask('0));

  // ---------------------------------------------------------------------------------------------------- registers
  logic signed [PW-1:0] xr [COLS];                       // the row
  logic signed [PW-1:0] xm [COLS];                       // its copy for the multipliers' partial products
  logic signed [PW-1:0] xo [COLS];                       // its copy for the requantizers (out_row: a flop's output alone)
  logic signed [PW-1:0] xa [COLS];                       // its copy for ADD_ROW's and BSUB's adders
  logic signed [31:0]   sr [4];                          // the scalars
  logic signed [31:0]   sa, sb;                          // S[a], S[b] (K[a], K[b] with ka, kb)
  logic signed [31:0]   sa_q, sb_q;                      // registered (the scalar ops' second cycle on)
  logic signed [31:0]   rst_q;                           // RS[t], registered (RSST's second cycle)
  logic signed [15:0]   sa16, sb16, sbq16;               // sat16 of S[a] (BMUL's operand), of S[b], registered (SMUL's)
  logic signed [31:0]   salu_q;                          // SALU's result, registered (written in the third cycle)
  assign sa    = fka ? kreg[fa] : sr[fa];
  assign sb    = fkb ? kreg[fb] : sr[fb];
  assign sa16  = (sa[31:15] == {17{sa[31]}}) ? sa[15:0] : (sa[31] ? 16'sh8000 : 16'sh7FFF);
  assign sb16  = (sb[31:15] == {17{sb[31]}}) ? sb[15:0] : (sb[31] ? 16'sh8000 : 16'sh7FFF);

  // the lanes' copies (see the header): the control per lane, from the next cycle's values; S[a] / S[b] per pair of lanes,
  // from this cycle's (the op's second cycle on). q ^ (q ^ d) = d: each copy's D through its own output
  logic [3:0]           lc_op [COLS];
  logic [4:0]           lc_sh [COLS];                   // ADD_ROW's and LUT's shifters
  logic [4:0]           lc_s1 [COLS];                   // the multiplier's offset row
  logic [4:0]           lc_s3 [COLS];                   // the multiplier's final shift
  logic [2:0]           lc_cq [COLS];
  logic                 lc_ex [COLS];
  logic                 lc_ld [COLS];
  logic signed [31:0]   lc_sa [NS];
  logic signed [31:0]   lc_sb [NS];
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int j = 0; j < COLS; j++) begin
        lc_op[j] <= '0; lc_sh[j] <= '0; lc_s1[j] <= '0; lc_s3[j] <= '0; lc_cq[j] <= '0; lc_ex[j] <= 1'b0; lc_ld[j] <= 1'b0;
      end
      for (int g = 0; g < NS; g++) begin lc_sa[g] <= '0; lc_sb[g] <= '0; end
    end else begin
      for (int j = 0; j < COLS; j++) begin
        lc_op[j] <= lc_op[j] ^ (lc_op[j] ^ ow_nx[3:0]);
        lc_sh[j] <= lc_sh[j] ^ (lc_sh[j] ^ ow_nx[14:10]);
        lc_s1[j] <= lc_s1[j] ^ (lc_s1[j] ^ ow_nx[14:10]);
        lc_s3[j] <= lc_s3[j] ^ (lc_s3[j] ^ ow_nx[14:10]);
        lc_cq[j] <= lc_cq[j] ^ (lc_cq[j] ^ cyc_nx);
        lc_ex[j] <= lc_ex[j] ^ (lc_ex[j] ^ exec_nx);
        lc_ld[j] <= lc_ld[j] ^ (lc_ld[j] ^ ld_nx);
      end
      for (int g = 0; g < NS; g++) begin
        lc_sa[g] <= lc_sa[g] ^ (lc_sa[g] ^ sa);
        lc_sb[g] <= lc_sb[g] ^ (lc_sb[g] ^ sb);
      end
    end
  end

  // ---------------------------------------------------------------------------------------------------- the lanes
  logic signed [15:0]   mbq   [COLS];                    // the multiplier's INT16 operand, registered in the first cycle
  logic signed [31:0]   mbias [COLS];                    // its offset above the shift (BI for MUL_SCALE)
  logic signed [31:0]   mres  [COLS];                    // round(X * mb >> sh) + offset, saturated (vec_mul)
  logic signed [39:0]   rsw   [COLS];                    // ADD_ROW, first cycle: RBUF << sh
  logic signed [33:0]   rsc   [COLS];                    // clamped to 34 bits (|X| < 2^31: the saturation is the same),
  logic signed [33:0]   rsq   [COLS];                    // registered
  logic signed [34:0]   asum  [COLS];                    // X + RBUF << sh, registered in ADD_ROW's second cycle
  logic signed [34:0]   asq   [COLS];
  logic signed [32:0]   bsum  [COLS];                    // X - S[a], registered in BSUB's second cycle
  logic signed [32:0]   bsq   [COLS];
  logic                 c_lt  [COLS];                    // CLAMP: X < S[a], X > S[b], S[a] > S[b], registered
  logic                 c_gt  [COLS];
  logic                 c_ab  [COLS];
  logic signed [31:0]   cres  [COLS];                    // its result
  logic signed [31:0]   xs    [COLS];                    // X >>> sh (the LUT's input before saturation)
  logic signed [15:0]   u16   [COLS];
  logic signed [17:0]   ly    [COLS];                    // the interpolated entry (vec_interp)
  logic signed [31:0]   xn    [COLS];                    // X after this cycle
  logic signed [PW-1:0] acc_in [COLS];                   // the accumulator's row (per-element assignments, as rbuf_wdata's)
  logic signed [5:0]    shl   [COLS];                    // a lane's sh as a signed operand: neosim types a shift signed
  generate                                              // only when both operands are (SystemVerilog: the left one alone)
    for (genvar j = 0; j < COLS; j++) begin : g_lane
      assign acc_in[j] = acc_rd_data[j];
      assign shl[j]    = {1'b0, lc_sh[j]};
      vec_mul    u_mul (.clk(clk), .x(xm[j]), .mb(mbq[j]), .bias(mbias[j]), .sh1(lc_s1[j]), .sh3(lc_s3[j]), .res(mres[j]));
      vec_interp u_ip  (.clk(clk), .e(lrd[j][31:0]), .frac(lfq[j]), .y(ly[j]));
    end
  endgenerate
  always_comb begin
    for (int j = 0; j < COLS; j++) begin
      mbias[j] = (lc_op[j] == OP_MULS) ? bi[j] : 32'sd0;
      // ADD_ROW, BSUB, CLAMP
      rsw[j]  = 40'(rbb[j]) <<< shl[j];
      rsc[j]  = (rsw[j][39:33] == {7{rsw[j][39]}}) ? rsw[j][33:0] : (rsw[j][39] ? 34'sh200000000 : 34'sh1FFFFFFFF);
      asum[j] = 35'(xa[j]) + 35'(rsq[j]);
      bsum[j] = 33'(xa[j]) - 33'(lc_sa[j / 2]);
      cres[j] = c_ab[j] ? lc_sb[j / 2] : c_lt[j] ? lc_sa[j / 2] : c_gt[j] ? lc_sb[j / 2] : xr[j];
      // LUT: the entry index and fraction from sat16(X >>> sh)
      xs[j]    = xr[j] >>> shl[j];
      u16[j]   = (xs[j][31:15] == {17{xs[j][31]}}) ? xs[j][15:0] : (xs[j][31] ? 16'sh8000 : 16'sh7FFF);
      lidx[j]  = {~u16[j][15], u16[j][14:8]};
      lfrac[j] = u16[j][7:0];
      lut_re[j] = lc_ex[j] && (lc_op[j] == OP_LUT) && (lc_cq[j] == 3'd0);
      // the row after this cycle: written in the op's last cycle
      xn[j] = xr[j];
      if (lc_ld[j]) xn[j] = acc_in[j];
      else if (lc_ex[j]) begin
        case (lc_op[j])
          OP_ADDR:  if (lc_cq[j] == 3'd2) xn[j] = (asq[j][34:31] == {4{asq[j][34]}}) ? asq[j][31:0] :
                                                  (asq[j][34] ? 32'sh80000000 : 32'sh7FFFFFFF);
          OP_MULS, OP_SQR, OP_BMUL: if (lc_cq[j] == 3'd3) xn[j] = mres[j];
          OP_CLAMP: if (lc_cq[j] == 3'd2) xn[j] = cres[j];
          OP_LUT:   if (lc_cq[j] == 3'd2) xn[j] = 32'(ly[j]);
          OP_BSUB:  if (lc_cq[j] == 3'd2) xn[j] = (bsq[j][32] == bsq[j][31]) ? bsq[j][31:0] :
                                                  (bsq[j][32] ? 32'sh80000000 : 32'sh7FFFFFFF);
          OP_RLD:   xn[j] = acc_in[j];
          default: ;
        endcase
      end
    end
  end

  // the lane tree: max or sum over the COLS lanes (padded with -2^31 / 0) in NST stages, each registered: the first
  // pairwise on X (X's fanout in front of one comparison), then radix 4 while four or more values remain (the max of four:
  // the first of the four that is >= the others -- six comparisons, a one-hot select; the sum of four: two 3:2
  // compressors and an adder, TW bits, exact), radix 2 for a last pair
  logic signed [PW-1:0] gmax  [NST][NP];                 // stage s's inputs: the lanes, then the stage before's registers
  logic signed [TW-1:0] gsum  [NST][NP];
  logic signed [PW-1:0] hmax  [NST][NP];                 // stage s's outputs (combinational), registered as qmax[s]
  logic signed [TW-1:0] hsum  [NST][NP];
  logic signed [PW-1:0] qmax  [NST][NP];
  logic signed [TW-1:0] qsum  [NST][NP];
  logic signed [31:0]   red_v;
  always_comb begin
    for (int s = 0; s < NST; s++)
      for (int i = 0; i < NP; i++) begin
        gmax[s][i] = 32'sh80000000; gsum[s][i] = '0; hmax[s][i] = 32'sh80000000; hsum[s][i] = '0;
      end
    for (int i = 0; i < NP; i++) begin
      if (i < COLS) begin gmax[0][i] = xr[i]; gsum[0][i] = TW'(xr[i]); end
    end
    for (int s = 1; s < NST; s++)
      for (int i = 0; i < NP; i++) begin gmax[s][i] = qmax[s-1][i]; gsum[s][i] = qsum[s-1][i]; end
    for (int s = 0; s < NST; s++) begin
      if ((s > 0) && ((NP >> (2 * s)) >= 2)) begin
        for (int i = 0; i < NP / 4; i++)
          if (i < (NP >> (2 * s + 1))) begin
            logic signed [PW-1:0] a, b, c, d;
            logic                 pa, pb, pc;
            logic signed [TW-1:0] s1, k1, s2, k2;
            a = gmax[s][4*i]; b = gmax[s][4*i+1]; c = gmax[s][4*i+2]; d = gmax[s][4*i+3];
            pa = (a >= b) && (a >= c) && (a >= d);
            pb = !pa && (b >= c) && (b >= d);
            pc = !pa && !pb && (c >= d);
            hmax[s][i] = pa ? a : pb ? b : pc ? c : d;
            s1 = gsum[s][4*i] ^ gsum[s][4*i+1] ^ gsum[s][4*i+2];
            k1 = ((gsum[s][4*i] & gsum[s][4*i+1]) | (gsum[s][4*i] & gsum[s][4*i+2]) | (gsum[s][4*i+1] & gsum[s][4*i+2])) << 1;
            s2 = s1 ^ k1 ^ gsum[s][4*i+3];
            k2 = ((s1 & k1) | (s1 & gsum[s][4*i+3]) | (k1 & gsum[s][4*i+3])) << 1;
            hsum[s][i] = s2 + k2;
          end
      end else begin
        for (int i = 0; i < NP / 2; i++)
          if (i < ((s == 0) ? (NP >> 1) : 1)) begin
            hmax[s][i] = (gmax[s][2*i] >= gmax[s][2*i+1]) ? gmax[s][2*i] : gmax[s][2*i+1];
            hsum[s][i] = gsum[s][2*i] + gsum[s][2*i+1];
          end
      end
    end
    if (fm[0]) red_v = (qsum[NST-1][0][TW-1:31] == {(TW-31){qsum[NST-1][0][TW-1]}}) ? qsum[NST-1][0][31:0] :
                       (qsum[NST-1][0][TW-1] ? 32'sh80000000 : 32'sh7FFFFFFF);
    else       red_v = qmax[NST-1][0];
  end

  // ---------------------------------------------------------------------------------------------------- the scalars
  logic signed [31:0]   smres;
  logic signed [32:0]   ssum;
  logic signed [31:0]   salu_v, sld_v;
  logic signed [5:0]    shs;                             // sh as a signed operand (see shl)
  logic signed [31:0]   ssh;                             // S[a] >>> sh (the scalar LUT's input)
  logic signed [15:0]   su16;
  logic signed [17:0]   sly;
  logic signed [32:0]   rsadd;
  assign shs = {1'b0, fsh};
  vec_mul    u_smul (.clk(clk), .x(sa_q), .mb(sbq16), .bias(32'sd0), .sh1(fsh), .sh3(fsh), .res(smres));
  vec_interp u_sip  (.clk(clk), .e(srd[31:0]), .frac(sfq), .y(sly));
  always_comb begin
    ssum  = fm[0] ? (33'(sa_q) - 33'(sb_q)) : (33'(sa_q) + 33'(sb_q));
    case (fm)
      2'd0, 2'd1: salu_v = (ssum[32] == ssum[31]) ? ssum[31:0] : (ssum[32] ? 32'sh80000000 : 32'sh7FFFFFFF);
      2'd2:       salu_v = (sa_q > sb_q) ? sa_q : sb_q;
      default:    salu_v = (sa_q < sb_q) ? sa_q : sb_q;
    endcase
    case (fm)
      2'd0:    sld_v = rsh[ft];
      2'd1:    sld_v = kreg[fk];
      2'd2:    sld_v = sa;
      default: sld_v = '0;
    endcase
    ssh   = sa >>> shs;
    su16  = (ssh[31:15] == {17{ssh[31]}}) ? ssh[15:0] : (ssh[31] ? 16'sh8000 : 16'sh7FFF);
    sidx  = {~su16[15], su16[14:8]};
    sfrac = su16[7:0];
    rsadd = 33'(rst_q) + 33'(sa_q);
    case (fm)
      2'd1:    rsst_v = (rst_q > sa_q) ? rst_q : sa_q;
      2'd2:    rsst_v = (rsadd[32] == rsadd[31]) ? rsadd[31:0] : (rsadd[32] ? 32'sh80000000 : 32'sh7FFFFFFF);
      default: rsst_v = sa_q;
    endcase
  end

  always_ff @(posedge clk) begin                         // the stage registers (data: the control says when they are used)
    for (int j = 0; j < COLS; j++) begin
      if (lc_op[j] == OP_MULS)     mbq[j] <= sc[j];
      else if (lc_op[j] == OP_SQR) mbq[j] <= (xm[j][31:15] == {17{xm[j][31]}}) ? xm[j][15:0] :
                                             (xm[j][31] ? 16'sh8000 : 16'sh7FFF);
      else                         mbq[j] <= sa16;
      rsq[j]  <= rsc[j];
      lfq[j]  <= lfrac[j];
      asq[j]  <= asum[j];
      bsq[j]  <= bsum[j];
      c_lt[j] <= xr[j] < lc_sa[j / 2];
      c_gt[j] <= xr[j] > lc_sb[j / 2];
      c_ab[j] <= lc_sa[j / 2] > lc_sb[j / 2];
    end
    for (int s = 0; s < NST; s++)
      for (int i = 0; i < NP; i++) begin qmax[s][i] <= hmax[s][i]; qsum[s][i] <= hsum[s][i]; end
    sa_q   <= sa;
    sb_q   <= sb;
    sbq16  <= sb16;
    rst_q  <= rsh[ft];
    rsst_q <= rsst_v;
    salu_q <= salu_v;
    sfq  <= sfrac;
  end
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int j = 0; j < COLS; j++) begin xr[j] <= '0; xm[j] <= '0; xo[j] <= '0; xa[j] <= '0; end
      for (int i = 0; i < 4; i++) sr[i] <= '0;
      rsh[0] <= '0; rsh[1] <= '0; rbe <= '0; rb_sel_q <= 1'b0;
    end else begin
      for (int j = 0; j < COLS; j++) begin
        xr[j] <= xn[j]; xm[j] <= xm[j] ^ (xm[j] ^ xn[j]); xo[j] <= xo[j] ^ (xo[j] ^ xn[j]); xa[j] <= xa[j] ^ (xa[j] ^ xn[j]);
      end
      rb_sel_q <= rd_idx[0];
      if (ld) begin
        rsh[0] <= rs_rd[31:0];
        rsh[1] <= rs_rd[63:32];
        rbe    <= rb_sel_q ? rb_pair_r[2*RBW-1:RBW] : rb_pair_r[RBW-1:0];
      end else if (exec && last && (op == OP_RSST)) begin
        rsh[ft] <= rsst_q;
      end
      if (exec && last) begin
        case (op)
          OP_RED:  sr[fd] <= red_v;
          OP_SLD:  sr[fd] <= sld_v;
          OP_SALU: sr[fd] <= salu_q;
          OP_SMUL: sr[fd] <= smres;
          OP_SLUT: sr[fd] <= 32'(sly);
          default: ;
        endcase
      end
    end
  end

  // EMIT
  assign out_valid = exec && (op == OP_EMIT);
  generate
    for (genvar j = 0; j < COLS; j++) begin : g_out
      assign out_row[j] = xo[j];
    end
  endgenerate

  // ---------------------------------------------------------------------------------------------------- parity
  logic perr;
  always_comb begin
    perr = 1'b0;
    if (exec && ((^{ow_p, owpar_p}) != 1'b1)) perr = 1'b1;
    if (exec && (op == OP_SLD) && (fm == 2'd1) && ((^{kreg[fk], kpar[fk]}) != 1'b1)) perr = 1'b1;
    if (exec && fka && ((^{kreg[fa], kpar[fa]}) != 1'b1)) perr = 1'b1;      // a constant operand
    if (exec && fkb && ((^{kreg[fb], kpar[fb]}) != 1'b1)) perr = 1'b1;
    if (ex0 && (op == OP_MULS))
      for (int j = 0; j < COLS; j++)
        if (((^{sc[j], scpar[j]}) != 1'b1) || ((^{bi[j], bipar[j]}) != 1'b1)) perr = 1'b1;
    if (ex1 && (op == OP_LUT))
      for (int j = 0; j < COLS; j++)
        if ((^lrd[j]) != 1'b1) perr = 1'b1;
    if (ex1 && (op == OP_SLUT) && ((^srd) != 1'b1)) perr = 1'b1;
  end
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n)         perr_sticky <= 1'b0;
    else if (err_clear) perr_sticky <= 1'b0;
    else if (perr)      perr_sticky <= 1'b1;
  end

  // what nothing reads (lint): the duplicate's datapath-side outputs (its compared ones are in ctl_d), the unused leaf ports
  logic unused_vec;
  assign unused_vec = ^{unused_d_exec, unused_d_cyc, unused_d_last, unused_d_ld, unused_d_rd, unused_d_idx, unused_d_m,
                        unused_d_cyn, unused_d_exn, unused_d_ldn, done_d, busy_d,
                        unused_rs_ra, unused_rs_bd, unused_rb_ra, unused_rb_bd, unused_sra, unused_sbd};
  generate
    for (genvar j = 0; j < COLS; j++) begin : g_unl
      logic unused_l;
      assign unused_l = ^{unused_lra[j], unused_lbd[j]};
    end
  endgenerate
endmodule
/* verilator coverage_on */
