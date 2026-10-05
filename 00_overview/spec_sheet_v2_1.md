# AstraCore Neo — Spec Sheet v2.1 (as implemented, handoff v0.14, 2026-10-05)

Every value below is read from the code in the package: RTL parameters and encodings from `01_rtl/`, the
compiler and backend from `03_models/compiler/`, the driver from `04_software/`, model numbers from
`03_models/` reports, and verification status from `logs/`. Where the silicon configuration differs from the
configuration that was simulated, both are given and the delta is named in section 10.

## 1. Product

| Item | Value |
| --- | --- |
| Device | AstraCore Neo, automotive ADAS inference accelerator with ASIL-D safety island, host-attached |
| Family | Neo 262 (64 tiles), Neo 196 (48), Neo 131 (32), Neo 65 (16): one die, tiles fused at test; gen 1.5 two-die package; gen 2 Neo-L |
| Host interface | PCIe Gen5 ×8 (IP, not in RTL); per-tile host register bus (in RTL, section 7) |
| Process | TSMC N5A |
| Qualification | AEC-Q100 Grade 2 (Ta −40..+105 °C), Tj −40..+125 °C, 15-year mission profile |
| Compliance | developed to ISO 26262 ASIL-D process; island ASIL-D HW metrics; accelerator ASIL-B(D); item ASIL-D by decomposition with a diverse monitor |

## 2. Compute

| Item | Silicon configuration | As simulated (tests) | Source |
| --- | --- | --- | --- |
| Tiles | 64, 8×8 mesh (NX = NY = 8) | 4, 2×2 mesh | `tile_mesh.sv` parameters NX, NY |
| Core array | 32×32 weight-stationary systolic + 1 ABFT check column (ROWS = COLS = 32) | 16×8 (core and mesh tests), 8×8 (two-layer test), 32×32 (streaming tests T1–T4, C3) | `neo_mac_core.sv` |
| MACs | 65,536 INT8 MACs + 2,048 check-column MACs | — | 64 × 32 × 32 |
| PE | INT8 × INT8 products, INT32 partial sums, 2-stage pipeline (PE_LAT = 2), double-buffered weight with swap token | same | `mac_pe.sv` |
| Core latency | PE_LAT·ROWS + COLS = 96 cycles (32×32) | 40 (16×8), 24 (8×8) | `neo_mac_core.sv` LATENCY |
| Clock | 2.0 GHz worst-case (SS, 125 °C, aged), 2.4 GHz typical | — | feasibility model |
| Peak throughput | 262 TOPS dense INT8; 524 TOPS 2:4-sparse (sparsity engine not in RTL) | — | 65,536 × 2 × 2.0 GHz |
| Check weight width | WCW = 8 + log2(ROWS) + 1 = 14 bits (32×32) | 13 (16 rows), 12 (8 rows) | `neo_mac_core.sv` |
| Fault hook | one PE (FAULT_ROW, FAULT_COL) adds 1 to its product while `fault_inject` is high; used by the island's checker self-test | same | `systolic_array.sv`, `host_if` SELFTEST |

## 3. Core blocks (per tile)

| Block | Function | Parameters as implemented |
| --- | --- | --- |
| `core_seq` (×2, lockstep) | runs a layer descriptor: every (channel tile, ky, kx) run, shadow weight loads overlapped with the previous run, swap tokens, drain of the accumulator, reduce state for K-split, `ct_free` pulse when a channel tile is finished; outputs compared every cycle (`seq_err`) | states IDLE, LOAD0, START, RUN, WAIT, DRAIN, DONE, REDUCE; run range `cfg_ct0/ky0/kx0`, `cfg_rn` |
| `act_feeder` | sliding-window activation feeder: for output pixel (oy, ox) and kernel position (ky, kx) reads pixel (oy·s + ky − p, ox·s + kx − p) or zeros; region = ct mod regions; output-row range; duplicated address generator and FSM with comparator (`ctrl_err`) | ABUF_DEPTH entries × ROWS bytes (256 entries simulated); regions a power of two (`cfg_regions_m1`) |
| `wbuf_mem` | weight buffer, one entry per tile row = COLS weights + check weight, stored as (39,32) SECDED lanes; corrected on the sequencer's read | WBUF_DEPTH entries (512 simulated); LANES = ⌈(COLS·8 + WCW)/32⌉ = 9 lanes (32×32), 3 (16×8) |
| `acc_bank` | INT32 accumulator, ACC_ROWS rows × (COLS + check) × 32 bit; K-tiles accumulate per row; ABFT check at drain; reduce port for partial sums from other tiles (local writes have priority) | ACC_ROWS = 512 (silicon, module default) / 64 (simulated) |
| `requant` (×2, compared) | per-channel: mult (16 bit) × acc, + 2^(shift−1), ≫ shift (5 bit), + zero point (8 bit), optional ReLU in the quantized domain, saturate to INT8; 2-cycle pipeline; odd parity per table entry checked on use | MW = 16, 32 table rows (one per column) |
| `neo_core` | sequencer pair + datapath + requant pair; flags: `array_abft`, `acc_abft`, `ctrl_err`, `seq_err`, `rq_err`, `wbuf_ce/ue`, `rq_tbl_perr` (all sticky, cleared by `err_clear`) | DV hooks: `fault_inject`, `ctrl_fault_inject`, `seq_fault_inject`, `rq_fault_inject`, `rq_tbl_fault_inject` |

Descriptor (20 × 16-bit registers + `cfg_m`), in host_if order 0x10–0x24: `cfg_h, cfg_w, cfg_ho, cfg_wo, cfg_oy0,
cfg_oy_n, cfg_iy0, cfg_s, cfg_p, cfg_k, cfg_ct_n, cfg_ct0, cfg_ky0, cfg_kx0, cfg_rn, cfg_contrib_n,
cfg_tile_pixels, cfg_regions_m1, (2 reserved), cfg_m` (rows to drain). A descriptor describes one output tile
(COLS output channels) over an output-row range of a layer, for a contiguous run range (K-split share).

## 4. Memory system

| Item | Silicon configuration | As simulated | Source |
| --- | --- | --- | --- |
| Shared SRAM | 128 MB as 64 banks × 2 MB, one bank per tile on its router's local port | 4 banks × 4,096 words | `sram_bank.sv` DEPTH, `bank_bist_wrap.sv` |
| Word protection | (39,32) SECDED per 32-bit word: single-bit corrected on read (`ecc_ce`), double detected (`ecc_ue`), both sticky | same | `ecc39.sv` (Hamming positions 1..38 + overall parity) |
| MBIST | March C− with backgrounds 0x00000000/0xFFFFFFFF and 0x55555555/0xAAAAAAAA (12 elements); owns the bank ports while active; reports first failing address; a read corrected by ECC during the sweep is reported as a fault (`fail_ce`) | BIST_WORDS = BANK_DEPTH (16 words in M11 for speed) | `mbist.sv` |
| Activation layout in a bank | row-major per channel tile: entry = ROWS channels as bytes (channel c in byte c mod 4 of word c div 4), WPA = ROWS/4 words per pixel | WPA = 4 (16 rows), 2 (8 rows) | `pack_words`, `tile_nic` fetch assembly |
| Weight layout in a bank | per run (ct, ky, kx), ROWS entries of COLS weight bytes followed by the check weight; WPW = ⌈(COLS·8 + WCW)/32⌉ words per entry | WPW = 9 (32×32), 3 (16×8 and 8×8) | same |
| Result layout | INT32: COLS words + check word per output row; INT8: COLS/4 words per row (= one activation entry when COLS = ROWS) | — | `tile_nic` drain modes |
| External memory | 384-bit LPDDR5X-8533, 410 GB/s raw, inline ECC (IP, not in RTL; decision 5 open against 256-bit) | — | spec |

## 5. Interconnect and tile interface

| Item | As implemented | Source |
| --- | --- | --- |
| Topology | 2-D mesh, 5-port routers (N, S, E, W, local), XY dimension-order routing, 2-entry input FIFO per port, round-robin arbitration per output, registered outputs with ready/valid | `noc_router.sv`, `noc_mesh.sv` |
| Flit | FW = 1 + 2·(XW + YW) + 8 + DW bits = {even parity, dst_y, dst_x, src_y, src_x, seq[7:0], payload[DW−1:0]}; DW = 64; XW = YW = 3 on 8×8 (FW = 85), 2 on 2×2 (FW = 81) | `noc_router.sv` |
| Parity | even parity over the whole flit checked at the head of every input FIFO; a bad flit is dropped and flagged (`parity_err`) | `noc_router.sv` |
| Payload | {type[2:0], tag[8:0], data[51:0]}; one 32-bit data word per RDRSP/WRDATA/PSUM flit | `tile_nic.sv` |
| Message types | 1 RDREQ (addr[19:0], len[11:0]), 2 RDRSP (word), 3 WRHDR (bank address), 4 WRDATA (word), 5 PSUM (row index in tag, word index in data), 6 CRC ({count[15:0], crc16[15:0]}), 7 RDY | `tile_nic.sv` |
| Message protection | CRC-16-CCITT over the data words of every stream, plus the word count: receiver flags `crc_err` on mismatch and `lost_err` on a count mismatch; one message in flight per sending tile; receiver keeps CRC and count state per source | `crc16_word.sv`, `tile_nic.sv` |
| Fetch engine | RDREQ to any bank; responses assembled into activation entries (WPA words) or weight entries (WPW words) from a base entry; `fetch_timeout` after FETCH_TIMEOUT cycles (8,192 default) abandons the fetch and raises the flag | `tile_nic.sv` |
| Bank server | serves RDREQ sequentially, one word per cycle while the link accepts (pipelined read), closes with the CRC flit | `tile_nic.sv` |
| Drain | INT32 rows or INT8 requantized rows to a bank address (WRHDR + WRDATA + CRC), or PSUM rows to an owner's reduce port; drain FIFO DFD = ACC_ROWS rows; RDY notification from an owner gates a contributor's PSUM stream (flow control) | `tile_nic.sv` |
| Link width delta | spec v2.1 calls for 1024-bit links (128 B/cycle/direction); the RTL as simulated moves one 32-bit word per flit per cycle. The protocol is width-agnostic; packing 32 words per 1024-bit flit in the server, fetch and drain serializers is the implementation change (section 10) | — |

## 6. DMA program engine (per tile)

16 entries × 64-bit instructions: `op[63:60], x[59:56], y[55:52], addr[51:32], len[31:20], base[19:4], arg[3:0]`.

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

| Address | Register | Function |
| --- | --- | --- |
| 0x00 | CTRL | w: bit 0 program start (pulse), bit 1 error clear (pulse) |
| 0x01 | STATUS | r: bit 0 prog_done, bit 1 drain_busy, bit 2 core done (latched since start), bits 8–15 parity, crc, array_abft, acc_abft, ctrl, seq, rq, ecc_ue; bit 16 ecc_ce |
| 0x02–0x04 | PROG_ADDR, PROG_LO, PROG_HI | program entry index; low word; high word (writing HI commits the entry) |
| 0x05 | ERR_MASK | w/r: bit i masks cause i from `err_pin` (bit 16 = watchdog) |
| 0x06 | ERR_CAUSE | r: latched causes (bit 16 = watchdog) |
| 0x07 | WD_CTRL | w: bit 0 enable, [31:8] window in cycles |
| 0x08 | WD_KICK | w: any write kicks the watchdog |
| 0x09 | SELFTEST | w: bit 0 asserts the core's fault-injection hook |
| 0x0A | MBIST | w: start; r: bit 0 active, bit 1 done, bit 2 fail, bit 3 fail found by ECC, [31:16] failing address |
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

| Item | Value |
| --- | --- |
| Decomposition | item ASIL-D = accelerator ASIL-B(D) + independent diverse monitor ASIL-B(D); safety island (2 × Cortex-R52+ lockstep, IP) ASIL-D |
| FTTI budget | detect ≤ 10 ms, react ≤ 20 ms (item FTTI from the lead OEM; 100 ms assumed) |
| Mechanisms proven on RTL by fault injection | ABFT check column (T2–T4, C4); ABFT at drain over the reduction (K2); duplicated feeder control (C5); lockstep sequencer (C7); duplicated requantization (C8); weight-buffer ECC (C9); requant table parity (C10); link parity (N2); end-to-end CRC (M3); bank ECC in flight (M5); lost-flit count (M9a); fetch timeout (M9b); reduction flow control (M2); ESM, error pin, watchdog, checker self-test (M8); MBIST (B1–B3, M11); SECDED code exhaustive (ecc39) |
| Not in RTL | LBIST (tool-inserted; SELFTEST register and FTTI-windowed watchdog are its hooks) |
| FMEDA v1 | core SPFM 99.9 %, LFM ≈ 100 %, PMHF 0.43 FIT per core; chip PMHF 62.3 FIT first-order, dominated by shared-SRAM SER (600 FIT/Mbit assumed, foundry data required); every row's coverage claim names its proving test; fault-simulation campaign on Xcelium replaces the claims at RTL freeze |

## 9. Performance, power, physical (models)

| Item | Value | Source |
| --- | --- | --- |
| YOLOv8-m class, 1280×736, one camera | 1.32 ms (56.7 % MAC utilization, busiest link 17 %, busiest bank 14 %); compute floor 0.93 ms | perf model v3, 64 tiles, 64 banks, 128 B/cycle links, K-split, dataflow across layers |
| YOLOv8-m class, 640² | 0.66 ms | same |
| Static compile of YOLOv8-m, 640², 8×8 mesh | 83 layers, 6,263 groups, 24,549 tile programs (1.8 MB of descriptors), K-split shares 1–9, balance mean 1.16 (worst 2.46), per frame: 828 MB activation fetch, 414 MB partial sums, 187 MB outputs | `neo_backend.py` |
| Other workloads (first-order roofline) | DMS 0.01 ms; 6-camera YOLOv8-m 6.4 ms; BEV fusion 12.4 ms; radar+lidar occupancy 1.3 ms; 8B INT4 LLM decode 13 ms/token, prefill 512 tokens 52 ms | feasibility model |
| Power | ~56 W typical at 60 % utilization (0.45 pJ/MAC system-level at 0.75 V, 11 W uncore, 9.8 W leakage); ~80 W at 100 %; TDP SKUs 65 / 30 / 15 W; always-on island ≤ 2 W | feasibility model |
| Die, package, cost (±30 %) | ~210 mm² N5A; FCBGA ~45×45 mm, ~2,000 balls; ~$105–130 at volume | feasibility model |

## 10. Deltas between the simulated RTL and the silicon configuration

| Delta | As simulated | Silicon | What closes it |
| --- | --- | --- | --- |
| Core geometry and mesh size | 16×8 / 8×8 cores on 2×2 | 32×32 on 8×8 | parameters (ROWS, COLS, NX, NY, XW, YW); the 32×32 core itself ran T1–T4 and C3 |
| Link width | one 32-bit word per flit per cycle | 1024-bit links, 32 words per flit | multi-word flit packing in `tile_nic` serve/fetch/drain; router is width-parameterized (DW) |
| Bank size, accumulator depth | 4,096 words; ACC_ROWS 64 | 512K words (2 MB); ACC_ROWS 512 | parameters BANK_DEPTH, ACC_ROWS (module default already 512) |
| Address widths | addr[19:0], len[11:0], base[15:0] in the DMA instruction | sized for 2 MB banks and 512-row chunks | instruction field widths (addr needs 19 bits for 512K words: fits) |
| Safety island, PHYs, controllers, HSM, ISP | not in RTL | licensed IP per the RFQ pack | integration |
| LBIST | not in RTL | inserted by Modus | DFT |
| Sparsity engine, FP8/INT4 modes, transformer ops, vector DSP | not in RTL (INT8 dense only) | spec v2.1 features | RTL to be written against the same core and tile contracts |
| Simulator | neosim (SystemVerilog subset) | Xcelium sign-off | `make xrun`, `make xrun_core`, `make xrun_tiles` first |

## 11. Software contract

- Compiler: `neo_compile.py` lowers a convolution to GEMM tiles and (output tile, M-chunk) groups, chooses the
  K-split factor by makespan; `neo_backend.py` places owners by longest-processing-time balance and contributors
  nearest-free, lays out banks, and emits per-tile descriptors and DMA programs with partial activation fetch and
  INT8 output for every layer but the last.
- Driver: `sw/neo_host.c` emits the identical descriptors and programs (`neo_compile_group`) and loads a tile through
  the register map (`neo_load_and_start`, `neo_wait_done`); checked word for word against the Python backend.
- Reference semantics: convolution as in `direct_conv`; requantization as `rq_model` (round-half-up, saturate);
  both bit-exact against the RTL in the regressions.

## 12. Verification status (see `logs/` and the package README)

Streaming core T1–T5 at 32×32 and 8×16; whole convolutions C1–C10 on the core with ten fault-injection cases;
NoC N1–N2; requantization; K-split K1–K2; SECDED exhaustive; MBIST B1–B3; tile mesh M1–M11 (DMA programs,
K-split with RDY, end-to-end CRC, region refill, bank ECC, compiler-emitted programs, M-chunks with partial fetch,
register-driven ESM/watchdog/self-test, lost flit and timeout, MBIST through registers); two-layer INT8 handoff
M10; C driver identical to the Python backend. All under neosim; Xcelium testbenches provided for the streaming
core, the core and the tile mesh.
