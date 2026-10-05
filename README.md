# AstraCore Neo — handoff package v0.16 (2026-10-05)

As of drop 0.15 the deliverable is described in IP form (tile and mesh as the block, `08_ip_product/`); the chip-level
material elsewhere in this package is the reference SoC configuration of the same RTL.

What this is: the RTL, verification, models, compiler, software, safety and procurement material for the Neo
automotive AI inference accelerator, at the furthest point reachable without sign-off tools, licensed IP and
silicon. It is RTL plus evidence, not sign-off. The receiving team's first job is to re-run every regression
here on Xcelium (and Verilator in CI) and treat any disagreement with the Python simulator (neosim) as a
neosim bug until proven otherwise.

## Folders

| Folder | Contents |
| --- | --- |
| `00_overview/` | the engineering record (spec v2.1, decisions, drop log, every drop's findings) exported from the project document; product strategy and development plan live inside it |
| `01_rtl/` | 25 SystemVerilog modules: PE, systolic array with ABFT check column, streaming core, feeder, accumulator, sequencer, requantization, weight buffer with ECC, (39,32) SECDED, SRAM bank with ECC and MBIST, CRC-16, NoC router and mesh, tile interface, DMA program engine, host register interface, error-signaling module, tile and tile mesh |
| `02_dv/` | directed testbenches for Xcelium (`tb/`), the assertion set with binds (`sva/`), the UVM skeleton (`uvm/`), the Python executable-spec harnesses and the neosim simulator (`sim/`), golden vectors (`vectors*/`), and the DV plan (`README.md`) |
| `03_models/` | feasibility model, cycle-accurate systolic reference, compiler (lowering, scheduling, backend emitting tile programs), performance models v2/v3 and their reports |
| `04_software/` | C host driver (emits descriptors and DMA programs identical to the Python backend; loads tiles through the register map) and its unit test |
| `05_safety/` | safety concept (in the record), FMEDA v1 workbook with the Evidence sheet, its generator, the assessor concept-review pack |
| `06_procurement/` | the RFQ-ready IP list with lead times and the issue sequence |
| `07_ci/` | Makefile and the GitHub Actions workflow (Python regression, C driver check, Icarus/Verilator testbenches, nightly tile mesh) |
| `08_ip_product/` | IP form of the deliverable (drop 0.15): datasheet with per-configuration PPA, SEooC safety-manual outline, test-chip plan, the PPA model's output; AXI-Lite wrapper and cocotb testbenches live in `01_rtl/` and `02_dv/cocotb/` |
| `09_synthesis/` | Genus and Design Compiler reference scripts for `neo_tile_axil` at the silicon configuration, the SDC, and the memory-macro map (drop 0.16; run where the N5A libraries are) |
| `10_fpga/` | ZCU102 proof-of-concept flow skeleton: Vivado project script, constraints, `fpga_top` (drop 0.16; completes where Vivado is) |
| `11_tool_qualification/` | compiler tool-qualification kit generator and its report: 122 cases (lowering value-exactness over random shapes, program-emission structure on 2×2 and 8×8 meshes, C driver equality, requantization arithmetic), with the TCL argument (drop 0.16) |
| `logs/` | every regression log from the final run on the final RTL (`regress_full.log` is the summary) |

## Build and run

Requirements for what runs here: Python 3.12 with numpy and openpyxl; gcc. For the HDL testbenches: Xcelium
(`make xrun`, `make xrun_core`, `make xrun_tiles`), or Icarus/Verilator (`make iverilog`, `make verilator`, `make lint`).

    cd 07_ci && cp Makefile ..  # or run from a checkout laid out as in the repository (rtl/, sim/, ... at the top)
    make regress                # golden models, neosim regressions, compiler, C driver (about 10 minutes)
    make tiles                  # the tile-mesh suite M1-M11 (an hour under neosim; per test: python3 sim/run_tile_test.py M4)
    make two_layer              # two layers back to back with INT8 handoff (M10)
    make xrun_tiles             # Xcelium: M1 through the register bus

The repository layout the Makefile and CI expect is the flat one (rtl/, sim/, tb/, dv/, model/, compiler/, sw/,
safety/, handoff/, vectors/, .github/); `make_repo.sh` in this package recreates it from the numbered folders.

## Status at handoff

- Verified on the final RTL (see `logs/`): streaming core T1-T5 at 32x32 and 8x16; whole convolutions C1-C10 on the
  core with ten fault-injection cases; NoC N1-N2; requantization; K-split K1-K2; SECDED exhaustive; MBIST B1-B3;
  tile mesh M1-M11 (DMA programs, K-split with RDY flow control, end-to-end CRC, region refill, bank ECC,
  compiler-emitted programs, M-chunks with partial fetch, register-driven self-test/ESM/watchdog, lost flit and
  timeout, MBIST through registers) and the two-layer INT8 handoff (M10); C driver identical to the Python backend.
- Spec v2.1 numbers: 1.32 ms for YOLOv8-m at 1280x736 on the v3 model (target <= 1.5 ms), 0.66 ms at 640^2;
  architecture 64 tiles x 1,024 MACs at 2.0 GHz, 128 MB in 64 banks, 1024-bit mesh links.
- FMEDA v1: core SPFM 99.9 % (ASIL-B(D) element), every row's coverage claim naming its proving test; chip PMHF 62.3 FIT
  dominated by shared-SRAM SER pending foundry data; LBIST is the one mechanism without an RTL proof (tool-inserted).


## Regression status at the moment of packaging

`logs/regress_full.log` is the summary of the final sequential run on the final RTL; `logs/<name>.log` are its per-test logs.
`logs/last_passing/` holds the last passing log of every test from the drop in which it last ran. Any row below marked
"not reached" is re-run by `make regress` / `make tiles` (or `python3 sim/run_tile_test.py Mn`) on the receiving side.

| Test | Final run on final RTL | Last passing run |
| --- | --- | --- |
| feasibility model (model/neo_feasibility.py) | PASS (rc=0) | drop 0.1 |
| systolic reference T1-T5 (model/systolic_ref.py) | PASS (rc=0) | drop 0.5 |
| streaming core T1-T4, 32x32 and 8x16 (sim/run_mac_core.py) | PASS (rc=0) | drop 0.5 |
| v0.2 datapath C1-C4 (sim/run_conv_v02.py) | not reached when packaged | drop 0.5 |
| neo_core C1-C8 (sim/run_conv_core.py) | not reached when packaged | drop 0.12 |
| neo_core C9-C10 (sim/run_conv_core.py --late) | not reached when packaged | drop 0.12 |
| NoC N1-N2 (sim/run_noc.py) | not reached when packaged | drop 0.7 |
| requantization (sim/run_requant.py) | not reached when packaged | drop 0.5 |
| K-split K1-K2 (sim/run_ksplit.py) | not reached when packaged | drop 0.10 |
| (39,32) SECDED exhaustive (sim/run_ecc39.py) | not reached when packaged | drop 0.9 |
| MBIST B1-B3 (sim/run_mbist.py) | not reached when packaged | drop 0.13 |
| compiler lowering --verify | not reached when packaged | drop 0.10 |
| backend static compile | not reached when packaged | drop 0.13 |
| C driver vs Python backend | not reached when packaged | drop 0.13 |
| tiles M3 end-to-end CRC | not reached when packaged | drop 0.9 (DMA program), pre-INT8-drain RTL |
| tiles M9 lost flit / timeout | not reached when packaged | drop 0.12, pre-INT8-drain RTL |
| tiles M11 MBIST via registers | not reached when packaged | drop 0.13, pre-INT8-drain RTL |
| tiles M8 registers/ESM/watchdog/self-test | not reached when packaged | drop 0.12, pre-INT8-drain RTL |
| tiles M1 fetch/compute/writeback | not reached when packaged | drop 0.9, pre-register-bus tile |
| tiles M2 K-split with RDY | not reached when packaged | drop 0.9, pre-register-bus tile |
| tiles M4 region refill | not reached when packaged | drop 0.9, pre-register-bus tile |
| tiles M5 bank ECC in flight | not reached when packaged | drop 0.9, pre-register-bus tile |
| tiles M6 compiler-emitted program | not reached when packaged | drop 0.10, pre-register-bus tile |
| tiles M7 M-chunks with partial fetch | not reached when packaged | drop 0.10, pre-register-bus tile |
| two-layer INT8 handoff M10 (sim/run_two_layer.py) | not reached when packaged | drop 0.13 (final RTL) |

Note: `mac_8x16` in the summary is a script error of mine (wrong arguments); the 8x16 configuration runs inside the
streaming-core test itself and passed there.

## Drop 0.16 (review response), what changed in the RTL

- Clock gating: the PE now carries a valid with its data (skewed per row like the activations) and clocks its
  product and add stages only when valid; synthesis turns the enables into ICG cells (`lp_insert_clock_gating` /
  `insert_clock_gating` in the scripts). Streaming core T1-T4 at 32x32 and 8x16 pass unchanged.
- Control-path protection: the DMA program memory is SECDED (`prog_mem.sv`, corrected on fetch, `prog_ce/ue`);
  two DMA engines run in lockstep with a comparator; descriptor registers are kept twice and compared; illegal
  FSM states are detected. Cause bits 15 (prog_ce), 16 (control path) and 17 (isolation) were added; watchdog is 18.
  M12 injects all three faults and sees each cause.
- Spatial partitions: a 4-bit partition id per tile (register 0x0B) travels in every flit; a flit from another
  partition is dropped at the destination and flagged. M13: a cross-partition fetch is dropped and flagged, the
  requester times out and completes; the same fetch inside one partition is clean.
- Not done, and why: CHI/ACE-Lite/SMMU integration belongs to the licensee's interconnect (the AXI-Lite control
  port and an AXI4 data bridge are the block's boundary); an FPGA bitstream needs Vivado (flow skeleton provided);
  FP8/INT4 lowerings wait for the release-2 PE modes.

## Regression status for drop 0.16 (clock-gated PE, control path, partitions)

Run on the final RTL in this drop: streaming core T1-T4 at 32x32 and 8x16 (ALL PASS); M12 control-path faults (ALL PASS);
M13 partitions (ALL PASS); core C1-C5, C7 PASS, C6 PASS after a harness fix (the suite split in drop 0.12 had left C6
comparing against the wrong layer's reference; the RTL was never wrong), C8-C10 and the tile suite M1-M11 were
re-launched in the background (`logs/regress_016.log`) and are re-run by `make core`, `make tiles` on the receiving side.

## Technical backlog (in priority order, all closable with a real simulator)

1. Run every regression on Verilator through `dv/cocotb` (`make -C dv/cocotb core`, `... tile`) and the directed
   testbenches (`make verilator`, `make lint`); fix the RTL where a sign-off-grade simulator disagrees with neosim.
2. Scale to the silicon configuration: ROWS = COLS = 32, NX = NY = 8, BANK_DEPTH for 2 MB, ACC_ROWS = 512; re-run.
3. Pack 32 words per 1024-bit flit in `tile_nic` (serve, fetch and drain serializers); the router is already
   width-parameterized (DW).
4. Release-2 datapath: 2:4 sparsity engine, INT4/MXFP4 and FP8 PE modes, FP16/BF16 at half rate.
5. Close timing and leakage at the Grade-1 corner (Tj 150 °C) in the reference flow; replace the first-order PPA in
   `08_ip_product/` with synthesis and memory-compiler numbers.
6. Fault-simulation campaign for the FMEDA coverage claims; UVM constrained-random environment on top of `dv/uvm`.

## Open items with no counterparty in this package

| Item | Owner | Why it is open |
| --- | --- | --- |
| Item definition and FTTI for the lead OEM's perception item | lead OEM, at the letter-of-intent gate | the safety concept states assumptions of use (detect <= 10 ms, react <= 20 ms) that only an item definition can confirm |
| LBIST (logic built-in self-test) of the core and tile logic | DFT team with Modus | tool-inserted; the island carries the hooks (self-test register, FTTI-windowed watchdog) and the schedule |
| IP: LPDDR5X, PCIe 5, MIPI, SerDes, UCIe, R52+, HSM, ISP, memory compilers, PLLs | procurement, per `06_procurement/ip_rfq_pack.md` | licensing lead times of 6-12 months; evaluation copies suffice to start integration |
| Fault-simulation campaign behind the FMEDA coverage numbers | DV team on Xcelium | the numbers are engineering claims tied to directed fault tests, not measured coverage |
| Sign-off: lint/CDC/RDC, UVM coverage closure, formal, synthesis, DFT, physical design, timing, power, DRC/LVS | the receiving team | not possible without the tools; everything here is structured to shorten the ramp (see the drop 0.11 record) |

## Known limitations of the development environment, stated plainly

- Every simulation result was produced by neosim, a SystemVerilog-subset simulator written for this project
  (`02_dv/sim/neosim.py`). It is strict and has found real RTL bugs, but it is not a sign-off simulator.
- The tile-mesh tests use 16x8 and 8x8 cores on a 2x2 mesh for simulation speed; the silicon configuration is
  32x32 cores on an 8x8 mesh, and every module is parameterized for it (the 32x32 core ran T1-T5 at full size).
- Performance numbers come from the event model (v3), not from RTL simulation of the full network.
