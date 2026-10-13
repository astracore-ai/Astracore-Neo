# AstraCore Neo — Spec Sheet v2.1 (as implemented, drop 0.41, 2026-10-13)

Every value below is read from the code in the package: RTL parameters and encodings from `01_rtl/`, the
compiler and backend from `03_models/compiler/`, the driver from `04_software/`, model numbers from
`03_models/` reports, and verification status from `logs/` and the CI log branches. Where the silicon configuration
differs from the configuration that was simulated, both are given and the delta is named in section 10.

**Status tags (drop 0.39).** Every headline row carries one of four tags, so that a reader can tell what the RTL in
this package backs from what is a plan, a licensed block or a model figure:

| Tag | Meaning |
| --- | --- |
| **RTL-proven** | in `01_rtl/` and exercised by a named test at the stated scale — the 2×2 meshes of 16×8 and 32×32 cores and the 8×8 silicon mesh of 32×32 cores under cocotb on Verilator in CI (run 133, every job green), neosim, and the Verilator SV testbenches |
| **planned** | not in the RTL; a design and a phase exist in `ROADMAP.md` (the phase is given) — the figure is a target of that phase, not a capability of this package |
| **IP** | a licensed block outside the RTL by design (PHYs, controllers, the safety island's CPUs, the PCIe host interface, DFT insertion); phase 2 defines the RTL side of each interface |
| **model** | a figure from the feasibility or performance model, or an analytical safety claim — not measured; the row names what replaces it |

## 0. Claims ledger (drop 0.39)

What the spec and the datasheet claim, against what the RTL backs today. The ledger is kept with the spec; a row moves
when its phase lands (the drop log records the move).

| Claim | Status | What backs it / what replaces it |
| --- | --- | --- |
| 262 TOPS dense INT8: 65,536 MACs, 64 tiles of 32×32 | **RTL-proven** (the MAC count and the mesh) × **model** (the 2.0 GHz clock) | the silicon mesh — 8×8 tiles of 32×32 cores, silicon depths, 1024-bit links — simulated at full scale in CI (hdl-mesh8, run 133, 11 of 11); the clock is a feasibility figure until phase 1 |
| 524 TOPS with 2:4 structured sparsity | **planned** (phase 3, drops 0.46–0.49) | no sparse PE, index path, compressed weight format, compiler packer or test exists in this package; the design is `ROADMAP.md` §2 (2:4 along K on PE pairs). The figure is struck from every headline until the engine is in the RTL and measured |
| INT4 LLM figures (8B decode 13 ms/token, prefill 512 tokens 52 ms) | **struck** (drop 0.39); INT4 weight-only storage **planned** (phase 4) | the datapath is INT8 only and no RTL endpoint provides the external-memory bandwidth the figures assumed; the figures return as measured numbers when phase 4's INT4 path and phase 2's bridges exist |
| 2.0 GHz worst case / 2.4 GHz typical | **model**; the paths **synthesized, ASAP7 proxy** since drop 0.40, the five design paths closed in 0.41 (stream A continues: the period sweep on two libraries, routed blocks) | the PE's product stage is 1,031 ps on ASAP7 SS (the proxy's ratio to N5A unknown — the number to watch); after drop 0.41 requant is 934 ps, the ABFT checker ~770, the router 735, the feeder 898, `neo_core` 1,331, and the one path still above the array is the NIC's writeback mask at 2,324 ps (§9; A2 takes it); the proxy's Fmax is read from the period sweep (A2), the N5A figure stays a model until a foundry library |
| ~56 W typical, ~80 W peak, TDP SKUs 65 / 30 / 15 W, always-on island ≤ 2 W | **model**; the mechanisms **planned** (phase 5), activity-based power (phase 7) | no clock gating, power gating or DVFS logic is in the RTL; phase 5 adds `clk_en` per block, tile power-gating hooks and telemetry; phase 7 computes power from recorded switching activity |
| Family 262 / 196 / 131 / 65 by fusing tiles at test | **planned** (phase 2: tile-enable registers); the fuse source **IP** | partition isolation exists (`REG_PARTITION`, the iso cause) and the compiler's tile-exclusion mask places around excluded tiles; no fuse or disable map exists in the RTL |
| LBIST | **IP** (tool-inserted at synthesis; the licensee's DFT flow); the hooks **RTL-proven** | SELFTEST register and the FTTI-windowed watchdog (M8); scan insertion and ATPG are outside the open tools |
| PCIe Gen5 ×8 host, ASIL-D safety island (2 × R52+ lockstep), HSM, ISP, PHYs, memory controllers | **IP** | outside the RTL by design; phase 2's chip top defines the RTL side of every interface (AXI bridges, the control plane, the error vector into the island) |
| 128 MB shared SRAM, 512-bit bank ports, SECDED, MBIST | **RTL-proven** at the silicon depth (2 MB banks on tile32 and mesh8); the macro area **model** | bank per tile with 16 SECDED lanes and March C− over rows (M5, M11, M17); the arrays are the `neo_mem_sp` / `neo_mem_rf` leaves (drop 0.40, `09_synthesis/memories.md`), black-boxed by the synthesis job and priced by its macro model (the bank 0.860 mm² on the proxy, 67 % of the tile) until a compiler run |
| 1024-bit mesh links, parity, CRC, timeouts, partitions | **RTL-proven** | WPF = 32 on every 32×32 build; M3, M9, M13, M14, M16 |
| Safety mechanisms (ABFT, duplication, lockstep, SECDED, ECC, parity, CRC, watchdog, self-test) | **RTL-proven**, each by fault injection | §8 names the test per mechanism; measured diagnostic coverage comes from phase 6's fault campaign |
| FMEDA SPFM 99.9 %, 62 FIT | **model** (analytical, each row naming its test) → FMEDA v2 (phase 6) | the fault campaign replaces the per-row claims with measured detected / masked / dangerous-undetected fractions |
| YOLOv8-m 1.90 ms at 1280×736, 0.99 ms at 640² | **model** (perf model v3 calibrated with the RTL's endpoint rates) → measured (phase 8) | a layer group of the network on the simulated mesh with cycle counts in the repository |
| Compiler, backend, C driver bit-exact with the RTL | **RTL-proven** | `--verify`, `--check`, M6 / M7 / M10 / M18 / M19, C driver vs backend ALL PASS in every CI run |

## 1. Product

| Item | Value | Status |
| --- | --- | --- |
| Device | AstraCore Neo, automotive ADAS inference accelerator with ASIL-D safety island, host-attached | **RTL-proven**: the accelerator (tile mesh); **IP**: the island, the host attach |
| Family | Neo 262 (64 tiles), Neo 196 (48), Neo 131 (32), Neo 65 (16): one die, tiles fused at test; gen 1.5 two-die package; gen 2 Neo-L | **planned** (phase 2: tile-enable registers; the fuse source is IP); the bins are a product plan — the RTL has partition isolation and the compiler a tile-exclusion mask, no fuse map |
| Host interface | PCIe Gen5 ×8 (IP, not in RTL); per-tile host register bus (in RTL, section 7) | **IP** (PCIe); **RTL-proven** (the register bus, M8 / M15); the aggregation of 64 register buses behind one host port is phase 2 |
| Process | TSMC N5A | **model** (a target; the open-PDK synthesis of phase 1 is a proxy, not this node) |
| Qualification | AEC-Q100 Grade 2 (Ta −40..+105 °C), Tj −40..+125 °C, 15-year mission profile | **model** (target; nothing in the package measures it) |
| Compliance | developed to ISO 26262 ASIL-D process; island ASIL-D HW metrics; accelerator ASIL-B(D); item ASIL-D by decomposition with a diverse monitor | **model** (the safety concept and FMEDA v1); the island is **IP**; the mechanisms it rests on are **RTL-proven** (§8) |

## 2. Compute

The compute headline splits into three facts of different standing: the MAC count and the mesh are **RTL-proven** at full
scale (the 8×8 mesh of 32×32 cores is simulated in CI since drop 0.37), the clock is a **model** figure until phase 1's
synthesis reports the critical paths, and the sparse figure is **planned** — the 2:4 sparsity engine does not exist in this
package, so **262 TOPS dense INT8 at 2.0 GHz is the only throughput headline the RTL backs**.

| Item | Silicon configuration | As simulated (tests) | Source | Status |
| --- | --- | --- | --- | --- |
| Tiles | 64, 8×8 mesh (NX = NY = 8) | 64, 8×8 (mesh8: M1–M3, M5–M7, M9, M12–M14, M16 in the hdl-mesh8 job); 4, 2×2 (tile, tile32) | `tile_mesh.sv` parameters NX, NY | **RTL-proven** at 64 tiles (run 133, 11 of 11) |
| Core array | 32×32 weight-stationary systolic + 1 ABFT check column (ROWS = COLS = 32) | 32×32 (tile32, core32, mesh8, streaming tests T1–T4), 16×8 (tile, core), 8×8 (two-layer test) | `neo_mac_core.sv` | **RTL-proven** |
| MACs | 65,536 INT8 MACs + 2,048 check-column MACs | the same array in every 32×32 build | 64 × 32 × 32 | **RTL-proven** (the count is the parameterization; phase 1's synthesis gates on PE count = ROWS × (COLS + 1) per core) |
| PE | INT8 × INT8 products, INT32 partial sums, 2-stage pipeline (PE_LAT = 2), double-buffered weight with swap token | same | `mac_pe.sv` | **RTL-proven** (T1–T5, C1) |
| Core latency | PE_LAT·ROWS + COLS = 96 cycles (32×32) | 96 (32×32), 40 (16×8), 24 (8×8) | `neo_mac_core.sv` LATENCY | **RTL-proven** |
| Clock | 2.0 GHz worst-case (SS, 125 °C, aged), 2.4 GHz typical | — | feasibility model; the paths from the `synth` job (drops 0.40 / 0.41) | **model** for the figure; the paths **synthesized, ASAP7 proxy**: the PE's product stage 1,031 ps, the check PE 1,093, the array 1,411 on ASAP7 SS (0.63 V / 100 °C, an academic 7 nm library slower than N5A by an unknown factor); since drop 0.41 every unit but the NIC sits at or near the array's own stage (§9), the NIC at 2,324 ps on its writeback mask (A2) |
| Peak throughput, dense | 262 TOPS dense INT8 = 65,536 × 2 × 2.0 GHz | — | the MAC count (RTL) × the clock (model) | **RTL-proven** count × **model** clock |
| Peak throughput, sparse | 524 TOPS with 2:4 structured sparsity | — | `ROADMAP.md` §2 (the design: 2:4 along K on PE pairs, two ABFT check columns, compressed weight entries) | **planned** (phase 3, drops 0.46–0.49): no sparse PE, index path, compressed weight format, compiler packer or test exists; the figure is a phase-3 target and appears in no other headline of this package |
| Check weight width | WCW = 8 + log2(ROWS) + 1 = 14 bits (32×32) | 13 (16 rows), 12 (8 rows) | `neo_mac_core.sv` | **RTL-proven** |
| Fault hook | one PE (FAULT_ROW, FAULT_COL) adds 1 to its product while `fault_inject` is high; used by the island's checker self-test | same | `systolic_array.sv`, `host_if` SELFTEST | **RTL-proven** (T2–T4, M8) |
| Other datatypes | INT4 / MXFP4 (2×), FP8, FP16 / BF16 (½×) | — | spec v2.1 feature list | **planned**: INT4 weight-only storage expanded at the weight-buffer load in phase 4 (no throughput multiplier — the datapath stays INT8); FP8 / BF16 arithmetic is a gen-2 study, decided with phase 1's area numbers (`ROADMAP.md` §7) |

## 3. Core blocks (per tile)

Status: every block of this table is **RTL-proven** — each is exercised by the core suite (C1–C3 under cocotb at 16×8 and
32×32, C1–C10 under neosim) and the mesh suites, and every safety flag it raises has a fault-injection test (§8) and a
functional-coverage bin (53 of 53 covered, run 133). Since drop 0.40 the arrays of `act_feeder`, `wbuf_mem` and `acc_bank` (and
the NIC's drain FIFO and the bank) are instances of the two memory leaves of `01_rtl/neo_mem.sv` — `neo_mem_sp` (write groups,
registered read) and `neo_mem_rf` (an asynchronous read port A, optionally a registered second one, port B) — the modules a memory
compiler's macros replace one for one (`09_synthesis/memories.md`); the behavioural leaves simulate everywhere and the
synthesis job black-boxes them. Since drop 0.41 the activation buffer, the weight buffer and the accumulator are read on port B
(an SRAM macro's synchronous read: address at the edge, data the next cycle; port A tied off, so the three are 1-write-1-read
synchronous macros), the feeder's address generator is incremental, the accumulate a two-cycle read-modify-write with a
one-stage forward, and the NIC's partial-sum row assembly (`prow`, 64 × 1,024 bits) is a fourth leaf; the drain FIFO still
reads at its head pointer (A2).

| Block | Function | Parameters as implemented |
| --- | --- | --- |
| `core_seq` (×2, lockstep) | runs a layer descriptor: every (channel tile, ky, kx) run, shadow weight loads overlapped with the previous run, swap tokens, drain of the accumulator, reduce state for K-split, `ct_free` pulse when a channel tile is finished; outputs compared every cycle (`seq_err`) | states IDLE, LOAD0, START, RUN, WAIT, DRAIN, DONE, REDUCE; run range `cfg_ct0/ky0/kx0`, `cfg_rn` |
| `act_feeder` | sliding-window activation feeder: for output pixel (oy, ox) and kernel position (ky, kx) reads pixel (oy·s + ky − p, ox·s + kx − p) or zeros; region = ct mod regions; output-row range; duplicated address generator and FSM with comparator (`ctrl_err`) — the buffer is organised in pairs of entries with one word-wide write port (two entries per cycle at an even address, a single entry at an odd one; drop 0.32) | ABUF_DEPTH entries × ROWS bytes (256 entries simulated); regions a power of two (`cfg_regions_m1`) |
| `wbuf_mem` | weight buffer, one entry per tile row = COLS weights + check weight, stored as (39,32) SECDED lanes; corrected on the sequencer's read — organised in pairs of entries with one word-wide write port and two sets of encoders (two entries per cycle at an even address; drop 0.32) | WBUF_DEPTH entries (512 simulated); LANES = ⌈(COLS·8 + WCW)/32⌉ = 9 lanes (32×32), 3 (16×8) |
| `acc_bank` | INT32 accumulator, ACC_ROWS rows × (COLS + check) × 32 bit; K-tiles accumulate per row; ABFT check at drain; reduce port for partial sums from other tiles (local writes have priority) | ACC_ROWS = 512 (silicon, module default) / 64 (simulated) |
| `requant` (×2, compared) | per-channel: mult (16 bit) × acc, + 2^(shift−1), ≫ shift (5 bit), + zero point (8 bit), optional ReLU in the quantized domain, saturate to INT8; 3-cycle pipeline since drop 0.41 (the product, the rounding bias and the zero point as carry-save rows; one adder; shift and saturate — 934 ps on the ASAP7 proxy, 127 k cells); odd parity per table entry checked on use | MW = 16, 32 table rows (one per column) |
| `neo_core` | sequencer pair + datapath + requant pair; flags: `array_abft`, `acc_abft`, `ctrl_err`, `seq_err`, `rq_err`, `wbuf_ce/ue`, `rq_tbl_perr` (all sticky, cleared by `err_clear`) | DV hooks: `fault_inject`, `ctrl_fault_inject`, `seq_fault_inject`, `rq_fault_inject`, `rq_tbl_fault_inject` |

Descriptor (20 × 16-bit registers + `cfg_m`), in host_if order 0x10–0x24: `cfg_h, cfg_w, cfg_ho, cfg_wo, cfg_oy0,
cfg_oy_n, cfg_iy0, cfg_s, cfg_p, cfg_k, cfg_ct_n, cfg_ct0, cfg_ky0, cfg_kx0, cfg_rn, cfg_contrib_n,
cfg_tile_pixels, cfg_regions_m1, (2 reserved), cfg_m` (rows to drain). A descriptor describes one output tile
(COLS output channels) over an output-row range of a layer, for a contiguous run range (K-split share).

## 4. Memory system

| Item | Silicon configuration | As simulated | Source | Status |
| --- | --- | --- | --- | --- |
| Shared SRAM | 128 MB as 64 banks × 2 MB, one bank per tile on its router's local port. **Bank port (drop 0.28): 512 bits, 64 B per cycle each way** — the bandwidth the performance model assumes; a row holds 16 words, addressed by row with a 16-lane write mask; word address = row × 16 + lane | 64 banks × 524,288 words (2 MB, the silicon depth) on mesh8; 4 × 524,288 on tile32; 4 × 4,096 words (256 rows) on the small builds | `sram_bank.sv` DEPTH, LANES (the array: a `neo_mem_sp` leaf of `neo_mem.sv`, drop 0.40); `bank_bist_wrap.sv` | **RTL-proven** at the silicon depth and count (mesh8); the array is inferred — its macro (`09_synthesis/memories.md`) and area are **model** until a memory-compiler run; phase 1 black-boxes it with the macro table's area |
| Word protection | (39,32) SECDED per 32-bit word, 16 ECC lanes per 624-bit row: every write encodes the lanes it writes, every read decodes all 16 — single-bit corrected on read (`ecc_ce`), double detected (`ecc_ue`), both sticky, `ce_lane` for the MBIST | same | `ecc39.sv` (Hamming positions 1..38 + overall parity) | **RTL-proven** (ecc39 exhaustive single-flip; M5, M17) |
| MBIST | March C− with backgrounds 0x00000000/0xFFFFFFFF and 0x55555555/0xAAAAAAAA (12 elements) over rows, the background in every lane, all 16 lanes checked in parallel (drop 0.28: 16× fewer steps — 32,768 rows for a 2 MB bank); owns the bank ports while active; reports the word address of the first failing lane; a lane corrected by ECC during the sweep is reported as a fault (`fail_ce`) | BIST_WORDS = BANK_DEPTH (16 words = one row in M11 for speed) | `mbist.sv` | **RTL-proven** (B1–B3, M11) on the inferred array; the macro's own BIST interface is an **IP** item of the memory compiler |
| Activation layout in a bank | row-major per channel tile: entry = ROWS channels as bytes (channel c in byte c mod 4 of word c div 4), WPA = ROWS/4 words per pixel | WPA = 8 (32 rows), 4 (16 rows), 2 (8 rows) | `pack_words`, `tile_nic` fetch assembly | **RTL-proven** (M1, M6, M18) |
| Weight layout in a bank | per run (ct, ky, kx), ROWS entries of COLS weight bytes followed by the check weight; WPW = ⌈(COLS·8 + WCW)/32⌉ words per entry | WPW = 9 (32×32), 3 (16×8 and 8×8) | same | **RTL-proven** |
| Result layout | INT32: COLS words + check word per output row; INT8: COLS/4 words per row (= one activation entry when COLS = ROWS) | — | `tile_nic` drain modes | **RTL-proven** (M1, M19) |
| External memory | 384-bit LPDDR5X-8533, 410 GB/s raw, inline ECC (IP, not in RTL; decision 5 open against 256-bit) | — | spec | **IP**; the bank-filling path from it (an AXI bridge on an edge tile position) is **planned** (phase 2) — until then no RTL endpoint moves data between the banks and anything outside the mesh |

## 5. Interconnect and tile interface

Status: every row of this table is **RTL-proven** on the 1024-bit links (WPF = 32 on tile32 and on the 8×8 mesh) and on the
64-bit links of the small builds; the status cell names the tests.

| Item | As implemented | Source | Status |
| --- | --- | --- | --- |
| Topology | 2-D mesh, 5-port routers (N, S, E, W, local), XY dimension-order routing, 2-entry input FIFO per port, round-robin arbitration per output, registered outputs with ready/valid. A router's (and a tile's) coordinates are input ports (`my_x`, `my_y`), strapped by the mesh from the generate indices (drop 0.35): every tile of the mesh is the same module — one elaboration, one hard macro — rather than a parameterization per position. The tile's four mesh links are packed vectors (port p at bit p / bits [p·FW +: FW]) | `noc_router.sv`, `noc_mesh.sv`, `neo_tile.sv`, `tile_mesh.sv` | **RTL-proven** (N1–N2 at 3×3 under neosim; every mesh test; the same tile module in all 64 positions of mesh8) |
| Flit | FW = 1 + 2·(XW + YW) + 8 + DWL bits = {even parity, dst_y, dst_x, src_y, src_x, seq[7:0], payload[DWL−1:0]}; DWL = 32 + 32·WPF with WPF words per flit: WPF = 32 (DWL = 1056, the 1024-bit links; FW = 1077 on 8×8 with XW = YW = 3) in silicon and on the 32×32 builds since drop 0.25, WPF = 1 (DWL = 64, FW = 81 on 2×2) on the 16×8 and 8×8-core builds | `noc_router.sv`, `link_pack.sv` | **RTL-proven** (M3, M16 at WPF 32) |
| Parity | even parity over the whole flit checked at the head of every input FIFO; a bad flit is dropped and flagged (`parity_err`) | `noc_router.sv` | **RTL-proven** (N2, M16: the corrupted flit dropped at the receiving router and flagged) |
| Payload | {type[2:0], tag[8:0], hdr[19:0], words[32·WPF−1:0]}; RDRSP/WRDATA/PSUM flits carry 1..WPF words (hdr[13:8] = words − 1; on PSUM hdr[7:0] is the column of word 0 and word i carries column + i, so a 33-word row is two flits); RDREQ, WRHDR, CRC and RDY are one-word flits. The tile interface moves **beats of up to BW = 16 words (one bank row)** in the same payload shape (drop 0.29): `link_packer` merges consecutive data beats of one stream (same destination, type, tag, hdr[19:14], PSUM columns consecutive) into a flit while they fit — full, or flushed by the next beat that cannot join; a data stream always ends with its CRC flit — and a beat that cannot join starts the next flit in the cycle the pending one leaves (a 32-word flit every second cycle); a beat is never split across flits, so a serve that starts inside a row produces one 17..31-word flit after its partial first beat. `link_unpacker` presents a flit as ceil(words / 16) beats. With WPF = 1 the packer sends a beat as single-word flits, so the small builds put the same flits on the link as before | `link_pack.sv`, `tile_nic.sv` | **RTL-proven** (M1–M3, M18 at WPF 32 and WPF 1) |
| Message types | 1 RDREQ (addr[19:0], len[11:0]), 2 RDRSP (word), 3 WRHDR (bank address), 4 WRDATA (word), 5 PSUM (row index in tag, word index in data), 6 CRC ({count[15:0], crc16[15:0]}), 7 RDY | `tile_nic.sv` | **RTL-proven** |
| Message protection | CRC-16-CCITT over the data words of every stream, plus the word count: receiver flags `crc_err` on mismatch and `lost_err` on a count mismatch; one message in flight per sending tile; receiver keeps CRC and count state per source (each source's write pointer, accumulator, count and partial-sum row in its own register block, drop 0.29). The accumulator takes a whole 16-word beat per cycle (`crc16_beat`: 16 chained word stages; the parallel form of the polynomial is the synthesis-time replacement); the count is per word, so a lost flit of any width is caught | `crc16_word.sv`, `crc16_beat.sv`, `tile_nic.sv` | **RTL-proven** (M3 end-to-end CRC, M9a lost-flit count) |
| Fetch engine | RDREQ to any bank; responses assembled into activation entries (WPA words) or weight entries (WPW words) from a base entry — at entry rate since drop 0.30, two entries per cycle since drop 0.32: up to two entries per cycle from the words staged from earlier beats plus the current 16-word beat, written through the buffer's word-wide port as a pair at an even entry address (a single entry at an odd address or when only one entry's words are there — the odd trailing entry), so a 16-word beat of two 8-word activation entries is taken in one cycle at 32×32 (weights, 9 words per entry, pair up when 18 words are at hand), the leftover words staged for the next beat, the CRC over the whole beat; `fetch_timeout` after FETCH_TIMEOUT cycles abandons the fetch and raises the flag. **Sizing rule (hard constraint, drop 0.24):** a tile has at most one fetch outstanding and a fetch is at most 4,095 words, so the longest wait at any bank is NX·NY − 1 queued maximal fetches plus the requester's own: FETCH_TIMEOUT ≥ NX·NY·(4,095 / R + H), R the per-flow rate in words per cycle and H the per-request overhead (request flit, response header and CRC, routing; well under 1,000 cycles on 8×8). R was 1 up to drop 0.29 (a response was consumed one word per cycle, whatever the bank and link widths); since drop 0.30 R = 8 for activations and 9 for weights at 32×32 (the receiver is the slowest stage: server 16 words per cycle, link 16, receiver one entry), so a maximal fetch is served in ~512 + H cycles and 64 queued ones in ~33,000 + 64·H. The constants are kept: 2^20 − 1 in silicon (now a 16× margin), 65,535 on the 32×32 builds, 8,192 on the small builds. The constraint is met in hardware, not by placement: the timer is as wide as the parameter needs (`$clog2(FETCH_TIMEOUT + 1)` bits) and the silicon value is 2^20 − 1 = 1,048,575 cycles (0.52 ms at 2 GHz; covers 64 tiles with H up to 12,000), so the compiler is free to put any number of requesters on one bank; a lost fetch is still flagged well inside the FTTI. Simulation builds pass 8,192 (16×8, 8×8 cores) and 65,535 (the 32×32 suites) so the timeout tests stay short; M14 queues as many maximal fetches at one bank as the build's value admits (14 at 65,535) and requires every one to complete without a cause | `tile_nic.sv` | **RTL-proven**, the sizing rule measured at 64 tiles (M14 on mesh8: 14 maximal fetches queued at one bank, 0.065 clocks per queued word, no cause; M9b timeout against a dead server) |
| Bank server | serves RDREQ sequentially from the bank's row port: 16 words per cycle while the link accepts (pipelined row read, the row read last held while the link stalls), the first beat starting at the request's word offset inside its row (16 − offset words), every later beat a full row until the last; a 4,095-word serve is 257 beats; closes with the CRC flit (drop 0.29). A zero-length request is answered by its CRC flit alone (count 0), so a requester completes cleanly; the toolchain refuses to emit one (`ins()`, the C driver's `emit()`) | `tile_nic.sv` | **RTL-proven** (M1, M14; `nic` under neosim at 16 words per cycle) |
| Drain | INT32 rows or INT8 requantized rows to a bank address (WRHDR + WRDATA + CRC), or PSUM rows to an owner's reduce port; drain FIFO DFD = ACC_ROWS rows; RDY notification from an owner gates a contributor's PSUM stream (flow control). The drain sends 16-word beats (drop 0.31): an INT32 row of COLS + 1 words is 16 + 16 + 1 at 32×32, an INT8 row of COLS / 4 words one beat; the packer merges beats into flits. The receiving tile writes each WRDATA beat into its bank as one row with the covered lanes masked in, or as two rows in two cycles when the beat crosses a row boundary (drop 0.29); an owner takes a PSUM beat whole — up to 16 columns per cycle into the row under assembly, the row handed to the reduce port in the cycle its check word arrives, that beat held only while the two-entry FIFO is full (drop 0.31) | `tile_nic.sv` | **RTL-proven** (M1 INT32, M19 INT8, M2 / M7 partial sums with RDY flow control) |
| Link width | 1024-bit links (128 B/cycle/direction) as spec v2.1 calls for: WPF = 32 words per flit on the router side (drop 0.25), 16-word beats on the tile interface side (drop 0.29); the serve, fetch and drain paths all cross the packer/unpacker. Per flow every endpoint now moves the 16-word beat, 64 B/cycle: the bank port on the serve and writeback paths (drop 0.29), the drain and the owner's reduce port (drop 0.31), the fetch into the activation and weight buffers through their word-wide (two-entry) write ports (drop 0.32). The link carries two such flows; the performance model with these rates is in section 9 | `link_pack.sv`, `tile_nic.sv`, `act_feeder.sv`, `wbuf_mem.sv` | **RTL-proven** at 1024 bits (every 32×32 build); the per-flow 64 B/cycle ceiling is a design fact the performance model carries (§9) |

## 6. DMA program engine (per tile)

Status: **RTL-proven** — every op below is executed by the mesh suites (M1–M19 through the register-loaded programs and, in M6 / M7 /
M10 / M18, the compiler-emitted ones), the DMA engine runs in lockstep pairs, and the DMA-op coverage bins are all hit (run 133).

32 entries × 64-bit instructions (drop 0.22; 16 before): `op[63:60], x[59:56], y[55:52], addr[51:32], len[31:20], base[19:4], arg[3:0]`. `len` is 12 bits: the compiler and the host driver split a longer fetch into instructions of at most 4,095 words on entry boundaries (drop 0.21).

| Op | Name | Behaviour |
| --- | --- | --- |
| 1 | FETCH_A | fetch `len` words from bank (x,y) at `addr` into activation entries from `base`; waits; `tiles_ready` += 1 |
| 2 | FETCH_W | same into weight entries; waits |
| 3 | DRAIN_WR | route the drain to bank (x,y) at `addr`, `len` rows; `arg[0]` = INT8 rows |
| 4 | DRAIN_PSUM | route the drain as partial sums to the owner at (x,y), `len` rows |
| 5 | GO | start the core (program continues) |
| 6 | WAIT_FREE | wait until the core has released `arg`+1 channel-tile regions (`ct_free`) |
| 7 | WAIT_DONE | wait for the core's `done` |
| 8 | END | stop; `prog_done` |
| 9 | NOTIFY | send RDY to (x,y); waits until the flit has left |
| 10 | WAIT_RDY | wait for an RDY flit (contributor, before DRAIN_PSUM) |
| 11 | WAIT_REDUCE | wait until the core is in its reduce state (owner, before NOTIFY) |

The sequencer starts the first run of channel tile ct only when `tiles_ready > ct − cfg_ct0`, which makes a
2-region activation buffer refilled behind the running core safe.

## 7. Host register interface (per tile, 8-bit word address, 32-bit data)

Status: **RTL-proven** per tile (M8 ESM / watchdog / self-test, M11 MBIST, M15 read-back of every register, `host_if.sv` at 100 % line
coverage); the address decode that puts 64 of these buses behind one host port is **planned** (phase 2, the control plane).

| Address | Register | Function |
| --- | --- | --- |
| 0x00 | CTRL | w: bit 0 program start (pulse), bit 1 error clear (pulse) |
| 0x01 | STATUS | r: bit 0 prog_done, bit 1 drain_busy, bit 2 core done (latched since start), bits 8–15 parity, crc, array_abft, acc_abft, ctrl, seq, rq, ecc_ue; bit 16 ecc_ce |
| 0x02–0x04 | PROG_ADDR, PROG_LO, PROG_HI | program entry index (0..31); low word; high word (writing HI commits the entry) |
| 0x05 | ERR_MASK | w/r: bit i masks cause i from `err_pin` (bit 16 = watchdog) |
| 0x06 | ERR_CAUSE | r: latched causes (bit 16 = watchdog) |
| 0x07 | WD_CTRL | w: bit 0 enable, [31:8] window in cycles |
| 0x08 | WD_KICK | w: any write kicks the watchdog |
| 0x09 | SELFTEST | w: bit 0 asserts the core's fault-injection hook |
| 0x0A | MBIST | w: start; r: bit 0 active, bit 1 done, bit 2 fail, bit 3 fail found by ECC, [31:12] failing address (20 bits, drop 0.22; was [31:16]) |
| 0x10–0x23 | CFG[0..19] | descriptor registers (16-bit) |
| 0x24 | CFG_M | rows to drain |
| 0x30–0x32 | RQ_TBL, RQ_ADDR, RQ_RELU | requantization table row {zp[31:24], shift[20:16], mult[15:0]} at RQ_ADDR; ReLU enable |

Timing rules (from `host_if.sv`): writes take effect one cycle after the bus cycle; a read in the cycle after a
CTRL clear still returns the pre-clear state; after a start write (CTRL or MBIST) the engine leaves its done state
two cycles later, so firmware polls done low, then high.

Error-signaling module: 16 latched causes {—, bist_fail, fetch_timeout, lost_err, rq_tbl_perr, wbuf_ue, wbuf_ce,
ecc_ce, ecc_ue, rq_err, seq_err, ctrl_err, acc_abft, array_abft, crc_err, parity_err} plus the windowed
watchdog; `err_pin` = any unmasked cause; cleared together by error clear.

## 8. Safety

| Item | Value | Status |
| --- | --- | --- |
| Decomposition | item ASIL-D = accelerator ASIL-B(D) + independent diverse monitor ASIL-B(D); safety island (2 × Cortex-R52+ lockstep, IP) ASIL-D | **model** (the safety concept); the island is **IP**; the accelerator-side mechanisms the decomposition rests on are **RTL-proven** (next rows) |
| FTTI budget | detect ≤ 10 ms, react ≤ 20 ms (item FTTI from the lead OEM; 100 ms assumed) | **model** (an assumption until the lead OEM's item definition); the watchdog window and the detection latencies of the mechanisms are **RTL-proven** (M8, M9) |
| Mechanisms proven on RTL by fault injection | ABFT check column (T2–T4, C4); ABFT at drain over the reduction (K2); duplicated feeder control (C5); lockstep sequencer (C7); duplicated requantization (C8); weight-buffer ECC (C9); requant table parity (C10); link parity (N2); end-to-end CRC (M3); bank ECC in flight (M5); lost-flit count (M9a); fetch timeout against a dead server, its transmit engine held in the serve-read state so the taken request is never answered (M9b; drop 0.29); reduction flow control (M2); ESM, error pin, watchdog, checker self-test (M8); MBIST (B1–B3, M11); SECDED code exhaustive (ecc39) | **RTL-proven** — each mechanism by its named test, every flag bin covered (53 of 53, run 133); the diagnostic coverage *fractions* are **model** until phase 6's fault campaign measures them |
| Not in RTL | LBIST (tool-inserted; SELFTEST register and FTTI-windowed watchdog are its hooks) | **IP** (LBIST is inserted by the licensee's / the implementation team's DFT flow); its hooks — SELFTEST and the FTTI-windowed watchdog — are **RTL-proven** (M8) |
| FMEDA v1 | core SPFM 99.9 %, LFM ≈ 100 %, PMHF 0.43 FIT per core; chip PMHF 62.3 FIT first-order, dominated by shared-SRAM SER (600 FIT/Mbit assumed, foundry data required); every row's coverage claim names its proving test; fault-simulation campaign on Xcelium replaces the claims at RTL freeze | **model** (analytical, every row naming its test) → FMEDA v2 from the fault campaign (phase 6, drops 0.59–0.63): detected / masked / dangerous-undetected measured per block |

## 9. Performance, power, physical (models)

Status of the whole section: **model**, with one exception since drop 0.40 — the **synthesized, ASAP7 proxy** row, the
`synth` job's first numbers (area and paths of every unit of the tile on an open 7 nm library, not the target node). The
YOLOv8-m figures come from the performance model (its endpoint rates calibrated against the RTL's, drop 0.32), the power and
the die figures from the feasibility model, and the static-compile row is the one **RTL-proven** entry (the compiler's output
runs bit-exactly on the mesh in M6 / M7 / M18). Each row names the phase that replaces it: phase 1 (the proxy row below,
refined in drop 0.41 and by stream A's period sweep and routed blocks), phase 5 (the power mechanisms), phase 7 (routed blocks, power from recorded activity), phase 8
(a layer group measured on the simulated mesh). **Struck in drop 0.39:** the INT4 LLM figures (8B
decode 13 ms/token, prefill 512 tokens 52 ms) — the datapath is INT8 only and no RTL endpoint provides the external-memory
bandwidth they assumed; they return as measured numbers with phase 4's INT4 weight path and phase 2's bridges.

| Item | Value | Source | Status |
| --- | --- | --- | --- |
| YOLOv8-m class, 1280×736, one camera | 1.32 ms (56.7 % MAC utilization, busiest link 17 %, busiest bank 14 %); compute floor 0.93 ms | perf model v3, 64 tiles, 64 banks, 128 B/cycle links, K-split, dataflow across layers | **model** (superseded by the next-but-one row's endpoint-rate figures; kept as the fabric's ceiling) → phase 8 |
| YOLOv8-m class, 640² | 0.66 ms | same | **model** (superseded likewise) → phase 8 |
| **The same model with the RTL's endpoint rates** | **After drop 0.32 (fetch two entries per cycle, drain in 16-word beats, partial sums a beat per cycle — every endpoint at the 16-word beat, `--rx 64 --tx 64 --psum 64`): 1.90 ms at 1280×736 (39.6 % MAC utilization), 0.99 ms at 640² (32.9 %), at the 2 GHz clock.** After drop 0.31 (`--rx 32`, one entry per cycle) it was 2.89 ms (26.0 %) and 1.56 ms (20.9 %); after drop 0.30 (`--rx 32 --tx 4 --psum 4`) 11.1 ms (6.7 %) and 7.2 ms (4.5 %); attribution at 640² then: the serial partial-sum path alone took the frame from 0.65 to 6.7 ms, the entry-rate fetch alone to 1.48 ms, the one-word-per-beat drain alone to 0.70 ms. What remains between these figures and the superseded 1.31 / 0.65 ms is the per-flow ceiling of the tile interface itself — 64 B/cycle, the 16-word beat and the bank row, against the 128 B/cycle link that carries two such flows — an architectural limit, not a stage left to close. Without K-split the 640² frame is 1.45 ms at the drop-0.30 rates (0.63 ms with the old assumption) | perf model v3 with the endpoint rates; the pre-0.30 rows above assumed every endpoint absorbs a full 128 B/cycle link and are superseded | **model**, calibrated: the endpoint rates are the RTL's (M1 / M14 / M19 measure them), the layer schedule and link contention are the model's → phase 8 measures a layer group on the simulated mesh |
| Static compile of YOLOv8-m, 640², 8×8 mesh | 83 layers, 6,263 groups — **every one placed by the bank allocator (drop 0.33)** — 24,549 tile programs (1.9 MB of descriptors, longest 24 of the 32 words, at most 12 FETCH_A per program), K-split shares 1–9, balance mean 1.16 (worst 2.46), per frame: 828 MB activation fetch, 414 MB partial sums, 187 MB outputs. Memory: activations in bands of rows per channel tile, each band one contiguous region of one 2 MB bank in 16-word rows (the image's 12.5 MB in 7 banks), weights resident per output tile (36.5 MB over the mesh), a layer's input released once it is compiled; peak occupancy 99.6 % of one bank, 36.7 MB in use at the end; a tile-exclusion mask (`--exclude`) takes tiles out of the owner pool and their banks out of the allocator and every group still places with (3,3) excluded. Before drop 0.33, 430 groups of the three 640-wide layers could not be emitted (a channel tile placed whole exceeded the address field) | `neo_backend.py`, `neo_alloc.py`; `make backend_check` in CI | **RTL-proven** in its contract (the emitted descriptors and programs run bit-exactly: M6, M7, M18; `--check` ALL PLACED in every CI run); the per-frame byte counts are the compiler's accounting |
| Other workloads (first-order roofline) | DMS 0.01 ms; 6-camera YOLOv8-m 6.4 ms; BEV fusion 12.4 ms; radar+lidar occupancy 1.3 ms; ~~8B INT4 LLM decode 13 ms/token, prefill 512 tokens 52 ms~~ (struck, drop 0.39: no INT4 datapath or storage, no external-memory endpoint in the RTL) | feasibility model | **model** (first-order roofline; the transformer and BEV figures need phase 4's vector unit and attention tiling to be more than a roofline); the INT4 LLM figures are **struck** (phase 4) |
| Power | ~56 W typical at 60 % utilization (0.45 pJ/MAC system-level at 0.75 V, 11 W uncore, 9.8 W leakage); ~80 W at 100 %; TDP SKUs 65 / 30 / 15 W; always-on island ≤ 2 W | feasibility model | **model**; no clock gating, power gating or DVFS logic is in the RTL — the mechanisms are **planned** (phase 5), activity-based power **planned** (phase 7); the TDP SKUs and the island's ≤ 2 W are product targets |
| **Synthesized, ASAP7 proxy** (drop 0.40) | The tile: 1.283 mm² = 0.287 mm² of standard cells (2.31 M cells, 239 k flops) + 0.995 mm² of macro model (FakeRAM2.0 7 nm, 0.042 µm²/bit: the 2 MB bank 0.860 mm², the core's four arrays 0.101 mm², the NIC's drain FIFO 0.034 mm²). The PE 138 µm² (1,124 cells; the check-column PE 187), the 32×32 array 0.148 mm², the 32×32 core 0.305 mm² with its arrays, the NIC 0.063 mm² of cells (65,536 of its 72 k flops the per-source row assembly `prow`), the router 0.015 mm², requant 0.020 mm² each of the pair. Minimum register-to-register periods on ASAP7 SS (0.63 V / 100 °C) against the 500 ps budget: PE 1,031 ps (the product stage), check PE 1,093, array 1,411, sequencer 1,095, MBIST 1,030, bank 767, link unpacker 756, DMA 558, ESM 540, host_if within 30 ps, the ECC blocks inside; over by design: requant 3,039 (round / shift / zero point / saturate in one stage; its 32×16 product 1.85 ns), the NIC 7,662 (16-word serial CRC behind the transmit beat — the tile's worst path, 5.9 ns over on the bank's read data into it; the receive CRC 5.5 ns), the router 2,571 (`% 5` round-robin arithmetic), the accumulator 1,732 (33-operand ABFT sum), the feeder 3.43 ns at the core level (channel-tile register → address → asynchronous activation read → array) | `07_ci/synth`, `synth_report.md` on `ci-logs-synth`; 64 tiles × the tile's cell area = 18.4 mm² of logic on the proxy before the uncore | **synthesized, ASAP7 proxy** — the shares and the paths are the design's, the µm² and ps the library's (no foundry data, SS corner slower than N5A by an unknown factor); drops 0.41 / 0.42 fix the five paths and re-run; a memory compiler replaces the macro model |
| **Synthesized, ASAP7 proxy** (drop 0.41, run 162) | The tile: 1.243 mm² = 0.225 mm² of standard cells (1.79 M cells, 186 k flops) + 1.018 mm² of macro model (the bank 0.860 mm², the core's four arrays 0.120 mm² — the activation and weight buffers and the accumulator now 1W1R synchronous macros, the NIC's drain FIFO 0.034 mm² and its partial-sum rows `prow` 0.004 mm²). Against 0.40: −512 k cells and −53 k flops (`prow` 65,536 flops → a 4,133 µm² macro; requant 192,872 → 127,456 cells each; the router 108,904 → 89,608; the NIC 477,805 → 104,987). Minimum register-to-register periods on ASAP7 SS against the 500 ps budget: PE 1,031 ps (unchanged — the array's own stage), check PE 1,093, array 1,411, `neo_mac_core` 952, requant 934, `act_feeder` 898, `wbuf_mem` 791, `acc_bank` 1,131, sequencer 1,110, `neo_core` 1,331 (the lockstep sequencers' compare), router 735, link unpacker 756, bank 767, MBIST 1,030, DMA 558, ESM 540; still above the array: `tile_nic` 2,324 (the WRDATA writeback mask behind the 64:1 per-source pointer mux, and 2,281 register-to-register on `rx_woff → rx_cnt`), `link_packer` 571 with −263 ps on the merge decision in its input cycle; the tile's own paths through the NIC 3,145. The five 0.40 items: requant 3,039 → 934, the NIC's beat CRC 4,723 → 733 (`crc16_beat`), the router 2,571 → 735, the ABFT sum 1,447 / 1,644 → ~770 behind the row registers (the accumulator's forward-select adder is now its 1,131), the feeder 3,005 → 846 on its per-run start stage (the per-pixel path 541–749) | `07_ci/synth`, `synth_report.md` on `ci-logs-synth` (run 162); `02_dv/formal` for the equivalence and invariant proofs behind the restructured blocks | **synthesized, ASAP7 proxy** — the shares and the paths are the design's, the µm² and ps the library's; A2 adds Nangate45, a period sweep (the proxy's Fmax = the array's stage unless the NIC's remaining path says otherwise) and routed blocks through OpenROAD |
| Die, package, cost (±30 %) | ~210 mm² N5A; FCBGA ~45×45 mm, ~2,000 balls; ~$105–130 at volume | feasibility model | **model** → phase 1 (the proxy row above: 64 × 1.283 mm² = 82 mm² of tiles on ASAP7's model before the uncore — a different node, so a ratio check, not a die size), phase 7 (routed 32×32 core and tile, a chip-level floorplan sketch); package and cost stay model figures |

## 10. Deltas between the simulated RTL and the silicon configuration

Status: the geometry, link, bank and address rows are **closed** — the silicon mesh is simulated as such in CI; the remaining rows
are **IP** (island, PHYs, controllers, HSM, ISP; LBIST insertion) and **planned** (sparsity engine, INT4 storage, transformer
operators, vector unit — phases 3 and 4 of `ROADMAP.md`).

| Delta | As simulated | Silicon | What closes it |
| --- | --- | --- | --- |
| Core geometry and mesh size | 32×32 cores on 2×2 with the silicon depths, 2 MB banks and 1024-bit links (tile32: M1–M13 and C1 in CI); 16×8 / 8×8 cores on 2×2 (the small suites); **the silicon mesh itself — 8×8 tiles of 32×32 cores, silicon depths, 1024-bit links — built hierarchically since drop 0.35** (mesh8: the mesh-level tests M1–M3, M5–M7, M9, M12–M14 in the hdl-mesh8 job; mesh8s, the same mesh on 16×8 cores, stays as the fallback variant) | 32×32 on 8×8 | parameters (ROWS, COLS, NX, NY, XW, YW). The flat 64-tile model never verilated in CI's 16 GB (drop 0.34; run 115 measured the 16×8-core variant at 14.0 GB): Verilator flattens the design per instance, so 64 tiles cost 64× one tile. Closed by hierarchical verilation (drop 0.35): `neo_tile` is a hierarchical block, verilated once and instantiated 64 times — 36 s and 0.76 GB for the silicon mesh. Its two conditions are design facts now: the tile's coordinates are ports strapped by the mesh (one and the same module in every position, as the hard macro will be), and the testbench's backdoor, fault and observation hooks are `dv_*` ports gated by `DV_HOOKS` (absent from the silicon tile) rather than hierarchical references. The block's relaunch of the `verilator` wrapper needs a `VERILATOR_ROOT` that holds the programs and the include tree alike; a packaged Verilator splits them (Ubuntu's: run 126), so the hierarchical builds export one (`vroot.sh`, drop 0.37) |
| Link width | closed in drop 0.25: WPF = 32 on the 32×32 builds (tile32, mesh8), WPF = 1 on the small builds | 1024-bit links, 32 words per flit | `link_pack.sv` between the tile interface and its router; the router is width-parameterized |
| Bank port width (found while closing the link delta) | stage 1 closed in drop 0.28 (512-bit bank, 16 SECDED lanes, MBIST over rows); stage 2 in drop 0.29 (serve and writeback at 16 words per cycle, CRC over a beat, `bank_word_port` removed); stage 3 in drop 0.30 (fetch receive at entry rate; the performance model re-run with the RTL's endpoint rates); the drain in beats and the partial sums at beat rate in drop 0.31; the fetch two entries per cycle through word-wide buffer ports in drop 0.32 | closed: every endpoint of a flow moves the 16-word beat (64 B/cycle), the design's per-flow ceiling | — (a wider beat would be a new link/bank decision, not a stage of this item) |
| Bank size, accumulator depth | 4,096 words; ACC_ROWS 64 | 512K words (2 MB); ACC_ROWS 512 | parameters BANK_DEPTH, ACC_ROWS (module default already 512) |
| Memory arrays | behavioural leaves `neo_mem_sp` / `neo_mem_rf` (drop 0.40); since drop 0.41 the activation buffer, the weight buffer and the accumulator read on the synchronous port B (1W1R macros, port A tied off) with the feeder's addresses incremental and the accumulate a two-cycle forwarded read-modify-write, and the NIC's `prow` a leaf; the NIC's drain FIFO still reads at its head pointer | compiler macros with registered reads (an SRAM reads in a cycle) | the swap is one module per kind (`09_synthesis/memories.md`, the port column per leaf); the drain FIFO's head read is the one asynchronous read left (A2); the storage the synchronous reads add behind the R7 / R10 comparators (the port-B address latches, `w_load_q`, the feeder's `inb_q` / `a0_q`, the accumulate's stage-0 control) is a D2 item — compared or shown covered |
| Address widths | addr[19:0], len[11:0], base[15:0] in the DMA instruction | sized for 2 MB banks and 512-row chunks | instruction field widths (addr needs 19 bits for 512K words: fits); the bank allocator (drop 0.33) keeps every region inside its bank, so no address ever exceeds the field |
| Safety island, PHYs, controllers, HSM, ISP | not in RTL | licensed IP per the RFQ pack | integration (**IP**); the RTL side of each interface — AXI bridges on edge tile positions, the control plane, the error vector — is **planned** (phase 2, `ROADMAP.md` §4) |
| LBIST | not in RTL | inserted by Modus | DFT (**IP** / implementation item; the SELFTEST and watchdog hooks are RTL-proven, M8) |
| Sparsity engine, FP8/INT4 modes, transformer ops, vector DSP | not in RTL (INT8 dense only) | spec v2.1 features | **planned**: the 2:4 sparsity engine in phase 3 (`ROADMAP.md` §2), the vector unit, attention tiling and INT4 weight-only storage in phase 4 (§3), written against the same core and tile contracts; FP8 / BF16 arithmetic is a gen-2 study decided with phase 1's area numbers. Until then the 524 sparse TOPS and every INT4 figure are targets, not capabilities of this package |
| Simulator | neosim (SystemVerilog subset) | Xcelium sign-off | `make xrun`, `make xrun_core`, `make xrun_tiles` first |

## 11. Software contract

Status: **RTL-proven** — the compiler's descriptors and programs run bit-exactly on the mesh (M6, M7, M10, M18, M19), the C
driver matches the Python backend word for word in every CI run, and the reference semantics are the comparators of every
test; the ONNX importer, post-training quantization and the runtime over the chip top's queues are **planned** (phase 8).

- Compiler: `neo_compile.py` lowers a convolution to GEMM tiles and (output tile, M-chunk) groups, chooses the
  K-split factor by makespan; `neo_backend.py` places owners by longest-processing-time balance and contributors
  nearest-free, lays out banks through `neo_alloc.py` (drop 0.33: regions in 16-word bank rows — activations in bands
  of rows per channel tile aligned to the producing layer's output chunks, weights resident per output tile, a
  layer's input released once it is compiled, a tile-exclusion mask), and emits per-tile descriptors and DMA programs
  with partial activation fetch (one FETCH_A per band the window overlaps) and INT8 output for every layer but the last.
- Driver: `sw/neo_host.c` emits the identical descriptors and programs (`neo_compile_group`) and loads a tile through
  the register map (`neo_load_and_start`, `neo_wait_done`); checked word for word against the Python backend.
- Reference semantics: convolution as in `direct_conv`; requantization as `rq_model` (round-half-up, saturate);
  both bit-exact against the RTL in the regressions.

## 12. Verification status (see `logs/` and the package README)

Status of the record: CI run 133 (8 Oct 2026, main c4e0740) is the proof behind every **RTL-proven** tag of this document — the
hdl-sim job (lint gate, the Icarus and Verilator SV testbenches, cocotb tile / tile8 / tile32 / core32, the coverage builds with 53 of
53 functional bins and 97.7 / 96.3 / 97.4 % line / branch / toggle), the hdl-mesh8 job (the silicon mesh, 11 of 11) and
python-regress (neosim, compiler, tool_qual 130 / 130) all green; the logs are on the `ci-logs-<job>` branches and the per-drop
read-outs in `drop_log.md`.

Lint (drop 0.35): `make lint` runs Verilator 5.020 `-Wall` with nothing waived on `neo_core`, `noc_mesh`, `neo_tile_axil` and
`tile_mesh`, 0 warnings, every warning fails the hdl-sim job (102 warnings cleared in the RTL: explicit widths on the
beat-path arithmetic, source and column indices at their own widths, unused-bit sinks named `unused_*`, dead parameters removed).
Testbench hooks (`DV_HOOKS = 1` on the cocotb and SV builds only): bank backdoor, fault injection into bank word / program
memory / descriptor register / DMA pc / router output register / NIC transmit engine, router observation — ports of the tile,
so the tile is a hierarchical block of the Verilator build; the cocotb wrapper packs every per-tile port, the twelve sticky flags included (`flags`, drop 0.37). The tile-internal hooks write at the falling edge; the two router hooks act on the flit the output register loads at the rising edge (drop 0.38: the register's reader is the neighbouring tile, and a falling-edge write crossed that boundary a cycle late in the flat 32×32 build).

Synthesis (drop 0.40): the `synth` job (`07_ci/synth`, `ci-logs-synth`) synthesizes every unit of the silicon tile with Yosys
(slang front end) on ASAP7 and times each with OpenSTA, the children as extracted timing models; its gates — every unit
synthesizes, no latch, no multiply-driven net, a consistent hierarchy, the PE count 1,056 / 144, no `dv_*` logic at
`DV_HOOKS = 0` anywhere in the tree, every timing run complete — passed on the sandbox run of all 37 units (47 min); timing is
reported in §9, not gated. The leaf arrays changed no simulation result — run 146 (fix37 on main): hdl-sim green with every
figure of run 133 (cocotb tile 17 + M14 skipped at 46,282.52 ns, tile32 17 + M14 skipped at 162,147.02 ns, core32 3 of 3 at
16,290.00 ns, tile8 1 of 1, the coverage builds with 53 of 53 functional bins, the Verilator SV testbenches, the lint gate),
hdl-mesh8 11 of 11 on the 64-tile silicon mesh, python-regress green; the `synth` job's first run stopped in its OpenSTA
build on the runner (fix38 made that build diagnosable; run 151 showed the missing `FlexLexer.h`, `libfl-dev` fixed it) and run 153
was its first green run, reproducing the sandbox figures exactly.

Drop 0.41 (A1 + the formal job, four pull requests merged together; run 162 on the merged main): every job green — hdl-sim 65 min
(lint clean, the SV testbenches and `make crc16` ALL PASS, cocotb tile 17 + M14 skipped at 46,298.52 ns, tile8 1 of 1 at 1,451.50,
tile32 17 + M14 skipped at 162,214.52, core 4 of 4 at 8,521.50 and core32 4 of 4 at 17,473.50 with the new C4, coverage 53 of 53
bins), hdl-mesh8 11 of 11 at 117,778.01 ns, python-regress green, synth GATES all passed in 8.4 min with the §9 (0.41) figures, and
the new `formal` job: nine full proofs in 95 s (`02_dv/formal/README.md`: ecc39 SECDED exhaustive, `crc16_beat` equal to its frozen
serial form at BW 4 and 16, packer → unpacker round trip, the router's invariants, the two lockstep pairs, requant table parity, the
ABFT verdict). The end times moved by design: +2 cycles per descriptor (the feeder's synchronous read and the accumulator's write),
+1 per requant (three stages) and +1 per hop through the registered packer on the 32-word links.

Streaming core T1–T5 at 32×32 and 8×16; whole convolutions C1–C10 on the core with ten fault-injection cases;
NoC N1–N3 (N3: the one-hot router against the 0.40 router, 9,365 grants); requantization Q1–Q4; K-split K1–K3 (K3: back-to-back
partial sums to one accumulator row); SECDED exhaustive; MBIST B1–B3; the NIC I1–I14 (I13 the matrix CRC, I14 the registered packer against
the 0.40 one); tile mesh M1–M11 (DMA programs,
K-split with RDY, end-to-end CRC, region refill, bank ECC, compiler-emitted programs, M-chunks with partial fetch,
register-driven ESM/watchdog/self-test, lost flit and timeout, MBIST through registers); two-layer INT8 handoff
M10; C driver identical to the Python backend. All under neosim; Xcelium testbenches provided for the streaming
core, the core and the tile mesh. Under cocotb on Verilator in CI (drops 0.21–0.36): M1–M9, M11–M13 on 16×8 and
32×32 cores, M10 on 8×8 cores, M14 and M16 on the 8×8 mesh, C1 and C2 (the core's own fault hooks: feeder control,
lockstep sequencer, duplicated requantization, table parity) on the 16×8 and 32×32 cores, M15 (register read-back),
M16 (link parity), M17 (double-bit bank error), M18 (descriptor sweep: kernel 1/3/5, stride 1/2, one to four channel
tiles) and M19 (INT8 drain against the requantization model) since drop 0.36; functional coverage over the
descriptor space, DMA ops, drain modes and every safety flag (`coverage_report.md` on the hdl-sim log branch).
