# `07_ci/synth` — the open-PDK synthesis job (drop 0.40)

What the `synth` job of `regress.yml` runs: the RTL through **Yosys (slang front end) → OpenSTA** on the **ASAP7** 7.5-track
RVT library (SS corner, 0.63 V / 100 °C, 500 ps clock), unit by unit up to the silicon tile, with the memory leaves
black-boxed and priced by a macro model. Nangate45 is a second library for a sanity comparison. Everything comes from GitHub
(apt only for OpenSTA's build dependencies on the runner; the sandbox builds those from source as well).

| File | Role |
| --- | --- |
| `tools.sh` | installs the pinned tools into `$NEO_SYNTH_TOOLS` (default `/opt/neo-synth`; the CI job sets `/home/runner/neo-synth`, no sudo): OSS CAD Suite nightly 2026-10-11 (Yosys 0.69+274 with the slang plugin, yosys-abc), OpenSTA f861c62 built from source (the binary is cached by `actions/cache`), the ASAP7 / Nangate45 liberty files, adder maps and the Kogge-Stone carry-lookahead map from OpenROAD-flow-scripts 3451ca1 |
| `synth.py` | the flow (below): the units, the Yosys run per unit, the macro liberty, the repeater trees, the per-unit OpenSTA with the children as timing models, the gates; `synth_results.json` |
| `abc_speed.script` | ORFS's `abc_speed.script` with the `{D}` delay target on the final mapping and sizing |
| `report.py` | `synth_report.md` and the per-unit files for `ci_logs/synth/` |

## Units

The design is synthesized as **units**: one Yosys run per module at its silicon parameters, every child module that is a
unit of its own black-boxed — so each module's logic is synthesized once and instantiated by name (the 32×32 array is
1,024 instances of the `mac_pe` unit and 32 of `mac_pe_chk`, not 1,056 copies through ABC) — while the small helpers
(delay lines, ECC, CRC, the checkers) flatten into their parents. The unit table is `UNITS` in `synth.py`: the tile's units
(`mac_pe`, `mac_pe_chk`, `systolic_array`, `neo_mac_core`, `act_feeder`, `acc_bank`, `neo_mac_core_v02`, `wbuf_mem`,
`requant`, `core_seq`, `neo_core`, `sram_bank`, `mbist`, `bank_bist_wrap`, `crc16_beat`, `ecc39_enc`, `ecc39_dec`,
`link_packer`, `link_unpacker`, `tile_nic`, `noc_router`, `tile_dma`, `prog_mem`, `host_if`, `esm`, `neo_tile`) and the
16×8 core's (`c16_*`, the configuration the small cocotb suites run). slang reads one module at its parameters in
seconds, where one read of the whole tile is beyond the sandbox's memory in Yosys's own front end; the black-box instances
are regrouped by their parameters after the read (`chtype` to the unit or macro name, `setparam -unset`), a memory leaf
becomes a macro cell named by its geometry (`neo_mem_sp_w624_d32768_g16`, `neo_mem_rf_w1056_d512_g1_rb1`, …), and the
**hierarchy check** of the job is that every parameterization of a child module has a unit with exactly those parameters.

Per unit: `synth -booth -noabc` with ORFS's Kogge-Stone carry-lookahead map, `extract_fa` onto the library's FA / HA cells,
the flops named after the registers they hold (`<net>_reg`, so the paths read in RTL terms), `dfflibmap`, ABC's speed
script at the period with the platform's driver and load; `stat`, `check`, the netlist and its JSON. A net with more than
32 loads gets a tree of the library's buffers in the netlist (`buffer_fanout`: the synthesis-stage stand-in for the
placer's repeaters — the per-unit synthesis cannot see the loads behind its black-box children, and the sequencer's
weight-load strobe reaches 1,059 PE inputs; clk and rst_n excepted), the netlist re-read for the stat.

## Timing

OpenSTA runs on every unit's own netlist with its **child units as extracted timing models**: each unit's run ends with
`write_timing_model` (setup / hold at the inputs, clock-to-output and input-to-output arcs, in a liberty file), and the
parent's run reads its children's models — the paths inside a child are the child's report, the paths between the units
the parent's, and a flat read of the tile's 2.5 M cells (more than 6 GB in OpenSTA) is never needed. The tile runs under
`09_synthesis/constraints.sdc` in the library's units (links at 0.4 of the period each side, host bus relaxed, the status
flags multicycle 4, `rst_n` and the coordinate straps false paths), a block under the clock and 0.2 of the period on
every input and output, a combinational block under a virtual clock; ideal clock, no wire load (synthesis-stage STA, as
ORFS's `synth` step). Reported per unit: WNS / TNS, the minimum period (`report_clock_min_period`: register-to-register
paths of the clock, a child's clock-to-output and setup arcs included), the worst ten paths with their stages
(`paths_max.txt`), the endpoint slack histogram; the macro ports carry the model's access time (218 ps ASAP7 / 435 ps
Nangate45: clock-to-Q on the registered read ports, address-to-data on the asynchronous port A of `neo_mem_rf`) and 50 ps
setup / hold.

## Area

**Cells and area** per unit from `stat -liberty`, summed through the tree (instances × unit); the macro area of the
black-boxed leaves from the macro model: ABKGroup FakeRAM2.0's 7 nm bit cell at ORFS's `asap7/fakeram.cfg` parameters
(0.042 µm² per bit) for ASAP7, `fakeram45_2048x39` (0.569 µm² per bit) for Nangate45; register-file style leaves
(asynchronous read: the weight and activation buffers, the drain FIFO; two read ports: the accumulator) at 1.5 × and 2 ×
the single-port bit cell. The arrays' real figures come from a memory compiler on the target node; these keep the tile's
area shares honest.

## Gates (the job fails on any)

Every unit synthesizes; no latch in the generic netlist; no multiply-driven net (`check`); the hierarchy check; the
`mac_pe` count equals ROWS × (COLS + 1) on the 16×8 core (144) and the tile (1,056); at `DV_HOOKS = 0`, on the synthesized
netlists, no `dv_*` input of any unit drives logic and every `dv_*` output is a constant or a child's output — over the
whole tile tree; every OpenSTA run completes and writes its model. **Timing is reported, not gated**: ASAP7 is an academic
predictive 7 nm PDK without foundry data; its SS corner is slower than an N5A worst-case corner by a factor the report does
not know — the picoseconds are the library's, the *paths* and the *shares* are the design's. A path that is 3 × over 500 ps
here is a design item; one 20 % over is a question for the real library. Nangate45 (45 nm) is a second data point for the
ratios, not a 2 GHz node.

## Running it

CI: the `synth` job (`make synth`, `SYNTH_JOBS` from the runner's memory: 2 on 16 GB, 1 below 14 GB — the two largest units,
the NIC and the router, peak near 5 GB each in Yosys). Locally, from the flat layout: `bash synth/tools.sh && make synth`,
or `python3 synth/synth.py --blocks mac_pe,requant --out synth_out` for a subset (a unit's subtree is included; `--pdk
nangate45 --corner TT`, `--period 625` for other points; `--no-sta`, `--sta-only`, `--resume` to keep the units already
synthesized in `--out`). With `NEO_YOSYS`, `NEO_STA` and `NEO_PDK_LIB` set, `synth.py` uses those tools instead of
`$NEO_SYNTH_TOOLS` (the sandbox: Yosys from the same OSS CAD Suite tarball, OpenSTA built against a Tcl 8.6, SWIG, CUDD and
Eigen built from GitHub, since the sandbox has no apt; `--jobs 1` under 8 GB).

## The memory leaves

`01_rtl/neo_mem.sv` (drop 0.40) holds every array a memory compiler replaces: `neo_mem_sp` (one write port of G groups, a
registered read — the bank's 32,768 × 624-bit rows with a write group per lane) and `neo_mem_rf` (the same write port, an
asynchronous read port A and, with RB = 1, a registered port B — the weight buffer 512 × 702 and the activation buffer
1,024 × 512 as pairs of entries with a group per entry; the accumulator 512 × 1,056 with A for the read-modify-write and B
for the drain; the NIC's drain FIFO 512 × 1,065). The flow black-boxes exactly these two module names;
`09_synthesis/memories.md` lists the instances. The behavioural arrays simulate everywhere; the testbench hooks (backdoor,
stuck-at, flip) are ports of the leaves under `DV_HOOKS`, which the silicon configuration ties off.
