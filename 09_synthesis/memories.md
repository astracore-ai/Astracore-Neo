# Memory macros for synthesis (drop 0.16; the leaves of drop 0.40; their FakeRAM2.0 LEF / LIB of drop 0.42)

Every array a memory compiler replaces lives in one of the two leaf modules of `01_rtl/neo_mem.sv` (drop 0.40), so that
the swap is one module per kind: `neo_mem_sp` (one write port of G write groups, a registered read port) and `neo_mem_rf`
(the same write port, an asynchronous read port A and, with RB = 1, a registered read port B). The behavioural arrays run in
every simulator; the open-PDK synthesis job (`07_ci/synth`) black-boxes the two modules and prices them with its macro model;
the implementation flow replaces them by the compiler's macros through the usual wrapper-and-swap (the leaf's ports are the
wrapper's). The testbench hooks (backdoor write / read, stuck-at, flip) are ports of the leaves under `DV_HOOKS`, tied off
in the silicon configuration.

| Instance (module.instance) | Leaf and organisation (silicon configuration) | Macro type | Notes | FakeRAM2.0 macro of the pnr job (LEF size, area) |
| --- | --- | --- | --- | --- |
| `sram_bank.u_mem` | `neo_mem_sp` 32,768 words × 624 bits, G = 16 (a write group per 39-bit SECDED lane), registered row read | single-port HD SRAM, 1 cycle; as 16 lane macros of 32,768 × 39 or as the compiler prefers (2 MB + ECC = 20.4 Mbit) | the ECC is in RTL around the leaf (16 encoders on the write side, 16 decoders on the read data); MBIST drives the bank's row ports through `bank_bist_wrap`; word a is lane a mod 16 of row a div 16  | `fakeram7_32768x624_g16_1w1r`: 80.94 × 10,617.60 µm, 859,389 µm² (0.0420 µm²/bit, the model at 1 ×); the 32,768-word column unfolded is a 131:1 strip -- a compiler folds it (column mux) or splits it into the 16 lane macros; drop 0.43 (A3): the pnr job's form, 16 lane macros `fakeram7_32768x39_cm64_1w1r` (one per write group, folded by a column mux of 64: 512 rows of 2,496 bit cells), 323.57 × 166.60 µm each, 862,508 µm² for the 16 (0.0422 µm²/bit) |
| `wbuf_mem.u_mem` | `neo_mem_rf` 512 words × 936 bits (drop 0.42, the NCHK = 4 core: a pair of coded entries, 12 lanes × 39 each; the entry is 376 bits = 32 × (8-bit weight + 2-bit index) + 4 check weights × 14 bits, one format for dense and 2:4 sparse layers), G = 2 (one entry per group), registered read of the pair on port B every cycle, port A tied off, RB = 1 (drop 0.41; until 0.40 an asynchronous read on port A) | one-write-one-read synchronous SRAM (468 kbit; 512 × 702 bits = 359 kbit with the 271-bit entry of 9 lanes of the dense-only core, NCHK = 1, which the RTL keeps as its default): address at the edge, data the cycle after -- the core delays the shadow-load strobe one cycle to match (`neo_core` `w_load_q`), in the shadow-load path that overlaps the previous run | per-lane SECDED in RTL behind the read; the entry inside the pair is selected after the read by the address bit of the previous cycle; the entry is `{idx[31..0], wc[3..0], w[31..0]}` LSB first (the weights and check column 0 where the 271-bit entry had them; a dense entry is the old one with zeros above), at 16x8 132 bits in 5 lanes (78 bits in 3 before)  | `fakeram7_512x702_g2_1w1r`: 91.01 × 166.60 µm, 15,162 µm² (0.0422 µm²/bit; the model's 2 × for a second read port: 30,226 µm²) (generated for the 0.41 entry of 702 bits; the NCHK = 4 entry is 936) |
| `act_feeder.u_mem` | `neo_mem_rf` 1,024 words × 624 bits (drop 0.44: a pair of SECDED-coded entries, each entry's 32 channels × 8 bits as 8 lanes of (39,32); 512 bits of data before drop 0.44), G = 2 (one write group per coded entry), registered read of the pair on port B (`rb_en` = a row generated), port A tied off, RB = 1 (drop 0.41; until 0.40 an asynchronous read on port A) | one-write-one-read synchronous SRAM (624 kbit = 0.609 Mbit; 512 kbit before): the feeder's incremental address generator presents the address at the edge, the 16 decoders and the entry select follow the registered read and the decoded row is registered (one cycle more of feeder latency, drop 0.44) | the SECDED is in RTL around the leaf (16 encoders on the DMA's beat, 16 decoders behind the read); regions are address ranges, no macro feature needed | `fakeram7_1024x512_g2_1w1r`: 66.50 × 331.80 µm, 22,065 µm² (0.0421 µm²/bit; model 44,091 µm² at 2 ×) -- generated for the 512-bit word; at 624 bits the model prices 26,868 µm² (+ 21.9 %), the macro to be regenerated with `fakeram.py --leaf` |
| `acc_bank.u_mem` | `neo_mem_rf` 512 words × 1,056 bits (COLS + 1 partial sums of 32 bits), G = 1, one registered read port B shared by the accumulate (at the row the arriving operation adds to) and the drain, port A tied off, RB = 1 (drop 0.41; until 0.40 port A read asynchronously for a same-cycle read-modify-write) | one-write-one-read synchronous SRAM (540 kbit): the read-modify-write takes two cycles (read, then add and write, with a one-stage forward for back-to-back operations on one row); a read and a write of different rows in one cycle | the ABFT check is on port B's data; the adders and the forward are in RTL  | `fakeram7_512x1056_g1_1w1r`: 136.99 × 166.60 µm, 22,823 µm² (0.0422 µm²/bit; model 45,468 µm² at 2 ×) |
| `tile_nic.u_dfifo` | `neo_mem_rf` 512 words × 1,069 bits (DFD = ACC_ROWS drain rows: {the parity of each of its 3 beats, the index's parity (drop 0.44), index, check sum, COLS partial sums}), G = 1, registered read of the head on port B at the read pointer's next value every cycle, port A tied off, RB = 1 (drop 0.42; until 0.41 1,065 bits read asynchronously on port A) | one-write-one-read synchronous SRAM (547 kbit): a pushed row is sendable from the second cycle after its push (the transmit engine counts the rows the port shows) | found by the synthesis of drop 0.40 as 545,280 flops: the drain FIFO is sized for a full accumulator drain without backpressure; the beat parities (formed at the push) make the transmitted flit's parity a select instead of an XOR tree over the beat  | `fakeram7_512x1065_g1_1w1a` (asynchronous read: an arc from every address bit): 138.13 × 166.60 µm, 23,012 µm² (0.0422 µm²/bit; model 34,392 µm² at 1.5 ×) (generated for the 0.41 width of 1,065 bits; A2a's registered head adds 3 parity bits) |
| `tile_nic.u_prow` | `neo_mem_rf` 64 words × 1,024 bits (NSRC = 64 sources: the partial-sum row under assembly per source, COLS = 32 columns of 32 bits), G = 32 (a write group per column: a PSUM beat writes the 1..16 columns it carries at its source's word), asynchronous read port A at the source of the beat carrying the check word, RB = 0 (drop 0.41) | register file (64 kbit, a write and a read port at the same word: the check beat writes the columns it carries at its source's word and reads the rest of that word in the same cycle, the read returning the old content); for a synchronous-read macro the check-word beat would wait one cycle while its row is read | found by the synthesis of drop 0.40 as 65,536 flops, 91 % of the NIC's; no reset: every column the check beat reads was written by an earlier beat of the same row (tile_nic.sv, run_nic.py I11c/I11d with the array filled with random bits)  | `fakeram7_64x1024_g32_1w1a`: 132.81 × 100.80 µm, 13,387 µm² -- pin-limited: 2,093 pins on the left edge at the 48 nm track need 100.5 µm where the 64 words need 20.7 µm (0.204 µm²/bit, 3.2 × the model's 4,133 µm²) |
| `prog_mem.mem` | 32 × (2 × 39) bit | flops | keep as flops; SECDED in RTL  | -- |
| `noc_router.fifo` | 2 × 1,077 bit per port | flops | keep as flops  | -- |
| `requant` tables | 32 × 29 bit | flops | parity in RTL  | -- |

The synthesis job's macro model (ASAP7: FakeRAM2.0's 7 nm bit cell, 0.042 µm² per bit, 218 ps access, 50 ps setup / hold;
Nangate45 since drop 0.42: ORFS's FakeRAM45 macros, 0.557 µm² per bit, 435 ps; a leaf priced by the read ports it uses --
1 × the bit cell for a registered read only, 1.5 × with the asynchronous port A, 2 × with both) stands in for the
compiler's figures in `synth_report.md`; `neo_ip_ppa.py`'s estimate prices the same arrays at N5A compiled-macro densities.
The synthesis report with the macros swapped in is what replaces both.

**LEF / LIB provenance (drop 0.42, stream A2b).** The place-and-route job (`07_ci/pnr`) makes one macro per leaf type
with `07_ci/pnr/fakeram.py`: FakeRAM2.0 as vendored in OpenROAD-flow-scripts at 3451ca1f5 (`tools/FakeRAM2.0`, the
commit `07_ci/synth/tools.sh` pins), ORFS's asap7 process parameters (`orfs_asap7/generate.py` = `platforms/asap7/
fakeram.cfg`: bit cell 2 CPP × 10 fin pitches = 0.108 × 0.270 µm, × 1.2 × 1.2 periphery, width snapped to 0.19 µm and
height to 1.4 µm, signal pins 24 nm M4 stubs on the left edge at the 48 nm track, the height grown to fit them, VDD / VSS
rails on M4, M1-M4 obstructed), FakeRAM2.0's LEF exporter and liberty writers, and this table's timing model (218 ps
access, 50 ps setup / hold, 157 ps minimum period, 5 fF inputs). The leaf's tied-off ports are not macro pins: the
macro has the functional ports under the leaf's names -- a 1-write-1-read SRAM (`clk`, `we[G]`, `waddr`, `wdata`,
`rb_en`, `rb_addr`, `rb_data` and, since drop 0.43, `rb_addr_q`: the port-B address readback the core compares, AW
clocked outputs; an RBQ = 0 user leaves it open) for the RB = 1 arrays, a register file with an asynchronous port A
(`ra_addr` -> `ra_data`) for the NIC's -- and a wrapper with the leaf's name and ports instantiates it, the hooks and
the unused port unconnected (`prep.py` checks the parent ties them off and reads nothing they drop). The last column
above is `fakeram.py --leaf` at the silicon parameters. Against the model: the arrays read on port B alone since drop
0.41 are 1-write-1-read macros at the single-port bit cell (0.042 µm²/bit), half the 2 × the model gives every RB = 1
leaf; the drain FIFO is 0.67 × its 1.5 ×; the partial-sum rows are pin-limited at 3.2 ×; the bank matches at 1 ×.
The 16 × 8 core the job places has three:

| Leaf (`c16_*`) | Macro | LEF size | Area | µm² / bit | Model |
| --- | --- | --- | --- | --- | --- |
| `c16_wbuf_mem.u_mem` `neo_mem_rf_w234_d256_g2_rb1` | `fakeram7_256x234_g2_1w1r` (496 pins) | 30.40 × 84.00 µm | 2,553.6 µm² | 0.0426 | 5,037.7 µm² (2 ×) |
| `c16_act_feeder.u_mem` `neo_mem_rf_w256_d128_g2_rb1` | `fakeram7_128x256_g2_1w1r` (537 pins) | 33.25 × 42.00 µm | 1,396.5 µm² | 0.0426 | 2,755.7 µm² (2 ×) |
| `c16_acc_bank.u_mem` `neo_mem_rf_w288_d64_g1_rb1` | `fakeram7_64x288_g1_1w1r` (597 pins, pin-limited) | 37.43 × 29.40 µm | 1,100.4 µm² | 0.0597 | 1,550.1 µm² (2 ×) |

Drop 0.43 adds `rb_addr_q` to these three: 8, 7 and 6 pins, regenerated with `fakeram.py --leaf`. The footprints are
unchanged, since the new pins fit within the height the bit cells or the other pins already set. The pnr job placed and
routed these three in `c16_neo_core` (run 168) and the NIC's two in `tile_nic` (run 174), ORFS's
macro placer putting them on the core's edges, 0 DRC after detailed routing: OpenROAD reads the LEF / LIB as written
(`pnr_c16_neo_core.md`, `pnr_tile_nic.md`).
