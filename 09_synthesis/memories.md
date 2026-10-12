# Memory macros for synthesis (drop 0.16; the leaves of drop 0.40)

Every array a memory compiler replaces lives in one of the two leaf modules of `01_rtl/neo_mem.sv` (drop 0.40), so that
the swap is one module per kind: `neo_mem_sp` (one write port of G write groups, a registered read port) and `neo_mem_rf`
(the same write port, an asynchronous read port A and, with RB = 1, a registered read port B). The behavioural arrays run in
every simulator; the open-PDK synthesis job (`07_ci/synth`) black-boxes the two modules and prices them with its macro model;
the implementation flow replaces them by the compiler's macros through the usual wrapper-and-swap (the leaf's ports are the
wrapper's). The testbench hooks (backdoor write / read, stuck-at, flip) are ports of the leaves under `DV_HOOKS`, tied off
in the silicon configuration.

| Instance (module.instance) | Leaf and organisation (silicon configuration) | Macro type | Notes |
| --- | --- | --- | --- |
| `sram_bank.u_mem` | `neo_mem_sp` 32,768 words × 624 bits, G = 16 (a write group per 39-bit SECDED lane), registered row read | single-port HD SRAM, 1 cycle; as 16 lane macros of 32,768 × 39 or as the compiler prefers (2 MB + ECC = 20.4 Mbit) | the ECC is in RTL around the leaf (16 encoders on the write side, 16 decoders on the read data); MBIST drives the bank's row ports through `bank_bist_wrap`; word a is lane a mod 16 of row a div 16 |
| `wbuf_mem.u_mem` | `neo_mem_rf` 512 words × 702 bits (a pair of coded entries, 9 lanes × 39 each), G = 2 (one entry per group), asynchronous read of the pair, RB = 0 | single-port SRAM or register file (359 kbit); the read must become registered for an SRAM macro (one cycle in the shadow-load path, which overlaps the previous run) | per-lane SECDED in RTL; the entry inside the pair is selected after the read |
| `act_feeder.u_mem` | `neo_mem_rf` 1,024 words × 512 bits (a pair of entries of 32 channels × 8 bits), G = 2, asynchronous read of the pair, RB = 0 | single-port SRAM or register file (512 kbit); the read must become registered for an SRAM macro, with the address pipelined (the feeder's address generator precedes it in the same cycle) | regions are address ranges, no macro feature needed |
| `acc_bank.u_mem` | `neo_mem_rf` 512 words × 1,056 bits (COLS + 1 partial sums of 32 bits), G = 1, asynchronous read port A at the written row (the read-modify-write), registered port B for the drain, RB = 1 | two-port register file / 2P SRAM (540 kbit); the same-cycle read-modify-write becomes the two-cycle one the RTL's comment promises when a macro replaces it | the ABFT check is on port B's data; the adders are in RTL |
| `tile_nic.u_dfifo` | `neo_mem_rf` 512 words × 1,065 bits (DFD = ACC_ROWS drain rows: {index, check sum, COLS partial sums}), G = 1, asynchronous read at the head pointer, RB = 0 | single-port SRAM or register file (545 kbit); the head read must become registered for an SRAM macro (one cycle on the drain's first beat) | found by the synthesis of drop 0.40 as 545,280 flops: the drain FIFO is sized for a full accumulator drain without backpressure |
| `tile_nic.u_prow` | `neo_mem_rf` 64 words × 1,024 bits (NSRC = 64 sources: the partial-sum row under assembly per source, COLS = 32 columns of 32 bits), G = 32 (a write group per column: a PSUM beat writes the 1..16 columns it carries at its source's word), asynchronous read port A at the source of the beat carrying the check word, RB = 0 (drop 0.41) | register file (64 kbit, a write and a read port at the same word: the check beat writes the columns it carries at its source's word and reads the rest of that word in the same cycle, the read returning the old content); for a synchronous-read macro the check-word beat would wait one cycle while its row is read | found by the synthesis of drop 0.40 as 65,536 flops, 91 % of the NIC's; no reset: every column the check beat reads was written by an earlier beat of the same row (tile_nic.sv, run_nic.py I11c/I11d with the array filled with random bits) |
| `prog_mem.mem` | 32 × (2 × 39) bit | flops | keep as flops; SECDED in RTL |
| `noc_router.fifo` | 2 × 1,077 bit per port | flops | keep as flops |
| `requant` tables | 32 × 29 bit | flops | parity in RTL |

The synthesis job's macro model (ASAP7: FakeRAM2.0's 7 nm bit cell, 0.042 µm² per bit, 218 ps access, 50 ps setup / hold;
register-file style leaves at 1.5 × and 2 × the bit cell for the asynchronous and the second read port) stands in for the
compiler's figures in `synth_report.md`; `neo_ip_ppa.py`'s estimate prices the same arrays at N5A compiled-macro densities.
The synthesis report with the macros swapped in is what replaces both.
