# Stream D1 -- the formal job: SymbiYosys proofs of the protection logic on every push

Branch `d1-formal`. Read COMMON.md first. New files: `02_dv/formal/` (the harnesses and the `.sby` files),
`07_ci/formal/run.py` (runner and report), a `formal` target in `07_ci/Makefile`, a `formal` job in `07_ci/regress.yml`
(ALSO the identical copy at `.github/workflows/regress.yml` -- the workflow file GitHub runs; keep the two byte-identical),
a small flag in `07_ci/synth/tools.sh` (below), and a `02_dv/formal/README.md`. You do not change any RTL; if a property
fails, that is a finding -- report it with the trace, do not "fix" the RTL or weaken the property (the integrator decides).

## The tools

The OSS CAD Suite tarball that `07_ci/synth/tools.sh` already pins (`OSS_DATE=2026-10-11`) contains `sby`, `yosys` with the
slang plugin, `yosys-smtbmc`, `yices`, `boolector`, `bitwuzla`, `z3`, `abc`. Add to `tools.sh` an environment flag
`NEO_TOOLS_ONLY=oss` that returns right after the OSS CAD Suite step (before the ORFS libraries and OpenSTA), so the formal
job installs the same pinned suite in about a minute with no OpenSTA build and no cache; nothing else in that script
changes. In the sandbox the suite is at `/home/claude/tools/oss/oss-cad-suite/bin` (put it on PATH).

What the front end accepts (verified in this sandbox with `sby` + `read_slang`): immediate assertions and assumptions
inside clocked blocks (`always_ff @(posedge clk) if (past_valid && ...) assert (...)`), `$past`, `assume property (<boolean>)`,
`initial` for a `past_valid` register under `FORMAL`. NOT accepted: SVA sequences and implications (`|->`, `|=>`, `##n`) --
`read_slang` reports "unsupported SVA feature". Write every property as an immediate assertion with explicit `$past` /
counters. Script section of each `.sby`:
```
[script]
plugin -i slang
read_slang -DFORMAL --top <harness> <files...>
prep -top <harness>
```
with `[engines] smtbmc yices` (prove: k-induction) or `abc pdr` for the combinational proofs, `[options] mode prove`
(or `mode bmc depth N` where a full proof is out of reach -- say which and why in the README). The DUTs come from `rtl/`
unchanged (the flat layout: `ln -sfn $W/02_dv/formal formal`, DUT files by path); the harness wraps a DUT, drives it from
free inputs, assumes the protocol, asserts the property. Memory leaves (`neo_mem_rf`/`neo_mem_sp`) elaborate as arrays;
keep depths small through the parameters (the harness picks the parameters).

## The proofs (one `.sby` each; a harness may hold several asserts)

1. `ecc39` -- (39,32) SECDED, combinational, full proof: for all data d, `dec(enc(d))` returns d with `ce = 0`, `ue = 0`;
   for every single-bit flip of the 39-bit codeword (a free 39-bit vector constrained to weight 1 -- or a free 6-bit
   position) the decoder returns d with `ce = 1`, `ue = 0`; for every double flip (two distinct positions) `ue = 1`.
2. `crc16_beat` -- equivalence to the serial reference: copy today's `crc16_beat` + `crc16_word` into
   `02_dv/formal/crc16_ref.sv` as `crc16_beat_ref` / `crc16_word_ref` (the golden serial form, never to change); the
   harness instantiates `crc16_beat` from `rtl/` and the reference with the same `crc_in`, `words`, `n` and asserts equal
   `crc_out`, at BW = 4 (full proof) and BW = 16 (full proof if the solver finishes within ten minutes on the runner --
   try `abc pdr`, `smtbmc bitwuzla`, and a `yosys` `equiv_*` flow; else bmc and say so). Today the two are the same text
   and the proof is trivial; stream A1a replaces `crc16_beat` by a matrix form on another branch, and this job is what
   proves that change on every later push -- which is why it exists.
3. `link_packer` + `link_unpacker` round trip at the silicon widths (BW = 16, WPF = 32, XW = YW = 3): a free beat stream
   into the packer, the packer's link into the unpacker, assume the sources obey ready/valid (a beat held until accepted;
   data types carry 1..16 words with `hdr[13:8]` = words - 1, PSUM columns as the header says; a stream ends with a CRC
   flit), assert that the beats out carry the same words in the same order with the same type/tag/hdr fields (a small
   scoreboard of the last K words in the harness), every emitted link flit has even parity and at most WPF words, and
   nothing is accepted that is not eventually presented (bounded: bmc depth 20 is acceptable here; state it).
4. `noc_router` at XW = YW = 3, DW small (the harness's DW, e.g. 8 -- the properties do not depend on the payload width):
   FIFO invariants (`count <= 2`, `in_ready == (count != 2)`); XY consistency -- a flit on output E has `dst_x > my_x`,
   W `dst_x < my_x`, S `dst_x == my_x && dst_y > my_y`, N `dst_x == my_x && dst_y < my_y`, L `dst == (my_x, my_y)`;
   a flit with odd parity is never forwarded and raises `parity_err_sticky`; every forwarded flit was accepted on an input
   (no invention: track the accepted flits' `seq` fields with a small harness memory, or prove the weaker "the output flit
   equals some FIFO entry"); bounded fairness -- an input whose head requests o while o fires every cycle is granted within
   5 fires (a counter in the harness). Full proof where k-induction closes, else bmc depth 16; report which.
5. The lockstep pairs: two `core_seq` at the 16x8 parameters (ROWS = 16, COLS = 8) with identical inputs from reset
   assert all the compared outputs equal every cycle (the comparator's own list in `neo_core.sv`: f_start, f_first, f_ct,
   f_ky, f_kx, w_load, w_raddr, rd_en, rd_idx, done, reduce_ready, state_dbg) -- k-induction should close at depth 1-2;
   two `requant` at COLS = 4 with identical inputs, assume `in_valid` only after all four table entries have been written
   (track `tbl_we`/`tbl_addr` in the harness), `tbl_fault_inject = 0`: assert equal `out_valid`/`out_q`. These prove that
   the comparators' "never fires without a fault" is a property of the RTL, not of the test vectors.
6. `requant` table parity (COLS = 4): with `tbl_fault_inject = 0` the sticky never rises; with the fault asserted after
   entry 0 is written and `in_valid`, `tbl_perr_sticky` is 1 within two cycles.
7. `abft_checker` (COLS = 4, PW = 8): `err` iff `valid && (sum != y_chk)` -- a one-line sanity proof that also pins the
   interface for the pipelined version another stream is writing (if `abft_checker` gains an `err_valid` output on main
   before you merge, adapt the harness to it).

Every property that fails against today's RTL is a real finding: keep the `.sby` (expected FAIL noted in the README with
the trace summary), do not change the RTL, and put it at the top of your report.

## The job

- `make formal` (from the flat layout): `python3 formal/run.py --jobs 2 --timeout 600` runs every `02_dv/formal/*.sby`
  (sby's own `-f`, one directory per task under `formal_out/`), collects PASS / FAIL / UNKNOWN / TIMEOUT, engine, depth,
  the elapsed time, and writes `formal_out/formal_report.md` (a table) plus the failing traces' `.vcd` paths; exit 1 if
  any task is not PASS (a documented expected-FAIL task is still a failure of the job -- there must be none at merge).
- The `formal` job in `regress.yml`: `ubuntu-latest`, `timeout-minutes: 60`, checkout, the flat layout as the other jobs
  do it (`cp -r 02_dv/formal formal`), `NEO_TOOLS_ONLY=oss NEO_SYNTH_TOOLS=/home/runner/neo-synth bash synth/tools.sh`,
  `make formal` with the suite's `bin` on PATH, publish `ci_logs/formal/` (the report, every task's `logfile.txt`, the
  traces) to the orphan branch `ci-logs-formal` exactly as the other jobs publish theirs (copy the publish step). Total
  budget under 20 minutes on the 4-core runner; the per-task timeout keeps a hard proof from hanging the job.
- `07_ci/synth/README.md` is the synthesis job's; write `02_dv/formal/README.md` for this one: the tool, each proof (what
  is proven, mode, depth, engine, time), the assumptions, how to run one task locally.

## Verification and report

Local: every `.sby` task runs green from the flat layout within its timeout (report each task's time and engine);
`make lint` still 0 warnings (the harnesses are not in the lint set unless you add them -- do not); the two workflow copies
identical (`diff 07_ci/regress.yml .github/workflows/regress.yml`). Then the PR: its `formal` job green and the report on
`ci-logs-formal`; the four other jobs unchanged (your change touches no RTL, no Makefile target they use). Report: the
table of proofs with mode/depth/engine/time (local and CI), any finding, and the PR number.
