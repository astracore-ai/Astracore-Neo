# Stream D2 -- the fault campaign and FMEDA v2: measured diagnostic coverage, the comparator-storage gap closed,
# gate-level simulation of synthesized blocks, the Icarus 12 clean-up

Branch `d2-faults`. Read COMMON.md first, then `00_overview/ROADMAP.md` §6 and §1 (D2's row), `05_safety/` (the FMEDA v1
`neo_fmeda_v1.xlsx`, `build_fmeda.py`, `assessor_concept_review.md`), the spec §7 (safety mechanisms and the FMEDA claims),
`02_dv/dv/cocotb/` (the tile and core suites, the Verilator build flags in the Makefile), `01_rtl/neo_tile.sv` (the
hooks under DV_HOOKS and the twelve sticky flags), `02_dv/formal/README.md`. Streams A3, B2 and C2 change RTL beside you
(timing items, clock gating and telemetry, the vector unit): your RTL changes are limited to item 2 (the comparator
storage in `act_feeder.sv`, `wbuf_mem.sv` / `neo_core.sv`, `acc_bank.sv`) and item 4 (`ecc39.sv`'s tables and any other
`always_comb` without sensitivities that CI's Icarus 12 misruns) -- say exactly which lines in the PR.

## 1. The `faults` job (nightly + on demand)

- A Verilator build of the 16 x 8 2 x 2 tile mesh (the cocotb `tile` build) and of the 16 x 8 core with
  `--public-flat-rw`, so every flop is reachable from the test; a Python driver (cocotb, in `02_dv/dv/cocotb/faults/`) that,
  per fault: picks a flop bit from the model's symbol table (uniformly over the design's flops, or per block with a
  weighting file so every block gets a minimum sample), picks a cycle inside an M1 / M18-class tile run or a C1-class core
  run, injects a stuck-at (held for the rest of the run) or a single-cycle flip, and classifies the outcome against the
  fault-free run: **detected** (which sticky cause, how many cycles after injection, within the FTTI budget the spec gives
  the island), **masked** (results bit-exact, no cause), **dangerous undetected** (results wrong, no cause within the
  FTTI, or a cause after it), **hang** (the run does not finish: the watchdog's territory -- detected if `fetch_timeout` /
  the watchdog fires, else dangerous). A run at 16 x 8 is ~5 s; thousands of faults per nightly on the runner (4 workers;
  the per-run cost measured and the sample sized to the job's 6 h), the sample seeded so it is reproducible.
- Output: `faults.csv` (block, flop, cycle, type, outcome, cause, latency), the per-block diagnostic coverage table
  (`faults_report.md`), the undetected list with the flop names grouped by block -- published to `ci-logs-faults`.
- The job: `faults` in BOTH workflow copies, `schedule` nightly (02:30 UTC) + `workflow_dispatch` with the sample size as
  an input; not on push.

## 2. The comparator-storage gap of drop 0.41

A1c noted the synchronous reads added state behind the R7 / R10 comparators: the leaves' port-B address latches (the
activation buffer 10 bits, the weight buffer 9, the accumulator 9), the feeder's `inb_q` / `a0_q`, the weight path's
`w_load_q`, the accumulate's stage-0 control. Close it: duplicate and compare `inb_q` / `a0_q` / `w_load_q` and the
stage-0 control in the existing comparators (the feeder's R7, the core's R10 list); for the macros' address latches,
either compare the leaf's registered address against a duplicate register (a `neo_mem_rf` option that exports `rb_addr_q`
for comparison -- the leaf interface may grow by that output; D2 is the stream allowed to touch `neo_mem.sv` this week,
minimally) or show by the campaign that a corrupted address is caught by ABFT / the CRC / the drain check with the
latency the FTTI allows (a stuck address in the weight buffer gives a wrong weight row -> ABFT; in the activation buffer
a wrong pixel -> ABFT? -- only if the check column sees the same wrong pixel: it does, so NOT caught; measure). Whatever
the campaign shows, the FMEDA row says it with the measured number.

## 3. FMEDA v2

`05_safety/build_fmeda.py` takes `faults.csv` and the formal report: every row's diagnostic coverage becomes the measured
figure (with its sample size and confidence interval), the formal proofs are cited where they cover a row (ecc39, the
lockstep pairs, the parity, the router's no-loss), the dangerous-undetected list drives the RTL items (item 2 and whatever
else the campaign finds -- report, do not fix beyond item 2); `neo_fmeda_v2.xlsx` and a `fmeda_v2.md` summary with the
SPFM / LFM / PMHF recomputed from the measured coverage and the same base failure rates as v1 (state them).

## 4. Gate-level simulation and the Icarus 12 clean-up

- Gate-level: the synthesis flow's netlists (`netlist.v` per unit, ASAP7 cells) simulated with the ASAP7 Verilog cell
  models (ORFS's `asap7sc7p5t_*.v` behavioural models -- `tools.sh` can fetch them from the pinned ORFS commit's platform
  directory) under Icarus / Verilator for `mac_pe`, `requant`, `crc16_beat`, `ecc39_*`, `link_packer` against the RTL on
  the same vectors (a `gls` target in the Makefile, run in the `synth` job after the netlists exist, or in `faults`);
  the point is that the netlist the pnr flow places is the RTL (no synthesis bug), with the reset / X behaviour noted.
- Icarus 12 (CI's version) misruns `always_comb` blocks without sensitivities (constant tables in `ecc39.sv`), which is why
  the two Icarus steps of hdl-sim print standing X failures: rewrite those tables as `localparam` / `assign` (every
  simulator, neosim included, must still agree -- COMMON.md's subset), run both Icarus steps with Icarus 12 locally
  (`/home/claude/tools/iverilog12/bin/iverilog`) until they pass, then make the steps gating in the workflow (the
  `continue-on-error` or the `|| true` they carry today goes away).

## Verification and report

Local: the campaign driver on a few hundred faults in the sandbox (memory rules: one Verilator model at a time), `make
lint`, the suites touched by item 2 and 4 (`make iverilog` with Icarus 12 and 14, `make verilator`, neosim `run_ecc39.py`,
`run_conv_core.py`, cocotb `core` / `tile`), `make formal` (the lockstep harness lists the registers you add). The PR
(`gh pr create` or `gh api -X POST repos/astracore-ai/Astracore-Neo/pulls -f title=... -f head=d2-faults -f base=main -f
body=...`), green on every job; the `faults` job dispatched once on the branch with a sample the runner finishes in ~1 h
(read `ci-logs-faults` by SHA). Report: the campaign's design and its first results (per-block coverage table, the
undetected list, latencies), item 2's changes and what the campaign says about the macros' address latches, FMEDA v2's
SPFM / LFM / PMHF against v1, the gate-level results, the Icarus 12 status (both steps gating), anything unresolved, the
PR number.
