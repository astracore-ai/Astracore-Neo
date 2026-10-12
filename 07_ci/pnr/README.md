# `07_ci/pnr` — the place-and-route job (drop 0.42, stream A2b)

What the `pnr` job of `regress.yml` runs: a unit's netlist from the synthesis job (`07_ci/synth/synth.py`, the same
Yosys + slang flow, at the place-and-route period) through **OpenROAD-flow-scripts** (ORFS) on **ASAP7**, SS corner —
floorplan with ORFS's macro placer, tap cells and the platform's PDN, timing-driven global placement, CTS, global and
detailed routing, the final report with OpenRCX parasitics — with the memory leaves of `01_rtl/neo_mem.sv` as
**FakeRAM2.0** macros. One job per block, in parallel; the routed figures, the routed / synthesized ratio and every
stage's runtime and peak memory go to `ci-logs-pnr` and to `pnr_<block>.md` (`09_synthesis/pnr_<block>.md` holds the
measured runs).

| File | Role |
| --- | --- |
| `tools.sh` | after `synth/tools.sh` (the OSS CAD Suite's Yosys, OpenSTA, the ASAP7 liberty files): pulls the ORFS docker image of the ORFS commit `synth/tools.sh` pins (`openroad/orfs:26Q4-124-g3451ca1f5` for 3451ca1f5 — one pin for both jobs) and checks its `flow/` against that commit; sparse-checks out `tools/FakeRAM2.0` of the same commit |
| `fakeram.py` | one LEF + LIB per memory-leaf type of the block's subtree from FakeRAM2.0's own classes, and the wrapper that puts the leaf's ports around the macro |
| `prep.py` | the subtree's netlists as one hierarchical netlist for ORFS (tie cells, pass-through buffers — ORFS's own synthesis tail), the macro boundary checked; the unit's `block.sdc` |
| `run.py` | one block: `fakeram.py`, `prep.py`, the synthesized estimate (the prepared netlist flat in OpenSTA), ORFS stage by stage in the image within the job's time, `neo_report.tcl`, the logs collected, `report.py` |
| `neo_report.tcl` | after ORFS's final stage, on `6_final.odb` with `6_final.spef`: slacks, the register-to-register minimum period (synth.py's figure) and the one with the I/O paths, worst paths, clock skew, the cells driving clock nets, power, areas, wire length |
| `report.py` | `pnr_<block>.md` (and `metrics.json`, every ORFS metric merged) from a block's logs |
| `publish.sh` | a block's `ci_logs/pnr/<block>/` into `ci-logs-pnr` on top of the other blocks' (fetch, commit, push; retried on a lost race) |
| `designs/neo_common.mk`, `designs/<block>/config.mk` | the ORFS design configurations: `mac_pe`, `c16_systolic_array`, `c16_neo_core` (+ `macro_place.tcl`, the macro-placement fallback) |

## Tools: why the docker image

No GitHub release ships an OpenROAD binary for the pinned ORFS: The-OpenROAD-Project/OpenROAD's releases 26Q1–26Q4
carry the source only, Precision Innovations moved its Ubuntu `.deb` builds off GitHub in 26Q1, and ORFS's GHCR images
are tagged `latest` only. Building OpenROAD and its dependencies from source is an hour of the 6 h job before the
first block. So the job runs the image ORFS's release pipeline pushes for every master commit, `openroad/orfs:<git
describe>`, at the commit `synth/tools.sh` already pins for the liberty files — the libraries synth.py maps to and the
ones ORFS places with are the same files. `tools.sh` prints the image's OpenROAD / Yosys versions and compares the
image's `flow/Makefile` and `flow/scripts/variables.yaml` with the pinned commit's. Everything else comes from GitHub as
in the other jobs. The workflow input `pnr_image` overrides the image.

ORFS runs as the runner's user in the image (`make -f /OpenROAD-flow-scripts/flow/Makefile DESIGN_CONFIG=... WORK_HOME=...
NEO_PNR_IN=...`, the workspace mounted at its own path), one make target per stage: `synth` (with `SYNTH_NETLIST_FILES`
ORFS skips Yosys, copies our netlist to `1_2_yosys.v` and links it: `1_synth.odb`), `floorplan`, `place`, `cts`, `route`,
`do-finish` (no GDS: the merge needs KLayout and gives no figure), then `make run RUN_SCRIPT=neo_report.tcl`.

## The flow, per block

- **Netlist.** `synth.py --blocks <unit> --period <P>` (the job's input `pnr_period`, 1,000 ps until A2a's sweep sets the
  proxy's period); each unit's `netlist.v` holds the unit's module alone, so `prep.py` reads the subtree's netlists and
  the macro wrappers into one hierarchical netlist (OpenROAD flattens it at link; instance paths join with `/`). Yosys
  then applies ORFS's own synthesis tail: `splitnets`, `opt_clean -purge`, `hilomap -singleton` onto TIEHI / TIELO
  (synth.py leaves constants on pins: the tied-off hooks, the reset flops' SETN), `insbuf`, `check -assert -mapped`. The
  purge removes the dead full / half adders synth.py's stat still counts (opt_clean keeps instances of the adder cells
  while they are unknown modules): 70 of `mac_pe`'s 1,124 cells.
- **Constraints.** The unit's `block.sdc` as synth.py wrote it at the same period: the clock, 8 % setup uncertainty, 0.2
  of the period on every input and output, the reset false path, max fanout 32 / transition 0.16 of the period, the
  descriptor multicycles. ORFS's CTS propagates the clock.
- **Macros.** The memory leaves are wrapped and swapped (`09_synthesis/memories.md`): a module with the leaf's name and
  ports around a FakeRAM2.0 macro on the functional ports — RB = 1 (port A tied off: the core's three arrays) a
  1-write-1-read SRAM `fakeram7_<D>x<W>_g<G>_1w1r` (clk, we[G], waddr, wdata, rb_en, rb_addr, rb_data), RB = 0 a
  register file with an asynchronous read `_1w1a` (ra_addr -> ra_data). `prep.py` checks in the synthesized netlist that
  every leaf pin the wrapper drops is a constant (inputs) or unread (outputs). FakeRAM2.0 is ORFS's vendored copy with
  the asap7 platform's process parameters (bit cell 0.108 × 0.270 um, × 1.2 × 1.2 periphery = 0.042 um² per bit, pins on
  M4 at the left edge, M1–M4 obstructed, the height grown to fit the pins); FakeRAM's fixed port names and its
  two-read-write-port dual-port part do not fit the leaves, so `fakeram.py` builds its `Memory` with the leaf's pin list
  and uses FakeRAM's sizing, pin / rail geometry, LEF exporter and liberty writers. Timing: the synthesis flow's model —
  218 ps access, 50 ps setup / hold, 157 ps minimum period.
- **ORFS settings** (`designs/neo_common.mk`): `CORNER = WC` (ASAP7 SS 0.63 V / 100 °C, the files synth.py times with; the
  platform's default is BC); `CORE_UTILIZATION = 55` with the platform's `PLACE_DENSITY` 0.60 (at 60 % the uniform density
  after tap cells, port buffers and the floorplan's repair would exceed the default density and global placement stops);
  the platform's default clock tree, routing layers (M2–M7) and PDN; ORFS's macro placer (`rtl_macro_placer`), with
  `NEO_MACRO_PLACEMENT=1` as the fallback (the macros on the core's left edge, mirrored so FakeRAM's pins face the core);
  no IR-drop analysis in the final report. The two large blocks set `SKIP_LAST_GASP = 1` (runtime). The workflow input
  `pnr_flow_args` adds make variables (e.g. `TNS_END_PERCENT=5`).
- **Budget.** Each block's job has 350 minutes; `run.py` gives ORFS what is left after the tools and the synthesis minus
  30 minutes and stops a stage that would overrun, then reports on the last database there is — a block that does not
  finish still publishes how far it got and why.

## Outputs per block (`ci-logs-pnr`: `ci_logs/pnr/<block>/`)

`pnr_<block>.md` (report.py): die and core area, utilization, cells after placement and after routing (standard cells,
macros, taps, ties, fill), routed wire length, DRC / antenna counts after detailed routing, global-route congestion,
setup / hold WNS and TNS from the routed final STA (OpenRCX), the minimum period register to register and with the I/O
paths, the worst routed paths, power at the flow's default activity (A3 replaces it with the cocotb runs' recorded
activity), clock skew and the cells driving clock nets, the macros' LEF sizes against the synthesis model, every ORFS
step's runtime and peak memory; and the **routed / synthesized** ratio: the same netlist flat in OpenSTA with the same
SDC, ideal clock, no wires (`in/synth_sta/`) against the routed figure, beside synth.py's own per-unit figures.
`orfs/logs`, `orfs/reports` (ORFS's logs, metrics JSON and reports, `reports/neo/` neo_report.tcl's), `in/` (the macros'
LEF / LIB / wrappers, prep.py's netlist statistics and checks, the SDC), `synth.txt`, `tools.txt`, `run.json`.

## Running it

CI: dispatch `regress.yml` with `pnr_only` (the other jobs skipped) and `pnr_blocks`; the weekly run (Sunday 02:00 UTC)
and a push to main that touches `07_ci/pnr/**` run the three blocks. Locally, from the flat layout (`pnr/` beside
`synth/` and `rtl/`): `python3 synth/synth.py --blocks c16_neo_core --period 1000 --out synth_out`, then `python3
pnr/run.py --block c16_neo_core --synth synth_out --out pnr_out --logs logs/c16_neo_core --runner docker --image
openroad/orfs:26Q4-124-g3451ca1f5` (`--runner local --flow-home <ORFS>/flow` with an ORFS install; `--runner dry` prints
the ORFS commands after running the macro generation, the netlist preparation and the synthesized STA — what the sandbox
without docker can run). `NEO_PNR_TOOLS/FakeRAM2.0` (or `--fakeram`) is ORFS's `tools/FakeRAM2.0`.
