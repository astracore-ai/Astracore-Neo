# `02_dv/formal` — the formal job (drop 0.41)

SymbiYosys proofs of the protection logic, run by the `formal` job of `regress.yml` on every push and PR: the SECDED code,
the beat CRC against a frozen serial reference, the link packer/unpacker round trip, the router's invariants, the two
lockstep pairs of the core, the requant table parity and the ABFT checker. The harnesses wrap the RTL of `01_rtl/`
unchanged; a property that fails against the RTL is a finding for the integrator, never a reason to edit the property.

| File | Role |
| --- | --- |
| `<proof>.sby` | one SymbiYosys file per proof: the first comment line says what is proven (`run.py` puts it in the report), the next lines the details |
| `<proof>_fv.sv` | the harness: instantiates the DUT(s) from `rtl/`, drives every input from free inputs, assumes the protocol, asserts the property (and the helper invariants that make k-induction close) |
| `crc16_ref.sv` | `crc16_word_ref` / `crc16_beat_ref`: `crc16_word` and `crc16_beat` as they stood from drop 0.29 to 0.40, renamed — the golden serial CRC, never to change |
| `07_ci/formal/run.py` | the runner: every task of every `*.sby`, report `formal_out/formal_report.md`, logs and traces to `ci_logs/formal/`, exit 1 unless all PASS |

## The tools and what the front end takes

The OSS CAD Suite nightly 2026-10-11 that `07_ci/synth/tools.sh` pins (`NEO_TOOLS_ONLY=oss` installs it alone, about a
minute, no OpenSTA): `sby`, Yosys 0.69+274 with the slang plugin, `yosys-smtbmc` with yices 2 and bitwuzla 0.9.1
(link_pack), `yosys-abc`. Every `[script]` is `plugin -i slang; read_slang -DFORMAL [-G P=v] --top <harness> <files>;
prep -top <harness>`. What `read_slang` takes, and so what the harnesses use: immediate `assert` / `assume` in
`always_comb` and clocked blocks, `$past` / `$stable` / `$rose`, declaration initialisers for the reset and `past_valid`
registers, hierarchical references into the DUT instances (the internal invariants). Not taken: SVA sequences and
implications (`|->`, `|=>`, `##n`: "unsupported SVA feature"), `$anyconst` (unknown system name) and the `(* anyconst *)`
attribute (dropped: the wire becomes undriven). A free constant is therefore an unreset register that holds itself
(`always_ff @(posedge clk) k <= k;`, an arbitrary initial value, never changing).

## The proofs

Times: the sandbox on the merged tree of drop 0.41 (main 9f834a1 with this link_pack harness; 2 cores shared with other
agents, `--jobs 1`, load about 1) and the PR's `formal` job of D1 (run 159: 4-core runner, `--jobs 2`), which ran the tree
before the A1 merge (d69b862, the drop 0.40 RTL, link_pack on yices) — the merged tree's first run replaces that column.
Mode prove is a full proof: k-induction for smtbmc (k = the induction length it closed at; the basecase checks the first
*depth* steps from reset), pdr for abc.

| Proof | What is proven | Parameters | Mode, depth (reached) | Engine | Sandbox | CI (run 159, drop 0.40) |
| --- | --- | --- | --- | --- | --- | --- |
| `ecc39` | for all 2^32 data words: decode(encode(d)) = d with ce = ue = 0; any single flip of the 39 stored bits ({op, p, d}) gives d, ce = 1, ue = 0; any two distinct flips give ue = 1, ce = 0 | — | prove (combinational) | `abc collapse -r; muxes; strash; pdr` | 0.5 s | 0.5 s |
| `crc16_beat_bw4` | `rtl/crc16_beat` (the matrix form of drop 0.41) = `crc16_beat_ref` for every crc_in, every beat, every n = 0..BW (one instance pair per n) | BW = 4 | prove (combinational) | same | 1.5 s | 1.1 s |
| `crc16_beat_bw16` | the same at the silicon width | BW = 16 | prove (combinational) | same | 22.5 s | 31 s |
| `link_pack` `stream` / `parity` | packer → unpacker, the packer of drop 0.42 with both sides registered (the beat in an input register, the link flit in an output register): the beats out carry the accepted words in order, none invented or repeated, each with its head, type, tag, hdr[19:14], PSUM column (one-word types their whole hdr); link flits and beats out 1..32 / 1..16 words; a one-word type (the CRC closing a stream) taken from the input register leaves only itself in flight besides the beat accepted behind it, everything accepted before it presented (drop 0.41: accepting it did; drop 0.40: nothing left in flight at all); with the sink ready no beat waits more than 2 cycles — the bound of drop 0.40, the two registers add no cycle to it (below) — that is task `stream`; task `parity`: link flits and beats out even parity, with the running-parity invariants | BW = 16, WPF = 32, XW = YW = 3 (silicon) | prove, depth 2 (k = 1) | `smtbmc bitwuzla` | 332.7 s / 13.2 s (drop 0.41, one task: 116.0 s) | 49 s (yices, the drop 0.40 harness) |
| `noc_router` | count <= 2, in_ready == (count != 2); each output register holds only flits whose XY port it is; no output register ever holds odd parity, a bad head is popped the cycle it is the head and `parity_err_sticky` is exactly "a bad head was dropped since err_clear"; every flit value that leaves was accepted; a picked accepted flit stays in its FIFO slot, moves into exactly one output register, unchanged until it leaves, and that register is its XY port; a requesting head is granted within 5 fires of its output. The router of drop 0.41 stores the parity and the route with the entry at the push (`fbad`, `freq`), keeps the occupancy as a thermometer (`occ`) and arbitrates with a one-hot rotating pointer (`ptr`, `gnt`; `rr` / `sel` are gone): helper invariants tie the stored bits to the stored flit, `rd_a` to `rd`, `ptr` one-hot | XW = YW = 3, DW = 8, DV_HOOKS = 0 | prove, depth 2 (k = 1) | `smtbmc yices` | 53.6 s | 18 s |
| `lockstep_core_seq` | two `core_seq` with identical inputs from reset: neo_core's twelve compared outputs (f_start, f_first, f_ct, f_ky, f_kx, w_load, w_raddr, rd_en, rd_idx, done, reduce_ready, state_dbg) and the other three equal every cycle; resets at any time (drop 0.41's `red_last` joins the register-by-register invariant) | ROWS = 16, COLS = 8, IDXW = 6, WBUF_DEPTH = 512 | prove, depth 2 (k = 1) | `smtbmc yices` | 1.2 s | 1.2 s |
| `lockstep_requant` | two `requant` (three stages since drop 0.41), the stream only once all four table entries are written: out_valid equal, out_q equal while out_valid, the parity stickies equal; helper invariants: the stage-1 registers (`ra1`, `rb1`, `n1`, `sh1`, `zp1`) equal while `v1`, the stage-2 registers (`sum2`, `n2`, `sh2`, `zp2`) while `v2` (the two-stage form's `prod` is gone) | COLS = 4, PW = 32, **MW = 4** (below) | prove, depth 2 (k = 1) | `smtbmc yices` | 16.6 s | 21 s |
| `requant_parity` | `tbl_perr_sticky` is exactly "tbl_fault_inject met a written entry 0 under in_valid since err_clear": with the fault off it never rises whatever the unwritten entries hold; with it, 1 the next cycle (the brief's "within two"), held until err_clear | COLS = 4, PW = 32, MW = 16 | prove, depth 4 (k = 1) | `smtbmc yices` | 1.5 s | 0.7 s |
| `abft_checker` | the two-stage checker of drop 0.41: the row presented with valid in cycle t has its verdict in cycle t + 2, err == (sum of the row != y_chk) with err_valid (valid delayed by two), err never without err_valid; the sum wrapped to PW bits (formed as a tree in the harness, the RTL as carry-save rows and a carry-free compare); err_sticky sets the cycle after err, holds, clears on abft_clear (drop 0.40: err combinational from valid) | COLS = 4, PW = 8 | prove, depth 2 (k = 2) | `smtbmc yices` | 0.5 s | 0.3 s |

All ten are full proofs (link_pack two tasks since drop 0.42); no task needed the bounded fallback. The whole set with
the drop 0.42 packer: 480 s sequential in the sandbox, with other agents' builds beside it (the drop 0.41 set of nine:
214 s, link_pack 116 s of it); on the runner (run 170, A2a's PR, two at a time) 210 s, link_pack_stream 207.9 s and
link_pack_parity 10.5 s of it. Run 159 (drop 0.40): 69 s two at a time on the runner, where the job took about two
minutes with the 19 s tool install (budget: 20 minutes; the per-task timeout of 600 s keeps a hard proof from holding the
job).

## Assumptions

Every harness holds the DUT's reset in the first cycle (an initialised register), then releases it.

| Proof | Assumed |
| --- | --- |
| `ecc39` | the flip positions are < 39 and the two of the double flip differ |
| `crc16_beat` | nothing; n > BW (outside the port's contract, no caller produces it) is not checked |
| `link_pack` | the source: no beat during reset; a beat held, unchanged, until accepted; a data beat (RDRSP, WRDATA, PSUM) has hdr[13:8] <= 15 (1..16 words); RDRSP / WRDATA carry hdr[7:0] = 0, as `tile_nic` sends them (`tx_hdr = {6'd0, n - 1, 8'd0}`) — hdr[7:0] of those types is not in the packer's merge key, so a later beat's would be replaced by the first beat's; the sink is free |
| `noc_router` | nothing on the inputs (valid, flit and out_ready free every cycle, no protocol); the coordinates constant (the mesh straps them); the token is a flit with even parity |
| `lockstep_core_seq` | nothing (inputs free, shared) |
| `lockstep_requant` | in_valid only once all four table entries have been written (the tables are not reset — the use neo_core makes of them); tbl_fault_inject = 0 on both |
| `requant_parity`, `abft_checker` | nothing |

## Engines: what closed and what did not

- **crc16_beat.** Against the matrix form of stream A1a (drop 0.41; first measured on its branch `a1a-nic-crc-prow` at
  25c274d) the miter is pure XOR — every output bit a parity over up to 528 inputs — which is exponential for
  resolution. At BW = 4 (144 inputs, a 5,171-node AIG): smtbmc yices (n free) did not finish in 100 s, smtbmc bitwuzla
  (n constant per pair) not in 300 s, plain abc pdr (n free) not in 100 s, abc iprove not in 60 s, and Yosys's
  `equiv_make` / `equiv_simple` / `equiv_induct` left all 16 output bits unproven. BDDs keep a parity linear in size, so
  the engine is ABC's `collapse` (no reordering) ahead of pdr, with n a constant per instance pair: with n free the
  output is a (BW + 1)-way mux of parities and the BDD blew up (BW = 4: over 100 s and 3.3 GB, killed). Against the
  serial RTL of drop 0.40 (the same text as the reference) Yosys merged the two circuits and the miter was constant
  before ABC; against the matrix form as merged the proof takes 1.5 s at BW = 4 and 22.5 s at BW = 16 in the sandbox
  (at 25c274d: 3.3 s and 50 s, 48 s of them Yosys elaboration, 1 s in ABC). Each output bit is its own assertion, so a
  broken form fails as fast as a good one passes (the 16-bit compare, an OR of differing parities, has an exponential
  BDD: the first attempt at a reversed word order timed out), and `muxes` turns the BDDs into an AIG without going
  through SOP covers (`strash` alone gives up at a million cubes and leaves UNKNOWN).
- **ecc39.** The syndrome is a parity too: BDD collapse 1 s, plain abc pdr 17 s, smtbmc yices more than 3 minutes.
- **lockstep_requant at MW = 16.** Measured on the two-stage requant of drop 0.40: the induction step holds the two
  copies' 32x17 multipliers on equal inputs — an identical-circuit miter that SAT-based engines do not close: yices
  over 170 s, bitwuzla and boolector over 120 s, z3 and abc pdr over 150 s; with the tables' initial values made equal
  (which would also hide a read of an unwritten entry) still over 150 s; even a two-register micro test (two registers
  loaded from one input, each times a 16-bit multiplier) times out with yices and pdr. The narrowing closes it —
  PW = 8 / MW = 8: 62 s, PW = 32 / MW = 4: 58 s (27 s quiet), PW = 16 / MW = 8 and PW = 32 / MW = 8: over 150 s — so the
  task runs at PW = 32, MW = 4 (16.6 s on the three-stage form of drop 0.41, whose carry-save tree of MW + 1 rows was
  not retried at MW = 16). The property does not depend on the width: the copies compute one function of their inputs
  and tables; what the proof has to show — that out_valid, out_q and the stickies never depend on an unwritten entry or
  an unreset register — is width-independent (`requant_parity` runs at MW = 16: its cone has no multiplier).
- **noc_router.** No invention, loss and duplication were first one property: per flit value F, accepted minus departed
  copies equal to the copies held in the 15 slots. That counting (a cardinality argument) stalled the basecase at step 4
  for more than 2 minutes; the no-invention flag per value plus the positional token close in seconds. The `% 5` round
  robin of drop 0.40 was 25 32-bit `$mod` cells in the model; drop 0.41's one-hot pointer has none, and the harness maps
  it back to the last granted input (`ptr_rr`) for the fairness helper.
- **link_pack.** The brief allowed bmc depth 20 here; at the silicon widths bmc of the drop 0.40 packer reached step 5 in
  78 s and not step 6 in 300 s (abc bmc3: frame 4 in 30 s), the 1,024-bit pending flit's variable shifts multiplying per
  step, so the proof rests on invariants that make the properties 1-inductive. For the registered packer of drop 0.41
  (the harness rewritten on the merged tree): the words in flight are the output register's still to be presented (its
  count - 16 idx) plus the pending flit's (pend_cnt); word j sits at (j - out_cnt) + 16 idx in the register, else at
  (j - out_cnt) - (the register's words) in the pending buffer `cur` selects, with its fields; while the register holds
  a flit at most one beat is pending (pend_cnt <= 16); pend_thr, pend_cm1, pend_room and pend_ncol agree with pend_cnt;
  the running parity pend_par0 ^ ^last_wp is the fixed fields' parity XOR the parities of the buffer's words below
  pend_cnt (the words at and above it are stale, masked only in the emitted flit). The one-beat invariant is why the
  wait bound stays 2: a one-word beat waits at most for the register's first of two beats and the pending flit's move
  into it, or, the register empty, for that move and the moved flit's first beat; a data beat at most one cycle.
  Engines: without the parity property and its invariant yices closes the rest in 122 s; with them yices times out at
  540 s, whether the invariant is stated directly (an XOR of 32 masked word parities, the words written through the
  merge's barrel shift) or through a ghost of the prefix parities (one bit per word, rebuilt from the RTL's `take` /
  `mergeable` / `in_wp` on every taken beat). bitwuzla closes the ghost form in 112-116 s and the direct form in 138 s
  (boolector: over 330 s), so the task runs on bitwuzla with the ghost.
  Drop 0.42 (stream A2a) put an input register in front of the merge decision (`iv` / `ib`: one beat, taken when the
  register is empty or its beat moves on, `core_ready = !iv || i_ready`). The harness carries it: the words in flight are
  the output register's, the pending flit's, then the input register's (`fl = l_left + p_left + i_left`, at most WPF +
  2 BW); word j is in one of the three, at `(j - out_cnt) - l_left - p_left` in the input register with that beat's
  fields; the input register's beat satisfies the source's contract (hdr[13:8] <= 15, hdr[7:0] = 0 on RDRSP / WRDATA);
  the flush is stated when a one-word type leaves the input register (`prev_flush` from `i_ready`): only it and the beat
  accepted behind it are in flight; and a held source beat waits exactly as long as the input register's beat, whose
  remaining wait (`r_data` / `r_one` of that beat) makes the bound inductive (`held_v -> iv`, `wcnt + r <= 1 / 2`). All
  of it in one task closed at k = 1 but took 788 s in the sandbox, above the job's 600 s per task; the properties are
  split into two tasks of one harness (`ifndef LP_NO_STREAM` / `LP_NO_PARITY`), each a full proof of its group with the
  structural invariants (the input register's fields, the counts, the output register, the unpacker): `stream` 333 s
  and `parity` 13 s with bitwuzla in the sandbox, two agents beside it (`stream` with yices: 690 s).

## Non-vacuity: each harness against a broken RTL

Each harness was run against a copy of `01_rtl/` with one deliberate bug; every one stops the job (not PASS). On the
merged tree (9f834a1 with the drop 0.41 link_pack harness), except the two ecc39 rows (d69b862; the SECDED RTL and its
harness did not change since) and the link_pack rows, re-run on the drop 0.42 packer and harness (stream A2a; the
assertion the induction step breaks in brackets):

| Mutation | Task | Result |
| --- | --- | --- |
| `ecc39_dec`: ce only with a non-zero syndrome | ecc39 | FAIL 0.8 s |
| `ecc39_dec`: the correction not applied | ecc39 | FAIL 1.6 s |
| `crc16_beat`: words in reverse order | crc16_beat_bw4 | FAIL 1.6 s |
| `crc16_beat`: one constant bit flipped (ROW5, word 0 bit 0: a wrong matrix entry) | crc16_beat_bw4 / bw16 | FAIL 1.7 s / 23.5 s |
| `abft_checker`: err without valid (the stage-2 `v_q` gate dropped) | abft_checker | UNKNOWN 0.4 s; bmc FAIL at step 3, 0.4 s |
| `requant`: even parity stored | requant_parity | FAIL 2.8 s |
| `requant`: parity checked on unwritten entries | requant_parity | FAIL 3.0 s |
| `requant`: v1 not reset | lockstep_requant | FAIL 2.1 s |
| `core_seq`: tcnt not reset | lockstep_core_seq | FAIL 2.1 s |
| `noc_router`: S routed to N | noc_router | UNKNOWN 3.9 s |
| `noc_router`: parity never checked | noc_router | UNKNOWN 2.4 s |
| `noc_router`: a granted head not popped | noc_router | UNKNOWN 2.6 s |
| `link_unpacker`: the second beat one word early (the word select's base (BW - 1) idx_w) | link_pack stream | UNKNOWN 16.5 s (a word out differs) |
| `link_packer`: beats of different tags merged | link_pack stream | UNKNOWN 15.4 s (the pending flit's fields against the word's) |
| `link_packer`: a merge does not fold the last beat's word parities into pend_par0 | link_pack parity | UNKNOWN 17.5 s (the running-parity invariant) |
| `link_packer`: the output register not reloaded as it drains (oready = !link_valid) | link_pack stream | UNKNOWN 7.6 s (the words in flight) |
| `link_packer`: a beat that would merge never taken | link_pack stream | UNKNOWN 9.4 s (the wait bound) |
| `link_packer`: the input register not reloaded as its beat moves on (core_ready = !iv) | link_pack stream | UNKNOWN 6.6 s (the words in flight) |
| `link_unpacker`: the word select's index copy stepping by 2 | link_pack stream | UNKNOWN 8.5 s (idx_w == idx) |

UNKNOWN is a failed induction step (the counterexample to induction is `engine_0/trace_induct.vcd`, copied to
`ci_logs/formal/`): the router, ABFT and link_pack basecases are 2 steps deep, shorter than the bug's first appearance
(a deeper bmc of the router and link_pack costs minutes per step, above). For a real trace after an UNKNOWN, run the task
as a bmc: copy the `.sby`, set `mode bmc` and `depth 8`, run it with `sby -f -d formal_out/<name>_bmc <copy>.sby`. The
drop 0.41 link_pack mutations were also run as a bmc of depth 10 with bitwuzla of the harness without its invariants
(the asserted properties alone), each failing at step 3-5 (a word out differs, flit parity, a word never accepted, the
wait bound): with the invariants, a bmc stops at the first broken invariant, often a step earlier.

## Internal names the harnesses read

A restructuring of these signals changes the harness with the RTL (the property stays):

| Harness | Reads |
| --- | --- |
| `noc_router_fv` | `fifo`, `fbad`, `freq` (the parity and route bits stored at the push), `occ`, `count`, `rd`, `rd_a`, `wr` (the FIFOs), `ptr` (the one-hot rotating pointer), `fire`, `gnt`, `pop` (the grants) |
| `link_pack_fv` | `u_pk.g_merge.iv / ib / i_ready` (the input register, drop 0.42), `pend_valid / cur / pbuf0 / pbuf1 / pend_cnt / pend_thr / pend_cm1 / pend_room / pend_head / pend_type / pend_tag / pend_hhi / pend_col0 / pend_ncol / pend_par0 / last_wp` and, for the parity ghost, `take / mergeable / in_wp`; `u_up.idx / idx_w` (the unpacker's word-select copy, drop 0.42) |
| `lockstep_core_seq_fv` | every register of `core_seq`: `state`, `ct`, `ky`, `kx`, `first_q`, `lcnt`, `loading`, `loaded`, `tcnt`, `wcnt`, `dcnt`, `ltile`, `rcnt`, `ext_cnt`, `red_last`, `ct_free_q`, `ct_free_idx_q` |
| `lockstep_requant_fv` | `wrt`, `mult`, `shft`, `zp`, `par`, `v1`, `ra1`, `rb1`, `n1`, `sh1`, `zp1`, `v2`, `sum2`, `n2`, `sh2`, `zp2` |
| `requant_parity_fv` | `wrt`, `mult`, `shft`, `zp`, `par` |
| `ecc39_fv`, `crc16_beat_fv`, `abft_checker_fv` | ports only |

The A1 merge (drop 0.41) changed five harnesses with the RTL: `abft_checker_fv` (the verdict two cycles after valid, with
`err_valid`), `lockstep_requant_fv` (the three stages' registers), `noc_router_fv` (the stored parity and route bits, the
occupancy thermometer, the one-hot pointer), `lockstep_core_seq_fv` (`red_last`) and `link_pack_fv`, rewritten for the
registered packer; the matrix-form `crc16_beat` needed no change. Drop 0.42 (A2a) changed `link_pack_fv` with the
packer's input register and split `link_pack.sby` into the tasks `stream` and `parity`.

## Running it

CI: the `formal` job — `NEO_TOOLS_ONLY=oss bash synth/tools.sh`, the suite's `bin` on PATH, `make formal` (two tasks at a
time, 600 s each), the report and logs on the branch `ci-logs-formal`. Locally, from the flat layout of `COMMON.md` plus
`ln -sfn $W/02_dv/formal formal`, with `/home/claude/tools/oss/oss-cad-suite/bin` on PATH:

```
make formal FORMAL_RUN=$W/07_ci/formal/run.py FORMAL_JOBS=1     # every task; CI copies run.py into formal/ instead
python3 $W/07_ci/formal/run.py --only noc_router,ecc39 --jobs 1   # some tasks
sby -f -d formal_out/noc_router formal/noc_router.sby              # one task by hand
sby -f -d formal_out/crc16_beat_bw16 formal/crc16_beat.sby bw16    # one task of a multi-task file
```

`FORMAL_JOBS=1` on a 2-core machine: a smtbmc prove task runs two solver processes (basecase and induction). The runner
starts each `sby` as a session leader and kills the whole session at the timeout — sby puts every engine in a process
group of its own, so killing sby or its group leaves solvers running (seen in the sandbox: a 3.3 GB ABC orphan).

## Findings against today's RTL

None: all nine proofs pass on the merged tree of drop 0.41 (main 9f834a1 with this link_pack harness; on d69b862 before
it). The interface facts the proofs rest on, for the integrator: the packer keeps the first beat's hdr[7:0] for RDRSP /
WRDATA (tile_nic sends 0 there), and since drop 0.41 a CRC it accepts is still in flight for at least a cycle, in its
output register -- since drop 0.42 it may also wait in the input register behind the pending flit, the stream flushed
when it leaves that register; the requant tables are unreset and every compared output is clean only because in_valid follows the
table load; the router forwards a flit to exactly one register and only to its XY port, and its one-hot rotating
arbitration is fair (within 5 fires).
