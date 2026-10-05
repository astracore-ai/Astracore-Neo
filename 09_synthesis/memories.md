# Memory macros for synthesis (drop 0.16)

The RTL infers every memory as an array so that it simulates anywhere. For synthesis and the PPA report, replace
these arrays by memory-compiler macros (TSMC N5A, or the licensee's) through the usual wrapper-and-swap flow:

| Array (module.signal) | Organisation (silicon configuration) | Macro type | Notes |
| --- | --- | --- | --- |
| `sram_bank.mem` | 524,288 x 39 bit | single-port HD SRAM, 1 cycle | the ECC is in RTL around the macro; MBIST drives the macro ports through `bank_bist_wrap` |
| `acc_bank` accumulator | 512 x (33 x 32) bit | two-port register file / 2P SRAM | read-modify-write per row; the ABFT check is on the read data |
| `wbuf_mem.mem` | 1,024 x (9 x 39) bit | single-port SRAM | per-lane SECDED in RTL |
| `act_feeder.abuf` | 2,048 x 256 bit | single-port SRAM | regions are address ranges, no macro feature needed |
| `prog_mem.mem` | 16 x (2 x 39) bit | flops | keep as flops; SECDED in RTL |
| `noc_router.fifo` | 2 x 85 bit per port | flops | keep as flops |
| `requant` tables | 32 x 29 bit | flops | parity in RTL |

The `neo_ip_ppa.py` estimate prices these arrays at compiled-macro densities; the synthesis report with the
macros swapped in is what replaces it.
