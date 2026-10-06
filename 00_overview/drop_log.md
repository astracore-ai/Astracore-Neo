# Drop log (2026-10-04 to 2026-10-05)

| Drop | What landed | Proof |
| --- | --- | --- |
| 0.1 | feasibility model; cycle-accurate systolic reference with ABFT tests; RTL v0.1 and Xcelium testbench; spec v2.0; development plan; product strategy | model T1–T5 |
| 0.2 | neosim SystemVerilog-subset simulator; RTL executed at 8×16 and 32×32; compiler lowering verified through cycle model and RTL; layer-level performance model; v0.2 requirements | T1–T3, compiler --verify |
| 0.3 | core v0.2 (double-buffered weights with swap token, activation feeder, accumulator with ABFT at drain); whole convolutions on RTL; FMEDA skeleton; safety concept draft | C1–C4 |
| 0.4 | sequencer RTL (one descriptor runs a layer); duplicated feeder control (R7); NoC router and 3×3 mesh with parity; Xcelium testbench for neo_core | C5, N1–N2 |
| 0.5 | pipelined PE (2.0 GHz contract); requantization and INT8 path; ct_free; 64-core event model; compiler group scheduling; findings R8 (K-split), R9 (banks) | C6, regress |
| 0.6 | cross-core K-split in RTL with ABFT travelling with partial sums; makespan-minimising split; bank layouts modelled; spec v2.1 memory system decided | K1–K2 |
| 0.7 | tile: bank, network interface (fetch, serve, writeback, partial sums, per-source state, end-to-end CRC), neo_tile, tile_mesh | M1–M3 |
| 0.8 | lockstep sequencer and duplicated requantization (R10); weight buffer split out; perf model v3 (dataflow across layers); affinity rejected with numbers; spec v2.1 applied | C7–C8 |
| 0.9 | DMA program engine per tile; activation regions refilled behind the core; reduction deadlock found and fixed with RDY flow control; (39,32) SECDED on banks | M4–M5, ecc39 |
| 0.10 | compiler backend emitting descriptors and programs executed as emitted; output-row range in descriptors; pipelined bank server; static compile of 83 layers; locality policies rejected | M6–M7 |
| 0.11 | host register interface; C host driver identical to the Python backend; assertion set with binds; UVM skeleton; CI; IP RFQ pack; assessor pack | host_if check, C driver check |
| 0.12 | weight-buffer ECC per lane; requant table parity; lost-flit detection and fetch timeout; ESM with watchdog and self-test registers; host_if and ESM inside the tile | C9–C10, M8–M9 |
| 0.13 | March C− MBIST integrated behind registers; INT8 drain and the first two-layer execution; backend LPT scheduler and INT8 output; FMEDA v1 with evidence; Xcelium testbench for tile_mesh | B1–B3, M10–M11 |
| 0.14 | full regression on the final RTL; this handoff package | logs/ |
| 0.15–0.19 | Verilator sign-off runs through GitHub Actions (see the engineering record): wbuf_mem/ecc39 multi-driver fix, portability fixes, program-memory SECDED and lockstep DMA, functional covergroups, cocotb mesh suite with the packed-port wrapper and bank backdoor | CI runs 1–29 |
| 0.20 | cocotb mesh suite made cycle-exact on Verilator: fetch watchdog at the RTL default (8192) for the whole suite (600 truncated every weight fetch), faults injected at the falling edge through wrapper ports (bank stuck-at, program memory, descriptor copy, DMA pc, router flit/valid, NIC serve_busy) with router observation ports, wrapper generalized to any NX×NY, diagnostics on mismatch; M10 (two-layer INT8 handoff) as the 8×8-core cocotb target; results checker fails the CI step on any failing test | CI run after fix17 |
