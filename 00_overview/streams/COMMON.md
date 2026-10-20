# Common brief for the AstraCore Neo stream agents (the Claude Code edition, 20 Oct 2026)

A *stream* is one unit of work on its own branch: an agent reads this file and the stream's brief (`<stream>.md` beside
it), implements it, verifies it locally, opens a pull request, iterates until the PR's CI run is green, and reports. The
integrator (the main session) merges the green PRs into `main` as one *drop*, writes the drop-log row, the spec update and
the Doc section, and starts the next round. Twelve briefs were delivered this way between 12 and 14 Oct (drops 0.41–0.43);
they stay here as the record of how each piece was specified and as the style to follow. The briefs still open are
`S1_safety_closure.md`, `A4_timing_physical.md`, `D3_software.md` and `F1_freeze.md`.

## The repository

SystemVerilog NPU: a mesh of tiles, each a 32 x 32 weight-stationary systolic core with ABFT check columns, a 2:4 sparsity
path, a vector unit on the drain, a 2 MB SECDED bank, a NoC router, a tile network interface, a lockstep DMA pair and host
registers; a chip top with AXI4 bridges and an AXI4-Lite control plane; a Python golden model and compiler (`03_models`),
a C driver (`04_software`), the FMEDA (`05_safety`), the CI (`07_ci`), the synthesis / place-and-route reports
(`09_synthesis`). Read `00_overview/ROADMAP.md`, `00_overview/spec_sheet_v2_1.md` (the sections your stream touches),
`00_overview/drop_log.md` (the last three rows), `HANDOFF-5.md` and `CLAUDE.md` first. The folders are numbered; the CI
and the Makefile use a *flat layout* built by copies or links (below).

## Your branch and the result you deliver

- Work on your own branch (`git switch -c <stream>-<slug>` from `main`'s head), never on `main`. Commit there; push the
  branch; open a pull request against `main` (`gh pr create` -- if GraphQL is blocked where you run, `gh api -X POST
  repos/astracore-ai/Astracore-Neo/pulls -f title=... -f head=<branch> -f base=main -f body=...`). The PR run of the
  full workflow is the real verification: python-regress (~10 min), hdl-sim (~100–140 min; limit 200), hdl-mesh8 (~60),
  synth (~60: two libraries, the sweep, the gate-level simulations), formal (~5). `pnr` and `faults` run on schedule or by
  `workflow_dispatch` (`gh workflow run regress.yml --ref <branch> -f pnr_only=true -f pnr_blocks=...` / `-f faults_only=true`).
- Reading a run: `gh run list --branch <branch> --json databaseId,status,conclusion`, `gh run view <id> --json jobs`.
  Where job-log download is blocked, every job publishes its logs to an orphan branch `ci-logs-<job>` (force-pushed by
  every run on any branch): find the run's commit with `gh api "repos/astracore-ai/Astracore-Neo/activity?ref=refs/heads/ci-logs-<job>&per_page=40"`
  (timestamps), then `git fetch origin <sha>` and `git archive <sha> | tar -x -C <dir>`.
- Iterate on the branch until the PR is green on every job, then report (your final message): what changed and why, the
  measured results (local and CI, with the numbers), anything you could not resolve, the PR number. Do not edit
  `00_overview/` documents, `README.md`, `HANDOFF-5.md` or `CLAUDE.md`; the integrator writes those.
- Every commit message ends with the attribution lines the session is given (in the Cowork sessions of 12–14 Oct:
  `Co-Authored-By: Claude ... <noreply@anthropic.com>` and `Claude-Session: <url>`; Claude Code supplies its own). Commit
  messages say what and why in RTL terms (the repository's style: dense, specific, numbers included); no marketing.
- Never force-push, never rewrite history, never run `pkill -f` / `pgrep -f` with a pattern that matches your own shell.
- If a stream's provisional drop label (`drop 0.4x` in headers and tests) differs from the merge's, the integrator renames
  it at the merge; use the number the brief gives.

## Tools (install what the CI installs; `07_ci/synth/tools.sh` fetches the rest from GitHub)

- Verilator 5.020 and Icarus 12 (`apt-get install iverilog verilator`, as hdl-sim's step), cocotb 1.9.2 and numpy by pip,
  Python 3.12. Icarus 14 (the OSS CAD Suite's) runs the SV testbenches too.
- `bash 07_ci/synth/tools.sh` with `NEO_SYNTH_TOOLS=<dir>`: the OSS CAD Suite nightly (Yosys with the slang front end, sby,
  the SMT solvers, Icarus 14), OpenSTA built from source (cmake, tcl-dev, swig, bison, flex, libfl-dev, libeigen3-dev), the
  ASAP7 and Nangate45 libraries and the ASAP7 cell models from OpenROAD-flow-scripts, FakeRAM; `NEO_TOOLS_ONLY=oss` stops
  after the suite (the formal job's install). Then `export NEO_YOSYS=... NEO_STA=... NEO_PDK_LIB=...` or let `synth.py`
  find them under `NEO_SYNTH_TOOLS`.
- Place and route needs docker (`openroad/orfs:26Q4-124-g3451ca1f5`) and more than 16 GB for detailed routing of the large
  blocks: a CI-only step (`07_ci/pnr/README.md`).

## The flat layout

```
W=<your checkout>; F=<a directory outside every git tree>
mkdir -p $F && cd $F
ln -sfn $W/01_rtl rtl; ln -sfn $W/02_dv/tb tb; ln -sfn $W/02_dv/dv dv; ln -sfn $W/02_dv/formal formal
mkdir -p sim && ln -sfn $W/02_dv/sim/* sim/            # a real directory: ../compiler imports resolve from it
for v in $W/02_dv/vectors*; do ln -sfn $v $(basename $v); done
ln -sfn $W/03_models/model model; ln -sfn $W/03_models/compiler compiler; ln -sfn $W/04_software sw; ln -sfn $W/05_safety safety
cp $W/07_ci/Makefile Makefile; ln -sfn $W/07_ci/synth synth
```
cocotb writes `sim_build_*` and `results*.xml` inside `dv/cocotb/` -- inside your checkout; `.gitignore` covers them, but
`git status` before every commit and `git add` only the files you changed. `make iverilog_core` regenerates
`02_dv/vectors_core/desc.vh`: `git checkout -- 02_dv/vectors_core/desc.vh` before committing.

## What to run, and the limits

- Fast (run freely): `make lint` (ten Verilator `-Wall` passes, must stay at 0 warnings -- every warning fails hdl-sim),
  `make iverilog_core` / `make iverilog` (both gating since 0.43, Icarus 12 and 14), `make crc16`, the neosim unit tests
  (`python3 sim/run_nic.py`, `run_requant.py`, `run_noc.py`, `run_mbist.py`, `run_ecc39.py`, `run_vec_unit.py`,
  `run_mac_core.py --vectors vectors`), one synthesis unit of a small module, `make formal FORMAL_JOBS=1` (10 proofs, ~7 min).
- Medium (one at a time, ~10 min, ~1 GB): `make -j1 -C dv/cocotb core` (16 x 8 core), `make -j1 -C dv/cocotb tile`
  (2 x 2 mesh of 16 x 8, the M1–M25 suite, ~20 min), `make -j1 -C dv/cocotb top` (the chip top, two clock ratios),
  `make verilator` (the SV testbenches; its last cocotb steps are CI-only), neosim `python3 sim/run_tile_test.py M3`
  (~2.5 min; M2 / M5 / M9 take 15–60 min), `run_conv_core.py` (~50 min).
- Heavy (CI only): tile32 / core32 / mesh8 / coverage cocotb builds (2–16 GB), the synthesis of `tile_nic` / `noc_router`
  (4.7 GB) or the full unit set, place and route, the fault campaign at scale. A job killed with exit 137 / 143 ran out of
  memory.
- Baselines that must not move unless your change moves them by design (main after drop 0.43, CI run 184): cocotb core
  9 / 9 (C1–C5 at 1,127.50 / 4,512.00 / 2,257.50 / 624.50 / 804.00 ns, C6 13,536.00, C12 1,594.00, C13 3,188.00, C14 2,077.00),
  tile 22 + 2 skipped at 185,551.02 ns with M1–M19 at 46,410.02, tile8 1,459.50, tile32 162,332.02 + M22 / M25, core32
  19,081.51, mesh8 11 / 11 at 117,822.51 ns, top 9 / 9 at 93,070.41 (aclk = clk 80,003.01), coverage 86 / 86 functional
  bins, formal 10 / 10, the SV testbench `tb_tile_mesh` M1 in 3,818 clocks. If your change adds latency the end times
  change and the new figures with their cycle accounting are part of your report.

## Style

- RTL: parameterised SystemVerilog as the repository writes it; explicit widths (lint is `-Wall` with one waiver in
  `02_dv/tb/lint/silicon.vlt`; unused bits go to `unused_*` sinks); a header comment per module stating the drop and the
  mechanism; no vendor primitives; memory arrays only through the leaves `neo_mem_sp` / `neo_mem_rf` (`01_rtl/neo_mem.sv`);
  the clock gate only through `neo_icg`.
- Every simulator must agree: Verilator 5.020, Icarus 12 and 14, neosim (a Python SystemVerilog-subset simulator:
  module / param / ports, always_ff posedge / negedge, always_comb, generate for / if, instances, `+:` part-selects, size
  casts, $clog2; no streaming operators, classes, interfaces, `inside`, `unique case`; `~x` evaluated at unbounded width
  inside a comparison -- compare through sized nets). Lessons kept: Verilator 5.020 schedules an `always_comb` that packs a
  testbench-driven unpacked array as settle-only logic (use continuous assignments); `read_json` in Yosys shorts nets when a
  port's net name is not moved with its bit; Icarus 12 misruns `always_comb` without sensitivities (constant tables as
  `localparam` / `assign`); cocotb 1.9 collapses unpacked arrays reached through generate indices onto the first one
  (resolve from the module scope after the last index). When simulators disagree, the RTL changes, not the test.
- Tests: extend the existing suites (cocotb `02_dv/dv/cocotb/test_*.py`, neosim `02_dv/sim/run_*.py`, SV `02_dv/tb/`, formal
  `02_dv/formal/`) rather than adding one-off scripts; a new mechanism gets a test that fails without it; a test number is
  unique across the suites (the integrator renumbers clashes: B2's telemetry test became M25).
- Shared files (`neo_core.sv`, `tile_nic.sv`, `host_if.sv`, `synth.py`, the two Makefiles, the two workflow copies, which must
  stay byte-identical): say in the PR which lines you touched; expect the integrator to resolve the merge with the other
  streams, or to hand you the merge on a branch when the conflicts are yours to judge.
