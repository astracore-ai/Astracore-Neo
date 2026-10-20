# Stream A2b -- place and route on the open PDK: the 16x8 core (and the blocks the budget allows) through OpenROAD, in CI

Branch `a2b-pnr`. Read COMMON.md first, then `00_overview/ROADMAP.md` §1 (A2 / A3 rows) and §5, `07_ci/synth/README.md`
and `synth.py` (the netlists it writes per unit: `netlist.v` with ASAP7 cells, `block.sdc`, the black-boxed memory leaves
and their macro model in `09_synthesis/memories.md`), `07_ci/synth/tools.sh` (how the job installs pinned tools from
GitHub releases and sparse checkouts; the OpenROAD-flow-scripts commit it pins). New files only: `07_ci/pnr/` (the flow
scripts, a `tools.sh` of your own or a documented docker invocation, a `report.py`), a `pnr` job in BOTH
`07_ci/regress.yml` and `.github/workflows/regress.yml` (byte-identical), `09_synthesis/pnr_<block>.md` reports, and the
`09_synthesis/memories.md` macro rows gaining LEF / LIB provenance. Do not change RTL or `synth.py` (A2a owns it this week;
if you need a netlist written differently, say so and work from what `synth.py` writes today).

## The flow

- **Tools.** OpenROAD-flow-scripts (ORFS) is the flow; the runner can either run the ORFS docker image
  (`openroad/orfs` -- pin a tag; ubuntu-latest has docker) or install a prebuilt OpenROAD release (GitHub releases of
  The-OpenROAD-Project/OpenROAD ship Ubuntu .deb packages) with ORFS's `flow/` checked out at the commit `tools.sh` pins.
  Choose what runs within the job's limits (6 h, 16 GB, 4 cores) and installs from GitHub only; document the choice. The
  sandbox cannot run docker and has no OpenROAD: develop the scripts here against ORFS's structure (a sparse checkout of
  `flow/` is cheap), and let the PR's job be the first real run -- iterate on the branch until it is green.
- **Input.** Start ORFS from our synthesized netlist, not from RTL: ORFS's own Yosys has no slang front end and would not
  read the SystemVerilog. The flow runs `make floorplan place cts route finish` with `results/<platform>/<design>/base/
  1_synth.v` pre-populated from `synth.py`'s `netlist.v` for the unit (ORFS supports starting at floorplan when `1_synth.v`
  and `1_synth.sdc` exist -- check the Makefile's targets at the pinned commit; `synth.py`'s `block.sdc` becomes the
  `1_synth.sdc`). The netlist's memory leaves are black boxes: generate LEF + LIB macros for them with **FakeRAM2.0** (the
  generator ORFS's asap7 platform uses; its config and the 7 nm parameters are the model `memories.md` quotes) at the unit's
  exact word count and width -- one macro per distinct leaf instance type (`synth.py`'s report names them, e.g.
  `neo_mem_rf_w512_d128_g2_rb1`), the LIB with the same access time the synthesis flow assumed (218 ps), and record each
  macro's generated LEF size in `memories.md`.
- **Blocks, in this order, each its own ORFS design directory under `07_ci/pnr/designs/`:** (1) `mac_pe` (1.1 k cells -- the
  flow's smoke test, minutes), (2) `c16_systolic_array` (168 k cells, no macros -- the first real routed block: utilization,
  routed WNS at the proxy period from A2a's sweep or 1,000 ps if that PR is not in yet, congestion, wire length), (3)
  `c16_neo_core` (292 k cells + the three leaf macros + requant ×2 -- the 16 x 8 core as ROADMAP's phase-7 first target),
  (4) if the budget allows, `noc_router` and `tile_nic` (the mesh's own blocks; `tile_nic` has a macro). Core utilization
  ~60 %, ASAP7's default placement density, the platform's default clock tree; no manual floorplanning beyond macro placement
  (ORFS's macro placer, or a simple `MACRO_PLACEMENT` file if it fails).
- **Outputs per block** (`report.py` -> `pnr_<block>.md` and the job log): die and core area, utilization, cell count after
  placement, routed wire length, the DRC / antenna counts, setup / hold WNS and TNS from the routed `final` STA at the
  target period, the worst routed path (start, end, arrival), power from the flow's default activity (and note that A3
  replaces it with recorded activity), the clock tree's skew and buffer count, the runtime and peak memory of each stage.
- **The job.** `pnr` in the workflow, `workflow_dispatch` plus a weekly schedule (Sunday 02:00 UTC) and on pushes that touch
  `07_ci/pnr/**` -- not on every push (the full set may take hours); publishes `ci_logs/pnr/` to `ci-logs-pnr` like the
  other jobs; `timeout-minutes: 350`. Make the block list a job input so one block can be run on demand.

## Verification and report

Local: the scripts lint (`bash -n`, `python3 -m py_compile`), the FakeRAM2.0 generation runs here (it is Python) and its LEF /
LIB parse with OpenROAD's reader in CI; the macro table rows. Then the PR: the `pnr` job run by `workflow_dispatch` on your
branch (`gh workflow run regress.yml --ref a2b-pnr -f ...` if inputs exist, or `gh api -X POST
repos/astracore-ai/Astracore-Neo/actions/workflows/<id>/dispatches`), the four other jobs plus `formal` unchanged (your
change touches no RTL). Iterate until `mac_pe` and `c16_systolic_array` are routed and reported; `c16_neo_core` routed if
it fits the budget, otherwise its failure mode written up (memory, runtime, macro placement) with what it needs. Report: the
tool choice and versions, the per-block tables, the routed worst paths against the synthesis flow's estimates for the same
blocks (the ratio routed / synthesized is the number A3 and the spec need), the macro LEF sizes against `memories.md`'s
model, the job's runtimes, anything unresolved, the PR number.
