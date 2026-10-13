/* verilator lint_off DECLFILENAME */        // two modules, vec_ctrl and vec_unit, in one file by design
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
//   per-layer constants without loading them). Cycles in brackets:
//     0  END              the row's program ends (slot 0 END = no program: the bypass)                                [1]
//     1  EMIT             X goes to the requantizer (one output row)                                                 [1]
//     2  ADD_ROW sh       X[j] += RBUF[m][j] << sh  (the INT8 residual, shifted up to the accumulator's scale)        [1]
//     3  MUL_SCALE sh     X[j] = round(X[j] * SC[j] >> sh) + BI[j]  (per column: INT16 scale, INT32 offset)           [2]
//     4  CLAMP a b        X[j] = min(max(X[j], S[a]), S[b])                                                          [1]
//     5  LUT sh           X[j] = LUTV(sat16(X[j] >>> sh))  (256 entries {D16, V16}: V[i] + round(D[i] * frac / 256),
//                         i = the top byte of the INT16 offset by 128, frac = its low byte)                          [2]
//     6  RED d mode       S[d] = max (mode 0) / sum (mode 1) over the COLS lanes (a tree in two stages)              [2]
//     7  BSUB a           X[j] -= S[a]                                                                               [1]
//     8  BMUL a sh        X[j] = round(X[j] * sat16(S[a]) >> sh)                                                     [2]
//     9  SQR sh           X[j] = round(X[j] * sat16(X[j]) >> sh)                                                     [2]
//    10  SLD d mode t k a S[d] = RS[t] (mode 0), K[k] (1), S[a] (2), 0 (3)                                           [1]
//    11  RSST a t mode    RS[t] = S[a] (mode 0, 3), max(RS[t], S[a]) (1), RS[t] + S[a] (2)                           [1]
//    12  SALU d a b mode  S[d] = S[a] + S[b] (0), S[a] - S[b] (1), max (2), min (3)                                  [1]
//    13  SMUL d a b sh    S[d] = round(S[a] * sat16(S[b]) >> sh)                                                     [2]
//    14  SLUT d a sh      S[d] = LUTS(sat16(S[a] >>> sh))  (the scalar table, the same format as LUTV)               [2]
//    15  RELOAD           X = the row as read (the accumulator's port B holds it while the row's ops run): the second
//                         pass over a row whose first pass reduced a function of it (an in-row layer norm squares X)  [1]
//   A row costs 2 cycles (the row's load and its END) plus its ops' cycles; the first row one more (its read). The
//   program runs on cfg_m rows (cfg_m = 0: ACC_ROWS rows, as the sequencer counts).
//
//   Tables, written through the host bus (host_if's VEC_ADDR / VEC_DATA; vh_addr[11:8] the table, [7:0] the entry):
//   0 PROG (16 op words), 1 LUTV (256 x {D[31:16], V[15:0]}), 2 LUTS (the same), 3 SC (COLS x INT16), 4 BI (COLS x INT32),
//   5 K (4 x INT32). Every entry stores odd parity, formed at the write and checked on use (the op word when its slot
//   executes, a LUT entry when an op reads it, SC / BI by MUL_SCALE, K by SLD): perr_sticky (R10-style, the requant
//   tables' mechanism). LUTV is a leaf per lane (COLS copies written together, each read by its lane), LUTS one leaf; the
//   program, SC, BI and K are flops, reset to zero with their parity. The control -- state, program counter, op cycle,
//   row index and the op decode -- is two vec_ctrl copies with a comparator (R7-style): ctrl_err_sticky. Each copy's op
//   word is registered from the program at the next cycle's pc (vec_ctrl's pc_nx) with its parity bit, so the program's
//   16:1 select is not on the datapath's control paths (ASAP7 SS: 3,185 -> 2,667 ps at 8 lanes). The datapath is
//   covered by the drain CRC and the next layer's checks (the brief's FMEDA split).
module vec_ctrl #(
  parameter int IDXW = 9
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
  output logic [3:0]       pc_nx,        // the pc of the next cycle: the unit registers the op word a cycle ahead
  output logic [IDXW+33:0] ctl,          // everything the comparator compares: state, pc, cycle, row, decode, strobes
  output logic             exec,         // an op executes this cycle
  output logic             cyc,          // its second cycle (two-cycle ops)
  output logic             ld,           // the row read last cycle is loaded this cycle
  output logic             rd_issue,     // read row rd_idx (accumulator, RS, RBUF) this cycle
  output logic [IDXW-1:0]  rd_idx,
  output logic [IDXW-1:0]  m,            // the row in execution
  output logic             done,
  output logic             busy
);
  localparam logic [2:0] S_IDLE = 3'd0, S_WAIT = 3'd1, S_RD = 3'd2, S_LD = 3'd3, S_EX = 3'd4, S_DONE = 3'd5;
  logic [2:0]      state;
  logic [IDXW-1:0] mq, cm1, mn;
  logic [3:0]      op;
  logic            two, last, row_end, last_row;
  logic            cq;

  assign op       = op_word[3:0];
  assign two      = (op == 4'd3) || (op == 4'd5) || (op == 4'd6) || (op == 4'd8) || (op == 4'd9) || (op == 4'd13) || (op == 4'd14);
  assign last     = !two || cq;
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
  always_comb begin                      // the pc update below, a cycle early
    pc_nx = pc;
    if (state == S_LD) pc_nx = '0;
    else if (exec) begin
      if (row_end)             pc_nx = '0;
      else if (!(two && !cq))  pc_nx = pc + 4'd1;
    end
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state <= S_IDLE; pc <= '0; cq <= 1'b0; mq <= '0;
    end else begin
      case (state)
        S_IDLE: if (go && active) state <= S_WAIT;
        S_WAIT: if (seq_done) begin state <= S_RD; mq <= '0; end
        S_RD:   state <= S_LD;
        S_LD:   begin state <= S_EX; pc <= '0; cq <= 1'b0; end
        S_EX: begin
          if (row_end) begin
            pc <= '0; cq <= 1'b0;
            if (last_row) state <= S_DONE;
            else begin mq <= mn; state <= S_LD; end
          end else if (two && !cq) begin
            cq <= 1'b1;
          end else begin
            cq <= 1'b0; pc <= pc + 4'd1;
          end
        end
        S_DONE: state <= S_IDLE;
        default: state <= S_IDLE;
      endcase
    end
  end
  logic unused_ctl;
  assign unused_ctl = ^op_word[30:22];
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
  localparam int L1  = (LV + 1) / 2;                    // the tree is registered after level L1
  localparam int NQ  = NP >> L1;                        // values registered there
  localparam int TW  = PW + LV;                         // the sum tree's width
  localparam int RBW = ROWS * 8;                        // an RBUF entry
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
  logic [30:0]          ow_p, ow_d;                      // each copy's op word: registered from the next pc (timing: the
  logic                 owpar_p;                         // program's 16:1 select is off the datapath's control paths)
  logic [IDXW+33:0]     ctl_p, ctl_d;
  logic                 exec, cyc, ld, rd_issue, done_d, busy_d;
  logic [IDXW-1:0]      rd_idx, m;
  logic                 unused_d_exec, unused_d_cyc, unused_d_ld, unused_d_rd;
  logic [IDXW-1:0]      unused_d_idx, unused_d_m;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin ow_p <= '0; ow_d <= '0; owpar_p <= 1'b1; end
    else begin
      ow_p    <= prog[pcn_p] ^ {tbl_fault_inject && (pcn_p == 4'd0), 30'd0};
      ow_d    <= prog[pcn_d];
      owpar_p <= ppar[pcn_p];
    end
  end
  logic unused_pc;
  assign unused_pc = ^{pc_p, pc_d};
  vec_ctrl #(.IDXW(IDXW)) u_ctl (
    .clk(clk), .rst_n(rst_n), .go(go), .active(active), .seq_done(seq_done), .cfg_m(cfg_m), .op_word(ow_p),
    .fault_inject(ctrl_fault_inject), .pc(pc_p), .pc_nx(pcn_p), .ctl(ctl_p), .exec(exec), .cyc(cyc), .ld(ld), .rd_issue(rd_issue),
    .rd_idx(rd_idx), .m(m), .done(done), .busy(busy));
  vec_ctrl #(.IDXW(IDXW)) u_ctl_dup (
    .clk(clk), .rst_n(rst_n), .go(go), .active(active), .seq_done(seq_done), .cfg_m(cfg_m), .op_word(ow_d),
    .fault_inject(1'b0), .pc(pc_d), .pc_nx(pcn_d), .ctl(ctl_d), .exec(unused_d_exec), .cyc(unused_d_cyc), .ld(unused_d_ld),
    .rd_issue(unused_d_rd), .rd_idx(unused_d_idx), .m(unused_d_m), .done(done_d), .busy(busy_d));
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

  // the decode (the primary's op word)
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
  logic        ex0, ex1;                                 // an op's first cycle, a two-cycle op's second
  assign ex0 = exec && !cyc;
  assign ex1 = exec && cyc;

  // ---------------------------------------------------------------------------------------------------- leaves
  // row statistics: word m = {RS1, RS0} of accumulator row m, read with the row, RSST writes one half
  logic [63:0]          rs_rd;
  logic signed [31:0]   rsh   [2];                       // the row's RS0 / RS1 (loaded with the row, updated by RSST)
  logic signed [31:0]   rsst_v;
  logic [1:0]           rs_we;
  logic [63:0]          unused_rs_ra, unused_rs_bd;
  assign rs_we = {exec && (op == OP_RSST) && ft, exec && (op == OP_RSST) && !ft};
  neo_mem_rf #(.W(64), .D(ACC_ROWS), .G(2), .RB(1), .DV_HOOKS(0)) u_rs (
    .clk(clk), .we(rs_we), .waddr(m), .wdata({rsst_v, rsst_v}),
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
  logic [7:0]           lfrac_q [COLS];
  logic [32:0]          lrd   [COLS];                    // second cycle: the entries read
  logic                 lut_we, luts_we;
  logic [7:0]           sidx, sfrac, sfrac_q;
  logic [32:0]          srd;
  logic [32:0]          unused_lra [COLS];
  logic [32:0]          unused_lbd [COLS];
  logic [32:0]          unused_sra, unused_sbd;
  assign lut_we  = vh_we && (vh_tbl == 4'd1);
  assign luts_we = vh_we && (vh_tbl == 4'd2);
  generate
    for (genvar j = 0; j < COLS; j++) begin : g_lut
      neo_mem_rf #(.W(33), .D(256), .G(1), .RB(1), .DV_HOOKS(0)) u_lutv (
        .clk(clk), .we(lut_we), .waddr(vh_ent), .wdata({vh_par, vh_wdata}),
        .ra_addr('0), .ra_data(unused_lra[j]), .rb_en(ex0 && (op == OP_LUT)), .rb_addr(lidx[j]), .rb_data(lrd[j]),
        .bd_we('0), .bd_addr('0), .bd_wdata('0), .bd_rdata(unused_lbd[j]),
        .fi_en(1'b0), .fi_or(1'b0), .fi_addr('0), .fi_mask('0));
    end
  endgenerate
  neo_mem_rf #(.W(33), .D(256), .G(1), .RB(1), .DV_HOOKS(0)) u_luts (
    .clk(clk), .we(luts_we), .waddr(vh_ent), .wdata({vh_par, vh_wdata}),
    .ra_addr('0), .ra_data(unused_sra), .rb_en(ex0 && (op == OP_SLUT)), .rb_addr(sidx), .rb_data(srd),
    .bd_we('0), .bd_addr('0), .bd_wdata('0), .bd_rdata(unused_sbd),
    .fi_en(1'b0), .fi_or(1'b0), .fi_addr('0), .fi_mask('0));

  // ---------------------------------------------------------------------------------------------------- registers
  logic signed [PW-1:0] xr [COLS];                       // the row
  logic signed [31:0]   sr [4];                          // the scalars
  logic signed [31:0]   sa, sb;                          // S[a], S[b] (K[a], K[b] with ka, kb)
  assign sa = fka ? kreg[fa] : sr[fa];
  assign sb = fkb ? kreg[fb] : sr[fb];

  // sat16 of S[a] and S[b] (the multipliers' 16-bit operands) and of K[k] is not needed: K reaches the datapath through SLD
  logic signed [15:0]   sa16, sb16;
  assign sa16 = (sa[31:15] == {17{sa[31]}}) ? sa[15:0] : (sa[31] ? 16'sh8000 : 16'sh7FFF);
  assign sb16 = (sb[31:15] == {17{sb[31]}}) ? sb[15:0] : (sb[31] ? 16'sh8000 : 16'sh7FFF);

  // rounding constant of the shift: 2^(sh-1), 0 at sh = 0
  logic signed [49:0]   rnd50;
  logic signed [5:0]    shs;                             // sh as a signed operand: neosim types a shift signed only when
  assign shs   = {1'b0, fsh};                            // both operands are (SystemVerilog: the left one alone)
  assign rnd50 = (50'sd1 <<< shs) >>> 1;

  // ---------------------------------------------------------------------------------------------------- the lanes
  logic signed [15:0]   mb    [COLS];                    // the multiplier's second operand
  logic signed [47:0]   mp    [COLS];                    // X * mb
  logic signed [47:0]   p1    [COLS];                    // registered in the op's first cycle
  logic signed [49:0]   mt    [COLS];                    // round(p1 >> sh) (+ BI for MUL_SCALE)
  logic signed [31:0]   mres  [COLS];                    // saturated
  logic signed [39:0]   asum  [COLS];                    // X + (RBUF << sh)
  logic signed [32:0]   bsum  [COLS];                    // X - S[a]
  logic signed [31:0]   cl1   [COLS];                    // max(X, S[a])
  logic signed [31:0]   xs    [COLS];                    // X >>> sh (the LUT's input before saturation)
  logic signed [15:0]   u16   [COLS];
  logic signed [15:0]   lv    [COLS];                    // the entry's V, D
  logic signed [15:0]   ld16  [COLS];
  logic signed [9:0]    lf    [COLS];                    // frac as a signed operand
  logic signed [25:0]   lprod [COLS];
  logic signed [17:0]   ly    [COLS];
  logic signed [31:0]   xn    [COLS];                    // X after this cycle's op
  logic signed [PW-1:0] acc_in [COLS];                   // the accumulator's row (per-element assignments, as rbuf_wdata's)
  generate
    for (genvar j = 0; j < COLS; j++) begin : g_accin
      assign acc_in[j] = acc_rd_data[j];
    end
  endgenerate
  always_comb begin
    for (int j = 0; j < COLS; j++) begin
      // the multiplier: SC[j] (MUL_SCALE), sat16(S[a]) (BMUL), sat16(X[j]) (SQR)
      if (op == OP_MULS)      mb[j] = sc[j];
      else if (op == OP_SQR)  mb[j] = (xr[j][31:15] == {17{xr[j][31]}}) ? xr[j][15:0] : (xr[j][31] ? 16'sh8000 : 16'sh7FFF);
      else                    mb[j] = sa16;
      mp[j]  = 48'(xr[j]) * 48'(mb[j]);
      mt[j]  = ((50'(p1[j]) + rnd50) >>> shs) + ((op == OP_MULS) ? 50'(bi[j]) : 50'sd0);
      mres[j] = (mt[j][49:31] == {19{mt[j][49]}}) ? mt[j][31:0] : (mt[j][49] ? 32'sh80000000 : 32'sh7FFFFFFF);
      // ADD_ROW, BSUB, CLAMP
      asum[j] = 40'(xr[j]) + (40'(rbb[j]) <<< shs);
      bsum[j] = 33'(xr[j]) - 33'(sa);
      cl1[j]  = (xr[j] < sa) ? sa : xr[j];
      // LUT: the entry index and fraction from sat16(X >>> sh); the interpolation from the entry read
      xs[j]   = xr[j] >>> shs;
      u16[j]  = (xs[j][31:15] == {17{xs[j][31]}}) ? xs[j][15:0] : (xs[j][31] ? 16'sh8000 : 16'sh7FFF);
      lidx[j] = {~u16[j][15], u16[j][14:8]};
      lfrac[j] = u16[j][7:0];
      lv[j]   = lrd[j][15:0];
      ld16[j] = lrd[j][31:16];
      lf[j]   = {2'b00, lfrac_q[j]};
      lprod[j] = 26'(ld16[j]) * 26'(lf[j]);
      ly[j]   = 18'(lv[j]) + 18'((lprod[j] + 26'sd128) >>> 8);
      // the row after this cycle
      xn[j] = xr[j];
      if (ld) xn[j] = acc_in[j];
      else if (exec) begin
        case (op)
          OP_ADDR:  xn[j] = (asum[j][39:31] == {9{asum[j][39]}}) ? asum[j][31:0] : (asum[j][39] ? 32'sh80000000 : 32'sh7FFFFFFF);
          OP_MULS, OP_BMUL, OP_SQR: if (cyc) xn[j] = mres[j];
          OP_CLAMP: xn[j] = (cl1[j] > sb) ? sb : cl1[j];
          OP_LUT:   if (cyc) xn[j] = 32'(ly[j]);
          OP_BSUB:  xn[j] = (bsum[j][32] == bsum[j][31]) ? bsum[j][31:0] : (bsum[j][32] ? 32'sh80000000 : 32'sh7FFFFFFF);
          OP_RLD:   xn[j] = acc_in[j];
          default: ;
        endcase
      end
    end
  end

  // the lane tree: max or sum over the COLS lanes (padded with -2^31 / 0), registered after level L1
  logic signed [PW-1:0] tmax [LV+1][NP];
  logic signed [TW-1:0] tsum [LV+1][NP];
  logic signed [PW-1:0] tmax_q [NQ];
  logic signed [TW-1:0] tsum_q [NQ];
  logic signed [PW-1:0] umax [LV+1][NQ];                // the levels after the register
  logic signed [TW-1:0] usum [LV+1][NQ];
  logic signed [31:0]   red_v;
  always_comb begin
    for (int l = 0; l <= LV; l++)
      for (int i = 0; i < NP; i++) begin tmax[l][i] = 32'sh80000000; tsum[l][i] = '0; end
    for (int i = 0; i < NP; i++) begin
      if (i < COLS) begin tmax[0][i] = xr[i]; tsum[0][i] = TW'(xr[i]); end
    end
    for (int l = 0; l < L1; l++)
      for (int i = 0; i < NP / 2; i++)
        if (i < (NP >> (l + 1))) begin
          tmax[l+1][i] = (tmax[l][2*i] > tmax[l][2*i+1]) ? tmax[l][2*i] : tmax[l][2*i+1];
          tsum[l+1][i] = tsum[l][2*i] + tsum[l][2*i+1];
        end
    for (int l = 0; l <= LV; l++)
      for (int i = 0; i < NQ; i++) begin umax[l][i] = 32'sh80000000; usum[l][i] = '0; end
    for (int i = 0; i < NQ; i++) begin umax[L1][i] = tmax_q[i]; usum[L1][i] = tsum_q[i]; end
    for (int l = L1; l < LV; l++)
      for (int i = 0; i < NQ / 2; i++)
        if (i < (NP >> (l + 1))) begin
          umax[l+1][i] = (umax[l][2*i] > umax[l][2*i+1]) ? umax[l][2*i] : umax[l][2*i+1];
          usum[l+1][i] = usum[l][2*i] + usum[l][2*i+1];
        end
    if (fm[0]) red_v = (usum[LV][0][TW-1:31] == {(TW-31){usum[LV][0][TW-1]}}) ? usum[LV][0][31:0] :
                       (usum[LV][0][TW-1] ? 32'sh80000000 : 32'sh7FFFFFFF);
    else       red_v = umax[LV][0];
  end

  // ---------------------------------------------------------------------------------------------------- the scalars
  logic signed [47:0]   smp, sp1;
  logic signed [49:0]   smt;
  logic signed [31:0]   smres;
  logic signed [32:0]   ssum;
  logic signed [31:0]   salu_v, sld_v;
  logic signed [31:0]   ssh;                             // S[a] >>> sh (the scalar LUT's input)
  logic signed [15:0]   su16;
  logic signed [15:0]   slv, sld16;
  logic signed [9:0]    slf;
  logic signed [25:0]   slprod;
  logic signed [17:0]   sly;
  logic signed [32:0]   rsadd;
  always_comb begin
    smp   = 48'(sa) * 48'(sb16);
    smt   = (50'(sp1) + rnd50) >>> shs;
    smres = (smt[49:31] == {19{smt[49]}}) ? smt[31:0] : (smt[49] ? 32'sh80000000 : 32'sh7FFFFFFF);
    ssum  = fm[0] ? (33'(sa) - 33'(sb)) : (33'(sa) + 33'(sb));
    case (fm)
      2'd0, 2'd1: salu_v = (ssum[32] == ssum[31]) ? ssum[31:0] : (ssum[32] ? 32'sh80000000 : 32'sh7FFFFFFF);
      2'd2:       salu_v = (sa > sb) ? sa : sb;
      default:    salu_v = (sa < sb) ? sa : sb;
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
    slv   = srd[15:0];
    sld16 = srd[31:16];
    slf   = {2'b00, sfrac_q};
    slprod = 26'(sld16) * 26'(slf);
    sly   = 18'(slv) + 18'((slprod + 26'sd128) >>> 8);
    rsadd = 33'(rsh[ft]) + 33'(sa);
    case (fm)
      2'd1:    rsst_v = (rsh[ft] > sa) ? rsh[ft] : sa;
      2'd2:    rsst_v = (rsadd[32] == rsadd[31]) ? rsadd[31:0] : (rsadd[32] ? 32'sh80000000 : 32'sh7FFFFFFF);
      default: rsst_v = sa;
    endcase
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int j = 0; j < COLS; j++) begin xr[j] <= '0; p1[j] <= '0; lfrac_q[j] <= '0; end
      for (int i = 0; i < 4; i++) sr[i] <= '0;
      for (int i = 0; i < NQ; i++) begin tmax_q[i] <= '0; tsum_q[i] <= '0; end
      rsh[0] <= '0; rsh[1] <= '0; rbe <= '0; rb_sel_q <= 1'b0; sp1 <= '0; sfrac_q <= '0;
    end else begin
      for (int j = 0; j < COLS; j++) begin
        xr[j]      <= xn[j];
        p1[j]      <= mp[j];
        lfrac_q[j] <= lfrac[j];
      end
      for (int i = 0; i < NQ; i++) begin tmax_q[i] <= tmax[L1][i]; tsum_q[i] <= tsum[L1][i]; end
      sp1     <= smp;
      sfrac_q <= sfrac;
      rb_sel_q <= rd_idx[0];
      if (ld) begin
        rsh[0] <= rs_rd[31:0];
        rsh[1] <= rs_rd[63:32];
        rbe    <= rb_sel_q ? rb_pair_r[2*RBW-1:RBW] : rb_pair_r[RBW-1:0];
      end else if (exec && (op == OP_RSST)) begin
        rsh[ft] <= rsst_v;
      end
      if (exec) begin
        case (op)
          OP_RED:  if (cyc) sr[fd] <= red_v;
          OP_SLD:  sr[fd] <= sld_v;
          OP_SALU: sr[fd] <= salu_v;
          OP_SMUL: if (cyc) sr[fd] <= smres;
          OP_SLUT: if (cyc) sr[fd] <= 32'(sly);
          default: ;
        endcase
      end
    end
  end

  // EMIT
  assign out_valid = exec && (op == OP_EMIT);
  generate
    for (genvar j = 0; j < COLS; j++) begin : g_out
      assign out_row[j] = xr[j];
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
  assign unused_vec = ^{unused_d_exec, unused_d_cyc, unused_d_ld, unused_d_rd, unused_d_idx, unused_d_m, done_d, busy_d,
                        unused_rs_ra, unused_rs_bd, unused_rb_ra, unused_rb_bd, unused_sra, unused_sbd};
  generate
    for (genvar j = 0; j < COLS; j++) begin : g_unl
      logic unused_l;
      assign unused_l = ^{unused_lra[j], unused_lbd[j]};
    end
  endgenerate
endmodule
