# Stream A2a -- the second library, the period sweep, and the paths A1 left: the packer's merge decision, the NIC's
# writeback mask and drain-FIFO head, the router's input parity, the duplicated requant

Branch `a2a-sweep-carryovers`. Read COMMON.md first, then `00_overview/ROADMAP.md` §1 (A2's row) and §5 ("Status after
drop 0.41" -- the measured state you start from), `07_ci/synth/README.md`, `07_ci/synth/synth.py` (`PDKS` has `asap7` and
`nangate45`; `--pdk`, `--corner`, `--period`; the SDC templates; the report), `09_synthesis/synth_report_drop040_sandbox.md`
for the format, and the drop 0.41 synthesis report on `ci-logs-synth` (or regenerate the figures you need per unit). Files:
`07_ci/synth/synth.py` + `report.py` + `README.md`, `01_rtl/link_pack.sv`, `01_rtl/tile_nic.sv`, `01_rtl/noc_router.sv`
(only if §4 says so), `09_synthesis/` (a new report), tests under `02_dv/`. Stream B1 adds UNITS entries for `neo_top`'s
blocks to `synth.py` at the same time -- keep your edits to the library / sweep / report code so the merge is clean;
stream A2b builds the place-and-route flow in a new `07_ci/pnr/` and does not touch your files.

## 1. Nangate45 as the second data point

`tools.sh` already installs `NangateOpenCellLibrary_typical.lib` and its adder map (`/home/claude/tools/pdk/lib/nangate45`
in the sandbox). Make `--pdk nangate45` work end to end: the Yosys mapping (dfflibmap / abc with the typical library, the
adder map, the Kogge-Stone LCU as for ASAP7), the macro model for the leaves (FakeRAM45 figures: `fakeram45_*.lib` are in
the directory -- derive a per-bit area and an access time from them, and say in the report how), OpenSTA with the one
corner, the SDC in the library's units (ns), the report's tool line naming both libraries. Then run the full unit set on
Nangate45 (the units are the same; `noc_router` / `tile_nic` are heavy on this machine -- leave the full run to CI: add the
second library to the `synth` job as a second invocation writing `synth_out_nangate45/` and a second report, published to
`ci-logs-synth` beside the first; the job's budget is 300 min, the ASAP7 run takes ~35 min on the runner, Nangate45 is
expected to be faster to map; if the two together exceed ~90 min, run Nangate45 on the lighter half of the units and say so).

## 2. The period sweep

Timing is cheap once the netlists exist. Add `--sweep 500,750,1000,1250,1500` (ps on ASAP7; the Nangate45 equivalent in
ns): for each period, re-run OpenSTA per unit with the period in the SDC (the ETMs do not change) and record per unit:
WNS, violating endpoints / endpoints, the min period. The report gains a sweep table (unit x period: violating endpoints)
and a one-line verdict per library: the smallest swept period at which every unit except the PE-limited ones (`mac_pe`,
`mac_pe_chk`, `systolic_array`, `neo_mac_core` and their parents) is clean, and the PE's own stage. The tile's Fmax on the
proxy is that number; write it into the report's summary. Validate the mechanism locally with `--sta-only` on a copy of a
complete output directory (drop 0.40's is at `/home/claude/work/flat37/synth_final` -- the RTL has moved since, so use it
only to test the sweep code; the real figures are the PR's).

## 3. The paths A1 left (RTL)

- **`link_packer`**: WNS −263 ps at the unit level -- the merge decision (`same` / `fits` on the incoming beat against
  the pending flit) is in the input cycle and fans into ~60 count flops. Move the decision off the input path: register the
  incoming beat (an input skid stage, 16 words + fields) so `same` / `fits` compare register against register, and keep the
  throughput (one 32-word flit every second cycle at 16 words per beat; back-to-back flits one per cycle) -- A1b's test
  `run_nic.py` I14 compares against the drop 0.40 packer cycle for cycle with one cycle of latency: update its expected
  latency (two cycles now) and keep its equality check; the formal harness `02_dv/formal/link_pack_fv.sv` reads the packer's
  internals (`pbuf0/1`, `cur`, `pend_*`, the output register) -- update it with the RTL and keep the full proof (D1's
  README explains the invariants; the proof is part of the `formal` job that must stay green). Every tile / mesh end time
  moves by one more cycle per hop: report them.
- **`tile_nic`**: after A1a the unit is at 2,324 ps on `rx_flit[553] -> b_wmask[15]` (the WRDATA lane mask computed behind
  the 64:1 `wr_ptr[rx_src]` mux) and 2,281 ps register-to-register on `rx_woff -> rx_cnt`. Bring the NIC to the PE's level
  (≤ ~1,000 ps): the per-source write pointers read a cycle earlier (register the selected `wr_ptr` / offsets as the beat's
  header arrives -- the data beats follow the header, so the pointer is known a cycle ahead), the mask from the registered
  pointer, and the drain FIFO's head read registered (the leaf's port B, as A1c did for the other arrays -- the drain FIFO
  is a `neo_mem_rf` instance in `tile_nic`; the transmit path then sees the head a cycle later: adjust the tx state machine
  and report the latency). Verify with `run_nic.py` (all tests), neosim `run_tile_test.py M3` and the cocotb tile suite; the
  PR's mesh8. Report the NIC's new min period and worst path.
- **`noc_router`**: the parity reduce at FIFO entry puts ~545 ps of setup on the mesh input pins, which violates the tile
  SDC's 0.4-period link budget at 500 ps but not at the sweep's period. Judge it with the sweep: if at the proxy's Fmax
  period (§2) the link inputs are clean, leave the RTL and record it; if not, register the incoming flit (a one-flit input
  register per port before the FIFO, +1 cycle per hop) and report the end-time change.
- **The duplicated requant** (2 × 127 k cells, R10 lockstep): an analysis, not an implementation -- the cell and area cost
  of the duplicate on both libraries, and a design note (in the new `09_synthesis/` report) for a residue-checked single
  copy (a mod-3 or mod-7 check of `acc × mult` plus a duplicated shift / saturate stage) with its coverage argument against
  the FMEDA's R10 row; the decision is the integrator's.

## 4. Verification and report

Local: `make lint` 0 warnings; `make verilator`; `make iverilog_core`; neosim `run_nic.py`, `run_noc.py`, `run_tile_test.py
M3`; `make formal FORMAL_RUN=<worktree>/07_ci/formal/run.py FORMAL_JOBS=1` (the link_pack proof with your packer); cocotb
`make -j1 -C dv/cocotb tile`; unit synthesis of `link_packer` and `crc16_beat` on both libraries (`tile_nic` and
`noc_router` on CI). Then the PR (`gh pr create` or `gh api -X POST repos/astracore-ai/Astracore-Neo/pulls`; runs via
`gh run list --branch a2a-sweep-carryovers`), green on every job, and the report: the two libraries' unit tables (min
period, cells, area), the sweep tables and the Fmax verdict per library, the NIC's and packer's new paths, the router
decision with its numbers, the requant analysis, the end-time table with the cycle accounting, the new report's path
(`09_synthesis/synth_report_drop042.md` or as the integrator names it), the PR number.
