# Stream A4 -- timing and physical leftovers: the merged tree back at 1,250 ps, the NIC and the bank wrapper routed
# clean, the 32 x 32 core routed, the GLS of gated units, the spec's figures reconciled with the proxy

Branch `a4-timing-physical`. Read COMMON.md first, then `09_synthesis/synth_report_drop043.md` (the verdict and how it
is computed), `pnr_drop043.md` + `pnr_tile_nic.md` + `pnr_bank_bist_wrap.md` + `pnr_neo_core.md` (A3's routed results and
what stopped the core), `power_drop043.md`, `floorplan/README.md`, `07_ci/pnr/README.md`, the last synth report on
`ci-logs-synth` (the merged main's figures: `tile_nic` one endpoint at 1,250 ps, the bridges at ~1,500 ps, `neo_core` 63
endpoints at 1,250 on the requantizers' input select, the PE's stage 1,095 ps with C1's byte select).

## The items

1. **The tile verdict back at 1,250 ps on the merged tree.** `tile_nic`'s one endpoint (the merged NIC: B2's loopback channel,
   C2's INT4 unpack and A3's drain-format select together -- find which); `neo_core`'s 63 endpoints: C2's input select
   (accumulator row or the vector unit's row) in front of the requantizers' sign-bit inputs -- move the select a stage
   earlier (the unit's bypass registered) so dense timing is main's again; the data PE's 1,095 ps: C1's one-hot byte select
   in the product stage -- try the select on the previous stage's output register (the byte chosen as `x_out` is latched,
   the multiplier sees a plain byte) if it keeps the PE's cycle contract; the chip-top bridges (`noc_axi_bridge` ~1,360 ps,
   1,339 endpoints at 1,500): the receive CRC and the beat's word-count dependence B1 named -- the NIC's A2a treatment
   (beats registered at the port, the CRC split by linearity) applied to the bridge; `top_ctrl` 774 ps and the router's
   839 are fine at 1,250. Verdict: every unit outside the PE-limited ones clean at 1,250 ps on the merged tree, the chip
   top's units included in the report's verdict line (extend the verdict to a second line over the chip-top units).
2. **Routed timing.** `tile_nic` −891 ps at 1,250 (the drain-format select's fan-out: register the select per lane group or
   duplicate it); the bank wrapper −1.76 ns as its own block (the self-test broadcast registered per lane group, the 16 lane
   macros packed around the logic with a `macro_place.tcl`); the 16 x 8 core −104 ps and `wbuf_mem` −90 ps on its I/O
   (constraints or a small restructure); re-run the four through the `pnr` job and report.
3. **The 32 x 32 core routed.** A3's floorplan reached 89.5 % of `repair_design` at the job's limit: route it as four
   quadrant hard macros (each a routed 16 x 16 block of the array with its own LEF / LIB from the flow -- ORFS supports a
   hierarchical flow with abstracted macros) plus the core's rest, or document a larger self-hosted runner's requirements
   (memory, hours) and leave the flow ready for it; the fix of the acc macro's pin orientation A3 found.
4. **The gate-level simulations of gated units**: `requant` under GLS with `neo_icg` on the RTL side (fixed 20 Oct in
   `gls.py`) -- confirm green in CI and extend the GLS set to `tile_telem`, `host_if`, `vec_unit` at 8 lanes.
5. **Power and die figures reconciled.** `power_drop043.md`'s gaps closed where cheap (the program memory's words traced,
   the flops without recorded activity reported as a share), the tile's power with S1's SECDED and B2's gating measured on
   the routed blocks, and a one-page `09_synthesis/proxy_to_target.md`: what the proxy figures mean against the spec's N5A
   claims (2.0 GHz, 210 mm², 56 W) -- the ratios that are the design's (shares, routed / synthesized, utilization) and the
   absolute figures that are the library's, with the method a foundry library would be plugged into (the units, the SDC,
   the macro model) -- the integrator rewrites spec §9 from it.

## Verification and report

Local: `make lint`; the suites of the files you touch (`make verilator`, `make iverilog`, neosim `run_nic.py`,
`run_mac_core.py`, `run_vec_unit.py`, cocotb `core` / `tile` / `top`); `make formal`; the unit syntheses before / after.
The PR, green on every job, the `pnr` dispatches read from `ci-logs-pnr`. Report: the verdict lines before / after (the
tile and the chip top), the routed table, the core's route or its requirements, the GLS set, the reconciliation page's
numbers, anything unresolved, the PR number.
