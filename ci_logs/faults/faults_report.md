# Fault campaign report (`faults` job; 02_dv/dv/cocotb/faults/)

Faults injected into the flops of the cocotb builds through the model's VPI (--public-flat-rw), each run classified against the fault-free run of the same driver: **detected** (a sticky cause or the watchdog; the latency is the first cycle the cause was seen after the injection), **masked** (results bit-exact, no cause), **dangerous** (results wrong, no cause), **hang** (the run did not end; hang_detected when the watchdog or the fetch timeout fired). DC = detected / (detected + dangerous), masked faults (safe in this workload) excluded; the interval is the 95 % Wilson interval of that fraction. The FTTI budget (10 ms, 20,000,000 cycles at 2 GHz) exceeds every run, so every cause seen is within it.

## The core build (16 x 8 neo_core, NCHK = 4, workload C1)

Seed 251, 21228 of 25000 faults (20000 sampled over the flops, 5000 over the memory arrays) run by 4 workers in 4507.8 s (847.4 ms per fault); the fault-free run 1698 cycles (0.4 s); population 3403 flop elements, 47581 bits.

| Outcome | Faults |
| --- | --- |
| detected | 7457 |
| masked | 13054 |
| dangerous | 698 |
| hang_detected | 19 |
| hang | 0 |

The whole flop population, each fault weighted by its block's bits / its block's sample: DC 92.9 %, dangerous 2.9 % of all faults, masked 58.5 %.

**Flops**

| Block | Bits | Faults | Det. | Masked | Dang. | Hang det. / undet. | DC (95 % CI) | Dangerous of all (95 % CI) | Latency median / max (cycles) | First causes |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |
| neo_mac_core | 35240 | 12341 | 5777 | 6146 | 418 | 0 / 0 | 93.3 % (92.6 % .. 93.9 %) | 3.4 % (3.1 % .. 3.7 %) | 27 / 540 | array_abft 5777 |
| neo_core | 6331 | 2263 | 3 | 2258 | 2 | 0 / 0 | 60.0 % (23.1 % .. 88.2 %) | 0.1 % (0.0 % .. 0.3 %) | 4 / 244 | seq_err 3 |
| requant | 3368 | 1172 | 350 | 822 | 0 | 0 / 0 | 100.0 % (98.9 % .. 100.0 %) | 0.0 % (0.0 % .. 0.3 %) | 777 / 1690 | rq_err 286, rq_tbl_perr 64 |
| neo_core.macro | 617 | 242 | 0 | 242 | 0 | 0 / 0 | n/a (none dangerous or detected) | 0.0 % (0.0 % .. 1.6 %) | - |  |
| acc_bank | 601 | 236 | 113 | 123 | 0 | 0 / 0 | 100.0 % (96.7 % .. 100.0 %) | 0.0 % (0.0 % .. 1.6 %) | 706 / 1655 | acc_abft 102, seq_err 11 |
| core_seq | 412 | 164 | 65 | 84 | 0 | 15 / 0 | 100.0 % (95.4 % .. 100.0 %) | 0.0 % (0.0 % .. 2.3 %) | 2 / 1363 | seq_err 80 |
| act_feeder | 380 | 143 | 66 | 73 | 0 | 4 / 0 | 100.0 % (94.8 % .. 100.0 %) | 0.0 % (0.0 % .. 2.6 %) | 9 / 917 | ctrl_err 70 |
| neo_mac_core_v02 | 280 | 124 | 0 | 40 | 84 | 0 / 0 | 0.0 % (0.0 % .. 4.4 %) | 67.7 % (59.1 % .. 75.3 %) | - |  |
| acc_bank.abft_checker | 164 | 73 | 36 | 37 | 0 | 0 / 0 | 100.0 % (90.4 % .. 100.0 %) | 0.0 % (0.0 % .. 5.0 %) | 1006 / 1616 | acc_abft 36 |
| neo_mac_core.abft_checker | 164 | 79 | 56 | 23 | 0 | 0 / 0 | 100.0 % (93.6 % .. 100.0 %) | 0.0 % (0.0 % .. 4.6 %) | 3 / 33 | array_abft 56 |
| wbuf_mem.macro | 8 | 26 | 14 | 12 | 0 | 0 / 0 | 100.0 % (78.5 % .. 100.0 %) | 0.0 % (0.0 % .. 12.9 %) | 59 / 713 | seq_err 14 |
| act_feeder.macro | 7 | 23 | 16 | 7 | 0 | 0 / 0 | 100.0 % (80.6 % .. 100.0 %) | 0.0 % (0.0 % .. 14.3 %) | 2 / 40 | ctrl_err 16 |
| acc_bank.macro | 6 | 23 | 19 | 4 | 0 | 0 / 0 | 100.0 % (83.2 % .. 100.0 %) | 0.0 % (0.0 % .. 14.3 %) | 4 / 55 | seq_err 19 |
| wbuf_mem | 3 | 23 | 20 | 3 | 0 | 0 / 0 | 100.0 % (83.9 % .. 100.0 %) | 0.0 % (0.0 % .. 14.3 %) | 1 / 21 | wbuf_ce 7, seq_err 7, wbuf_ue 6 |

**Memory arrays (the SRAM leaves' cells; a quota per block)**

| Block | Bits | Faults | Det. | Masked | Dang. | Hang det. / undet. | DC (95 % CI) | Dangerous of all (95 % CI) | Latency median / max (cycles) | First causes |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |
| wbuf_mem.sram | 99840 | 1077 | 380 | 697 | 0 | 0 / 0 | 100.0 % (99.0 % .. 100.0 %) | 0.0 % (0.0 % .. 0.4 %) | 488 / 1508 | wbuf_ce 380 |
| neo_core.sram | 88320 | 1081 | 0 | 1081 | 0 | 0 / 0 | n/a (none dangerous or detected) | 0.0 % (0.0 % .. 0.4 %) | - |  |
| act_feeder.sram | 32768 | 1070 | 0 | 876 | 194 | 0 / 0 | 0.0 % (0.0 % .. 1.9 %) | 18.1 % (15.9 % .. 20.6 %) | - |  |
| acc_bank.sram | 18432 | 1068 | 542 | 526 | 0 | 0 / 0 | 100.0 % (99.3 % .. 100.0 %) | 0.0 % (0.0 % .. 0.4 %) | 815 / 1685 | acc_abft 542 |

Left out of the population: 2-D array (not reachable through cocotb on Verilator 5.020): neo_core 1072 bits; array not reachable through cocotb 1.9's VPI layer in this build (common.py): neo_core 84 bits, requant 64 bits.

## The tile build (2 x 2 mesh of 16 x 8 tiles, workload M1)

Seed 252, 17726 of 24998 faults (20000 sampled over the flops, 4998 over the memory arrays) run by 4 workers in 13364.1 s (3012.8 ms per fault); the fault-free run 3864 cycles (1.494 s), watchdog window 7728 cycles; population 3533 flop elements, 60592 bits.

| Outcome | Faults |
| --- | --- |
| detected | 3802 |
| masked | 13653 |
| dangerous | 234 |
| hang_detected | 37 |
| hang | 0 |

The whole flop population, each fault weighted by its block's bits / its block's sample: DC 97.3 %, dangerous 0.7 % of all faults, masked 74.7 %.

**Flops**

| Block | Bits | Faults | Det. | Masked | Dang. | Hang det. / undet. | DC (95 % CI) | Dangerous of all (95 % CI) | Latency median / max (cycles) | First causes |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |
| neo_mac_core | 18296 | 4184 | 2223 | 1959 | 2 | 0 / 0 | 99.9 % (99.7 % .. 100.0 %) | 0.0 % (0.0 % .. 0.2 %) | 47 / 2035 | array_abft 2223 |
| tile_nic | 14192 | 3221 | 149 | 3049 | 19 | 4 / 0 | 89.0 % (83.4 % .. 92.8 %) | 0.6 % (0.4 % .. 0.9 %) | 1407 / 7227 | crc_err 94, array_abft 43, lost_err 16 |
| neo_core | 6331 | 1459 | 13 | 1445 | 0 | 1 / 0 | 100.0 % (78.5 % .. 100.0 %) | 0.0 % (0.0 % .. 0.3 %) | 3 / 7406 | seq_err 13, watchdog 1 |
| host_if | 3756 | 863 | 438 | 418 | 0 | 7 / 0 | 100.0 % (99.1 % .. 100.0 %) | 0.0 % (0.0 % .. 0.4 %) | 3 / 1330 | ctrl_path_err 442, iso_err 3 |
| requant | 3368 | 774 | 185 | 589 | 0 | 0 / 0 | 100.0 % (98.0 % .. 100.0 %) | 0.0 % (0.0 % .. 0.5 %) | 1657 / 3505 | rq_err 185 |
| neo_tile | 3080 | 697 | 1 | 696 | 0 | 0 / 0 | 100.0 % (20.7 % .. 100.0 %) | 0.0 % (0.0 % .. 0.5 %) | 2 / 2 | ctrl_path_err 1 |
| sram_bank.macro | 2496 | 580 | 35 | 545 | 0 | 0 / 0 | 100.0 % (90.1 % .. 100.0 %) | 0.0 % (0.0 % .. 0.7 %) | 18 / 87 | ecc_ce 35 |
| neo_core.macro | 2468 | 553 | 0 | 553 | 0 | 0 / 0 | n/a (none dangerous or detected) | 0.0 % (0.0 % .. 0.7 %) | - |  |
| noc_router | 1904 | 448 | 27 | 412 | 0 | 9 / 0 | 100.0 % (90.4 % .. 100.0 %) | 0.0 % (0.0 % .. 0.9 %) | 161 / 4793 | parity_err 21, crc_err 13, lost_err 4 |
| tile_nic.macro | 1180 | 276 | 2 | 237 | 37 | 0 / 0 | 5.1 % (1.4 % .. 16.9 %) | 13.4 % (9.9 % .. 17.9 %) | 20 / 24 | crc_err 2 |
| tile_dma | 848 | 213 | 122 | 89 | 0 | 2 / 0 | 100.0 % (97.0 % .. 100.0 %) | 0.0 % (0.0 % .. 1.8 %) | 3 / 469 | ctrl_path_err 124 |
| acc_bank | 601 | 160 | 50 | 110 | 0 | 0 / 0 | 100.0 % (92.9 % .. 100.0 %) | 0.0 % (0.0 % .. 2.3 %) | 1319 / 3493 | acc_abft 43, seq_err 6, crc_err 1 |
| core_seq | 412 | 114 | 37 | 73 | 0 | 4 / 0 | 100.0 % (91.4 % .. 100.0 %) | 0.0 % (0.0 % .. 3.3 %) | 3 / 2346 | seq_err 41 |
| act_feeder | 380 | 103 | 39 | 58 | 0 | 6 / 0 | 100.0 % (92.1 % .. 100.0 %) | 0.0 % (0.0 % .. 3.6 %) | 29 / 1689 | ctrl_err 45 |
| neo_mac_core_v02 | 280 | 83 | 0 | 40 | 43 | 0 / 0 | 0.0 % (0.0 % .. 8.2 %) | 51.8 % (41.2 % .. 62.2 %) | - |  |
| prog_mem | 264 | 74 | 0 | 71 | 3 | 0 / 0 | 0.0 % (0.0 % .. 56.2 %) | 4.1 % (1.4 % .. 11.3 %) | - |  |
| esm | 176 | 60 | 21 | 39 | 0 | 0 / 0 | 100.0 % (84.5 % .. 100.0 %) | 0.0 % (0.0 % .. 6.0 %) | 1 / 1 | acc_abft 3, ctrl_path_err 3, seq_err 2 |
| mbist | 168 | 53 | 3 | 48 | 2 | 0 / 0 | 60.0 % (23.1 % .. 88.2 %) | 3.8 % (1.0 % .. 12.8 %) | 2 / 5 | bist_fail 3 |
| neo_mac_core.abft_checker | 164 | 60 | 42 | 18 | 0 | 0 / 0 | 100.0 % (91.6 % .. 100.0 %) | 0.0 % (0.0 % .. 6.0 %) | 18 / 1886 | array_abft 42 |
| acc_bank.abft_checker | 164 | 62 | 27 | 35 | 0 | 0 / 0 | 100.0 % (87.5 % .. 100.0 %) | 0.0 % (0.0 % .. 5.8 %) | 1539 / 3378 | acc_abft 27 |
| link_packer | 20 | 22 | 2 | 15 | 1 | 4 / 0 | 85.7 % (48.7 % .. 97.4 %) | 4.5 % (0.8 % .. 21.8 %) | 5974 / 6544 | watchdog 4, crc_err 2, lost_err 2 |
| sram_bank | 12 | 22 | 9 | 13 | 0 | 0 / 0 | 100.0 % (70.1 % .. 100.0 %) | 0.0 % (0.0 % .. 14.9 %) | 2 / 2 | ecc_ue 5, ecc_ce 4 |
| link_unpacker | 8 | 22 | 0 | 22 | 0 | 0 / 0 | n/a (none dangerous or detected) | 0.0 % (0.0 % .. 14.9 %) | - |  |
| wbuf_mem.macro | 8 | 17 | 4 | 13 | 0 | 0 / 0 | 100.0 % (51.0 % .. 100.0 %) | 0.0 % (0.0 % .. 18.4 %) | 70 / 290 | seq_err 4 |
| act_feeder.macro | 7 | 17 | 8 | 9 | 0 | 0 / 0 | 100.0 % (67.6 % .. 100.0 %) | 0.0 % (0.0 % .. 18.4 %) | 106 / 1656 | ctrl_err 8 |
| acc_bank.macro | 6 | 19 | 12 | 7 | 0 | 0 / 0 | 100.0 % (75.7 % .. 100.0 %) | 0.0 % (0.0 % .. 16.8 %) | 317 / 1812 | seq_err 12 |
| wbuf_mem | 3 | 15 | 10 | 5 | 0 | 0 / 0 | 100.0 % (72.2 % .. 100.0 %) | 0.0 % (0.0 % .. 20.4 %) | 2 / 1808 | seq_err 4, wbuf_ce 3, wbuf_ue 3 |

**Memory arrays (the SRAM leaves' cells; a quota per block)**

| Block | Bits | Faults | Det. | Masked | Dang. | Hang det. / undet. | DC (95 % CI) | Dangerous of all (95 % CI) | Latency median / max (cycles) | First causes |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |
| sram_bank.sram | 638976 | 599 | 10 | 589 | 0 | 0 / 0 | 100.0 % (72.2 % .. 100.0 %) | 0.0 % (0.0 % .. 0.6 %) | 533 / 1405 | ecc_ce 10 |
| tile_nic.sram | 79616 | 593 | 0 | 585 | 8 | 0 / 0 | 0.0 % (0.0 % .. 32.4 %) | 1.3 % (0.7 % .. 2.6 %) | - |  |
| wbuf_mem.sram | 59904 | 592 | 181 | 411 | 0 | 0 / 0 | 100.0 % (97.9 % .. 100.0 %) | 0.0 % (0.0 % .. 0.6 %) | 842 / 3072 | wbuf_ce 181 |
| act_feeder.sram | 32768 | 616 | 0 | 497 | 119 | 0 / 0 | 0.0 % (0.0 % .. 3.1 %) | 19.3 % (16.4 % .. 22.6 %) | - |  |
| neo_core.sram | 20736 | 568 | 0 | 568 | 0 | 0 / 0 | n/a (none dangerous or detected) | 0.0 % (0.0 % .. 0.7 %) | - |  |
| acc_bank.sram | 18432 | 587 | 152 | 435 | 0 | 0 / 0 | 100.0 % (97.5 % .. 100.0 %) | 0.0 % (0.0 % .. 0.7 %) | 984 / 3366 | acc_abft 152 |

Left out of the population: 2-D array (not reachable through cocotb on Verilator 5.020): prog_mem 9984 bits, neo_core 4288 bits, tile_nic 4096 bits, noc_router 3360 bits; idle core (tiles other than the computing one): neo_core.sram 264960 bits, wbuf_mem.sram 179712 bits, act_feeder.sram 98304 bits, neo_mac_core 65544 bits, acc_bank.sram 55296 bits, neo_core 19245 bits, requant 10296 bits, acc_bank 1803 bits, core_seq 1236 bits, act_feeder 1140 bits, neo_mac_core_v02 840 bits, acc_bank.abft_checker 492 bits, neo_mac_core.abft_checker 492 bits, wbuf_mem.macro 24 bits, act_feeder.macro 21 bits, acc_bank.macro 18 bits, wbuf_mem 9 bits; array not reachable through cocotb 1.9's VPI layer in this build (common.py): neo_core.sram 67584 bits, neo_mac_core 3552 bits, neo_core 84 bits, requant 64 bits, noc_router 60 bits, tile_nic 8 bits.

## The comparator storage of drop 0.41 (item 2) in the sample

| Register | Faults | Detected | Masked | Dangerous | Hang | Latency max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| feeder inb_q | 1 | 1 | 0 | 0 | 0 | 2 |
| feeder a0_q | 1 | 1 | 0 | 0 | 0 | 2 |
| feeder first_q | 1 | 1 | 0 | 0 | 0 | 3 |
| feeder addr_dq (copy) | 4 | 2 | 2 | 0 | 0 | 1073 |
| activation buffer port-B latch | 40 | 24 | 16 | 0 | 0 | 1656 |
| core w_load_q | 1 | 1 | 0 | 0 | 0 | 2 |
| core d_w_raddr_q (copy) | 2 | 2 | 0 | 0 | 0 | 244 |
| weight buffer port-B latch | 43 | 18 | 25 | 0 | 0 | 713 |
| weight buffer ra0_q | 14 | 11 | 3 | 0 | 0 | 1808 |
| acc v1 | 1 | 1 | 0 | 0 | 0 | 2 |
| acc first1 | 1 | 1 | 0 | 0 | 0 | 2 |
| acc idx1 | 5 | 5 | 0 | 0 | 0 | 466 |
| acc fwd1 | 3 | 3 | 0 | 0 | 0 | 3 |
| acc v1_n (copy) | 1 | 1 | 0 | 0 | 0 | 2 |
| acc first1_n (copy) | 2 | 2 | 0 | 0 | 0 | 2 |
| acc idx1_n (copy) | 3 | 3 | 0 | 0 | 0 | 4 |
| acc rbq_n (copy) | 2 | 1 | 1 | 0 | 0 | 2 |
| accumulator port-B latch | 42 | 31 | 11 | 0 | 0 | 1812 |

The undetected faults' flops, grouped by block: `undetected.md` (932 faults).
