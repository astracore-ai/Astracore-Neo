# HANDOFF-5 — AstraCore Neo at drop 0.43 (20 October 2026)

The state of the design, what was achieved between drops 0.38 and 0.43, what is proven and how, what is open, and the
plan to the freeze. Written for the Claude Code session that continues the work; the human owner reads it the same way.
`CLAUDE.md` is the operating manual for the repository; `00_overview/ROADMAP.md` the plan; `00_overview/drop_log.md`
the proof of every drop; `00_overview/spec_sheet_v2_1.md` the specification as implemented with its claims ledger.

## 1. Where things stand

- **Repository:** `astracore-ai/Astracore-Neo`, branch `main` at the commit of this file (the previous one, `a70ebdb`,
  is drop 0.43's integration; drops are merge batches of green pull requests). The twelve stream PRs #1–#12 are merged
  and closed. The working tree holds nothing else of value: every branch's work is on `main`.
- **CI:** seven jobs (`regress.yml`): `python-regress`, `hdl-sim`, `hdl-mesh8`, `synth` (ASAP7 + Nangate45, the period
  sweep, the gate-level simulations), `formal`, and on schedule / dispatch `pnr` (weekly) and `faults` (nightly). On the
  last push to `main` (run 184, 13 Oct) every simulation job, the formal job and the pnr blocks `mac_pe` /
  `c16_systolic_array` were green; `synth` was red at its last step only — the gate-level simulation of `requant` did not
  elaborate (`neo_icg` missing from the GLS's RTL-side file list after B2's clock gating), fixed in this commit
  (`07_ci/synth/gls.py`); the synthesis and both libraries' gates had passed. The nightly fault campaign has run green
  every night since (14–20 Oct). The push of this commit starts the next full run.
- **Figures that describe the design today** (ASAP7 7.5-track RVT SS 0.63 V / 100 °C, an academic 7 nm proxy; the ratios,
  paths and shares are the design's, the picoseconds and square microns the library's):
  - The tile synthesizes clean at **1,250 ps = 800 MHz** for every unit outside the PE-limited ones (drop 0.43 verdict);
    the PE's own product stage is 1,095 ps (1,031 before the 2:4 byte select); on the merged 0.43 tree the sweep reads
    1,500 ps again because of one NIC endpoint and the chip-top bridges (A4's first item). Nangate45 (typical) is not clean
    at any swept period and serves as a second opinion.
  - Area: the tile 1.221 mm² synthesized (cells 0.230, macro model 0.991 with the 2 MB bank 0.86), 1.233 mm² as the sum of
    routed units (ratio 1.010); the die sketch 152 mm² on the proxy against the spec's ~210 mm² N5A model (not a
    confirmation: the model's tile is a fifth of its die, the proxy tile 2.6 × the model's).
  - Power (routed, recorded activity, 800 MHz): the 16 × 8 core 90 mW at 56.7 % MAC utilization, 1.10 pJ per MAC in the
    array → the 32 × 32 core ≈ 485 mW, the tile ≈ 575 mW.
  - Safety: measured diagnostic coverage 92.5 % (core flops) / 97.7 % (tile flops); **FMEDA v2 chip PMHF 1,931 FIT against
    the 100 FIT ASIL-B(D) target — fails**, 28.5 of the core's 29.6 FIT from the activation buffer, which has no error
    code (FMEDA v1 had credited it with SECDED). This is the first item of the next round (S1).
  - Throughput mechanisms: 2:4 sparsity 1.93 × on a sparse convolution; an attention head (53,838 cycles), a ViT block
    (70,561) and an LLM layer with INT4 weights (82,760) run bit-exact on the 2 × 2 mesh of 16 × 8 cores; the perf model
    predicts them within 2–3 %; measured MAC utilization on the small test layers 5–28 %.

## 2. What was achieved, drop by drop (12–14 Oct; every figure is in the drop log with its CI run)

| Drop | Streams (PR) | What landed | Key measurements |
| --- | --- | --- | --- |
| 0.40 (11 Oct) | fix36–38 | the open-PDK synthesis job (Yosys + slang, OpenSTA per unit with the children as extracted timing models, ASAP7), the memory leaves, the first numbers | tile 1.283 mm², 2.31 M cells; five paths over the budget by design: requant 3,039 ps, NIC 7,662, router 2,571, accumulator 1,732, feeder 3.43 ns |
| 0.41 (12 Oct) | A1a #1, A1b #3, A1c #2, D1 #4 | the five paths restructured: the beat CRC in generated matrix form (bit-identical, proved), `prow` as a leaf, requant in three stages, the ABFT checker in two stages with a carry-free compare, one-hot router arbitration with parity / route stored at FIFO entry (grant order unchanged cycle for cycle), the packer's link side registered, the arrays read synchronously like SRAM macros with an incremental feeder and a forwarded two-cycle accumulate, the descriptor as multicycle paths; the `formal` job with nine (now ten) full proofs | NIC 7,662 → 2,324 ps, requant 3,039 → 934, router 2,571 → 735, `neo_core` 3,430 → 1,331; −512 k cells, −53 k flops |
| 0.42 (13 Oct) | A2a #8, A2b #5, B1 #6, C1 #7 | Nangate45 as a second library, the period sweep with a verdict, NIC / packer / unpacker paths; OpenROAD place-and-route in CI from the synthesized netlists with FakeRAM macros; `neo_top` (bridge-node column, AXI4 memory and host bridges, AXI4-Lite control plane, tile-enable, IRQ / error aggregation, two clock domains with exactly three crossing structures, the `top` suite); the 2:4 sparsity engine (four-byte row buses, PE index select, four check columns, compressed weights, compiler and tests) | NIC 2,324 → 1,172 ps; five blocks routed, logic-bound at 0.92–0.97 of the synthesized period; sparsity 1.93 × at +4.3 % tile area |
| 0.43 (13–14 Oct) | A3 #10, B2 #11, C2 #9, D2 #12 | the tile clean at 1,250 ps (bank wrapper, program memory, sequencer), the check PE at the data PE's stage; 14 units routed, power from recorded activity, the die sketch; clock gating (94 % of flops), power-gating sequencers, telemetry, NoC priority classes (proved), dual-tile lockstep, bridge and command queues, the NIC's self-serve deadlock fixed; the vector unit on the drain, INT4 weights, attention tiling, the transformer compiler, M21 / M23 / M24; the fault campaign and FMEDA v2, the comparator storage covered, gate-level simulation of seven units, Icarus 12 gating | 800 MHz on the proxy; 575 mW per tile; 92.5 / 97.7 % coverage; **1,931 FIT** |

The detail of each item — the mechanism, the measured before / after, what fell short — is in the drop log's rows and
in the stream briefs (`00_overview/streams/`), which were the specifications the agents built from.

## 3. The design in one page (per tile unless said)

- **Core:** 32 × 32 weight-stationary systolic array, 2-stage PE (product | add), double-buffered weights with a swap token,
  4 ABFT check columns (NCHK; 1 = the dense-only core), four-byte activation buses with a per-PE one-hot index (2:4 along
  K in PE pairs), a sliding-window activation feeder with incremental addressing and a duplicated address generator
  (R7), a 1 Mbit activation buffer and a 468 kbit SECDED weight buffer read synchronously, a 540 kbit accumulator with a
  two-cycle forwarded read-modify-write and ABFT at drain, a lockstep sequencer pair (R10), a three-stage requantizer
  pair compared (R10) with parity-checked tables, the vector unit between drain and requantizer (programs of 16 op words,
  LUT nonlinearities, row reductions, a DMA-filled row buffer), telemetry counters, clock gating per block.
- **Tile:** the 2 MB bank as 16 SECDED lanes with March C- MBIST, the network interface (fetch / serve / writeback / partial
  sums over 64 sources, end-to-end CRC-16 per message, the matrix-form beat CRC, a loopback for the tile's own bank, drain
  formats INT32 / INT8 / PSUM, INT4 weight expansion), a 5-port XY router with one-hot arbitration, stored parity / route,
  two priority classes, link packer / unpacker between 16-word beats and 32-word (1,024-bit) link flits, a lockstep DMA
  pair over a SECDED program memory, host registers (descriptor, tables, status, telemetry, vector programs), an error /
  self-test / watchdog module, isolation and a separate router reset for fused tiles.
- **Chip top:** NX × NY tiles plus a column of bridge nodes; memory bridges (AXI4 master, bursts split at 4 KB, a read
  queue of 4, write combining) and a host bridge (AXI4 slave over the banks); the control plane (AXI4-Lite windows per
  tile, globals, the tile-enable / power map and sequencers, IRQ status / mask, a 64-bit timer, lockstep pairs, the command
  queue with completion records); `cdc_fifo` / `cdc_sync` / `cdc_bits` as the only crossings; `aresetn` before `rst_n`.
- **Software:** the compiler (lowering, K-split, bank allocation, 2:4 compression, the transformer operators, perf model
  v3 fed by telemetry), the C driver equal to the Python backend transaction for transaction, the neosim executable spec.

## 4. How it is verified (what "proven" means here)

- **Simulation:** cocotb on Verilator — `core` (16 × 8 and 32 × 32), `tile` (2 × 2 mesh of 16 × 8: M1–M25), `tile_sp`,
  `tile32` (the silicon depths), `mesh8` (the 64-tile silicon mesh), `top` (two clock ratios), `two_layer`; the SV
  testbenches under Verilator and Icarus 12 / 14; neosim over the whole convolutions and K-splits; every safety flag
  raised by a fault-injection test; functional coverage 86 / 86 bins, line / branch / toggle reported per build.
- **Formal:** ten full proofs on every push — ECC SECDED exhaustive, the CRC equal to its frozen serial form at BW 4 and 16,
  the packer → unpacker stream and parity, the router's invariants and the priority bound, both lockstep pairs, requant
  parity, the ABFT verdict (`02_dv/formal/README.md`).
- **Synthesis and timing:** every unit of the tile and the chip top on two libraries with the sweep's verdict; gates: every
  unit synthesizes, no latch, no multiply-driven net, hierarchy consistent, PE counts, DV_HOOKS = 0 clean, every clock gate
  mapped to the library's ICG, every timing run complete.
- **Physical:** 15 blocks placed and routed in OpenROAD with 0 DRC; power from recorded activity; the 32 × 32 core
  floorplanned and placed (not routed on the 16 GB runner).
- **Gate level:** seven synthesized units simulated against the RTL (0 mismatches over 2,992 cycles each).
- **Faults:** a nightly campaign of 20 k + 5 k injected faults per build with per-block coverage and latency; FMEDA v2 from it.

## 5. Open items, in priority order (the briefs in `00_overview/streams/`)

1. **S1 — safety closure (must, before the freeze):** SECDED on the activation buffer; checks on the index / first delay
   lines, the `skew_in` pipes, the NIC's drain-FIFO register, the MBIST state; the campaign again; FMEDA v3 meeting the
   100 FIT target or the claim restated to the measured figure.
2. **A4 — timing and physical leftovers:** the merged tree back at 1,250 ps (one NIC endpoint; `neo_core`'s requant input
   select from C2; the bridges at ~1,500 ps; the PE's byte select); `tile_nic` −891 ps and the bank wrapper −1.76 ns after
   routing; the 32 × 32 core as quadrant macros or on a larger runner; the GLS of gated units; a `proxy_to_target.md`
   reconciling the spec's 2.0 GHz / 210 mm² / 56 W with the proxy's 800 MHz / 152 mm² / 37 W of tiles.
3. **D3 — software to an evaluable level:** ONNX importer, post-training quantization, the runtime over the command queue,
   the virtual device, a YOLOv8-m layer group measured on the simulated mesh.
4. **F1 — the freeze (0.70):** criteria, the integration specification, the traceability matrix, the tagged release, HANDOFF-6.
5. Smaller, known: B2's reorder buffer for per-burst AXI IDs, retention and a power-good input, a mesh-only reset controller;
   C2's `vec_unit` at 1,082 ps against the array's 1,031 (split adders cost a cycle per op); the sequencer's `cfg_m` never
   drains all ACC_ROWS rows (plans use ACC_ROWS − 1); neosim M6 in `run_tiles.py` references a missing attribute; Nangate45's
   verdict (the bank at 1,509 ps); hdl-sim at ~140 min of its 200-min limit (the coverage builds in series cost 80 min).

Realistic effort from here: one round of three parallel streams (S1, A4, D3) ≈ one to two long sessions including the
merge and the documents, then the freeze ≈ one session. Each round's proof is a CI run of ~2.5 hours.

## 6. Working method (what worked, what to keep)

- A stream = a brief with the *why* in measured figures, the design decisions made, the files owned, the tests required
  and the report asked for; one agent per stream on its own branch; the PR's CI run as the proof; the integrator merges the
  green PRs as one drop and writes the documents. Twelve of twelve streams came back green; the integration faults were
  mine (the chip top's NCHK in 0.42, the GLS's clock gate in 0.43), both caught by the merged main's run.
- Conflicts in shared files are the integrator's; a conflict whose semantics belong to a stream is handed back to that
  stream on a branch (C2's merge was done that way).
- Always read a run before building on it; the figures in the drop log come from the CI logs (`ci-logs-<job>` by commit),
  never from memory.
- The documents are part of the drop: the drop-log row (with the run numbers), the spec's affected sections and the claims
  ledger, the roadmap's status, the Doc section. A documents-only push runs no CI.
- Sandbox lessons that cost time: job-log download blocked through the proxy (read the log branches); `gh pr create`
  blocked (use the REST API); the worktree guard refusing compound shell commands (script files); `pkill -f` killing the
  agent's own shell; registers without reset carrying faults between campaign runs (restore every flop); the scratchpad
  shared between agents (prefix file names); never publish files over 100 MB to the log branches; symlinked `sim/` breaks
  `../compiler` imports (a real directory of symlinks).

## 7. The project's Claude Doc

The running log of every drop since the beginning is the Claude Doc `7a5e2e87-f701-4bba-8a92-ab2a63af4df3` ("Drop 0.xx"
sections, one per drop, with the measured results and the run numbers); the owner reads it. Continue it: one section per
merged drop, written at the merge, the same facts as the drop-log row in shorter form.

## 8. Conventions recap

Drop numbers are merge batches, in merge order (0.41 = A1 + D1, 0.42 = A2a + A2b + B1 + C1, 0.43 = A3 + B2 + C2 + D2,
0.44 = S1 + A4 + D3, 0.70 = the freeze). Headers say the drop that introduced a mechanism; provisional labels are renamed
at the merge. Commit messages carry the read-out a README used to carry (no fixNN.zip since 0.41). The two workflow copies
are byte-identical. The fetch-timeout constants stay. Only technical deliverables live in the repository.
