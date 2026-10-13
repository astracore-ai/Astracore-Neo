# AstraCore Neo — roadmap from drop 0.38 to handoff (11 Oct 2026; compressed into four parallel streams 12 Oct, drop 0.41)

Scope: what can be done with the assistant, the GitHub runners (ubuntu-latest: 4 cores, 16 GB, 6 h per job, apt/pip/GitHub reachable)
and the sandbox (2 cores, 5.8 GB, GitHub reachable, apt/PyPI not), using open-source tools only, taking the design from "proven in
simulation at full scale" to a package a silicon team can take to a PDK. Since drop 0.41 the work runs as **four parallel streams on
branches** (A timing → physical, B chip top and power, C sparsity → transformers, D verification and software), each stream's step a
pull request whose CI run is its proof, merged into main when green; a drop is one merge (or a batch of them), numbered in merge order,
with its drop-log row, spec update and Doc section written at the merge. The fixNN.zip / README delivery of HANDOFF-4 §1 ended with
fix38: commits carry what the READMEs carried. Expected: the handoff candidate (0.70) about three weeks after 12 Oct at one long
session per day; the stream steps below are the plan, a step that needs a second PR shifts its stream, not the others.

## 0. The claims ledger (what the RTL backs today)

| Claim in the spec / datasheet | Status | Action |
| --- | --- | --- |
| 262 TOPS dense INT8, 65,536 MACs, 64 tiles of 32×32 | in RTL, simulated at full 64-tile scale (run 133) | keep |
| 524 TOPS with 2:4 structured sparsity | **not in RTL** (no sparse PE, index path, compressed weights, compiler packer, tests) | mark planned (0.39); implement (phase 3) |
| INT4 LLM figures (8B decode 13 ms/token, prefill 52 ms) | not in RTL: INT8 datapath only; the figures assume external memory bandwidth no RTL endpoint provides | strike (0.39); INT4 weight-only storage returns in phase 4 with measured numbers |
| 2.0 GHz worst case / 2.4 GHz typical | feasibility model; the paths synthesized on the ASAP7 proxy (0.40) and, after 0.41, every unit at or near the PE's own product stage (~1 ns on the SS proxy) | the period sweep on two libraries and routed timing (stream A, 0.42 / 0.43) give the proxy's Fmax; the N5A figure stays a model until a foundry library |
| ~56 W typical, TDP SKUs 65 / 30 / 15 W, always-on island ≤ 2 W | model; no clock gating, power gating or DVFS logic in RTL | mark pending; phase 5 adds the mechanisms, phase 7 the activity-based power |
| Family 262 / 196 / 131 / 65 by fusing tiles at test | partition isolation exists (`REG_PARTITION`, iso error); no fuse/disable map | mark pending; phase 2 adds tile-enable registers, the fuse source is an IP item |
| LBIST | tool-inserted at synthesis; hooks (SELFTEST, FTTI-windowed watchdog) exist | mark as integration item |
| PCIe Gen5 ×8 host, ASIL-D safety island (2 × R52+ lockstep), HSM, ISP, PHYs, memory controllers | licensed IP outside the RTL, by design | mark; phase 2 defines the RTL side of every interface |
| 128 MB shared SRAM, 512-bit bank ports, SECDED, MBIST | in RTL (bank per tile; macros per `09_synthesis/memories.md`) | keep; macro area from the macro table until a compiler run |
| 1024-bit mesh links, parity, CRC, timeouts, partitions | in RTL and tested | keep |
| Safety mechanisms (ABFT, duplication, lockstep, SECDED, ECC, parity, CRC, watchdog, self-test) | in RTL, fault-injected per mechanism | keep; measured diagnostic coverage in phase 6 |
| FMEDA SPFM 99.9 %, 62 FIT | analytical claims per row, each naming its test | FMEDA v2 from the fault campaign (phase 6) |
| YOLOv8-m 1.90 ms at 1280×736, 0.99 ms at 640² | performance model calibrated with the RTL's endpoint rates | measured on the simulated mesh for a layer group (phase 8) |

## 1. Streams, steps, deliverables (the compressed plan of 12 Oct)

The nine phases of the 11 Oct plan are kept as the design sections below (§2 sparsity, §3 vector unit, §4 chip top, §5 synthesis,
§6 formal and faults); their delivery is regrouped into four streams that run at the same time. Done steps are marked.

| Stream / step | Drop | What lands | Proof |
| --- | --- | --- | --- |
| **A1** timing fixes | **0.41 — done** | every path the 0.40 synthesis named: the NIC's beat CRC in matrix form (4,723 → 733 ps) and `prow` as a memory leaf (−65,536 flops); requant in three stages (3,039 → 934 ps); the ABFT sum in two stages with `err_valid` (1,447 → ~770 ps); the router's one-hot arbitration with parity and route stored at FIFO entry (2,571 → 735 ps), the grant order unchanged cycle for cycle; the packer's link side registered; the activation, weight and accumulator arrays read synchronously (port B — what an SRAM macro does) with an incremental feeder address generator and a two-cycle forwarded accumulate (the core 3,430 → 1,850 ps, ~1.37 ns with the descriptor constraints); the descriptor fields as 4-cycle multicycle paths in the unit and tile SDCs; the sequencer's cycle counts re-derived | PRs #1–#3 green (runs 156, 157, 161); the merged main's run; spec §9 |
| **D1** formal job | **0.41 — done** | `formal` CI job: SymbiYosys from the pinned OSS CAD Suite, nine full proofs (ecc39 SECDED for all data and all single / double flips; `crc16_beat` equal to a frozen serial reference at BW 4 and 16; packer → unpacker round trip word-exact and in order; router FIFO / XY / no-invention / one-token / fairness invariants; the two lockstep pairs equal from reset; requant table parity; the ABFT verdict), 73 s per run, mutation-checked (19 planted bugs caught) | PR #4 green (run 160); `ci-logs-formal` |
| **A2** two libraries, a period sweep, routed blocks | **0.42 — done** (A2a PR #8, A2b PR #5) | Nangate45 as the second data point; the minimum period of every unit on both libraries and the tile's Fmax on the proxy (= the PE's product stage unless a unit still sits above it: the packer's merge decision moved out of the input cycle, the NIC's writeback-mask path at 2.3 ns and its drain-FIFO head registered, the router's input parity against the link budget); OpenROAD-flow-scripts place-and-route of the 16×8 core on ASAP7: utilization, routed timing, congestion; the duplicated requant judged against a residue-checked single copy (2 × 127 k cells) | the `synth` report with two libraries; a `pnr` job or artefact with the routed figures; spec §9 |
| **A3** the last timing items, the tile's units routed, power from activity, the floorplan sketch | 0.43 | the three units A2a's sweep leaves at 1,250 ps (`bank_bist_wrap`, `prog_mem`, `core_seq`) fixed so the tile is clean at 1,250 ps = 800 MHz on the proxy; the check PE's wider product at the data PE's stage; the tile's remaining units routed through the `pnr` job and the 32×32 core placed and globally routed (detailed routing of 1.5 M cells exceeds the runner); power from switching activity recorded by the cocotb runs (VCD / SAIF); a chip-level floorplan sketch | §9 physical rows from routed blocks; the sweep verdict |
| **B1** the chip top | **0.42 — done** (PR #6) | `neo_top` (§4): edge bridges NoC ↔ AXI4, the AXI4-Lite control plane with address decode to every tile's register bus, command queue and doorbells, interrupt and error aggregation into the island's interface, boot and tile-enable registers (the fusing map), clock domains with documented CDCs, reset sequencing | a `top` cocotb suite in hdl-sim: bank fill through a bridge, a layer run across tiles started through the queue, results back, an error visible in the aggregated vector |
| **B2** power and product mechanics | 0.43 | architectural clock gating per block, tile power-gating hooks, telemetry counters behind the host registers, per-partition NoC priority, dual-tile lockstep mode | tests per feature; counters validated against the simulators' own cycle counts; the clock-gated activity in A3's power figure |
| **C1** sparsity engine | **0.42 — done** (PR #7) | 2:4 along K (§2): PE pair with index flops, feeder pairs, two check columns, compressed weight format, descriptor bit, validator and packer, sparse golden model | T6 / T7, C11, M20, coverage bins; the area delta from the `synth` job |
| **C2** transformer readiness | 0.43 | the vector unit on the drain path (§3), attention tiling with K as weights, KV cache in banks, INT4 weight-only storage expanded at the weight-buffer load; compiler operators MatMul, Softmax, LayerNorm, GELU / SiLU, residual, transpose-by-DMA | M21 one attention head vs numpy; a ViT block; an LLM layer with INT4 weights |
| **D2** fault campaign → FMEDA v2 | 0.43 | nightly `faults` job (§6): random stuck-at and transient faults in a tile's flops under M1 / C1-class workloads, classified detected / masked / dangerous-undetected → FMEDA v2 with measured diagnostic coverage; the storage behind the R7 / R10 comparators that 0.41's synchronous reads added (the port-B address latches, `w_load_q`, the feeder's `inb_q` / `a0_q`, the accumulate's stage-0 control) either compared or shown covered by ABFT / CRC; gate-level simulation of synthesized blocks; the Icarus 12 clean-up | the job; FMEDA v2 rows citing proofs and measured coverage |
| **D3** software to an evaluable level | 0.44 | ONNX importer (convolution and transformer operators) into `neo_backend.py`, post-training quantization (per-channel INT8, the 2:4 and INT4 paths), a runtime over B1's queues (`neo_host.c` extended), the virtual device (the performance model calibrated with RTL cycles and B2's counters); a YOLOv8-m layer group, a ViT block and an LLM layer end to end on the simulated mesh with accuracy against FP32 | results and cycle counts in the repo; `tool_qual` extended |
| **Freeze** | 0.70 | RTL freeze criteria met: synthesizable at target on the proxy, every suite, proof and campaign green, chip top integrated, FMEDA v2, the integration specification (clocks, resets, interfaces, memories, IP boundaries), the requirement → mechanism → test / proof → coverage traceability matrix generated from the test docstrings and the FMEDA, a tagged release, HANDOFF-5 | the release |

Drops are merge batches: 0.41 = A1 + D1 (12 Oct), 0.42 = A2a + A2b + B1 + C1 (13 Oct), 0.43 = A3 + B2 + C2 + D2 (the round
started 13 Oct), 0.44 = D3 and what the round before leaves, 0.70 the freeze. Dependences: A3 follows A2; B2 follows B1 (the
registers); C2 follows C1 (the feeder pairs) and B1 (banks fed through bridges for the KV cache); D3's runtime follows B1 and B2's
counters, its importer and quantization do not.
Stream A's PE product stage (1,031 ps on the SS proxy) is the one path the design cannot restructure without changing PE_LAT; the
period the proxy supports is read from A2's sweep, and the 2 GHz claim stays a model until a foundry library.

## 2. The sparsity engine (phase 3 design)

- **Mapping.** 2:4 structured sparsity along the input-channel (K) dimension: in every group of 4 consecutive input channels, two
  weights are nonzero. A group maps to a PE *pair* (rows 2g and 2g+1); each PE holds one nonzero weight and a 2-bit index (which of the
  four channels). The array covers 2·ROWS = 64 input channels per pass with the same 1,024 multipliers — the 2×. Dense mode is the
  present behaviour (index path idle).
- **PE (`mac_pe`).** One multiplier as today, an 8-bit 4:1 mux selecting the activation by the index, two index flops loaded with the
  weight through the swap token; the pair's four activation bytes = two from the PE's own row bus and two from the partner row's bus
  (a local wire between adjacent rows). Expected +10 % PE area; phase 1's `synth` job measures it.
- **Feeder (`act_feeder`, `skew_in`).** Twice the activation bandwidth per pass: the buffer already stores entry pairs behind one
  word-wide port (drop 0.32), so sparse mode reads a pair per cycle; the skew delay lines carry two bytes per row in sparse mode.
  Channel tiles are 64 channels in sparse mode (ct_n counts 64-channel tiles; the compiler's activation layout changes accordingly).
- **ABFT.** The check column needs the dense row sums of all four channels of a group — twice the check work. Two check columns with
  fixed indices (column A covers channels 0–1 of each group, B 2–3), the checker summing both; dense mode uses A alone. +32 PEs, ~3 %.
  Check weights stay WCW bits.
- **Weights (`wbuf_mem`, bank layout).** Compressed entry per PE row-pair: 32 columns × (8-bit weight + 2-bit index) = 320 bits plus
  the check weights; ~11 words per entry at 32 columns instead of 9; SECDED lanes and the fetch path unchanged (the entry is just
  longer). The bank holds weights in the compressed format; `fetch_weights` lengths follow WPW_sparse.
- **Control.** A `sparse` bit in the descriptor (cfg flags), the sequencer's run count in 64-channel tiles, the DMA instruction unchanged.
- **Model and compiler.** `systolic_ref` and `direct_conv` in sparse mode (ground truth is dense arithmetic on the pruned weights —
  pruning is training-side and not ours), a 2:4 validator and packer in `neo_backend.py` (rejects a weight tensor that is not 2:4 along
  K), `pack_words` sparse layout, the perf model's K-pass count halved for sparse layers.
- **Tests.** T6 streaming core sparse dataflow bit-exact, T7 a PE fault under sparse mode flagged by the ABFT (both check columns);
  C11 whole convolution on `neo_core` in sparse mode; M20 a sparse program through the DMA and the compiler on the tile; dense tests
  unchanged (the simulated times of HANDOFF-4 §2 must not move); funcov bins for the mode and the index positions.

## 3. The vector unit (phase 4 design)

- **Placement.** Between the accumulator drain (`rd_data[COLS]`, INT32 rows) and the requantizer, per tile; the drain path is already
  a row stream at one row per cycle, so the unit is a row-wide pipeline with a small program per layer (a sequence of up to ~16
  micro-ops loaded through new registers, as the requant tables are).
- **Operations.** Element-wise: add/subtract a bank row (residual), multiply by a per-column scale, clamp; LUT nonlinearities on
  INT16 after a shift (exp for softmax, GELU/SiLU, reciprocal, rsqrt; 256-entry tables with linear interpolation, loaded like the
  requant tables and parity-protected the same way); row reductions: max and sum across the row's columns (a tree over COLS), the
  result broadcast back for the next op (subtract max, multiply by reciprocal of the sum = softmax; mean/variance → rsqrt → scale and
  shift = layer-norm). Two passes over a row are allowed (the row is held in a small row buffer).
- **Attention.** Q·Kᵀ: K is produced by a previous layer into a bank; the compiler emits a weight-layout drain (a transpose by the DMA's
  drain op into entry layout) so the next layer fetches K as weights — the 1×1 convolution path is already a GEMM (M18's k = 1). Softmax
  on the drain, then P·V with V as weights the same way. KV cache = banks (128 MB on die; a cache spills to external memory through the
  bridges of phase 2).
- **INT4 weights.** Stored 4-bit in banks with a per-output-channel scale; expanded to INT8 at the weight-buffer load (a nibble
  unpack in the fetch assembly), the scale applied in the requantizer's multiplier — doubles weight capacity and fetch bandwidth, the
  decode bottleneck; per-group scales along K are a later refinement (partial-sum scaling in the accumulator).
- **Tests.** M21 one attention head (sequence 64, dim 32) on the mesh against numpy; a ViT block (attention + MLP + layer-norm) end to
  end; an LLM layer with INT4 weights; LUT accuracy bounds asserted against float references.

## 4. The chip top (phase 2 design)

- `neo_top`: `tile_mesh` plus **edge bridges** — at chosen edge positions a bridge occupies a local port instead of a tile: it speaks
  the NIC's RDREQ/RDRSP/WRHDR/WRDATA/CRC protocol on the mesh side and AXI4 on the other (one bridge per side for the LPDDR5X
  controllers, one for the host DMA); banks are filled and drained through them with ordinary DMA programs (a tile's FETCH from a bridge
  address, a WRITEBACK to it), so no new tile logic. The protocol document of the bridge's mesh side is written so the same port can
  become a die-to-die link (gen 1.5).
- **Control plane:** AXI4-Lite slave from the host with address decode to every tile's 8-bit register bus (the `host_axil` wrapper
  generalized), a command queue with doorbells and completion records in a small SRAM, interrupt aggregation (per-tile `err_pin` and
  `done` into maskable vectors), the error vector into the safety island's interface (level and pulse), a global 64-bit timer, boot
  registers (tile-enable map, partition defaults), version/ID registers.
- **Clocks and resets:** core clock domain for the mesh, AXI clock for the host/bridges, synchronizers at the two boundaries
  (documented in an integration spec; a CDC lint with open tools is limited, so the crossings are few and explicit), reset sequencing
  (island first, then control plane, then tiles).
- **Tests:** a `top` cocotb suite — bank fill through a bridge, a layer run across tiles started through the queue, results back through
  a bridge, an error raised in a tile visible in the aggregated vector; the suite in hdl-sim.

## 5. The synthesis job (phase 1 plan)

- Tools: the OSS CAD Suite nightly tarball (YosysHQ releases on GitHub: yosys, the slang frontend plugin, sby, solvers, OpenSTA is
  separate) — installs without apt, runs in the sandbox too; `sv2v` (GitHub release binary) as the fallback front end; OpenROAD-flow-
  scripts' `platforms/asap7` and `platforms/nangate45` for liberty files, or the ORFS docker image for the full flow in phase 7.
- Flow per block: `read_slang` (or sv2v → `read_verilog -sv`), `hierarchy -top`, `synth -top … -flatten` (per block; keep hierarchy for
  the tile), `dfflibmap`/`abc -liberty` with the ASAP7 7.5-track libraries, `stat -liberty`, write the netlist; OpenSTA with
  `constraints.sdc` adapted (2 GHz = 500 ps; ASAP7's timing is a proxy, the ratio to N5A documented) → critical paths, slack
  histogram. Memories black-boxed per `memories.md` (`sram_bank.mem`, `acc_bank`, `wbuf_mem.mem`, `act_feeder.abuf`) with areas from the
  macro table so the tile's area is realistic; `prog_mem` stays flops.
- Checks the job gates on: synthesizable without errors, no latches, no multi-driven nets, `DV_HOOKS = 0` build has zero `dv_*` logic
  (compare cell counts with and without), the PE count equals ROWS × (COLS + 1).
- Expected first findings: the requant pipeline (49-bit product, shift, saturate) and the NIC's beat assembly as the long paths; the
  router's 5×5 arbitration; the ecc39 decode on the bank read path (combinational over 39 bits before the data is used).

**Status after drop 0.40 (the first drop of the phase).** The job exists as planned, with two differences the sandbox forced
and that are better anyway: the design is synthesized as *units* (one Yosys run per module at its silicon parameters, the
child units black-boxed and regrouped by parameters — slang reads one module in seconds where one read of the tile is beyond
8 GB in Yosys's own front end; sv2v is not used), and OpenSTA runs per unit with the children as extracted timing models
(`write_timing_model`), since a flat read of the tile's 2.5 M cells needs more than 6 GB. The memories are black-boxed at
the two leaf modules of `01_rtl/neo_mem.sv` (`neo_mem_sp`, `neo_mem_rf`) rather than at instance paths. The gates run as
planned (plus the hierarchy check); timing is reported, not gated. The expected first findings were right on requant and the
NIC's beat path and wrong on the router (its `% 5` round-robin arithmetic, not the 5×5 arbitration or the parity tree) and
on the ECC decode (inside the budget behind the macro's access time).

**Status after drop 0.41 (A1, the five items, in four PRs by three streams).** Every path the 0.40 run named is restructured and
measured on the same flow (ASAP7 SS, the 500 ps budget; the figures are the PRs' CI runs, spec §9 carries the merged main's):

- **The NIC** (A1a, PR #1): `crc16_beat` as a generated XOR matrix — each CRC bit of the beat padded to 16 words is one XOR tree over
  244–288 of the 528 input bits, then one of 17 precomputed inverse matrices selected by the word count `n` (the serial chain
  multiplied the state by a fixed matrix per unused word; its inverse undoes it) — 4,723 → 733 ps at 3,430 cells, bit-identical to the
  serial form on 69,792 vectors (exhaustive by linearity for a fixed n) under three simulators and by the formal job's equivalence proof;
  `prow` as a `neo_mem_rf` leaf (64 × 1,024 bits, written per column group at `rx_src`, read whole at the check word; no reset needed
  because a row's columns are always written before its check word reads them, a lost flit being caught by the count and the CRC).
  `tile_nic` 7,662 → 2,324 ps and 477,805 → 104,987 cells; the tile 239,071 → 173,535 flops; the next NIC path is the WRDATA writeback
  mask behind the 64:1 `wr_ptr[rx_src]` mux (A2).
- **The arithmetic and the router** (A1b, PR #3): requant in three stages — the 32×16 product, the rounding bias and the zero point
  (shifted up by `shift`, so the add before the shift is exact) reduced as carry-save rows, one adder, then shift / fits-in-8-bits test /
  saturate, ReLU as "zero point when the accumulator is negative" (the same result without a compare) — 3,039 → 934 ps and 192,872 →
  127,456 cells, bit-identical on 246 k random rows and the new boundary test Q4; the ABFT checker in two stages with no carry-propagate
  adder at all (carry-save rows, then a carry-free equality test S + C == K ⇔ S ^ C ^ K == G << 1 with G the implied carries), the
  verdict two cycles after its row with a new `err_valid`; the router's arbitration as a one-hot rotating pointer with the flit's parity
  and route stored at FIFO entry, the grant order identical cycle for cycle (proved by neosim N3 against the 0.40 router over 9,365
  grants and by every mesh end time) — 2,571 → 735 ps; the packer's link side registered (one cycle per hop on the 32×32 builds); the
  descriptor fields `cfg_*` as 4-cycle multicycle paths in the unit and tile SDCs (`cfg_ct` / `cfg_ky` / `cfg_kx` excluded — the sequencer
  changes them one cycle before a run).
- **The arrays' reads** (A1c, PR #2): the feeder's address generator incremental (adds only in the per-pixel path, the run constants
  formed once per run in two multiplier-free cycles from radix-4 digit selects), the activation, weight and accumulator arrays read on
  the leaves' synchronous port B (an SRAM macro's read; port A tied off, so the three macros are 1-write-1-read synchronous), the
  accumulate as a two-cycle read-modify-write with a one-stage forward (back-to-back rows to the same address proved by the new cocotb C4
  / neosim K3), the sequencer's constants re-derived (drain wait LAT + 4, one extra reduce-to-drain cycle); `neo_core` 3,430 → 1,850 ps
  (1,366 ps of negative slack left under the descriptor constraints, all of it A1b's paths before their merge); the end times move by
  +2 cycles per descriptor and the tile's 32×32 chains by +1 per registered hop (cocotb tile 46,282.52 → 46,298.02 ns before the packer).
- **What A2 carries forward:** the packer's merge decision still in the input cycle (−263 ps at the unit level); the router's input-side
  parity (≈545 ps of setup on the mesh input pins — inside a 1 ns period with the link's 0.4 budget, outside the 500 ps proxy budget);
  the NIC's writeback mask (2.3 ns) and the drain FIFO head; the PE's product stage at 1,031 ps, which sets the proxy's period and
  which the period sweep on both libraries measures; the duplicated requant (2 × 127 k cells) against a residue-checked single copy.

## 6. Formal and fault campaign (phase 6 plan)

**Status after drop 0.41:** the `formal` job exists (D1, PR #4; `02_dv/formal/README.md`): nine full proofs on every push, 73 s, the properties below as written plus the lockstep pairs and the two-stage ABFT verdict; crc16 equivalence needed BDDs (ABC `collapse` + `pdr`), not SAT — an all-XOR miter defeats the SMT engines. The fault campaign is D2 (0.48).

- SymbiYosys: `mode bmc` and `prove` per block with the block's own properties: ecc39 (for all 2^32 data: encode → any single flip →
  decode gives the data and `ce`; any double flip gives `ue`; the overall-parity bit cases), crc16 (linearity and the detection of any
  single burst ≤ 16 bits), link packer/unpacker (words in = words out, order kept, for every WPF/BW pairing), router (a flit entering with
  good parity leaves exactly once at the XY port; bad parity never leaves and always sets the sticky), lockstep comparators (any
  divergence of the duplicated engines raises the flag within one cycle), requant table parity (any single-bit table corruption flags
  on use). Properties live beside the SVA bindings; the `formal` job runs them on every push (minutes each).
- Fault campaign: a Verilator build of the tile with `--public-flat-rw`, a Python driver that picks N random flop bits (from the
  model's symbol table), forces a stuck-at or a single-cycle flip at a random cycle of an M1-class run, and classifies the outcome:
  detected (which cause bit, how many cycles later), masked (results bit-exact, no cause), dangerous undetected (results wrong, no
  cause within the FTTI budget). Thousands of faults per nightly run on the runner (each run ~5 s at 16×8); per-block diagnostic
  coverage → FMEDA v2 (`05_safety/build_fmeda.py` takes the campaign's CSV); dangerous-undetected faults become RTL items.

## 7. What makes it competitive now (technical reading)

- Perception stacks in 2026 are BEV- and transformer-based, the newest are end-to-end driving policies, and in-cabin language models
  are a line item: phases 3 and 4 (real sparsity, the vector unit with softmax and layer-norm, attention tiling, INT4 weights) move Neo
  from a convolution engine to a transformer-capable one. The 128 MB of on-chip SRAM is a strength there (BEV features and KV caches stay
  on die) once phase 2's bridges feed it.
- Mixed criticality (per-partition NoC priority, isolation already there) and ASIL-D compute on chosen layers (dual-tile lockstep) are
  what automotive integrators ask for beyond TOPS; telemetry counters and the virtual device are what they evaluate with before silicon.
- Deterministic latency: descriptor-granular scheduling, bounded fetch timeouts and the measured M14 contention figure already give a
  latency story; the telemetry makes it demonstrable per layer.
- Gen-2 studies (not before handoff): FP8 (E4M3/E5M2) and BF16 arithmetic in the PE — changes the multiplier and accumulator, decide
  with phase 1's area numbers; die-to-die links on the mesh edges for the two-die part (the bridge protocol of phase 2 is written to
  become one); a sparsity-aware activation path (dynamic sparsity) after 2:4 is in.
- Not reachable with these tools: foundry libraries and sign-off STA/IR/EM, scan insertion and ATPG, AEC-Q qualification, silicon.
  Optional with a board (~ECP5-85k or Artix-7 200T): the open FPGA flow (Yosys + nextpnr / F4PGA) for a 16×8 tile bitstream; the
  ZCU102 skeleton in `10_fpga/` needs Vivado.
