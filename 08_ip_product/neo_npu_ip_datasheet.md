# Neo NPU IP — product datasheet (preliminary, v0.15, 2026-10-05)

**What it is.** A licensable, safety-rated AI inference accelerator block for automotive SoCs: a mesh of 4 to 64
identical tiles, each a 32×32 INT8 systolic core with its own 2 MB ECC SRAM bank, network interface, DMA program
engine, host register interface and error-signaling module. Delivered as parameterized RTL with the compiler,
runtime, verification kit, implementation kit and ISO 26262 safety package; a two-tile test chip (2027) provides
the silicon proof. The licensee puts it inside their own SoC next to their CPU cluster, safety island and PHYs.

**Why it is different.** The incumbents reach ASIL-B/D by duplicating the accelerator. Neo reaches ASIL-B(D)
hardware metrics with mechanisms built into the datapath: an algorithm-based fault-tolerance check column
(3.1 % of the array), ECC on every memory, end-to-end CRC with word counts on every transfer, a lockstep sequencer,
a duplicated requantization stage, a built-in self-test of the checkers and a March C− memory BIST, about 5 % of
tile area in total. Every mechanism has a fault-injection test in the delivery.

## Configurations (N5A, 2.0 GHz worst-case corner, first-order ±30 %)

| Tiles | Dense INT8 TOPS | 2:4-sparse TOPS | On-block SRAM | Area | Typical power (60 %, Grade 2) | Typical power (Grade 1) | Peak power |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 4 | 16 | 33 | 8 MB | 3.0 mm² | 2.4 W | 2.5 W | 3.9 W |
| 8 | 33 | 66 | 16 MB | 5.5 mm² | 4.8 W | 5.0 W | 7.7 W |
| 16 | 66 | 131 | 32 MB | 10.9 mm² | 9.6 W | 10.0 W | 15.5 W |
| 32 | 131 | 262 | 64 MB | 20.9 mm² | 19.1 W | 20.1 W | 30.9 W |
| 64 | 262 | 524 | 128 MB | 40.7 mm² | 38.3 W | 40.1 W | 61.9 W |

Per tile: 4.1 TOPS peak, 0.98 W at 100 % (4.2 TOPS/W), 0.61 W at 60 %; efficiency point 1.6 GHz at 0.65 V:
3.3 TOPS peak at 5.4 TOPS/W. Grade 1 (Tj 150 °C) adds ~0.035 W of leakage per tile. Area per tile 0.62 mm² with the
bank, 0.13 mm² without it (for licensees who bring their own memory system). Sparse figures assume the 2:4
sparsity engine planned for the second release.

## Functional summary

| Item | Specification |
| --- | --- |
| Datatypes | INT8 × INT8 → INT32 (release 1); INT4/MXFP4 and FP8 (release 2); FP16/BF16 at half rate (release 2) |
| Operators | 2-D convolution (any kernel, stride, padding), GEMM, depthwise, pooling, elementwise and activation through the requantization stage; transformer attention through GEMM; op set frozen as a static graph for tool qualification |
| Per-tile memory | 2 MB SECDED bank, 64 KB activation buffer with regions, 1,024-entry weight buffer with per-lane SECDED, 512-row INT32 accumulator |
| Interconnect | 2-D mesh, 1024-bit links, XY routing, flit parity, per-message CRC-16 with word count, fetch timeout, RDY flow control for reductions |
| Programming | per-tile 16-entry DMA program (fetch, drain INT32/INT8, go, waits, notify) and a 21-register descriptor; host register map (8-bit address, 32-bit data), AXI-Lite/APB wrapper |
| Host interfaces | register bus per tile or through an aggregator; data through the licensee's NoC into the banks (AXI bridge in release 1) |
| Safety | SEooC, ASIL-B(D) HW metrics target for the block (SPFM ≥ 99 %, LFM ≥ 90 %, PMHF < 1 FIT per tile), ASIL-D systematic process; error pin and cause registers for the licensee's safety island; FTTI detect ≤ 10 ms |
| Test | March C− MBIST per bank, checker self-test, scan-ready; LBIST via the licensee's DFT flow |
| Process | soft IP, portable; reference implementation N5A at Grade 1 and Grade 2 corners; N3A and N6 reference flows on request |
| Software | ONNX/PyTorch importer, compiler (lowering, scheduling, per-tile program emission), C runtime and driver, bit-exact reference model; tool-confidence evidence (ISO 26262-8 Clause 11) through verified output |

## Deliverables (technical)

Design (RTL, integration guide), verification kit (testbenches, assertions, UVM, cocotb, golden models and vectors),
implementation kit (constraints, reference flow, PPA reports, reference hard macro per process), software (compiler,
runtime, driver, reference model), safety package (safety manual, FMEDA, fault-injection suite), silicon report.

## Status

RTL and compiler in place with every mechanism proven by fault injection under a subset simulator; Xcelium
testbenches delivered; PPA from first-order models. Phase 1 (12 months) produces the licensable release with
synthesis-based PPA and the FPGA demonstrator; the 2027 test chip produces measured numbers and the assessor's
certificate follows the fault-simulation campaign.
