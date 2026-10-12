# Neo NPU IP — product datasheet (preliminary, v0.16, 2026-10-11)

**What it is.** A licensable, safety-rated AI inference accelerator block for automotive SoCs: a mesh of 4 to 64
identical tiles, each a 32×32 INT8 systolic core with its own 2 MB ECC SRAM bank, network interface, DMA program
engine, host register interface and error-signaling module. Delivered as parameterized RTL with the compiler,
runtime, verification kit, implementation kit and ISO 26262 safety package; a two-tile test chip (2027) provides
the silicon proof. The licensee puts it inside their own SoC next to their CPU cluster, safety island and PHYs.

**Status tags (drop 0.39).** This sheet uses the tags of `00_overview/spec_sheet_v2_1.md` §0, the claims ledger: **RTL-proven**
(in the delivered RTL and exercised by a named test at the stated scale — the 64-tile mesh of 32×32 cores is simulated in CI),
**planned** (not in the RTL; a design and a phase in `00_overview/ROADMAP.md`), **IP** (the licensee's or a licensed block, outside
this RTL by design) and **model** (a feasibility-model figure, ±30 %, not measured; what replaces it is named).

**Why it is different.** The incumbents reach ASIL-B/D by duplicating the accelerator. Neo reaches ASIL-B(D)
hardware metrics with mechanisms built into the datapath: an algorithm-based fault-tolerance check column
(3.1 % of the array), ECC on every memory, end-to-end CRC with word counts on every transfer, a lockstep sequencer,
a duplicated requantization stage, a built-in self-test of the checkers and a March C− memory BIST, about 5 % of
tile area in total. Every mechanism has a fault-injection test in the delivery.

## Configurations (N5A, 2.0 GHz worst-case corner, first-order ±30 %)

Status: the tile count, the MAC count behind the dense TOPS and the SRAM are **RTL-proven** (the mesh is parameterized in NX × NY and
simulated at 2×2 and 8×8); the 2.0 GHz clock, the area and the power columns are **model** figures until the open-PDK synthesis
(ROADMAP phase 1) and the physical proxy (phase 7) replace them; the **2:4-sparse column is planned** (phase 3) — no sparsity engine
exists in the delivered RTL, and the column is a target of that phase, not a capability of release 1.

| Tiles | Dense INT8 TOPS (RTL count × model clock) | 2:4-sparse TOPS (**planned**, phase 3) | On-block SRAM | Area (model) | Typical power (60 %, Grade 2; model) | Typical power (Grade 1; model) | Peak power (model) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 4 | 16 | 33 | 8 MB | 3.0 mm² | 2.4 W | 2.5 W | 3.9 W |
| 8 | 33 | 66 | 16 MB | 5.5 mm² | 4.8 W | 5.0 W | 7.7 W |
| 16 | 66 | 131 | 32 MB | 10.9 mm² | 9.6 W | 10.0 W | 15.5 W |
| 32 | 131 | 262 | 64 MB | 20.9 mm² | 19.1 W | 20.1 W | 30.9 W |
| 64 | 262 | 524 | 128 MB | 40.7 mm² | 38.3 W | 40.1 W | 61.9 W |

Per tile: 4.1 TOPS peak, 0.98 W at 100 % (4.2 TOPS/W), 0.61 W at 60 %; efficiency point 1.6 GHz at 0.65 V:
3.3 TOPS peak at 5.4 TOPS/W. Grade 1 (Tj 150 °C) adds ~0.035 W of leakage per tile. Area per tile 0.62 mm² with the
bank, 0.13 mm² without it (for licensees who bring their own memory system). All of these are **model** figures
(`neo_ip_ppa.txt`). The sparse column assumes the 2:4 sparsity engine of ROADMAP phase 3 (release 2): it is **not in the
delivered RTL** — no sparse PE, index path, compressed weight format, compiler packer or test — and no other figure of this
sheet depends on it.

## Functional summary

| Item | Specification | Status |
| --- | --- | --- |
| Datatypes | INT8 × INT8 → INT32 (release 1); INT4/MXFP4 and FP8 (release 2); FP16/BF16 at half rate (release 2) | INT8 **RTL-proven**; INT4 weight-only storage **planned** (phase 4, expanded to INT8 at the weight-buffer load — no throughput multiplier); FP8 / BF16 a gen-2 study |
| Operators | 2-D convolution (any kernel, stride, padding), GEMM, depthwise, pooling, elementwise and activation through the requantization stage; transformer attention through GEMM; op set frozen as a static graph for tool qualification | convolution (kernel 1 / 3 / 5, stride 1 / 2, padding, channel tiles, K-split) and GEMM (k = 1) **RTL-proven** (M18, M6 / M7); ReLU in the requantizer **RTL-proven**; depthwise, pooling, general elementwise, softmax / layer-norm and attention tiling **planned** (phase 4's vector unit and compiler operators) |
| Per-tile memory | 2 MB SECDED bank, 64 KB activation buffer with regions, 1,024-entry weight buffer with per-lane SECDED, 512-row INT32 accumulator | **RTL-proven** at these depths (tile32, mesh8); the arrays are inferred — macros per `09_synthesis/memories.md`, their area **model** until a compiler run |
| Interconnect | 2-D mesh, 1024-bit links, XY routing, flit parity, per-message CRC-16 with word count, fetch timeout, RDY flow control for reductions | **RTL-proven** (8×8 mesh at 1024 bits in CI) |
| Programming | per-tile 32-entry DMA program (fetch, drain INT32/INT8, go, waits, notify) and a 21-register descriptor; host register map (8-bit address, 32-bit data), AXI-Lite/APB wrapper | **RTL-proven** (the programs and registers in every mesh test; `neo_tile_axil` lint-clean, its AXI-Lite path under neosim); an aggregator over many tiles **planned** (phase 2) |
| Host interfaces | register bus per tile or through an aggregator; data through the licensee's NoC into the banks (AXI bridge in release 1) | register bus **RTL-proven**; the AXI bridges on edge tile positions and the control plane **planned** (phase 2, drops 0.43–0.45); the NoC, DMA and memory controllers beyond them **IP** |
| Safety | SEooC, ASIL-B(D) HW metrics target for the block (SPFM ≥ 99 %, LFM ≥ 90 %, PMHF < 1 FIT per tile), ASIL-D systematic process; error pin and cause registers for the licensee's safety island; FTTI detect ≤ 10 ms | the mechanisms and the error signalling **RTL-proven** by fault injection (every flag bin covered); the metric values **model** (FMEDA v1, analytical) until the fault campaign's FMEDA v2 (phase 6); the island **IP** |
| Test | March C− MBIST per bank, checker self-test, scan-ready; LBIST via the licensee's DFT flow | MBIST and self-test **RTL-proven** (M11, M8); scan and LBIST insertion **IP** (the DFT flow) |
| Process | soft IP, portable; reference implementation N5A at Grade 1 and Grade 2 corners; N3A and N6 reference flows on request | **model**: the reference implementation does not exist yet — phase 1 synthesizes on the open ASAP7 / Nangate45 proxies, phase 7 places and routes the core and the tile on ASAP7; foundry flows are outside the open tools |
| Software | ONNX/PyTorch importer, compiler (lowering, scheduling, per-tile program emission), C runtime and driver, bit-exact reference model; tool-confidence evidence (ISO 26262-8 Clause 11) through verified output | compiler, backend, C driver and reference model **RTL-proven** (bit-exact on the mesh; tool_qual 130 / 130); the ONNX / PyTorch importer, quantization and the runtime over the chip top's queues **planned** (phase 8) |

## Deliverables (technical)

Design (RTL, integration guide), verification kit (testbenches, assertions, UVM, cocotb, golden models and vectors),
implementation kit (constraints, reference flow, PPA reports, reference hard macro per process), software (compiler,
runtime, driver, reference model), safety package (safety manual, FMEDA, fault-injection suite), silicon report.

## Status

RTL and compiler in place: the 64-tile mesh of 32×32 cores with 2 MB banks and 1024-bit links is simulated at full scale
under cocotb on Verilator in CI (run 133, 8 Oct 2026: every job green; functional coverage 53 of 53 bins, line / branch /
toggle 97.7 / 96.3 / 97.4 %), every safety mechanism proven by fault injection, the RTL lint-clean under four `-Wall`
passes, the Xcelium testbenches delivered. PPA is from first-order models. The next steps are those of
`00_overview/ROADMAP.md`: synthesis and timing on an open PDK (phase 1), the chip top with its AXI bridges and control plane
(phase 2), the sparsity engine (phase 3), transformer readiness (phase 4), power mechanics (phase 5), formal proofs and the
fault campaign that produce FMEDA v2 (phase 6), the physical proxy (phase 7) and the software stack (phase 8). The 2027
test chip produces measured numbers; the assessor's certificate follows the fault-simulation campaign.
