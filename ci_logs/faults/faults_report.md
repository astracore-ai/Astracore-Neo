# Fault campaign report (`faults` job; 02_dv/dv/cocotb/faults/)

Faults injected into the flops of the cocotb builds through the model's VPI (--public-flat-rw), each run classified against the fault-free run of the same driver: **detected** (a sticky cause or the watchdog; the latency is the first cycle the cause was seen after the injection), **masked** (results bit-exact, no cause), **dangerous** (results wrong, no cause), **hang** (the run did not end; hang_detected when the watchdog or the fetch timeout fired). DC = detected / (detected + dangerous), masked faults (safe in this workload) excluded; the interval is the 95 % Wilson interval of that fraction. The FTTI budget (10 ms, 20,000,000 cycles at 2 GHz) exceeds every run, so every cause seen is within it.

## The core build (16 x 8 neo_core, NCHK = 4, workload C1)

Seed 267, 21180 of 25000 faults (20000 sampled over the flops, 5000 over the memory arrays) run by 4 workers in 4507.8 s (849.4 ms per fault); the fault-free run 1698 cycles (0.399 s); population 3403 flop elements, 47581 bits.

| Outcome | Faults |
| --- | --- |
| detected | 7388 |
| masked | 13057 |
| dangerous | 717 |
| hang_detected | 17 |
| hang | 1 |

The whole flop population, each fault weighted by its block's bits / its block's sample: DC 92.7 %, dangerous 3.0 % of all faults, masked 58.7 %.

**Flops**

| Block | Bits | Faults | Det. | Masked | Dang. | Hang det. / undet. | DC (95 % CI) | Dangerous of all (95 % CI) | Latency median / max (cycles) | First causes |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |
| neo_mac_core | 35240 | 12379 | 5788 | 6154 | 437 | 0 / 0 | 93.0 % (92.3 % .. 93.6 %) | 3.5 % (3.2 % .. 3.9 %) | 27 / 600 | array_abft 5788 |
| neo_core | 6331 | 2229 | 2 | 2225 | 1 | 0 / 1 | 50.0 % (15.0 % .. 85.0 %) | 0.1 % (0.0 % .. 0.3 %) | 117 / 193 | seq_err 2 |
| requant | 3368 | 1204 | 333 | 871 | 0 | 0 / 0 | 100.0 % (98.9 % .. 100.0 %) | 0.0 % (0.0 % .. 0.3 %) | 780 / 1667 | rq_err 281, rq_tbl_perr 52 |
| neo_core.macro | 617 | 237 | 0 | 237 | 0 | 0 / 0 | n/a (none dangerous or detected) | 0.0 % (0.0 % .. 1.6 %) | - |  |
| acc_bank | 601 | 241 | 94 | 147 | 0 | 0 / 0 | 100.0 % (96.1 % .. 100.0 %) | 0.0 % (0.0 % .. 1.6 %) | 802 / 1649 | acc_abft 87, seq_err 7 |
| core_seq | 412 | 155 | 54 | 91 | 0 | 10 / 0 | 100.0 % (94.3 % .. 100.0 %) | 0.0 % (0.0 % .. 2.4 %) | 2 / 1316 | seq_err 64 |
| act_feeder | 380 | 154 | 74 | 73 | 0 | 7 / 0 | 100.0 % (95.5 % .. 100.0 %) | 0.0 % (0.0 % .. 2.4 %) | 12 / 263 | ctrl_err 81 |
| neo_mac_core_v02 | 280 | 120 | 0 | 40 | 80 | 0 / 0 | 0.0 % (0.0 % .. 4.6 %) | 66.7 % (57.8 % .. 74.5 %) | - |  |
| acc_bank.abft_checker | 164 | 83 | 38 | 45 | 0 | 0 / 0 | 100.0 % (90.8 % .. 100.0 %) | 0.0 % (0.0 % .. 4.4 %) | 787 / 1653 | acc_abft 38 |
| neo_mac_core.abft_checker | 164 | 74 | 49 | 25 | 0 | 0 / 0 | 100.0 % (92.7 % .. 100.0 %) | 0.0 % (0.0 % .. 4.9 %) | 3 / 27 | array_abft 49 |
| wbuf_mem.macro | 8 | 23 | 17 | 6 | 0 | 0 / 0 | 100.0 % (81.6 % .. 100.0 %) | 0.0 % (0.0 % .. 14.3 %) | 2 / 461 | seq_err 17 |
| act_feeder.macro | 7 | 24 | 18 | 6 | 0 | 0 / 0 | 100.0 % (82.4 % .. 100.0 %) | 0.0 % (0.0 % .. 13.8 %) | 2 / 151 | ctrl_err 18 |
| acc_bank.macro | 6 | 21 | 16 | 5 | 0 | 0 / 0 | 100.0 % (80.6 % .. 100.0 %) | 0.0 % (0.0 % .. 15.5 %) | 2 / 44 | seq_err 16 |
| wbuf_mem | 3 | 20 | 16 | 4 | 0 | 0 / 0 | 100.0 % (80.6 % .. 100.0 %) | 0.0 % (0.0 % .. 16.1 %) | 1 / 29 | wbuf_ce 6, seq_err 5, wbuf_ue 5 |

**Memory arrays (the SRAM leaves' cells; a quota per block)**

| Block | Bits | Faults | Det. | Masked | Dang. | Hang det. / undet. | DC (95 % CI) | Dangerous of all (95 % CI) | Latency median / max (cycles) | First causes |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |
| wbuf_mem.sram | 99840 | 1052 | 369 | 683 | 0 | 0 / 0 | 100.0 % (99.0 % .. 100.0 %) | 0.0 % (0.0 % .. 0.4 %) | 473 / 1562 | wbuf_ce 369 |
| neo_core.sram | 88320 | 1051 | 0 | 1051 | 0 | 0 / 0 | n/a (none dangerous or detected) | 0.0 % (0.0 % .. 0.4 %) | - |  |
| act_feeder.sram | 32768 | 1056 | 0 | 857 | 199 | 0 / 0 | 0.0 % (0.0 % .. 1.9 %) | 18.8 % (16.6 % .. 21.3 %) | - |  |
| acc_bank.sram | 18432 | 1057 | 520 | 537 | 0 | 0 / 0 | 100.0 % (99.3 % .. 100.0 %) | 0.0 % (0.0 % .. 0.4 %) | 771 / 1680 | acc_abft 520 |

Left out of the population: 2-D array (not reachable through cocotb on Verilator 5.020): neo_core 1072 bits; array not reachable through cocotb 1.9's VPI layer in this build (common.py): neo_core 84 bits, requant 64 bits.

## The tile build (2 x 2 mesh of 16 x 8 tiles, workload M1)

Seed 268, 17473 of 24998 faults (20000 sampled over the flops, 4998 over the memory arrays) run by 4 workers in 13369.1 s (3057.6 ms per fault); the fault-free run 3864 cycles (1.53 s), watchdog window 7728 cycles; population 3533 flop elements, 60592 bits.

| Outcome | Faults |
| --- | --- |
| detected | 3677 |
| masked | 13521 |
| dangerous | 236 |
| hang_detected | 39 |
| hang | 0 |

The whole flop population, each fault weighted by its block's bits / its block's sample: DC 97.4 %, dangerous 0.6 % of all faults, masked 75.2 %.

**Flops**

| Block | Bits | Faults | Det. | Masked | Dang. | Hang det. / undet. | DC (95 % CI) | Dangerous of all (95 % CI) | Latency median / max (cycles) | First causes |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |
| neo_mac_core | 18296 | 4064 | 2102 | 1959 | 3 | 0 / 0 | 99.9 % (99.6 % .. 100.0 %) | 0.1 % (0.0 % .. 0.2 %) | 49 / 2041 | array_abft 2102 |
| tile_nic | 14192 | 3172 | 129 | 3027 | 15 | 1 / 0 | 89.7 % (83.6 % .. 93.6 %) | 0.5 % (0.3 % .. 0.8 %) | 565 / 7511 | crc_err 92, array_abft 28, lost_err 7 |
| neo_core | 6331 | 1408 | 9 | 1399 | 0 | 0 / 0 | 100.0 % (70.1 % .. 100.0 %) | 0.0 % (0.0 % .. 0.3 %) | 3 / 635 | seq_err 9 |
| host_if | 3756 | 865 | 470 | 383 | 1 | 11 / 0 | 99.8 % (98.8 % .. 100.0 %) | 0.1 % (0.0 % .. 0.7 %) | 3 / 3601 | ctrl_path_err 480, watchdog 1 |
| requant | 3368 | 745 | 185 | 560 | 0 | 0 / 0 | 100.0 % (98.0 % .. 100.0 %) | 0.0 % (0.0 % .. 0.5 %) | 1668 / 3526 | rq_err 185 |
| neo_tile | 3080 | 740 | 4 | 736 | 0 | 0 / 0 | 100.0 % (51.0 % .. 100.0 %) | 0.0 % (0.0 % .. 0.5 %) | 2 / 2 | ctrl_path_err 4 |
| sram_bank.macro | 2496 | 574 | 35 | 539 | 0 | 0 / 0 | 100.0 % (90.1 % .. 100.0 %) | 0.0 % (0.0 % .. 0.7 %) | 19 / 251 | ecc_ce 35 |
| neo_core.macro | 2468 | 555 | 0 | 555 | 0 | 0 / 0 | n/a (none dangerous or detected) | 0.0 % (0.0 % .. 0.7 %) | - |  |
| noc_router | 1904 | 441 | 25 | 403 | 1 | 12 / 0 | 97.4 % (86.5 % .. 99.5 %) | 0.2 % (0.0 % .. 1.3 %) | 51 / 7672 | parity_err 28, crc_err 7, watchdog 2 |
| tile_nic.macro | 1180 | 281 | 1 | 252 | 28 | 0 / 0 | 3.4 % (0.6 % .. 17.2 %) | 10.0 % (7.0 % .. 14.0 %) | 265 / 265 | crc_err 1 |
| tile_dma | 848 | 212 | 125 | 86 | 0 | 1 / 0 | 100.0 % (97.0 % .. 100.0 %) | 0.0 % (0.0 % .. 1.8 %) | 3 / 4 | ctrl_path_err 126 |
| acc_bank | 601 | 160 | 51 | 109 | 0 | 0 / 0 | 100.0 % (93.0 % .. 100.0 %) | 0.0 % (0.0 % .. 2.3 %) | 1233 / 3048 | acc_abft 46, seq_err 4, crc_err 1 |
| core_seq | 412 | 102 | 35 | 61 | 0 | 6 / 0 | 100.0 % (91.4 % .. 100.0 %) | 0.0 % (0.0 % .. 3.6 %) | 3 / 3487 | seq_err 41 |
| act_feeder | 380 | 101 | 44 | 53 | 0 | 4 / 0 | 100.0 % (92.6 % .. 100.0 %) | 0.0 % (0.0 % .. 3.7 %) | 51 / 2124 | ctrl_err 48 |
| neo_mac_core_v02 | 280 | 79 | 0 | 30 | 49 | 0 / 0 | 0.0 % (0.0 % .. 7.3 %) | 62.0 % (51.0 % .. 71.9 %) | - |  |
| prog_mem | 264 | 87 | 2 | 83 | 1 | 1 / 0 | 75.0 % (30.1 % .. 95.4 %) | 1.1 % (0.2 % .. 6.2 %) | 1734 / 6448 | ctrl_path_err 1, watchdog 1, array_abft 1 |
| esm | 176 | 57 | 19 | 38 | 0 | 0 / 0 | 100.0 % (83.2 % .. 100.0 %) | 0.0 % (0.0 % .. 6.3 %) | 1 / 1 | acc_abft 4, ecc_ce 4, rq_tbl_perr 2 |
| mbist | 168 | 55 | 2 | 51 | 2 | 0 / 0 | 50.0 % (15.0 % .. 85.0 %) | 3.6 % (1.0 % .. 12.3 %) | 2 / 2 | bist_fail 2 |
| acc_bank.abft_checker | 164 | 53 | 30 | 23 | 0 | 0 / 0 | 100.0 % (88.6 % .. 100.0 %) | 0.0 % (0.0 % .. 6.8 %) | 2112 / 3495 | acc_abft 30 |
| neo_mac_core.abft_checker | 164 | 56 | 27 | 29 | 0 | 0 / 0 | 100.0 % (87.5 % .. 100.0 %) | 0.0 % (0.0 % .. 6.4 %) | 38 / 1887 | array_abft 27 |
| link_packer | 20 | 25 | 0 | 22 | 0 | 3 / 0 | 100.0 % (43.8 % .. 100.0 %) | 0.0 % (0.0 % .. 13.3 %) | 5281 / 6245 | watchdog 3 |
| sram_bank | 12 | 19 | 12 | 6 | 1 | 0 / 0 | 92.3 % (66.7 % .. 98.6 %) | 5.3 % (0.9 % .. 24.6 %) | 2 / 2 | ecc_ue 7, ecc_ce 5 |
| link_unpacker | 8 | 22 | 0 | 22 | 0 | 0 / 0 | n/a (none dangerous or detected) | 0.0 % (0.0 % .. 14.9 %) | - |  |
| wbuf_mem.macro | 8 | 20 | 10 | 10 | 0 | 0 / 0 | 100.0 % (72.2 % .. 100.0 %) | 0.0 % (0.0 % .. 16.1 %) | 4 / 2236 | seq_err 10 |
| act_feeder.macro | 7 | 17 | 11 | 6 | 0 | 0 / 0 | 100.0 % (74.1 % .. 100.0 %) | 0.0 % (0.0 % .. 18.4 %) | 9 / 996 | ctrl_err 11 |
| acc_bank.macro | 6 | 20 | 13 | 7 | 0 | 0 / 0 | 100.0 % (77.2 % .. 100.0 %) | 0.0 % (0.0 % .. 16.1 %) | 412 / 1658 | seq_err 13 |
| wbuf_mem | 3 | 18 | 12 | 6 | 0 | 0 / 0 | 100.0 % (75.7 % .. 100.0 %) | 0.0 % (0.0 % .. 17.6 %) | 2 / 1327 | wbuf_ce 8, seq_err 3, wbuf_ue 1 |

**Memory arrays (the SRAM leaves' cells; a quota per block)**

| Block | Bits | Faults | Det. | Masked | Dang. | Hang det. / undet. | DC (95 % CI) | Dangerous of all (95 % CI) | Latency median / max (cycles) | First causes |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |
| sram_bank.sram | 638976 | 593 | 16 | 577 | 0 | 0 / 0 | 100.0 % (80.6 % .. 100.0 %) | 0.0 % (0.0 % .. 0.6 %) | 587 / 1663 | ecc_ce 16 |
| tile_nic.sram | 79616 | 584 | 0 | 572 | 12 | 0 / 0 | 0.0 % (0.0 % .. 24.3 %) | 2.1 % (1.2 % .. 3.6 %) | - |  |
| wbuf_mem.sram | 59904 | 574 | 162 | 412 | 0 | 0 / 0 | 100.0 % (97.7 % .. 100.0 %) | 0.0 % (0.0 % .. 0.7 %) | 740 / 3030 | wbuf_ce 162 |
| act_feeder.sram | 32768 | 598 | 0 | 475 | 123 | 0 / 0 | 0.0 % (0.0 % .. 3.0 %) | 20.6 % (17.5 % .. 24.0 %) | - |  |
| neo_core.sram | 20736 | 586 | 0 | 586 | 0 | 0 / 0 | n/a (none dangerous or detected) | 0.0 % (0.0 % .. 0.7 %) | - |  |
| acc_bank.sram | 18432 | 590 | 146 | 444 | 0 | 0 / 0 | 100.0 % (97.4 % .. 100.0 %) | 0.0 % (0.0 % .. 0.6 %) | 1097 / 3471 | acc_abft 146 |

Left out of the population: 2-D array (not reachable through cocotb on Verilator 5.020): prog_mem 9984 bits, neo_core 4288 bits, tile_nic 4096 bits, noc_router 3360 bits; idle core (tiles other than the computing one): neo_core.sram 264960 bits, wbuf_mem.sram 179712 bits, act_feeder.sram 98304 bits, neo_mac_core 65544 bits, acc_bank.sram 55296 bits, neo_core 19245 bits, requant 10296 bits, acc_bank 1803 bits, core_seq 1236 bits, act_feeder 1140 bits, neo_mac_core_v02 840 bits, acc_bank.abft_checker 492 bits, neo_mac_core.abft_checker 492 bits, wbuf_mem.macro 24 bits, act_feeder.macro 21 bits, acc_bank.macro 18 bits, wbuf_mem 9 bits; array not reachable through cocotb 1.9's VPI layer in this build (common.py): neo_core.sram 67584 bits, neo_mac_core 3552 bits, neo_core 84 bits, requant 64 bits, noc_router 60 bits, tile_nic 8 bits.

## The comparator storage of drop 0.41 (item 2) in the sample

| Register | Faults | Detected | Masked | Dangerous | Hang | Latency max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| feeder inb_q | 2 | 0 | 2 | 0 | 0 | - |
| feeder first_oq | 1 | 0 | 1 | 0 | 0 | - |
| feeder addr_dq (copy) | 3 | 2 | 1 | 0 | 0 | 31 |
| activation buffer port-B latch | 41 | 29 | 12 | 0 | 0 | 996 |
| core w_load_q | 1 | 1 | 0 | 0 | 0 | 3 |
| core d_w_raddr_q (copy) | 5 | 3 | 2 | 0 | 0 | 635 |
| weight buffer port-B latch | 43 | 27 | 16 | 0 | 0 | 2236 |
| weight buffer ra0_q | 11 | 8 | 3 | 0 | 0 | 1327 |
| acc first1 | 1 | 1 | 0 | 0 | 0 | 3 |
| acc idx1 | 6 | 5 | 1 | 0 | 0 | 3 |
| acc idx1_n (copy) | 4 | 4 | 0 | 0 | 0 | 3 |
| acc rbq_n (copy) | 4 | 1 | 3 | 0 | 0 | 2 |
| accumulator port-B latch | 41 | 29 | 12 | 0 | 0 | 1658 |

The undetected faults' flops, grouped by block: `undetected.md` (954 faults).
