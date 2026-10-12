# AstraCore Neo — roadmap from drop 0.38 to handoff (11 Oct 2026; §5 status 12 Oct, drop 0.40)

Scope: what can be done with the assistant, the GitHub runners (ubuntu-latest: 4 cores, 16 GB, 6 h per job, apt/pip/GitHub reachable)
and the sandbox (2 cores, 8 GB, GitHub reachable, apt/PyPI not), using open-source tools only, taking the design from "proven in
simulation at full scale" to a package a silicon team can take to a PDK. Drop numbers are the plan; a phase that takes an extra drop
shifts the rest. Every drop keeps the rules of HANDOFF-4 §1 (fixNN.zip, README, CI proof, Doc section, drop log).

## 0. The claims ledger (what the RTL backs today)

| Claim in the spec / datasheet | Status | Action |
| --- | --- | --- |
| 262 TOPS dense INT8, 65,536 MACs, 64 tiles of 32×32 | in RTL, simulated at full 64-tile scale (run 133) | keep |
| 524 TOPS with 2:4 structured sparsity | **not in RTL** (no sparse PE, index path, compressed weights, compiler packer, tests) | mark planned (0.39); implement (phase 3) |
| INT4 LLM figures (8B decode 13 ms/token, prefill 52 ms) | not in RTL: INT8 datapath only; the figures assume external memory bandwidth no RTL endpoint provides | strike (0.39); INT4 weight-only storage returns in phase 4 with measured numbers |
| 2.0 GHz worst case / 2.4 GHz typical | feasibility model | synthesized critical paths on the open proxy PDK (phase 1) |
| ~56 W typical, TDP SKUs 65 / 30 / 15 W, always-on island ≤ 2 W | model; no clock gating, power gating or DVFS logic in RTL | mark pending; phase 5 adds the mechanisms, phase 7 the activity-based power |
| Family 262 / 196 / 131 / 65 by fusing tiles at test | partition isolation exists (`REG_PARTITION`, iso error); no fuse/disable map | mark pending; phase 2 adds tile-enable registers, the fuse source is an IP item |
| LBIST | tool-inserted at synthesis; hooks (SELFTEST, FTTI-windowed watchdog) exist | mark as integration item |
| PCIe Gen5 ×8 host, ASIL-D safety island (2 × R52+ lockstep), HSM, ISP, PHYs, memory controllers | licensed IP outside the RTL, by design | mark; phase 2 defines the RTL side of every interface |
| 128 MB shared SRAM, 512-bit bank ports, SECDED, MBIST | in RTL (bank per tile; macros per `09_synthesis/memories.md`) | keep; macro area from the macro table until a compiler run |
| 1024-bit mesh links, parity, CRC, timeouts, partitions | in RTL and tested | keep |
| Safety mechanisms (ABFT, duplication, lockstep, SECDED, ECC, parity, CRC, watchdog, self-test) | in RTL, fault-injected per mechanism | keep; measured diagnostic coverage in phase 6 |
| FMEDA SPFM 99.9 %, 62 FIT | analytical claims per row, each naming its test | FMEDA v2 from the fault campaign (phase 6) |
| YOLOv8-m 1.90 ms at 1280×736, 0.99 ms at 640² | performance model calibrated with the RTL's endpoint rates | measured on the simulated mesh for a layer group (phase 8) |

## 1. Phases, drops, deliverables

| Phase | Drops | What lands | Proof |
| --- | --- | --- | --- |
| 0 Truth in the spec | 0.39 | The ledger above in `spec_sheet_v2_1.md` (a status column on every headline row, §2 and §9 reworded), `08_ip_product/neo_npu_ip_datasheet.md` aligned, `00_overview/ROADMAP.md`; HANDOFF-4 | the spec |
| 1 Synthesis and timing on an open PDK | 0.40–0.42 | `synth` job + `07_ci/synth/`: Yosys (slang front end or sv2v), OpenSTA, ASAP7 and Nangate45 libraries from OpenROAD-flow-scripts; memories black-boxed with the macro table's areas; per block (PE, 16×8 core, router, NIC, bank wrapper, ecc39, requant, DMA, host_if), then the 32×32 core, then the tile; reports: cells, area shares, critical paths vs 2 GHz, `DV_HOOKS = 0` leaves no logic, synthesis lint (latches, multi-drivers); RTL timing fixes where the paths say so | numbers in spec §9 ("synthesized, ASAP7 proxy") |
| 2 Chip top | 0.43–0.45 | `neo_top`: the mesh plus edge endpoints bridging the NoC to AXI4 (LPDDR5X controller and host DMA fill the banks through them; a bridge is an edge tile position's local port, reusing the NIC's request/response protocol), a global control plane (address decode to tiles' register buses, command queue and doorbells, interrupt and error aggregation into the safety island's interface, a global timer), boot and tile-enable registers (the fusing map), clock domains (host/AXI vs core) with documented CDCs and synchronizers, reset sequencing | a `top` cocotb suite: fill a bank through a bridge, run a layer across tiles, read results back through a bridge and the host; the suite in CI |
| 3 Sparsity engine | 0.46–0.49 | 2:4 along K (design in §2 below); the compressed weight format; descriptor bit; compiler validator and packer; sparse golden model | T6/T7 on the streaming core, C11 on the core, M20 through DMA and compiler on the tile, coverage bins; area delta from the `synth` job |
| 4 Transformer readiness | 0.50–0.55 | A per-tile vector unit on the drain path (design in §3), attention tiling with K from a bank as weights, KV cache in banks, INT4 weight-only storage expanded at the weight-buffer load; compiler operators MatMul, Softmax, LayerNorm, GELU/SiLU, residual, transpose-by-DMA | M21 one attention head on the mesh vs numpy; a ViT block end to end; an LLM layer with INT4 weights; coverage bins |
| 5 Power and product mechanics | 0.56–0.58 | Architectural clock gating per core, NIC, router (idle detection, `clk_en` per block for synthesis to turn into ICGs — the Genus script already enables ICG insertion); tile power-gating hooks (isolation and retention plan, `tile_enable`); telemetry counters per tile (MAC utilization, link and bank stall cycles, drain cycles, fetch wait) behind the host registers; per-partition bandwidth and priority in the NoC for mixed criticality; dual-tile lockstep mode (two tiles run the same descriptor, the owner compares the CRCs of both drains, a mismatch is a sticky cause) | tests per feature; counters validated against the simulators' own cycle counts |
| 6 Verification to sign-off quality | 0.59–0.63 | `formal` job: SymbiYosys proofs (ecc39 exhaustive SECDED, crc16, packer/unpacker word conservation, router no-loss/no-duplication and bad-parity-always-dropped, lockstep and duplication comparators, requant table parity, DMA engine lockstep) with `sva/bind_all.sv` bound where Yosys reads it; nightly `faults` job: random stuck-at and transient faults in a tile's flops under M1/C1-class workloads, classified detected-by / masked / dangerous-undetected → FMEDA v2 with measured diagnostic coverage, the undetected list driving RTL changes (the duplicate engines' remaining outputs into the lockstep comparators); gate-level simulation of synthesized blocks with the cell models; the Icarus clean-up (constant tables as `assign`/`localparam`, a gated step once green); hierarchical or line-only `tile32_cov`; toggle closure; UVM kept for Xcelium | the jobs; FMEDA v2 rows cite proofs and measured coverage |
| 7 Physical proxy | 0.64–0.66 | OpenROAD on ASAP7 (the ORFS docker image runs on the runner): the 16×8 then 32×32 core placed and routed, the tile floorplanned as the hard macro the mesh assumes (four link ports, the bank macro, the local control), routed timing and congestion, power from switching activity recorded by the cocotb runs (VCD/SAIF), a chip-level floorplan sketch (64 macros, bridges, island, IO ring) | §9 physical rows from routed blocks |
| 8 Software to an evaluable level | 0.50 onward, in parallel | ONNX importer (convolution and transformer operators) feeding `neo_backend.py`, post-training quantization with per-channel INT8 and the 2:4 / INT4 paths, a runtime over the chip top's queues (`04_software/neo_host.c` extended), a virtual device: the performance model calibrated with RTL cycles and the telemetry counters, so a network is profiled before silicon; reference networks end to end on the simulated mesh (a YOLOv8-m layer group, a BEV/ViT block, an LLM layer) with accuracy against FP32 | results and cycle counts in the repo; `tool_qual` extended |
| 9 Handoff | 0.70 | RTL freeze criteria met: synthesizable at target on the proxy PDK, every suite, proof and campaign green, chip top integrated, FMEDA v2, integration specification (clocks, resets, interfaces, memories, IP boundaries), requirement → mechanism → test/proof → coverage traceability matrix generated from the test docstrings and the FMEDA, tagged release, HANDOFF-5 | the release |

Parallelism: phase 8 runs beside 3–7; phase 6's formal proofs can start any time after phase 1's front end exists (the same Yosys
read of the RTL serves both).

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
on the ECC decode (inside the budget behind the macro's access time). What drops 0.41 and 0.42 take up, in the order of the
measured damage (ASAP7 SS figures in spec §9):

- **0.41 — the NIC and the arrays' read ports.** A parallel 16-word beat CRC (a 16-input XOR matrix per CRC bit, two
  pipeline stages at most) on the transmit side (`tx_crc`, 7.7 ns) and the receive side (`crc_acc`, 5.5 ns), the beat
  path registered where the bank's read data and the core's drain data enter it; `prow`, the per-source partial-sum row
  under assembly (64 × 1,024 bits = 65,536 flops, 91 % of the NIC's flops), as a `neo_mem_rf` leaf or sized to the sources
  that can be mid-row at once; the activation buffer's and the weight buffer's reads registered with the address a stage
  earlier (the feeder's channel-tile-to-array path, 3.43 ns; `memories.md`), the accumulator's read-modify-write across two
  cycles (port A registered), the drain FIFO's head read registered; the sequencer's pipeline adjusted and the cocotb /
  neosim figures re-baselined (the latency changes).
- **0.42 — the arithmetic stages, the router, the constraints.** Requant in three stages (product | round and shift, the
  rounding bias as a carry-in after the shift | zero point, ReLU, saturate), its 32×16 product pipelined if the proxy still
  shows it over 1.5 × the PE's stage; the accumulator's 33-operand ABFT sum as a two-stage tree (the flag is sticky, so a
  stage costs nothing functionally); the router's grant from a one-hot rotating mask (no modulo) with the parity bit computed
  at FIFO entry; the link packer's merge registered; the descriptor fields (`cfg_*`) declared static during a run
  (`set_multicycle_path` / case constraints in `constraints.sdc` and the block SDCs) so the input paths stop masking the
  register-to-register ones; then the Nangate45 run as the second data point and the PE's product stage judged on both
  (the one path the design cannot restructure without changing PE_LAT).

## 6. Formal and fault campaign (phase 6 plan)

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
