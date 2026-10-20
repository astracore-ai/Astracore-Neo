# Stream A3 -- the last timing items (the tile clean at 1,250 ps on the proxy, the check column at the data PE's
# stage), the tile's units routed, power from recorded activity, the chip-level floorplan sketch

Branch `a3-physical`. Read COMMON.md first, then `00_overview/ROADMAP.md` §1 / §5, `09_synthesis/synth_report_drop042.md`
(A2a: the two libraries, the sweep and its verdicts -- the state you start from), the five `09_synthesis/pnr_*.md` and
`07_ci/pnr/README.md` (A2b: the OpenROAD flow, its runtimes and memory on the runner, the FakeRAM macros, the
routed / synthesized ratios), `07_ci/synth/README.md`. Files: `01_rtl/bank_bist_wrap.sv` + `mbist.sv` + `sram_bank.sv`,
`01_rtl/prog_mem.sv` (+ `tile_dma.sv` if the instruction register moves), `01_rtl/core_seq.sv` (one endpoint),
`01_rtl/mac_pe.sv` / `systolic_array.sv` / `deskew_out.sv` (the check column), `07_ci/pnr/**`, `07_ci/synth/synth.py`
only for the default sweep grid if §1 changes it, `09_synthesis/` reports, `02_dv/` tests. Streams B2 (power, telemetry,
NoC priority, the bridges' queues, the chip top), C2 (the vector unit, INT4) and D2 (the fault campaign) run beside you:
B2 adds clock-enable logic to the core / NIC / router -- coordinate by keeping your RTL edits to the files above.

## 1. The tile clean at 1,250 ps on ASAP7 SS (800 MHz on the proxy)

A2a's sweep: at 1,250 ps every unit outside the PE-limited ones is clean except `bank_bist_wrap` (403 endpoints, −155 ps:
the MBIST's path through the bank wrapper's port mux, `u_bank -> u_bist`), `prog_mem` (64 endpoints, −122 ps: the SECDED
decode of the 64-bit instruction read, two `ecc39_dec` lanes, into the DMA's decode) and `core_seq` (one endpoint, `f_ct ->
f_start`, −122 ps). Fix the three in the RTL (register the MBIST's compare / the wrapper's read mux; register the decoded
instruction -- the DMA's program counter then issues a cycle earlier or the engine takes one more cycle per instruction,
measure which costs less and report the end times; split the sequencer's `f_start` term), re-run the sweep locally with
`--sta-only` on the units (your flat layout; the heavy units' ETMs come from CI's `ci-logs-synth` run of main, fetched by
SHA), and the PR's synth job gives the verdict: **every unit outside the PE-limited ones clean at 1,250 ps**, so the tile's
period on the proxy becomes 1,250 ps = 800 MHz (the report's verdict lines).

## 2. The check column at the data PE's stage

`mac_pe_chk` (the check PE, an 8 x 14-bit product) is 1,093 ps register to register and 1,340 ps on its `x_in -> prod_q`
input path, and `systolic_array`'s 1,411 ps is that path seen from the array (the last data PE's `x_out` into the check
PE). Give the check PE's product a second stage (the check column one cycle later; `deskew_out` takes the check column's
tap one cycle deeper, so `y_chk` still arrives with its row; `neo_mac_core`'s LATENCY is unchanged for the data columns),
or split the 14-bit check weight into two 7-bit halves multiplied in parallel and added in the add stage -- whichever keeps
`mac_pe_chk` at or under the data PE's 1,031 ps with the smaller cost; the ABFT verdict must be unchanged on every test
(the fault-injection cases find the same rows). With C1 merged the array has NCHK = 4 check columns at the silicon
parameters: all four change alike. Expected: `systolic_array`'s minimum period = the data PE's, 1,031 ps.

## 3. The tile's units routed, the core placed

A2b routed `mac_pe`, `c16_systolic_array`, `c16_neo_core`, `noc_router`, `tile_nic` (ratios routed / synthesized 0.92–0.97
for logic-bound blocks, 1.31 for the router's crossbar fan-out). Extend the `pnr` job's block list and run (by
`workflow_dispatch`; the weekly schedule keeps the three default blocks) at the period §1 establishes (`pnr_period`
1,250 ps): `requant` (127 k cells -- the largest remaining logic block), `link_packer` + `link_unpacker`, `acc_bank`,
`act_feeder`, `wbuf_mem` (each with its FakeRAM macro at the silicon size, from `memories.md`), `prog_mem`, `host_if`,
`tile_dma`, `esm`, `mbist`, `bank_bist_wrap` with the bank as 16 lane macros of 32,768 x 39 (FakeRAM; the 131:1 single strip
A2b saw is not placeable), and the 32 x 32 `neo_core` **placed and globally routed only** (`make place` / `make
grt`-level stages; detailed routing of 1.5 M cells does not fit the 16 GB runner -- say what it would need). From the
routed blocks, build the table the spec needs: per block the routed cell area and die size, the sum over the tile's units
+ the macros = the tile's routed area estimate against the synthesis flow's 1.170 mm² (ASAP7) with the ratio per unit.

## 4. Power from recorded activity

Record switching activity from the cocotb runs (Verilator `--trace-saif` or a VCD on the 16 x 8 core for C1 / C2-class
tests and on the tile for M1 / M18 / M19; a `SAIF` or VCD per block hierarchy), and feed it to the routed blocks' power
analysis (OpenROAD `read_vcd` / `read_saif` + `report_power` on the routed design; A2b's reports used the flow's default
activity). Report per routed block: dynamic (switching + internal), leakage, the activity-derived figure against the
default-activity one, and the core's MAC-utilization during the recorded run (from the array's valid cycles) so the number
is labelled with its utilization. A table in `09_synthesis/power_drop043.md`, and the spec's §9 power line reads from it
(the integrator writes the spec; give the figures with their conditions: library, corner, period, utilization).

## 5. The chip-level floorplan sketch

A drawn figure (an SVG generated by a committed Python script under `09_synthesis/floorplan/`) of the die: 8 x 8 tile
macros at the tile's routed-area estimate (the bank macro's 16 lanes inside each tile, the core, the NIC and the router
placed as the routed blocks suggest), the bridge column at x = 8 (B1's `neo_top`), the control plane, the safety-island
and PHY / controller placeholders of spec §9 at their model sizes, the I/O ring; the total die area it implies on the proxy
and the ratio to the spec's ~210 mm² N5A figure (a different node: a ratio check, not a die size). One page in
`09_synthesis/floorplan/README.md` with the assumptions.

## 6. Verification and report

Local: `make lint` 0 warnings; the suites touched by §1 / §2 (`make verilator`, `make iverilog_core`, neosim `run_mbist.py`,
`run_mac_core.py`, `run_conv_core.py` -- the fault cases --, `run_tile_test.py M3`; cocotb `core` and `tile`); the unit
synthesis of `mac_pe_chk`, `systolic_array` (c16), `bank_bist_wrap`, `prog_mem`, `core_seq` before / after. The PR
(`gh pr create` or `gh api -X POST repos/astracore-ai/Astracore-Neo/pulls -f title=... -f head=a3-physical -f base=main
-f body=...`; `gh pr list` is blocked), green on every job (`formal` included: `lockstep_core_seq_fv` lists the
sequencer's registers -- add any new one), the `pnr` dispatches read from `ci-logs-pnr` (by SHA, as the other log
branches). Report: the sweep verdict before / after (§1), the check column's figures (§2), the routed table with ratios
and the tile estimate (§3), the power table with conditions (§4), the floorplan figure's numbers (§5), the end times with
their accounting, anything unresolved, the PR number.
