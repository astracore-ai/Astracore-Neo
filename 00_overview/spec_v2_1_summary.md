# AstraCore Neo — spec v2.1 at handoff (one page)

| Item | Value | Where it comes from |
| --- | --- | --- |
| Product | Gen 1: ADAS inference accelerator + ASIL-D safety island, host-attached over PCIe Gen5 x8; sold as Neo 262 / 196 / 131 bins of one die; gen 1.5 two-die package for L3; gen 2 Neo-L small die for the mandate segment | decisions 1, 9; product strategy |
| Process, qualification | TSMC N5A; AEC-Q100 Grade 2 (Ta −40..+105 °C), Tj −40..+125 °C, 15-year profile | decision 3 |
| Compute | 64 tiles × 1,024 INT8 MACs (32×32 weight-stationary systolic + ABFT check column) = 65,536 MACs; 2.0 GHz worst-case corner; 262 TOPS dense INT8, 524 TOPS 2:4-sparse; INT4/MXFP4 2×, FP16/BF16 ½× | decision 2; model/neo_feasibility.py |
| Memory | 128 MB shared SRAM as 64 banks × 2 MB, one per tile on its router's local port, (39,32) SECDED per word, MBIST; per-core weight buffer with SECDED per lane and activation buffer with regions; 384-bit LPDDR5X-8533 (410 GB/s raw) with inline ECC — decision 5 still open against 256-bit | spec v2.1 (drop 0.6); decision 5 |
| Interconnect | 2-D mesh of 64 tiles, 1024-bit links (128 B/cycle/direction), XY routing, flit parity, per-message end-to-end CRC with word count, fetch timeout; 2048-bit links as fallback | spec v2.1 (drops 0.6–0.12) |
| Tile | router + bank + network interface (fetch, serve, drain INT32/INT8, partial sums, RDY flow control) + DMA program engine (16 entries) + host register interface + error-signaling module with watchdog + core | drops 0.7–0.13 |
| Core | pipelined PE (2 stages), 2·ROWS+COLS latency, double-buffered weights with swap token, sliding-window activation feeder with duplicated control, local INT32 accumulator with ABFT at drain and a reduce port for K-split, lockstep sequencer, duplicated requantization with table parity | drops 0.3–0.12 |
| Safety | ASIL-D systematic; island ASIL-D HW metrics; accelerator ASIL-B(D); item ASIL-D by decomposition with a diverse monitor (decision 4 open). FTTI budget: detect ≤ 10 ms, react ≤ 20 ms (item FTTI from the lead OEM). FMEDA v1: core SPFM 99.9 %, chip PMHF 62.3 FIT first-order, dominated by shared-SRAM SER pending foundry data | safety concept; safety/neo_fmeda_v1.xlsx |
| Mechanisms proven on RTL | ABFT check column; ABFT at drain; duplicated feeder control; lockstep sequencer; duplicated requantization; weight-buffer ECC; requant table parity; link parity; end-to-end CRC; lost-flit word count; fetch timeout; bank ECC; MBIST; reduction flow control; ESM, error pin, watchdog, checker self-test | FMEDA v1 Evidence sheet (18 rows) |
| Not proven on RTL | LBIST (tool-inserted; hooks and FTTI-windowed schedule in place) | — |
| Workloads (perf model v3, v2.1 fabric) | YOLOv8-m 1280×736 one camera: 1.32 ms (target ≤ 1.5 ms; compute floor 0.93 ms); 640²: 0.66 ms; DMS 0.01 ms; BEV 6-camera 12.4 ms (first-order); 8B INT4 LLM decode 13 ms/token (bandwidth-bound) | compiler/neo_perf_v3.py; feasibility model |
| Power (model) | ~56 W typical at 60 % utilization, Tj 105 °C; ~80 W peak; TDP SKUs 65 / 30 / 15 W; always-on island ≤ 2 W | model/neo_feasibility.py |
| Physical (±30 %) | ~210 mm² in N5A; FCBGA ~45 × 45 mm, ~2,000 balls; unit cost ~$105–130 at volume | feasibility model |
| Compiler | lowering (conv → GEMM tiles → groups), K-split choice, LPT scheduler across the mesh, per-tile descriptor and DMA program emission with partial activation fetch and INT8 output; C host driver identical word for word | compiler/, sw/ |
| Verification status | every block and the 2×2 tile mesh pass their regressions under neosim; Xcelium testbenches for the streaming core, the core and the tile mesh; assertion set and UVM skeleton; CI workflow | logs/, dv/, tb/ |
