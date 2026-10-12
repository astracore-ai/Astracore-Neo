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
minute, no OpenSTA): `sby`, Yosys 0.69+274 with the slang plugin, `yosys-smtbmc` with yices 2, `yosys-abc`. Every
`[script]` is `plugin -i slang; read_slang -DFORMAL [-G P=v] --top <harness> <files>; prep -top <harness>`. What
`read_slang` takes, and so what the harnesses use: immediate `assert` / `assume` in `always_comb` and clocked blocks,
`$past` / `$stable` / `$rose`, declaration initialisers for the reset and `past_valid` registers, hierarchical
references into the DUT instances (the internal invariants). Not taken: SVA sequences and implications (`|->`, `|=>`,
`##n`: "unsupported SVA feature"), `$anyconst` (unknown system name) and the `(* anyconst *)` attribute (dropped: the wire
becomes undriven). A free constant is therefore an unreset register that holds itself (`always_ff @(posedge clk) k <= k;`,
an arbitrary initial value, never changing).

## The proofs

Times: the sandbox (2 cores shared with three other agents, `--jobs 1`, two runs: a quiet one and one at load 4) and the
PR's `formal` job (4-core runner, `--jobs 2`). Mode prove is a full proof: k-induction for smtbmc (k = the induction
length it closed at; the basecase checks the first *depth* steps from reset), pdr for abc.

| Proof | What is proven | Parameters | Mode, depth (reached) | Engine | Sandbox | CI |
| --- | --- | --- | --- | --- | --- | --- |
| `ecc39` | for all 2^32 data words: decode(encode(d)) = d with ce = ue = 0; any single flip of the 39 stored bits ({op, p, d}) gives d, ce = 1, ue = 0; any two distinct flips give ue = 1, ce = 0 | — | prove (combinational) | `abc collapse -r; muxes; strash; pdr` | 0.6 / 1.1 s | |
| `crc16_beat_bw4` | `rtl/crc16_beat` = `crc16_beat_ref` for every crc_in, every beat, every n = 0..BW (one instance pair per n) | BW = 4 | prove (combinational) | same | 1.6 / 2.9 s | |
| `crc16_beat_bw16` | the same at the silicon width | BW = 16 | prove (combinational) | same | 68 / 157 s | |
| `link_pack` | packer → unpacker: the beats out carry the accepted words in order, none invented or repeated, each with its head, type, tag, hdr[19:14], PSUM column (one-word types their whole hdr); link flits even parity and 1..32 words, beats out even parity and 1..16 words; accepting a one-word type (the CRC closing a stream) leaves nothing in flight; with the sink ready no beat waits more than 2 cycles | BW = 16, WPF = 32, XW = YW = 3 (silicon) | prove, depth 2 (k = 1) | `smtbmc yices` | 55 / 115 s | |
| `noc_router` | count <= 2, in_ready == (count != 2); each output register holds only flits whose XY port it is; no output register ever holds odd parity, a bad head is popped the cycle it is the head and `parity_err_sticky` is exactly "a bad head was dropped since err_clear"; every flit value that leaves was accepted; a picked accepted flit stays in its FIFO slot, moves into exactly one output register, unchanged until it leaves, and that register is its XY port; a requesting head is granted within 5 fires of its output | XW = YW = 3, DW = 8, DV_HOOKS = 0 | prove, depth 2 (k = 1) | `smtbmc yices` | 26 / 52 s | |
| `lockstep_core_seq` | two `core_seq` with identical inputs from reset: neo_core's twelve compared outputs (f_start, f_first, f_ct, f_ky, f_kx, w_load, w_raddr, rd_en, rd_idx, done, reduce_ready, state_dbg) and the other three equal every cycle; resets at any time | ROWS = 16, COLS = 8, IDXW = 6, WBUF_DEPTH = 512 | prove, depth 2 (k = 1) | `smtbmc yices` | 1.2 / 2.7 s | |
| `lockstep_requant` | two `requant`, the stream only once all four table entries are written: out_valid equal, out_q equal while out_valid, the parity stickies equal | COLS = 4, PW = 32, **MW = 4** (below) | prove, depth 2 (k = 1) | `smtbmc yices` | 27 / 53 s | |
| `requant_parity` | `tbl_perr_sticky` is exactly "tbl_fault_inject met a written entry 0 under in_valid since err_clear": with the fault off it never rises whatever the unwritten entries hold; with it, 1 the next cycle (the brief's "within two"), held until err_clear | COLS = 4, PW = 32, MW = 16 | prove, depth 4 (k = 1) | `smtbmc yices` | 0.9 / 1.8 s | |
| `abft_checker` | err == valid && (sum of the row != y_chk), the sum wrapped to PW bits (formed as a tree in the harness, the RTL sums a chain); err_sticky sets the cycle after, holds, clears on abft_clear | COLS = 4, PW = 8 | prove, depth 2 (k = 1) | `smtbmc yices` | 0.5 / 0.9 s | |

All nine are full proofs; no task needed the bounded fallback. The whole set: 180 s sequential in the quiet sandbox run,
386 s under load.

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

- **crc16_beat.** Against the matrix form of stream A1a (branch `a1a-nic-crc-prow`, 25c274d) the miter is pure XOR — every
  output bit a parity over up to 528 inputs — which is exponential for resolution. At BW = 4 (144 inputs, a 5,171-node
  AIG): smtbmc yices (n free) did not finish in 100 s, smtbmc bitwuzla (n constant per pair) not in 300 s, plain abc pdr
  (n free) not in 100 s, abc iprove not in 60 s, and Yosys's `equiv_make` / `equiv_simple` / `equiv_induct` left all 16
  output bits unproven. BDDs keep a parity linear in size, so the engine is ABC's `collapse` (no reordering) ahead of
  pdr, with n a constant per instance pair: with n free the output is a (BW + 1)-way mux of parities and the BDD blew up
  (BW = 4: over 100 s and 3.3 GB, killed). Against today's RTL (the same serial text as the reference) Yosys merges the
  two circuits and the miter is constant before ABC; against 25c274d the proof takes 3.3 s at BW = 4 and 50 s at
  BW = 16 in the quiet sandbox (48 s of it Yosys elaboration, 1 s in ABC; 94 s under load). Each output bit is its own
  assertion, so a broken form fails as fast as a good one passes (the 16-bit compare, an OR of differing parities, has an
  exponential BDD: the first attempt at a reversed word order timed out), and `muxes` turns the BDDs into an AIG without
  going through SOP covers (`strash` alone gives up at a million cubes and leaves UNKNOWN).
- **ecc39.** The syndrome is a parity too: BDD collapse 1 s, plain abc pdr 17 s, smtbmc yices more than 3 minutes.
- **lockstep_requant at MW = 16.** The induction step holds the two copies' 32x17 multipliers on equal inputs — an
  identical-circuit miter that SAT-based engines do not close: yices > 170 s, bitwuzla and boolector > 120 s, z3 > 150 s,
  abc pdr > 150 s; with the tables' initial values made equal (which would also hide a read of an unwritten entry) still
  > 150 s; even a two-register micro test (two registers loaded from one input, each times a 16-bit multiplier) times
  out with yices and pdr. The narrowing closes it — PW = 8 / MW = 8: 62 s, PW = 32 / MW = 4: 58 s (27 s quiet),
  PW = 16 / MW = 8 and PW = 32 / MW = 8: > 150 s — so the task runs at PW = 32, MW = 4. The property does not depend on
  the width: the copies compute one function of their inputs and tables; what the proof has to show — that out_valid,
  out_q and the stickies never depend on an unwritten entry or an unreset register — is width-independent (`requant_parity`
  runs at MW = 16: its cone has no multiplier).
- **noc_router.** No invention, loss and duplication were first one property: per flit value F, accepted minus departed
  copies equal to the copies held in the 15 slots. That counting (a cardinality argument) stalled the basecase at step 4
  for more than 2 minutes; the no-invention flag per value plus the positional token close in seconds. The RTL's `% 5`
  round robin is 25 32-bit `$mod` cells in the model; the harness itself uses none.
- **link_pack.** The brief allowed bmc depth 20 here; at the silicon widths bmc reached step 5 in 78 s and not step 6 in
  300 s (abc bmc3: frame 4 in 30 s), the 1,024-bit pending flit's variable shifts multiplying per step. The invariants
  over the pending flit (the words in flight = pend_cnt - 16 idx, word j at position (j - out_cnt) + 16 idx of pend_vec
  with its fields, the bits above pend_cnt clear, a two-beat flit held until its second beat goes) make the property
  1-inductive: a full proof in about a minute instead of a bound.

## Non-vacuity: each harness against a broken RTL

Each harness was run against a copy of `01_rtl/` with one deliberate bug; every one stops the job (not PASS):

| Mutation | Task | Result |
| --- | --- | --- |
| `ecc39_dec`: ce only with a non-zero syndrome | ecc39 | FAIL 0.8 s |
| `ecc39_dec`: the correction not applied | ecc39 | FAIL 1.6 s |
| `crc16_beat`: words in reverse order | crc16_beat_bw4 | FAIL 4.9 s |
| `crc16_word`: polynomial 0x1023 (the reference keeps its own copy) | crc16_beat_bw4 / bw16 | FAIL 2.6 s / 116 s |
| `abft_checker`: err without valid | abft_checker | FAIL 2.7 s |
| `requant`: even parity stored | requant_parity | FAIL 1.4 s |
| `requant`: parity checked on unwritten entries | requant_parity | FAIL 3.1 s |
| `requant`: v1 not reset | lockstep_requant | FAIL 1.1 s |
| `core_seq`: tcnt not reset | lockstep_core_seq | FAIL 2.5 s |
| `noc_router`: S routed to N | noc_router | UNKNOWN 5.3 s |
| `noc_router`: parity never checked | noc_router | UNKNOWN 5.0 s |
| `noc_router`: a granted head not popped | noc_router | UNKNOWN 4.9 s |
| `link_unpacker`: the second beat one word early | link_pack | UNKNOWN 4.2 s |
| `link_packer`: beats of different tags merged | link_pack | UNKNOWN 22.5 s |

UNKNOWN is a failed induction step (the counterexample to induction is `engine_0/trace_induct.vcd`, copied to
`ci_logs/formal/`): the router and link_pack basecases are 2 steps deep, shorter than the bug's first appearance, because
a deeper bmc of those two costs minutes per step (above). For a real trace after an UNKNOWN, run the task as a bmc: copy
the `.sby`, set `mode bmc` and `depth 8`, run it with `sby -f -d formal_out/<name>_bmc <copy>.sby`.

## Internal names the harnesses read

A restructuring of these signals changes the harness with the RTL (the property stays):

| Harness | Reads |
| --- | --- |
| `noc_router_fv` | `fifo`, `count`, `rd`, `wr` (the FIFOs), `rr` (the round-robin pointer), `fire`, `sel`, `pop` (the grants) |
| `link_pack_fv` | `u_pk.g_merge.pend_valid / pend_cnt / pend_vec / pend_head / pend_type / pend_tag / pend_hdr`, `u_up.idx` |
| `lockstep_core_seq_fv` | every register of `core_seq`: `state`, `ct`, `ky`, `kx`, `first_q`, `lcnt`, `loading`, `loaded`, `tcnt`, `wcnt`, `dcnt`, `ltile`, `rcnt`, `ext_cnt`, `ct_free_q`, `ct_free_idx_q` |
| `lockstep_requant_fv` | `wrt`, `mult`, `shft`, `zp`, `par`, `v1`, `prod`, `sh1`, `zp1` |
| `requant_parity_fv` | `wrt`, `mult`, `shft`, `zp`, `par` |
| `ecc39_fv`, `crc16_beat_fv`, `abft_checker_fv` | ports only |

The pipelined ABFT checker of drop 0.42 (an `err_valid` output) changes `abft_checker_fv` with it; the matrix-form
`crc16_beat` needs no change (proven above).

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

None: all nine proofs pass on main at d69b862. The interface facts the proofs rest on, for the integrator: the packer
keeps the first beat's hdr[7:0] for RDRSP / WRDATA (tile_nic sends 0 there); the requant tables are unreset and every
compared output is clean only because in_valid follows the table load; the router forwards a flit to exactly one
register and only to its XY port, and the `% 5` arbitration is correct (fairness within 5 fires).
