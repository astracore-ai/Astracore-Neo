# CLAUDE.md — working in the AstraCore Neo repository

Read `HANDOFF-5.md` first (the state of the project on 20 Oct 2026, what is proven, what is open), then
`00_overview/ROADMAP.md` §1 (the plan to the freeze) and `00_overview/streams/COMMON.md` (how a unit of work is specified,
built, verified and merged). This file is the operating manual: layout, commands, rules, how to read CI.

## What this is

An automotive NPU in SystemVerilog, taken from "proven in simulation" to "synthesized, routed and fault-measured on an
open PDK proxy" between 4 and 14 Oct 2026, now at drop 0.43 and heading for a freeze (drop 0.70) for handoff to an
implementation team. 64 tiles; each tile a 32 x 32 weight-stationary INT8 systolic core with ABFT check columns and a
2:4 sparsity path, a vector unit on the drain, a 2 MB SECDED bank with MBIST, a NoC router, a network interface, a lockstep
DMA pair, host registers, telemetry and clock gating; a chip top (`neo_top`) with AXI4 bridges and an AXI4-Lite control
plane. Every claim is tagged **RTL-proven** / **proxy-measured** / **model** in the spec's claims ledger (`spec_sheet_v2_1.md` §0).

## Layout (numbered folders; the CI and the Makefile use a flat layout built from them)

| Folder | Holds |
| --- | --- |
| `00_overview/` | `spec_sheet_v2_1.md` (the spec as implemented, kept current per drop), `drop_log.md` (one row per drop with its CI proof), `ROADMAP.md` (streams and drops), `decisions.md`, `streams/` (the briefs) |
| `01_rtl/` | the RTL, one module per file (two files hold two modules by design: `ecc39.sv`, `link_pack.sv`) |
| `02_dv/` | `tb/` SV testbenches (Verilator / Icarus; `lint/` the chip-top lint stub), `dv/cocotb/` the cocotb suites (`test_neo_core.py`, `test_tile_mesh.py`, `test_neo_top.py`, `test_two_layer.py`, `faults/` the fault campaign), `sim/` neosim (a Python SystemVerilog-subset simulator) and its tests `run_*.py`, `formal/` the SymbiYosys harnesses, `vectors*/` golden vectors |
| `03_models/` | `model/` the golden models (`systolic_ref.py`, `vec_ref.py`, perf / feasibility), `compiler/` (`neo_compile.py`, `neo_backend.py`, `neo_xfmr.py`, `neo_alloc.py`, `tool_qual.py`) |
| `04_software/` | the C driver (`neo_host.c` / `.h`), checked word for word against the Python backend |
| `05_safety/` | FMEDA v1 / v2 (`build_fmeda.py`, `fmeda_v2.md`, the xlsx), the safety concept review |
| `07_ci/` | `Makefile` (every target), `regress.yml` (the workflow — a byte-identical copy of `.github/workflows/regress.yml`; edit both), `synth/` (Yosys + OpenSTA flow, the sweep, the GLS), `formal/` (the runner), `pnr/` (OpenROAD flow, FakeRAM macros, reports) |
| `08_ip_product/` | datasheet, safety-manual outline, test-chip plan, `neo_top_integration.md` |
| `09_synthesis/` | the reports per drop (`synth_report_drop04x.md`, `pnr_*.md`, `power_drop043.md`, `floorplan/`), `memories.md` (every memory leaf instance and its macro), the SDC and vendor scripts |

## Commands (from a flat layout; the recipe is in `00_overview/streams/COMMON.md`)

- `make lint` — ten Verilator `-Wall` passes, must print clean (0 warnings; one documented waiver in `02_dv/tb/lint/silicon.vlt`).
- `make iverilog_core`, `make iverilog`, `make crc16`, `make verilator` — the SV testbenches (both Icarus steps are gating).
- `python3 sim/run_<x>.py` — neosim: `run_nic`, `run_noc`, `run_requant`, `run_mbist`, `run_ecc39`, `run_vec_unit`,
  `run_mac_core --vectors vectors`, `run_conv_core`, `run_conv_v02`, `run_ksplit`, `run_tile_test.py M3`.
- `make -j1 -C dv/cocotb core | tile | tile_sp | top | tile8 | tile32 | core32 | mesh8 | coverage` — cocotb on Verilator
  (`tile32` / `core32` / `mesh8` / `coverage` need 2–16 GB: CI only).
- `make formal FORMAL_JOBS=1` — the ten SymbiYosys proofs (`02_dv/formal/README.md`).
- `make synth` (`SYNTH_PDK=asap7|nangate45`, `--blocks`, `--sweep`, `--sta-only`, `--resume`; `07_ci/synth/README.md`),
  `make gls`, `make synth_compare`.
- `make faults FAULTS_N=...` — the fault campaign locally (small samples only).
- `make regress_ci`, `python3 compiler/tool_qual.py` — the Python regression and the tool-qualification kit.
- Tools: `bash 07_ci/synth/tools.sh` installs the synthesis / formal toolchain from GitHub (pinned); `apt-get install
  iverilog verilator`, `pip install cocotb==1.9.2 numpy` for the simulators; docker for place and route.

## CI (`regress.yml`)

Jobs on every push to `main` and every PR: `python-regress`, `hdl-sim` (~100–140 min, limit 200), `hdl-mesh8` (~60),
`synth` (both libraries, the sweep, the gate-level sims; ~60), `formal` (~5). On schedule / dispatch: `pnr` (Sunday 02:00
UTC and `-f pnr_only=true -f pnr_blocks=... -f pnr_period=...`), `faults` (nightly 02:30 UTC and `-f faults_only=true`).
A documents-only push (`00_overview/**`, `*.md`) runs nothing. One run per branch at a time (a new push cancels the
running one). Every job publishes its logs to an orphan branch `ci-logs-<job>` (force-pushed by every run on any branch):
when job-log download is blocked, find the run's commit with
`gh api "repos/astracore-ai/Astracore-Neo/activity?ref=refs/heads/ci-logs-<job>&per_page=40"` (match the timestamp),
then `git fetch origin <sha> && git archive <sha> | tar -x -C <dir>`. `gh run list/view --json` work; `gh pr list` may be
blocked (GraphQL) — use `gh api repos/astracore-ai/Astracore-Neo/pulls`.

## Rules

1. **Every simulator agrees**: Verilator 5.020, Icarus 12 and 14, neosim (its subset: no streaming operators, classes,
   interfaces, `inside`, `unique case`). When simulators disagree, the RTL changes, not the test.
2. **Lint at zero warnings; a new mechanism gets a test that fails without it; a baseline end time moves only by design**,
   with the cycle accounting in the commit message and the drop log (baselines in `COMMON.md`).
3. **Memories only through the leaves** `neo_mem_sp` / `neo_mem_rf` (`01_rtl/neo_mem.sv`), the clock gate only through
   `neo_icg`; `09_synthesis/memories.md` lists every leaf instance.
4. **Branch per unit of work, PR run green before merge, one drop per merge batch**: the drop-log row, the spec's affected
   sections and the claims ledger, `ROADMAP.md`'s status, and a section in the project's Claude Doc (see HANDOFF-5) are
   written at the merge. Provisional drop labels in headers / tests are renamed to the merged drop's number.
5. **The two workflow copies stay byte-identical**; `02_dv/vectors_core/desc.vh` is regenerated by `make iverilog_core` —
   do not commit the regenerated file.
6. **Commit messages**: what and why in RTL terms with numbers; end with the attribution lines your session is given.
   Never force-push `main`; never rewrite history; `pkill -f` with a pattern matching your own shell kills you — kill by PID.
7. **Fetch-timeout constants** (`FETCH_TIMEOUT`, the tile's watchdog defaults) stay unchanged unless a brief says otherwise.
8. **Technical deliverables only** in this repository: code, tests, reports, the spec; no business or strategy material.

## Streams (parallel agents)

The work since drop 0.41 runs as parallel streams: write a brief in `00_overview/streams/` (the twelve delivered ones are
the pattern — the *why* with measured figures, the design decisions made, the files owned, the tests, the report asked
for), start one agent per stream in its own worktree / branch with `COMMON.md` + the brief, let each open its PR and
iterate to green, then merge the green PRs as one drop (conflicts in shared files — `neo_core.sv`, `tile_nic.sv`,
`host_if.sv`, `synth.py`, the Makefiles — are the integrator's; hand a hard one back to the stream that owns the
semantics, on a branch). Four agents at a time was the practical limit on a 2-core / 6 GB machine; the CI runs in
parallel per branch (the repository is public: Actions minutes are free). The next round is drop 0.44: `S1_safety_closure.md`,
`A4_timing_physical.md`, `D3_software.md`; then `F1_freeze.md`.
